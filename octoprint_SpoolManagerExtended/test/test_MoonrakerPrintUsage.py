# coding=utf-8

# Tests for booking a printer-storage job on a Moonraker printer by what Klipper counted,
# and for the job-level report print_job_usage_booked / api_getJobFilamentUsage().
#
# Run with:  python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_MoonrakerPrintUsage.py

import copy
import datetime
import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

SPOOL_EVENT = "plugin_spoolmanagerextended_spool_weight_updated_after_print"
JOB_EVENT = "plugin_spoolmanagerextended_print_job_usage_booked"

JOB_PATH = "job.gcode"
SLICED_LENGTH = 31260.58
COUNTED_LENGTH = 31257.78
CANCELED_COUNT = 5737.0

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
    # a connector print: nothing streamed through OctoPrint, so nothing counted
    def __init__(self, extrusionAmounts=None):
        self.extrusionAmounts = extrusionAmounts or {}

    def getExtrusionAmount(self):
        return self.extrusionAmounts

    def reset_extruded_length(self):
        pass


class FakeMoonraker(object):
    # stands in for U1RfidManager's Moonraker helpers
    def __init__(self):
        self.printStats = None  # None: Moonraker unreachable
        self.requestedPaths = []

    def _getConnectorParams(self):
        return {"host": "printer.invalid", "port": 7125}

    def _httpGet(self, host, port, path, timeoutSeconds=5):
        self.requestedPaths.append(path)
        if self.printStats is None:
            return None
        return {"result": {"status": {"print_stats": self.printStats}}}


def _productionMethod(name):
    # getattr, so this module still loads against code that predates the method
    return getattr(SpoolmanagerPlugin, name, None)


class FakePlugin(object):
    commitOdometerData = SpoolmanagerPlugin.commitOdometerData
    _sendPayload2EventBus = SpoolmanagerPlugin._sendPayload2EventBus
    _calculateWeight = SpoolmanagerPlugin._calculateWeight
    _moonrakerUsagePerTool = _productionMethod("_moonrakerUsagePerTool")
    _readMoonrakerFilamentUsed = _productionMethod("_readMoonrakerFilamentUsed")
    _slicedLengthsPerTool = _productionMethod("_slicedLengthsPerTool")
    _printJobFileLocation = _productionMethod("_printJobFileLocation")
    _printJobIdentity = _productionMethod("_printJobIdentity")
    api_getJobFilamentUsage = _productionMethod("api_getJobFilamentUsage")
    MOONRAKER_FINISHED_PRINT_STATES = getattr(
        SpoolmanagerPlugin, "MOONRAKER_FINISHED_PRINT_STATES", None
    )
    MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE = (
        SpoolmanagerPlugin.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
    )

    def __init__(self, selectedSpools, slicedFilament, extrusionAmounts=None):
        self._settings = FakeSettings()
        self._identifier = "SpoolManagerExtended"
        self._event_bus = FakeEventBus()
        self._logger = logging.getLogger("test.moonrakerusage")
        self._mqttManager = None
        self._printer = None
        self._u1RfidManager = FakeMoonraker()
        self._lastPrintJobUsage = None
        self._slicedUsageAlreadyBooked = False
        self._printJobStartedTimestamp = None
        self._printJobStartFileLocation = ("printer", JOB_PATH)
        self._moonrakerUsageBooked = 0.0
        self._printJobUsageReported = False
        self.currentJobLocation = ("printer", JOB_PATH)
        self.slicedFilament = slicedFilament
        self.filamentMetaDataRequests = []
        self.metaDataFilamentLengths = []
        self.myFilamentOdometer = FakeOdometer(extrusionAmounts)
        self._selectedSpools = selectedSpools

    def loadSelectedSpools(self):
        return self._selectedSpools

    def _getCurrentJobFileLocation(self):
        return self.currentJobLocation

    def _getFilamentMetaData(self, origin, path, plate=1):
        self.filamentMetaDataRequests.append((origin, path, plate))
        return self.slicedFilament

    def _readingFilamentMetaData(self):
        # what the production method makes of the same metadata
        self.metaDataFilamentLengths = []
        for toolName, toolData in (self.slicedFilament or {}).items():
            toolIndex = int(toolName[4:])
            self.metaDataFilamentLengths += [0.0] * (
                toolIndex + 1 - len(self.metaDataFilamentLengths)
            )
            self.metaDataFilamentLengths[toolIndex] = toolData["length"]

    def _sendDataToClient(self, payloadDict):
        pass

    def events(self, eventName):
        return [
            payload
            for name, payload in self._event_bus.firedEvents
            if name == eventName
        ]


class _BookingTestCase(unittest.TestCase):
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

    def _spool(self, displayName):
        return SpoolModel.create(
            displayName=displayName,
            isActive=True,
            diameter=1.75,
            density=1.04,
            usedLength=0.0,
        )

    def _plugin(self, selectedSpools, slicedFilament, extrusionAmounts=None):
        plugin = FakePlugin(selectedSpools, slicedFilament, extrusionAmounts)
        plugin._databaseManager = self.databaseManager
        return plugin

    def _singleToolJob(self, **printStats):
        # a job sliced for tool 3 only
        spool = self._spool("Tool 3 spool")
        plugin = self._plugin(
            [None, None, None, spool], {"tool3": {"length": SLICED_LENGTH}}
        )
        if printStats:
            plugin._u1RfidManager.printStats = dict(filename=JOB_PATH, **printStats)
        return spool, plugin


class TestBookingWhatKlipperCounted(_BookingTestCase):
    def test_canceledJobBooksWhatKlipperExtruded(self):
        spool, plugin = self._singleToolJob(
            state="cancelled", filament_used=CANCELED_COUNT
        )

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, CANCELED_COUNT)
        events = plugin.events(SPOOL_EVENT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["toolId"], 3)
        self.assertEqual(events[0]["usedLength"], CANCELED_COUNT)
        self.assertEqual(events[0]["source"], "moonraker")
        self.assertEqual(events[0]["printStatus"], "canceled")

    def test_failedJobBooksWhatKlipperExtruded(self):
        spool, plugin = self._singleToolJob(state="error", filament_used=CANCELED_COUNT)

        plugin.commitOdometerData(printStatus="failed", printDuration=2820.0)

        self.assertEqual(spool.usedLength, CANCELED_COUNT)
        self.assertEqual(plugin.events(SPOOL_EVENT)[0]["source"], "moonraker")

    def test_successfulJobBooksTheCountRatherThanTheSlicedPlan(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        self.assertEqual(spool.usedLength, COUNTED_LENGTH)
        self.assertEqual(plugin.events(SPOOL_EVENT)[0]["source"], "moonraker")

    def test_jobOnSeveralToolsIsSplitBySlicedShares(self):
        # Klipper keeps one count per job
        firstSpool = self._spool("Tool 0 spool")
        thirdSpool = self._spool("Tool 2 spool")
        plugin = self._plugin(
            [firstSpool, None, thirdSpool],
            {"tool0": {"length": 1000.0}, "tool2": {"length": 3000.0}},
        )
        plugin._u1RfidManager.printStats = {
            "filename": JOB_PATH,
            "state": "complete",
            "filament_used": 2000.0,
        }

        plugin.commitOdometerData(printStatus="success", printDuration=3600.0)

        self.assertEqual(firstSpool.usedLength, 500.0)
        self.assertEqual(thirdSpool.usedLength, 1500.0)
        self.assertEqual(
            {event["source"] for event in plugin.events(SPOOL_EVENT)},
            {"moonrakerEstimated"},
        )

    def test_jobClearedBeforeItsEndIsTakenFromItsStart(self):
        # a connector may clear the current job before the end event arrives
        spool, plugin = self._singleToolJob(
            state="cancelled", filament_used=CANCELED_COUNT
        )
        plugin.currentJobLocation = (None, None)

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, CANCELED_COUNT)

    def test_repeatedEndEventBooksTheCountOnce(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)
        plugin.commitOdometerData(printStatus="success", printDuration=13801.0)

        self.assertEqual(spool.usedLength, COUNTED_LENGTH)
        self.assertEqual(len(plugin.events(SPOOL_EVENT)), 1)
        self.assertEqual(len(plugin.events(JOB_EVENT)), 1)
        self.assertEqual(
            plugin._lastPrintJobUsage["tools"][3]["usedLength"], COUNTED_LENGTH
        )


class TestWhenKlipperCountIsNotUsed(_BookingTestCase):
    def test_countOfAnotherJobIsIgnored(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )
        plugin._u1RfidManager.printStats["filename"] = "other.gcode"

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        self.assertEqual(spool.usedLength, SLICED_LENGTH)
        self.assertEqual(plugin.events(SPOOL_EVENT)[0]["source"], "slicedMetaData")

    def test_countThatIsNotFinalIsIgnored(self):
        spool, plugin = self._singleToolJob(
            state="printing", filament_used=COUNTED_LENGTH
        )

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        self.assertEqual(spool.usedLength, SLICED_LENGTH)

    def test_unreachableMoonrakerLeavesTheSlicedFallback(self):
        spool, plugin = self._singleToolJob()

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        self.assertEqual(spool.usedLength, SLICED_LENGTH)
        self.assertEqual(plugin.events(SPOOL_EVENT)[0]["source"], "slicedMetaData")

    def test_errorWhileReadingTheCountLeavesTheSlicedFallback(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )

        def failingMetaData(*args, **kwargs):
            raise RuntimeError("simulated metadata failure")

        plugin._getFilamentMetaData = failingMetaData

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        self.assertEqual(spool.usedLength, SLICED_LENGTH)
        self.assertEqual(len(plugin.events(JOB_EVENT)), 1)

    def test_localJobKeepsTheOdometerAndNeverAsksMoonraker(self):
        spool = self._spool("Streamed job spool")
        plugin = self._plugin(
            [spool], {"tool0": {"length": SLICED_LENGTH}}, extrusionAmounts={0: 250.0}
        )
        plugin.currentJobLocation = ("local", JOB_PATH)
        plugin._printJobStartFileLocation = ("local", JOB_PATH)

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        self.assertEqual(spool.usedLength, 250.0)
        self.assertEqual(plugin._u1RfidManager.requestedPaths, [])

    def test_pauseAndMidPrintSpoolChangeDoNotAskMoonraker(self):
        # only the job's end books Klipper's count, so it is booked whole, once
        spool, plugin = self._singleToolJob(
            state="paused", filament_used=CANCELED_COUNT
        )

        plugin.commitOdometerData(printStatus="paused", printDuration=1200.0)
        plugin.commitOdometerData()

        self.assertEqual(plugin._u1RfidManager.requestedPaths, [])
        self.assertEqual(spool.usedLength, 0.0)


class TestJobUsageReport(_BookingTestCase):
    def test_reportCarriesTheSnapshotAndTheJob(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )
        startedAt = datetime.datetime(2026, 9, 24, 19, 5, 0)
        plugin._printJobStartedTimestamp = startedAt.timestamp()
        plugin.currentJobLocation = ("printer", "folder/" + JOB_PATH)
        plugin._u1RfidManager.printStats["filename"] = "folder/" + JOB_PATH

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)

        reports = plugin.events(JOB_EVENT)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0], plugin._lastPrintJobUsage)
        self.assertEqual(
            reports[0]["job"],
            {
                "origin": "printer",
                "path": "folder/" + JOB_PATH,
                "name": JOB_PATH,
                "printStartDateTime": startedAt.isoformat(),
            },
        )
        self.assertEqual(reports[0]["printStatus"], "success")
        self.assertEqual(reports[0]["tools"][3]["usedLength"], COUNTED_LENGTH)
        self.assertEqual(reports[0]["tools"][3]["databaseId"], spool.databaseId)

    def test_reportIsSentAlsoWhenNothingWasBooked(self):
        # Moonraker unreachable and no sliced fallback for a cancel: nothing to book, yet
        # a consumer waiting for the report must get it
        spool, plugin = self._singleToolJob()

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        reports = plugin.events(JOB_EVENT)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["printStatus"], "canceled")
        self.assertEqual(reports[0]["tools"], [None, None, None, None])

    def test_reportIsACopy(self):
        spool, plugin = self._singleToolJob(
            state="complete", filament_used=COUNTED_LENGTH
        )

        plugin.commitOdometerData(printStatus="success", printDuration=13800.0)
        plugin.events(JOB_EVENT)[0]["tools"][3]["usedLength"] = 0.0

        self.assertEqual(
            plugin._lastPrintJobUsage["tools"][3]["usedLength"], COUNTED_LENGTH
        )

    def test_noReportForPauseOrMidPrintSpoolChange(self):
        spool, plugin = self._singleToolJob()

        plugin.commitOdometerData(printStatus="paused", printDuration=1200.0)
        plugin.commitOdometerData()

        self.assertEqual(plugin.events(JOB_EVENT), [])


class TestJobFilamentUsageApi(_BookingTestCase):
    SLICED = {
        "tool0": {"length": 0.0, "volume": 0.0},
        "tool3": {"length": 42117.06, "volume": 101.3},
    }

    def test_usageByPhysicalToolsWithoutUnusedTools(self):
        plugin = self._plugin([], copy.deepcopy(self.SLICED))

        self.assertEqual(
            plugin.api_getJobFilamentUsage(),
            {"tool3": {"length": 42117.06, "volume": 101.3}},
        )
        self.assertEqual(plugin.filamentMetaDataRequests, [("printer", JOB_PATH, 1)])

    def test_usageForAnExplicitFile(self):
        plugin = self._plugin([], copy.deepcopy(self.SLICED))

        plugin.api_getJobFilamentUsage("local", "other.gcode")

        self.assertEqual(plugin.filamentMetaDataRequests, [("local", "other.gcode", 1)])

    def test_resultIsACopy(self):
        plugin = self._plugin([], copy.deepcopy(self.SLICED))

        plugin.api_getJobFilamentUsage()["tool3"]["length"] = 0.0

        self.assertEqual(plugin.slicedFilament["tool3"]["length"], 42117.06)

    def test_noneWithoutJobOrMetadata(self):
        plugin = self._plugin([], copy.deepcopy(self.SLICED))
        plugin.currentJobLocation = (None, None)
        plugin._printJobStartFileLocation = (None, None)
        self.assertIsNone(plugin.api_getJobFilamentUsage())

        plugin = self._plugin([], None)
        self.assertIsNone(plugin.api_getJobFilamentUsage())


if __name__ == "__main__":
    unittest.main()
