# coding=utf-8

# Regression tests for the per-tool usage values carried in
# spool_weight_updated_after_print.
#
# PrintJobHistoryExtended needs this job's consumption at a point in time where it
# actually exists. api_getLastPrintJobUsage() cannot serve that: its snapshot is only
# written after the per-tool loop in commitOdometerData(), and OctoPrint defines no
# order between two plugins' PRINT_DONE handlers - the other plugin was measured
# reading the previous job's snapshot ~800ms before ours was written. The event fires
# right after saveSpool() for each tool, so the values ride along with it.
#
# The event also fires for a mid-print spool change (selectSpool with
# commitCurrentSpoolValues), which is NOT the end of a job. printStatus tells the two
# apart: it is None in that case. Without it a consumer would book a spool change as a
# finished job and then count the same filament again when the job really ends.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_PrintUsageEventPayload.py -v

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

EVENT_NAME = "plugin_spoolmanagerextended_spool_weight_updated_after_print"

################################################################################################ fakes


class FakeSettings(object):
    def __init__(self):
        self._values = {SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL: "€"}

    def get(self, keys):
        return self._values.get(keys[0])

    def get_boolean(self, keys):
        return False


class FakeEventBus(object):
    def __init__(self):
        self.firedEvents = []

    def fire(self, eventName, payload=None):
        self.firedEvents.append((eventName, payload))


class FakeOdometer(object):
    def __init__(self, extrusionAmounts):
        self.extrusionAmounts = extrusionAmounts
        self.resetCalled = False

    def getExtrusionAmount(self):
        return self.extrusionAmounts

    def reset_extruded_length(self):
        self.resetCalled = True


class FakePlugin(object):
    """
    Binds the real commitOdometerData/_sendPayload2EventBus/_calculateWeight from the
    production class onto a stand-in, instead of driving the full SpoolmanagerPlugin
    (which needs OctoPrint's plugin loader to inject self._printer etc).
    """

    commitOdometerData = SpoolmanagerPlugin.commitOdometerData
    _sendPayload2EventBus = SpoolmanagerPlugin._sendPayload2EventBus
    _calculateWeight = SpoolmanagerPlugin._calculateWeight
    MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE = (
        SpoolmanagerPlugin.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
    )

    def __init__(self, databaseManager, selectedSpools, extrusionAmounts):
        self._databaseManager = databaseManager
        self._settings = FakeSettings()
        self._identifier = "SpoolManagerExtended"
        self._event_bus = FakeEventBus()
        self._logger = logging.getLogger("test.printusage")
        self._mqttManager = None
        self._lastPrintJobUsage = None
        self._slicedUsageAlreadyBooked = False
        self.metaDataFilamentLengths = []
        self.myFilamentOdometer = FakeOdometer(extrusionAmounts)
        self._selectedSpools = selectedSpools
        self.metaDataReadCount = 0

    def loadSelectedSpools(self):
        return self._selectedSpools

    def _readingFilamentMetaData(self):
        self.metaDataReadCount += 1

    def _sendDataToClient(self, payloadDict):
        pass


class TestPrintUsageEventPayload(unittest.TestCase):
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

    def _create(self, **fields):
        # 1.75mm / 1.24 g/cm3 is the usual PLA pairing; tests that care about the
        # weight override density so the expected value cannot arise by coincidence
        defaults = {
            "displayName": "Test Spool",
            "isActive": True,
            "isTemplate": None,
            "diameter": 1.75,
            "density": 1.24,
        }
        defaults.update(fields)
        return SpoolModel.create(**defaults)

    def _plugin(self, selectedSpools, extrusionAmounts):
        return FakePlugin(self.databaseManager, selectedSpools, extrusionAmounts)

    def _usageEvents(self, plugin):
        return [
            payload
            for name, payload in plugin._event_bus.firedEvents
            if name == EVENT_NAME
        ]

    ############################################################################ payload contents

    def test_payloadCarriesUsageValuesOfThisJob(self):
        # usedLength must be this job's extrusion, not the spool's running total
        spool = self._create(displayName="Partly Used Spool", usedLength=1000.0)
        plugin = self._plugin([spool], {0: 250.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payloads = self._usageEvents(plugin)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["usedLength"], 250.0)
        self.assertEqual(payloads[0]["toolId"], 0)
        self.assertEqual(payloads[0]["source"], "odometer")
        self.assertEqual(payloads[0]["printStatus"], "success")
        # spool total advanced by exactly this job's amount
        self.assertEqual(spool.usedLength, 1250.0)

    def test_payloadUsageValuesMatchTheApiSnapshot(self):
        spool = self._create(
            displayName="Cross Check Spool", cost=20.0, totalWeight=1000.0
        )
        plugin = self._plugin([spool], {0: 300.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payload = self._usageEvents(plugin)[0]
        snapshot = plugin._lastPrintJobUsage["tools"][0]
        for key in ("usedLength", "usedWeight", "usedCost", "source"):
            self.assertEqual(payload[key], snapshot[key], "mismatch on %s" % key)

    def test_existingPayloadFieldsAreUnchanged(self):
        spool = self._create(
            displayName="Legacy Consumer Spool",
            material="PETG",
            colorName="Signal Red",
            totalWeight=1000.0,
            usedWeight=100.0,
        )
        plugin = self._plugin([spool], {0: 200.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payload = self._usageEvents(plugin)[0]
        self.assertEqual(payload["databaseId"], spool.databaseId)
        self.assertEqual(payload["spoolName"], "Legacy Consumer Spool")
        self.assertEqual(payload["material"], "PETG")
        self.assertEqual(payload["colorName"], "Signal Red")
        self.assertEqual(payload["remainingWeight"], spool.remainingWeight)

    ############################################################################ None-vs-zero

    def test_usedCostIsNoneWithoutPrice(self):
        # None means "no price on file" and must not be normalised to 0.0,
        # which would read as "this filament is free"
        spool = self._create(
            displayName="Unpriced Spool", cost=None, totalWeight=1000.0
        )
        plugin = self._plugin([spool], {0: 200.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        self.assertIsNone(self._usageEvents(plugin)[0]["usedCost"])

    def test_usedWeightIsNoneWithoutDensity(self):
        spool = self._create(displayName="Unknown Density Spool", density=None)
        plugin = self._plugin([spool], {0: 200.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payload = self._usageEvents(plugin)[0]
        self.assertIsNone(payload["usedWeight"])
        self.assertIsNone(payload["usedCost"])
        self.assertEqual(payload["usedLength"], 200.0)

    ############################################################################ printStatus

    def test_printStatusIsNoneOnMidPrintSpoolChange(self):
        # selectSpool with commitCurrentSpoolValues books the current tool mid-print;
        # a consumer must not mistake that for the end of a job
        spool = self._create(displayName="Swapped Mid Print")
        plugin = self._plugin([spool], {0: 150.0})

        plugin.commitOdometerData()

        payload = self._usageEvents(plugin)[0]
        self.assertIsNone(payload["printStatus"])
        self.assertEqual(payload["usedLength"], 150.0)
        # no job ended, so no api snapshot was taken
        self.assertIsNone(plugin._lastPrintJobUsage)

    def test_eventFiresForFailedAndCanceledPrints(self):
        for printStatus in ("failed", "canceled"):
            spool = self._create(displayName="Aborted Print Spool")
            plugin = self._plugin([spool], {0: 120.0})

            plugin.commitOdometerData(printStatus=printStatus, printDuration=600.0)

            payloads = self._usageEvents(plugin)
            self.assertEqual(len(payloads), 1, "no event for %s" % printStatus)
            self.assertEqual(payloads[0]["printStatus"], printStatus)
            self.assertEqual(payloads[0]["usedLength"], 120.0)

    ############################################################################ source

    def test_sourceIsSlicedMetaDataWhenOdometerTrackedNothing(self):
        # connector printers (bambu, moonraker) stream nothing through octoprint, so
        # the odometer stays at 0 and the sliced length is booked instead. 407.0 is a
        # value the odometer cannot produce here - it reports 0.0.
        spool = self._create(displayName="Connector Printer Spool")
        plugin = self._plugin([spool], {0: 0.0})
        plugin.metaDataFilamentLengths = [407.0]

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payload = self._usageEvents(plugin)[0]
        self.assertEqual(payload["source"], "slicedMetaData")
        self.assertEqual(payload["usedLength"], 407.0)

    ############################################################################ tools without events

    def test_toolWithoutSpoolFiresNoEvent(self):
        # tool 0 has no spool at all - a consumer cannot expect one event per tool
        spool = self._create(displayName="Second Tool Spool")
        plugin = self._plugin([None, spool], {0: 0.0, 1: 180.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payloads = self._usageEvents(plugin)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["toolId"], 1)
        self.assertEqual(payloads[0]["usedLength"], 180.0)

    def test_toolWithoutExtrusionStillFiresWithZeroUsage(self):
        # a selected tool the job never used reports 0.0, not None, so it passes the
        # "is None" guard below the sliced fallback and does fire an event. Documented
        # here because a consumer has to expect usedLength 0.0 for idle tools.
        idleSpool = self._create(displayName="Idle Tool Spool")
        usedSpool = self._create(displayName="Working Tool Spool")
        plugin = self._plugin([idleSpool, usedSpool], {0: 0.0, 1: 180.0})

        plugin.commitOdometerData(printStatus="success", printDuration=600.0)

        payloads = self._usageEvents(plugin)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["toolId"], 0)
        self.assertEqual(payloads[0]["usedLength"], 0.0)
        self.assertEqual(payloads[1]["usedLength"], 180.0)


if __name__ == "__main__":
    unittest.main()
