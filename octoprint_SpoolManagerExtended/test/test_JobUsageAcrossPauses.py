# coding=utf-8

# Tests for the job usage report (print_job_usage_booked / api_getLastPrintJobUsage) of a
# job that was paused or had its spool changed mid-print.
#
# A pause and a mid-print spool change both book what the odometer counted so far and
# reset it. The spools' totals came out right, but the report at the job's end carried
# only what came after the last of them. Observed on a streamed (serial) print that was
# paused and then emergency-stopped: the pause booked 653.24mm, the report at the failed
# end said 0.0mm, and PrintJobHistoryExtended stored the job without any filament.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_JobUsageAcrossPauses.py -v

import json
import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

SPOOL_EVENT = "plugin_spoolmanagerextended_spool_weight_updated_after_print"
JOB_EVENT = "plugin_spoolmanagerextended_print_job_usage_booked"

################################################################################################ fakes


class FakeSettings(object):
    def get(self, keys):
        return {SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL: "€"}.get(keys[0])


class FakeEventBus(object):
    def __init__(self):
        self.firedEvents = []

    def fire(self, eventName, payload=None):
        self.firedEvents.append((eventName, payload))


class FakeOdometer(object):
    # counts per tool until it is reset, like NewFilamentOdometer
    def __init__(self, toolCount):
        self.extrusionAmounts = [0.0] * toolCount

    def extrude(self, toolIndex, length):
        self.extrusionAmounts[toolIndex] += length

    def getExtrusionAmount(self):
        return self.extrusionAmounts

    def reset_extruded_length(self):
        self.extrusionAmounts = [0.0] * len(self.extrusionAmounts)

    def reset(self):
        self.reset_extruded_length()


def _productionMethod(name):
    # getattr, so this module still loads against code that predates the method
    return getattr(SpoolmanagerPlugin, name, None)


class NotOnMoonraker(object):
    # stands in for U1RfidManager on a printer that is not connected through Moonraker (a
    # serial one): no connector parameters, so Klipper is never asked
    def _getConnectorParams(self):
        return None


class FakePlugin(object):
    commitOdometerData = SpoolmanagerPlugin.commitOdometerData
    _on_printJobStarted = SpoolmanagerPlugin._on_printJobStarted
    _sendPayload2EventBus = SpoolmanagerPlugin._sendPayload2EventBus
    _calculateWeight = SpoolmanagerPlugin._calculateWeight
    _moonrakerUsagePerTool = SpoolmanagerPlugin._moonrakerUsagePerTool
    _moonrakerJobFilename = staticmethod(_productionMethod("_moonrakerJobFilename"))
    _readMoonrakerFilamentUsed = SpoolmanagerPlugin._readMoonrakerFilamentUsed
    _printJobFileLocation = SpoolmanagerPlugin._printJobFileLocation
    _printJobIdentity = SpoolmanagerPlugin._printJobIdentity
    _logToolWithoutSpool = SpoolmanagerPlugin._logToolWithoutSpool
    _recordJobUsage = _productionMethod("_recordJobUsage")
    _jobUsageReportTools = _productionMethod("_jobUsageReportTools")
    JOB_USAGE_SPOOL_FIELDS = getattr(SpoolmanagerPlugin, "JOB_USAGE_SPOOL_FIELDS", None)
    MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE = (
        SpoolmanagerPlugin.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
    )

    def __init__(self, databaseManager, selectedSpools):
        self._databaseManager = databaseManager
        self._settings = FakeSettings()
        self._identifier = "SpoolManagerExtended"
        self._event_bus = FakeEventBus()
        self._logger = logging.getLogger("test.jobusageacrosspauses")
        self._mqttManager = None
        self._u1RfidManager = NotOnMoonraker()
        self._lastPrintJobUsage = None
        self._slicedUsageAlreadyBooked = False
        self._printJobStartedTimestamp = None
        self._printJobStartFileLocation = (None, None)
        self._moonrakerUsageBooked = 0.0
        self._printJobUsageReported = False
        self._printJobUsagePerTool = {}
        self.metaDataFilamentLengths = []
        self.myFilamentOdometer = FakeOdometer(len(selectedSpools))
        self.selectedSpools = list(selectedSpools)

    def loadSelectedSpools(self):
        return self.selectedSpools

    def _getCurrentJobFileLocation(self):
        # a job OctoPrint streams itself, on a printer that is not on Moonraker
        return "local", "job.gcode"

    def _readingFilamentMetaData(self):
        # set by the test where the sliced fallback matters
        pass

    def _slicedLengthsPerTool(self, origin, path):
        return []

    def _sendDataToClient(self, payloadDict):
        pass

    def events(self, eventName):
        return [
            payload
            for name, payload in self._event_bus.firedEvents
            if name == eventName
        ]


class _JobTestCase(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)
        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True
        self.databaseManager.connectoToDatabase = lambda *a, **k: None
        self.databaseManager.closeDatabase = lambda *a, **k: None

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _spool(self, displayName, **fields):
        values = {
            "displayName": displayName,
            "isActive": True,
            "diameter": 1.75,
            "density": 1.24,
            "usedLength": 0.0,
        }
        values.update(fields)
        return SpoolModel.create(**values)

    def _startJob(self, *selectedSpools):
        plugin = FakePlugin(self.databaseManager, selectedSpools)
        plugin._on_printJobStarted()
        return plugin

    def _extrudeAndCommit(self, plugin, toolIndex, length, **commitArguments):
        plugin.myFilamentOdometer.extrude(toolIndex, length)
        plugin.commitOdometerData(**commitArguments)

    def _report(self, plugin):
        reports = plugin.events(JOB_EVENT)
        self.assertEqual(len(reports), 1)
        return reports[0]


class TestReportCoversTheWholeJob(_JobTestCase):
    def test_pauseThenEndReportsBothParts(self):
        # 850.0 is only reachable by adding both parts - the end alone booked 250.0
        spool = self._spool("Paused Spool", cost=25.0, totalWeight=1000.0)
        plugin = self._startJob(spool)

        self._extrudeAndCommit(
            plugin, 0, 600.0, printStatus="paused", printDuration=1200.0
        )
        self._extrudeAndCommit(
            plugin, 0, 250.0, printStatus="success", printDuration=2400.0
        )

        tool = self._report(plugin)["tools"][0]
        expectedWeight = plugin._calculateWeight(
            600.0, 1.75, 1.24
        ) + plugin._calculateWeight(250.0, 1.75, 1.24)
        self.assertEqual(tool["usedLength"], 850.0)
        self.assertAlmostEqual(tool["usedWeight"], expectedWeight)
        self.assertAlmostEqual(tool["usedCost"], 25.0 / 1000.0 * expectedWeight)
        self.assertEqual(tool["databaseId"], spool.databaseId)
        self.assertEqual(tool["source"], "odometer")
        # the spool itself was booked part by part, as before
        self.assertEqual(spool.usedLength, 850.0)

    def test_failedEndAfterPauseReportsWhatThePauseBooked(self):
        # the observed case: paused, then stopped - the end itself counted nothing
        spool = self._spool("Stopped Spool")
        plugin = self._startJob(spool)

        self._extrudeAndCommit(
            plugin, 0, 653.24, printStatus="paused", printDuration=1200.0
        )
        plugin.commitOdometerData(printStatus="failed", printDuration=1230.0)

        report = self._report(plugin)
        self.assertEqual(report["printStatus"], "failed")
        self.assertEqual(report["tools"][0]["usedLength"], 653.24)
        self.assertEqual(report, plugin._lastPrintJobUsage)

    def test_everyPauseIsCounted(self):
        spool = self._spool("Often Paused Spool")
        plugin = self._startJob(spool)

        for length in (100.0, 200.0, 300.0):
            self._extrudeAndCommit(
                plugin, 0, length, printStatus="paused", printDuration=600.0
            )
        self._extrudeAndCommit(
            plugin, 0, 50.0, printStatus="canceled", printDuration=2400.0
        )

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(tool["usedLength"], 650.0)
        self.assertEqual(
            [(s["databaseId"], s["usedLength"]) for s in tool["spools"]],
            [(spool.databaseId, 650.0)],
        )

    def test_perToolEventsStillCarryEachPartAheadOfTheReport(self):
        # a consumer of the per-tool event gets each booking, the report comes last
        spool = self._spool("Event Order Spool")
        plugin = self._startJob(spool)

        self._extrudeAndCommit(
            plugin, 0, 600.0, printStatus="paused", printDuration=1200.0
        )
        self._extrudeAndCommit(
            plugin, 0, 250.0, printStatus="success", printDuration=2400.0
        )

        fired = [
            (name, payload["printStatus"], payload.get("usedLength"))
            for name, payload in plugin._event_bus.firedEvents
            if name in (SPOOL_EVENT, JOB_EVENT)
        ]
        self.assertEqual(
            fired,
            [
                (SPOOL_EVENT, "paused", 600.0),
                (SPOOL_EVENT, "success", 250.0),
                (JOB_EVENT, "success", None),
            ],
        )

    def test_newJobStartsFromZero(self):
        spool = self._spool("Two Jobs Spool")
        plugin = self._startJob(spool)
        self._extrudeAndCommit(
            plugin, 0, 400.0, printStatus="paused", printDuration=600.0
        )
        self._extrudeAndCommit(
            plugin, 0, 100.0, printStatus="success", printDuration=1200.0
        )

        plugin._on_printJobStarted()
        self._extrudeAndCommit(
            plugin, 0, 70.0, printStatus="success", printDuration=900.0
        )

        reports = plugin.events(JOB_EVENT)
        self.assertEqual(
            [report["tools"][0]["usedLength"] for report in reports], [500.0, 70.0]
        )

    def test_repeatedEndEventKeepsTheJobTotal(self):
        # a connector may report the end twice; a second report must not shrink the job
        spool = self._spool("Double End Spool")
        plugin = self._startJob(spool)
        self._extrudeAndCommit(
            plugin, 0, 300.0, printStatus="paused", printDuration=600.0
        )
        self._extrudeAndCommit(
            plugin, 0, 120.0, printStatus="success", printDuration=1200.0
        )

        self._extrudeAndCommit(
            plugin, 0, 5.0, printStatus="success", printDuration=1201.0
        )

        reports = plugin.events(JOB_EVENT)
        self.assertEqual(
            [report["tools"][0]["usedLength"] for report in reports], [420.0, 425.0]
        )

    def test_reportCanBeSerialized(self):
        # event payloads travel to the browser as JSON
        firstSpool = self._spool("Serialized First Spool")
        secondSpool = self._spool("Serialized Second Spool")
        plugin = self._startJob(firstSpool)
        self._extrudeAndCommit(plugin, 0, 100.0)
        plugin.selectedSpools[0] = secondSpool
        self._extrudeAndCommit(
            plugin, 0, 200.0, printStatus="success", printDuration=900.0
        )

        json.dumps(self._report(plugin))


class TestSpoolChangeDuringTheJob(_JobTestCase):
    def test_bothSpoolsAreListedAndSummed(self):
        # different densities and prices: the total weight and cost can only come from
        # adding up what each spool booked itself
        firstSpool = self._spool(
            "First Spool",
            vendor="Vendor A",
            material="PLA",
            density=1.24,
            cost=20.0,
            totalWeight=1000.0,
        )
        secondSpool = self._spool(
            "Second Spool",
            vendor="Vendor B",
            material="PETG",
            density=1.27,
            cost=30.0,
            totalWeight=1000.0,
        )
        plugin = self._startJob(firstSpool)

        # selectSpool with commitCurrentSpoolValues books the old spool mid-print
        self._extrudeAndCommit(plugin, 0, 400.0)
        plugin.selectedSpools[0] = secondSpool
        self._extrudeAndCommit(
            plugin, 0, 300.0, printStatus="success", printDuration=2400.0
        )

        tool = self._report(plugin)["tools"][0]
        firstWeight = plugin._calculateWeight(400.0, 1.75, 1.24)
        secondWeight = plugin._calculateWeight(300.0, 1.75, 1.27)
        self.assertEqual(tool["usedLength"], 700.0)
        self.assertAlmostEqual(tool["usedWeight"], firstWeight + secondWeight)
        self.assertAlmostEqual(
            tool["usedCost"], 0.02 * firstWeight + 0.03 * secondWeight
        )
        self.assertEqual(tool["databaseId"], secondSpool.databaseId)
        self.assertEqual(tool["material"], "PETG")
        self.assertEqual(tool["density"], 1.27)
        self.assertEqual(
            [
                (s["databaseId"], s["vendor"], s["material"], s["usedLength"])
                for s in tool["spools"]
            ],
            [
                (firstSpool.databaseId, "Vendor A", "PLA", 400.0),
                (secondSpool.databaseId, "Vendor B", "PETG", 300.0),
            ],
        )
        self.assertAlmostEqual(tool["spools"][0]["usedWeight"], firstWeight)
        self.assertAlmostEqual(tool["spools"][1]["usedCost"], 0.03 * secondWeight)

    def test_spoolUsedAgainLaterIsListedOnceAndIsTheIdentity(self):
        firstSpool = self._spool("Returning Spool")
        secondSpool = self._spool("Visiting Spool")
        plugin = self._startJob(firstSpool)

        self._extrudeAndCommit(plugin, 0, 100.0)
        plugin.selectedSpools[0] = secondSpool
        self._extrudeAndCommit(plugin, 0, 200.0)
        plugin.selectedSpools[0] = firstSpool
        self._extrudeAndCommit(
            plugin, 0, 300.0, printStatus="success", printDuration=2400.0
        )

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(
            [(s["databaseId"], s["usedLength"]) for s in tool["spools"]],
            [(firstSpool.databaseId, 400.0), (secondSpool.databaseId, 200.0)],
        )
        self.assertEqual(tool["usedLength"], 600.0)
        # the spool printed with last, not the one listed last
        self.assertEqual(tool["databaseId"], firstSpool.databaseId)

    def test_weightIsUnknownWhenOneSpoolHasNoDensity(self):
        # a sum without one of its parts would look complete
        firstSpool = self._spool("Unknown Density Spool", density=None)
        secondSpool = self._spool("Known Density Spool")
        plugin = self._startJob(firstSpool)

        self._extrudeAndCommit(plugin, 0, 400.0)
        plugin.selectedSpools[0] = secondSpool
        self._extrudeAndCommit(
            plugin, 0, 300.0, printStatus="success", printDuration=2400.0
        )

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(tool["usedLength"], 700.0)
        self.assertIsNone(tool["usedWeight"])
        self.assertIsNone(tool["usedCost"])
        self.assertIsNone(tool["spools"][0]["usedWeight"])
        self.assertIsNotNone(tool["spools"][1]["usedWeight"])

    def test_spoolInsertedAfterTheLastBookingIsNotCredited(self):
        # changed while paused, then stopped before it extruded anything
        usedSpool = self._spool("Used Spool")
        unusedSpool = self._spool("Inserted Spool")
        plugin = self._startJob(usedSpool)

        self._extrudeAndCommit(
            plugin, 0, 400.0, printStatus="paused", printDuration=1200.0
        )
        plugin.selectedSpools[0] = unusedSpool
        plugin.commitOdometerData(printStatus="canceled", printDuration=1300.0)

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(tool["databaseId"], usedSpool.databaseId)
        self.assertEqual(tool["usedLength"], 400.0)
        self.assertEqual(
            [s["databaseId"] for s in tool["spools"]], [usedSpool.databaseId]
        )


class TestToolsInTheReport(_JobTestCase):
    def test_spoolsAreListedForASingleSpoolToo(self):
        spool = self._spool("Single Spool", vendor="Vendor C", material="ABS")
        plugin = self._startJob(spool)

        self._extrudeAndCommit(
            plugin, 0, 250.0, printStatus="success", printDuration=900.0
        )

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(len(tool["spools"]), 1)
        self.assertEqual(
            sorted(tool["spools"][0].keys()),
            sorted(
                [
                    "databaseId",
                    "spoolName",
                    "vendor",
                    "material",
                    "usedLength",
                    "usedWeight",
                    "usedCost",
                ]
            ),
        )
        self.assertEqual(tool["spools"][0]["usedLength"], 250.0)
        self.assertEqual(tool["spools"][0]["spoolName"], "Single Spool")

    def test_toolWithoutSpoolAtTheEndStillReportsWhatItBooked(self):
        # the spool was taken out during the pause; what it printed belongs to the job
        spool = self._spool("Removed Spool")
        plugin = self._startJob(spool)

        self._extrudeAndCommit(
            plugin, 0, 500.0, printStatus="paused", printDuration=1200.0
        )
        plugin.selectedSpools[0] = None
        plugin.commitOdometerData(printStatus="canceled", printDuration=1300.0)

        tool = self._report(plugin)["tools"][0]
        self.assertIsNotNone(tool)
        self.assertEqual(tool["databaseId"], spool.databaseId)
        self.assertEqual(tool["usedLength"], 500.0)

    def test_eachToolIndexOnceAndInPlace(self):
        firstToolSpool = self._spool("Tool 0 Spool")
        secondToolSpool = self._spool("Tool 1 Spool")
        plugin = self._startJob(firstToolSpool, secondToolSpool)

        plugin.myFilamentOdometer.extrude(1, 80.0)
        self._extrudeAndCommit(
            plugin, 0, 300.0, printStatus="paused", printDuration=600.0
        )
        self._extrudeAndCommit(
            plugin, 1, 40.0, printStatus="success", printDuration=1200.0
        )

        tools = self._report(plugin)["tools"]
        self.assertEqual([tool["toolIndex"] for tool in tools], [0, 1])
        self.assertEqual([tool["usedLength"] for tool in tools], [300.0, 120.0])

    def test_idleToolKeepsItsSpoolWithZeroUsage(self):
        # a selected tool the job never used reports 0.0, as before
        idleSpool = self._spool("Idle Tool Spool")
        workingSpool = self._spool("Working Tool Spool")
        plugin = self._startJob(idleSpool, workingSpool)

        self._extrudeAndCommit(
            plugin, 1, 180.0, printStatus="success", printDuration=900.0
        )

        idleTool = self._report(plugin)["tools"][0]
        self.assertEqual(idleTool["databaseId"], idleSpool.databaseId)
        self.assertEqual(idleTool["usedLength"], 0.0)
        self.assertEqual(
            [(s["databaseId"], s["usedLength"]) for s in idleTool["spools"]],
            [(idleSpool.databaseId, 0.0)],
        )

    def test_zeroBookingsDoNotMakeTheSourceMixed(self):
        # a pause on a printer whose odometer counts nothing books 0mm from the
        # odometer; the job's end then books the sliced length
        spool = self._spool("Connector Printer Spool")
        plugin = self._startJob(spool)

        plugin.commitOdometerData(printStatus="paused", printDuration=1200.0)
        plugin.metaDataFilamentLengths = [407.0]
        plugin.commitOdometerData(printStatus="success", printDuration=2400.0)

        tool = self._report(plugin)["tools"][0]
        self.assertEqual(tool["source"], "slicedMetaData")
        self.assertEqual(tool["usedLength"], 407.0)
        self.assertEqual(len(tool["spools"]), 1)


if __name__ == "__main__":
    unittest.main()
