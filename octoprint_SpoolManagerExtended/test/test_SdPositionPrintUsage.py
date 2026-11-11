# coding=utf-8

# Tests for booking a job a serial printer prints by itself from its SD card and ends
# early: the file position it reported last (M27) is mapped onto the extrusion counted
# when the file was uploaded (PrinterUploadAnalysis.usageAtPosition), source
# "sdPositionEstimated". A finished SD job keeps booking the sliced plan.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_SdPositionPrintUsage.py

import logging
import types
import unittest

import peewee

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

SPOOL_EVENT = "plugin_spoolmanagerextended_spool_weight_updated_after_print"
JOB_EVENT = "plugin_spoolmanagerextended_print_job_usage_booked"

JOB_PATH = "rocket~1.gco"
SLICED_LENGTH = 311.23
FILE_POSITION = 123456
FILE_SIZE = 456789
LENGTH_AT_POSITION = 98.76

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
    # the printer prints by itself: nothing streamed through OctoPrint, nothing counted
    def getExtrusionAmount(self):
        return {}

    def reset_extruded_length(self):
        pass


class NotOnMoonraker(object):
    def _getConnectorParams(self):
        return None


class OnMoonraker(object):
    def __init__(self, filamentUsed):
        self.filamentUsed = filamentUsed

    def _getConnectorParams(self):
        return {"host": "printer.invalid", "port": 7125}

    def _httpGet(self, host, port, path, timeoutSeconds=5):
        return {
            "result": {
                "status": {
                    "print_stats": {
                        "filename": JOB_PATH,
                        "state": "cancelled",
                        "filament_used": self.filamentUsed,
                    }
                }
            }
        }


class FakeAnalysis(object):
    def __init__(self, usage):
        self.usage = usage
        self.asked = []

    def usageAtPosition(self, path, filePosition, fileSizeOnPrinter=None):
        self.asked.append((path, filePosition, fileSizeOnPrinter))
        return self.usage


class FakePlugin(object):
    commitOdometerData = SpoolmanagerPlugin.commitOdometerData
    _sendPayload2EventBus = SpoolmanagerPlugin._sendPayload2EventBus
    _calculateWeight = SpoolmanagerPlugin._calculateWeight
    _moonrakerUsagePerTool = SpoolmanagerPlugin._moonrakerUsagePerTool
    _moonrakerJobFilename = staticmethod(SpoolmanagerPlugin._moonrakerJobFilename)
    _readMoonrakerFilamentUsed = SpoolmanagerPlugin._readMoonrakerFilamentUsed
    _sdPositionUsagePerTool = SpoolmanagerPlugin._sdPositionUsagePerTool
    _slicedLengthsPerTool = SpoolmanagerPlugin._slicedLengthsPerTool
    _printJobFileLocation = SpoolmanagerPlugin._printJobFileLocation
    _printJobIdentity = SpoolmanagerPlugin._printJobIdentity
    _logToolWithoutSpool = SpoolmanagerPlugin._logToolWithoutSpool
    _recordJobUsage = SpoolmanagerPlugin._recordJobUsage
    _jobUsageReportTools = SpoolmanagerPlugin._jobUsageReportTools
    api_getJobFilamentUsage = SpoolmanagerPlugin.api_getJobFilamentUsage
    JOB_USAGE_SPOOL_FIELDS = SpoolmanagerPlugin.JOB_USAGE_SPOOL_FIELDS
    MOONRAKER_FINISHED_PRINT_STATES = SpoolmanagerPlugin.MOONRAKER_FINISHED_PRINT_STATES
    MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE = (
        SpoolmanagerPlugin.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
    )

    def __init__(self, selectedSpools, slicedFilament, usageAtPosition, filePosition):
        self._settings = FakeSettings()
        self._identifier = "SpoolManagerExtended"
        self._event_bus = FakeEventBus()
        self._logger = logging.getLogger("test.sdpositionusage")
        self._mqttManager = None
        self._printer = None
        self._u1RfidManager = NotOnMoonraker()
        self._lastPrintJobUsage = None
        self._slicedUsageAlreadyBooked = False
        self._printJobStartedTimestamp = None
        self._printJobStartFileLocation = ("printer", JOB_PATH)
        self._moonrakerUsageBooked = 0.0
        self._moonrakerFilamentCache = {}
        self._printJobUsageReported = False
        self._printJobUsagePerTool = {}
        self._printerUploadAnalysis = FakeAnalysis(usageAtPosition)
        self._printJobEndFilePosition = filePosition
        self._sdPositionUsageBooked = False
        self.slicedFilament = slicedFilament
        self.metaDataFilamentLengths = []
        self.myFilamentOdometer = FakeOdometer()
        self._selectedSpools = selectedSpools

    def loadSelectedSpools(self):
        return self._selectedSpools

    def _getCurrentJobFileLocation(self):
        return self._printJobStartFileLocation

    def _getFilamentMetaData(self, origin, path, plate=1):
        return self.slicedFilament

    def _readingFilamentMetaData(self):
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


################################################################################################ tests


class _SdBookingTestCase(unittest.TestCase):
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

    def _sdJob(
        self,
        usageAtPosition={"tool0": {"length": LENGTH_AT_POSITION}},
        filePosition=(FILE_POSITION, FILE_SIZE),
        withSpool=True,
    ):
        spool = None
        if withSpool:
            spool = SpoolModel.create(
                displayName="SD spool",
                isActive=True,
                diameter=1.75,
                density=1.24,
                usedLength=0.0,
            )
        plugin = FakePlugin(
            [spool], {"tool0": {"length": SLICED_LENGTH}}, usageAtPosition, filePosition
        )
        plugin._databaseManager = self.databaseManager
        return spool, plugin


class TestBookingBySdPosition(_SdBookingTestCase):
    def test_canceledJobBooksTheExtrusionUpToTheFilePosition(self):
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, LENGTH_AT_POSITION)
        self.assertEqual(
            plugin._printerUploadAnalysis.asked, [(JOB_PATH, FILE_POSITION, FILE_SIZE)]
        )
        self.assertEqual(plugin.events(SPOOL_EVENT)[0]["source"], "sdPositionEstimated")
        jobReport = plugin.events(JOB_EVENT)[0]
        self.assertEqual(jobReport["source"], "sdPositionEstimated")
        self.assertEqual(jobReport["tools"][0]["usedLength"], LENGTH_AT_POSITION)

    def test_failedJobBooksItToo(self):
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="failed", printDuration=2820.0)

        self.assertEqual(spool.usedLength, LENGTH_AT_POSITION)
        self.assertEqual(plugin.events(JOB_EVENT)[0]["source"], "sdPositionEstimated")

    def test_finishedJobBooksTheSlicedPlanAsBefore(self):
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="success", printDuration=1800.0)

        self.assertEqual(spool.usedLength, SLICED_LENGTH)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])
        self.assertEqual(plugin.events(JOB_EVENT)[0]["source"], "slicedMetaData")

    def test_aPauseBooksNothingAndTheCancelEverythingUpToThere(self):
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="paused", printDuration=1200.0)
        self.assertEqual(spool.usedLength, 0.0)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)
        self.assertEqual(spool.usedLength, LENGTH_AT_POSITION)
        self.assertEqual(
            plugin.events(JOB_EVENT)[0]["tools"][0]["usedLength"], LENGTH_AT_POSITION
        )

    def test_theEndReportedTwiceBooksOnce(self):
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)
        plugin.commitOdometerData(printStatus="canceled", printDuration=2821.0)

        self.assertEqual(spool.usedLength, LENGTH_AT_POSITION)
        self.assertEqual(len(plugin._printerUploadAnalysis.asked), 1)

    def test_aJobThatEndedWithinAMinuteBooksNothing(self):
        # the same guard as for the sliced fallback and Klipper's count: spurious end events
        spool, plugin = self._sdJob()

        plugin.commitOdometerData(printStatus="canceled", printDuration=30.0)

        self.assertEqual(spool.usedLength, 0.0)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])

    def test_withoutAFilePositionNothingIsBooked(self):
        spool, plugin = self._sdJob(filePosition=(None, None))

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, 0.0)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])

    def test_aFileNeverAnalysedBooksNothing(self):
        spool, plugin = self._sdJob(usageAtPosition=None)

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, 0.0)
        # the end is still reported, with nothing on the tool
        self.assertEqual(len(plugin.events(JOB_EVENT)), 1)
        self.assertIsNone(plugin.events(JOB_EVENT)[0]["tools"][0])

    def test_aPositionBeforeTheFirstExtrusionBooksNothing(self):
        spool, plugin = self._sdJob(usageAtPosition={})

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, 0.0)

    def test_aLocalJobIsNotLookedUp(self):
        spool, plugin = self._sdJob()
        plugin._printJobStartFileLocation = ("local", "rocket.gcode")

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, 0.0)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])

    def test_klippersCountWinsOverTheFilePosition(self):
        spool, plugin = self._sdJob()
        plugin._u1RfidManager = OnMoonraker(filamentUsed=5737.0)

        plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        self.assertEqual(spool.usedLength, 5737.0)
        self.assertEqual(plugin._printerUploadAnalysis.asked, [])
        self.assertEqual(plugin.events(JOB_EVENT)[0]["source"], "moonraker")

    def test_toolWithoutSpoolWarnsWithTheEstimate(self):
        _, plugin = self._sdJob(withSpool=False)

        with self.assertLogs(plugin._logger, level="DEBUG") as logs:
            plugin.commitOdometerData(printStatus="canceled", printDuration=2820.0)

        warnings = [
            record.getMessage()
            for record in logs.records
            if record.levelno >= logging.WARNING
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("98.8mm printed up to the reported file position", warnings[0])
        self.assertIn("sdPositionEstimated", warnings[0])


class TestJobEndFilePosition(unittest.TestCase):
    def _plugin(self, currentData=None, failing=False):
        def get_current_data():
            if failing:
                raise RuntimeError("no printer")
            return currentData

        return types.SimpleNamespace(
            _printer=types.SimpleNamespace(get_current_data=get_current_data),
            _logger=logging.getLogger("test.jobendfileposition"),
            _lastSdStatusReport=(None, None),
            SD_STATUS_REPORT=SpoolmanagerPlugin.SD_STATUS_REPORT,
        )

    def test_theEventPayloadIsTakenFirst(self):
        # PRINT_CANCELLED and PRINT_PAUSED carry the position
        plugin = self._plugin({"progress": {"filepos": 1}})
        self.assertEqual(
            (FILE_POSITION, FILE_SIZE),
            SpoolmanagerPlugin._jobEndFilePosition(
                plugin, {"fileposition": FILE_POSITION, "size": FILE_SIZE}
            ),
        )

    def test_thePrinterIsAskedWhenTheEventHasNoPosition(self):
        # PRINT_FAILED on an error does not carry it
        plugin = self._plugin(
            {
                "progress": {"filepos": FILE_POSITION},
                "job": {"file": {"size": FILE_SIZE}},
            }
        )
        self.assertEqual(
            (FILE_POSITION, FILE_SIZE),
            SpoolmanagerPlugin._jobEndFilePosition(plugin, {"size": None}),
        )
        self.assertEqual(
            (FILE_POSITION, FILE_SIZE),
            SpoolmanagerPlugin._jobEndFilePosition(plugin, None),
        )

    def test_theLastSdStatusReportIsTheLastResort(self):
        # the printer died mid-print: PRINT_FAILED without a position, job deselected
        plugin = self._plugin({"progress": {}, "job": {}})
        for line in (
            "SD printing byte 0/0",  # Marlin's "not printing", no position
            "SD printing byte 1000/190996",
            "ok T:150.0 /150.0",
            "SD printing byte %d/%d" % (FILE_POSITION, FILE_SIZE),
        ):
            self.assertEqual(
                line, SpoolmanagerPlugin.on_receivedGCodeHook(plugin, None, line)
            )
        self.assertEqual(
            (FILE_POSITION, FILE_SIZE),
            SpoolmanagerPlugin._jobEndFilePosition(plugin, {"reason": "error"}),
        )

    def test_aNotPrintingReportLeavesTheLastPosition(self):
        plugin = self._plugin({})
        SpoolmanagerPlugin.on_receivedGCodeHook(
            plugin, None, "SD printing byte 500/1000"
        )
        SpoolmanagerPlugin.on_receivedGCodeHook(plugin, None, "SD printing byte 0/0")
        self.assertEqual(
            (500, 1000), SpoolmanagerPlugin._jobEndFilePosition(plugin, None)
        )

    def test_nothingKnownIsNothing(self):
        self.assertEqual(
            (None, None), SpoolmanagerPlugin._jobEndFilePosition(self._plugin({}), {})
        )
        self.assertEqual(
            (None, None),
            SpoolmanagerPlugin._jobEndFilePosition(self._plugin(failing=True), None),
        )


if __name__ == "__main__":
    unittest.main()
