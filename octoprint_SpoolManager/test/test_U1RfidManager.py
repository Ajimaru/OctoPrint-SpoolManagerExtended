# coding=utf-8

# Tests for the U1 RFID self-reporter (Snapmaker U1, paxx12 Extended Firmware). No
# hardware, no websocket connection and no database involved: everything below is pure
# logic, driven with fake plugin/printer/database objects.
#
# Run with:  python3 octoprint_SpoolManager/test/test_U1RfidManager.py
# (pytest's default import mode inserts the plugin package, whose __init__ needs flask/
#  OctoPrint; `pytest --import-mode=importlib` works too.)

import importlib.util
import logging
import os
import sys
import types
import unittest

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# U1RfidManager.py does `from octoprint_SpoolManager.common.SettingsKeys import
# SettingsKeys` - an absolute import of the real package, which would otherwise drag in
# octoprint_SpoolManager/__init__.py (flask, OctoPrint) just to reach a dependency-free
# settings-key class. Stand in minimal namespace packages instead, exactly deep enough
# for that one import to resolve, and load both real modules by path underneath them.
def _installNamespacePackages():
    if "octoprint_SpoolManager" not in sys.modules:
        pluginPackage = types.ModuleType("octoprint_SpoolManager")
        pluginPackage.__path__ = [_PLUGIN_ROOT]
        sys.modules["octoprint_SpoolManager"] = pluginPackage
    if "octoprint_SpoolManager.common" not in sys.modules:
        commonPackage = types.ModuleType("octoprint_SpoolManager.common")
        commonPackage.__path__ = [os.path.join(_PLUGIN_ROOT, "common")]
        sys.modules["octoprint_SpoolManager.common"] = commonPackage


def _loadModule(dottedName, relativePath):
    modulePath = os.path.join(_PLUGIN_ROOT, relativePath)
    spec = importlib.util.spec_from_file_location(dottedName, modulePath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[dottedName] = module
    return module


_installNamespacePackages()
_loadModule("octoprint_SpoolManager.common.SettingsKeys", "common/SettingsKeys.py")
U1RfidManager = _loadModule("octoprint_SpoolManager.U1RfidManager", "U1RfidManager.py")

normalizeCardUid = U1RfidManager.normalizeCardUid
extractTagMetadata = U1RfidManager.extractTagMetadata


################################################################################################ fakes


class FakeSettings(object):
    def __init__(self, enabled=True):
        self._enabled = enabled

    def get_boolean(self, keys):
        return self._enabled


class FakeConnectorPlugin(object):
    def __init__(self, enabled=True):
        self.enabled = enabled


class FakePluginManager(object):
    def __init__(self, connectorEnabled=True, connectorInstalled=True):
        self.plugins = {}
        if connectorInstalled:
            self.plugins["moonraker_connector"] = FakeConnectorPlugin(connectorEnabled)


class FakePrinter(object):
    def __init__(self, connectionState=None):
        # a plain dict, matching what ConnectedPrinter exposes on OctoPrint 2.0 - not a
        # callable, exercising the non-callable branch of _getConnectorParams()
        self.connection_state = connectionState

    def is_printing(self):
        return False


class FakeSpoolModel(object):
    def __init__(self, databaseId, displayName):
        self.databaseId = databaseId
        self.displayName = displayName


class FakeDatabaseManager(object):
    def __init__(self, spoolsByCode=None):
        self._spoolsByCode = spoolsByCode or {}
        self.lookedUpCodes = []

    def loadSpoolByCode(self, code):
        self.lookedUpCodes.append(code)
        return self._spoolsByCode.get(code)


class FakePlugin(object):
    def __init__(
        self,
        settingsEnabled=True,
        connectorEnabled=True,
        connectorInstalled=True,
        connectionState=None,
        spoolsByCode=None,
    ):
        self._settings = FakeSettings(settingsEnabled)
        self._plugin_manager = FakePluginManager(connectorEnabled, connectorInstalled)
        self._printer = FakePrinter(connectionState)
        self._databaseManager = FakeDatabaseManager(spoolsByCode)
        self._plugin_version = "test"
        self.sentMessages = []
        self.selectSpoolForToolCalls = []
        self._selectSpoolResult = {"status": "selected", "toolIndex": None}

    def _sendDataToClient(self, payload):
        self.sentMessages.append(payload)

    def selectSpoolForTool(self, toolIndex, databaseId):
        self.selectSpoolForToolCalls.append((toolIndex, databaseId))
        result = dict(self._selectSpoolResult)
        result["toolIndex"] = toolIndex
        return result


def _makeManager(**plugin_kwargs):
    plugin = FakePlugin(**plugin_kwargs)
    manager = U1RfidManager.U1RfidManager(plugin, logging.getLogger("test.u1rfid"))
    return manager, plugin


################################################################################################ normalizeCardUid


class TestNormalizeCardUid(unittest.TestCase):
    def test_byteArrayEncodesToUppercaseHex(self):
        # the documented shape: CARD_UID is list[int], per docs/design/filament_detect.md
        self.assertEqual(normalizeCardUid([161, 178, 195, 212]), "A1B2C3D4")

    def test_singleByteIsZeroPadded(self):
        self.assertEqual(normalizeCardUid([1]), "01")

    def test_emptyChannelReportsAsPlainZero(self):
        # a channel with no tag reports CARD_UID as a bare 0, not an empty list
        self.assertIsNone(normalizeCardUid(0))

    def test_emptyListMeansNoTag(self):
        self.assertIsNone(normalizeCardUid([]))

    def test_allZeroBytesMeansNoTag(self):
        self.assertIsNone(normalizeCardUid([0, 0, 0, 0]))

    def test_noneMeansNoTag(self):
        self.assertIsNone(normalizeCardUid(None))

    def test_integerFallbackForADeviatingFirmwareBuild(self):
        # defensive: some future/other firmware might report a plain integer instead of
        # the documented byte array
        self.assertEqual(normalizeCardUid(0xA1B2C3D4), "A1B2C3D4")

    def test_stringIsNormalizedAndSeparatorsStripped(self):
        self.assertEqual(normalizeCardUid("a1:b2-c3d4"), "A1B2C3D4")

    def test_stringOfAllZerosMeansNoTag(self):
        self.assertIsNone(normalizeCardUid("00000000"))

    def test_lookupAndTeachingMustAgree(self):
        # the single most important property: the same raw value normalizes identically
        # every time, regardless of call site - otherwise a taught UID never matches a
        # later scan of the same tag
        first = normalizeCardUid([32, 48, 227, 2])
        second = normalizeCardUid([32, 48, 227, 2])
        self.assertEqual(first, second)
        self.assertEqual(first, "2030E302")


################################################################################################ extractTagMetadata


class TestExtractTagMetadata(unittest.TestCase):
    def test_realTagPayloadFromALiveU1(self):
        # captured live from a Polymaker/Snapmaker PLA SnapSpeed spool
        channelInfo = {
            "ALPHA": 255,
            "BED_TEMP": 60,
            "CARD_TYPE": "M1",
            "COLOR_NUMS": 1,
            "HOTEND_MAX_TEMP": 230,
            "HOTEND_MIN_TEMP": 190,
            "MAIN_TYPE": "PLA",
            "MANUFACTURER": "Polymaker",
            "OFFICIAL": True,
            "RGB_1": 526861,
            "SUB_TYPE": "SnapSpeed",
            "VENDOR": "Snapmaker",
            "WEIGHT": 500,
            "CARD_UID": [32, 48, 227, 2],
        }
        metadata = extractTagMetadata(channelInfo)
        self.assertEqual(metadata["VENDOR"], "Snapmaker")
        self.assertEqual(metadata["MAIN_TYPE"], "PLA")
        self.assertEqual(metadata["SUB_TYPE"], "SnapSpeed")
        self.assertEqual(metadata["WEIGHT"], 500)
        self.assertEqual(metadata["BED_TEMP"], 60)
        self.assertEqual(metadata["HOTEND_MIN_TEMP"], 190)
        self.assertEqual(metadata["HOTEND_MAX_TEMP"], 230)
        self.assertEqual(metadata["RGB_1"], 526861)
        # CARD_UID/CARD_TYPE are presence markers, not filament data - not surfaced here
        self.assertNotIn("CARD_UID", metadata)

    def test_firmwarePlaceholdersAreDropped(self):
        # an empty channel's "unset" markers must never be prefilled as real values -
        # otherwise a spool ends up with vendor "NONE" or a 0 degC hotend temperature
        channelInfo = {
            "VENDOR": "NONE",
            "MANUFACTURER": "NONE",
            "MAIN_TYPE": "NONE",
            "SUB_TYPE": "NONE",
            "BED_TEMP": 0,
            "HOTEND_MIN_TEMP": 0,
            "HOTEND_MAX_TEMP": 0,
            "WEIGHT": 0,
            "CARD_UID": 0,
            "CARD_TYPE": "",
        }
        metadata = extractTagMetadata(channelInfo)
        self.assertEqual(metadata, {})

    def test_alphaZeroIsMeaningfulNotUnset(self):
        # ALPHA=0 means fully transparent, a real value - unlike BED_TEMP=0 it must
        # survive the "0 means unset" filter
        metadata = extractTagMetadata({"ALPHA": 0, "COLOR_NUMS": 1})
        self.assertEqual(metadata["ALPHA"], 0)
        self.assertEqual(metadata["COLOR_NUMS"], 1)

    def test_nonDictInputYieldsEmptyMetadata(self):
        self.assertEqual(extractTagMetadata(None), {})
        self.assertEqual(extractTagMetadata("garbage"), {})

    def test_missingFieldsAreSimplyAbsent(self):
        metadata = extractTagMetadata({"VENDOR": "Snapmaker"})
        self.assertEqual(metadata, {"VENDOR": "Snapmaker"})


################################################################################################ detection chain


class TestDetectionChain(unittest.TestCase):
    def test_allStagesPassWithALiveU1Snapshot(self):
        # mirrors the connection_state a real MoonrakerConnector reports for the U1
        manager, plugin = _makeManager(
            connectionState={
                "connector": "moonraker",
                "host": "192.168.1.120",
                "port": 7125,
            }
        )
        manager._probeFilamentDetect = lambda host, port: True
        manager._fetchPrinterInfo = lambda host, port: {
            "machineType": "Snapmaker U1",
            "deviceName": "RobsU1",
            "firmwareVersion": "1.5.2",
        }

        manager.evaluateDetectionChain()
        status = manager.getStatus()

        self.assertTrue(status["chain"][U1RfidManager.U1RfidManager.STAGE_CONNECTOR])
        self.assertTrue(status["chain"][U1RfidManager.U1RfidManager.STAGE_MOONRAKER])
        self.assertTrue(status["chain"][U1RfidManager.U1RfidManager.STAGE_PRINTER])
        self.assertTrue(
            status["chain"][U1RfidManager.U1RfidManager.STAGE_FILAMENT_DETECT]
        )
        self.assertTrue(status["supported"])
        self.assertEqual(status["host"], "192.168.1.120")
        self.assertEqual(status["port"], 7125)
        self.assertEqual(status["printerInfo"]["machineType"], "Snapmaker U1")
        self.assertEqual(status["chainMessage"], "")

    def test_connectorPluginMissingStopsAtStageOne(self):
        manager, plugin = _makeManager(connectorInstalled=False)
        manager.evaluateDetectionChain()
        status = manager.getStatus()
        self.assertFalse(status["chain"][U1RfidManager.U1RfidManager.STAGE_CONNECTOR])
        self.assertFalse(status["supported"])
        self.assertIn("MoonrakerConnector", status["chainMessage"])

    def test_connectorInstalledButDisabledStopsAtStageOne(self):
        manager, plugin = _makeManager(connectorEnabled=False)
        manager.evaluateDetectionChain()
        status = manager.getStatus()
        self.assertFalse(status["chain"][U1RfidManager.U1RfidManager.STAGE_CONNECTOR])
        self.assertFalse(status["supported"])

    def test_nonMoonrakerPrinterStopsAtStageTwo(self):
        # e.g. the A1mini instance, which runs bambu_connector instead
        manager, plugin = _makeManager(
            connectionState={"connector": "bambu", "host": "192.168.1.113"}
        )
        manager.evaluateDetectionChain()
        status = manager.getStatus()
        self.assertTrue(status["chain"][U1RfidManager.U1RfidManager.STAGE_CONNECTOR])
        self.assertFalse(status["chain"][U1RfidManager.U1RfidManager.STAGE_MOONRAKER])
        self.assertFalse(status["supported"])
        self.assertIn("Moonraker", status["chainMessage"])

    def test_noConnectionStateAtAllStopsAtStageTwo(self):
        # OctoPrint 1.x has no connection_state attribute on the printer at all
        manager, plugin = _makeManager(connectionState=None)
        manager.evaluateDetectionChain()
        status = manager.getStatus()
        self.assertFalse(status["chain"][U1RfidManager.U1RfidManager.STAGE_MOONRAKER])
        self.assertFalse(status["supported"])

    def test_stockFirmwareStopsAtStageFour(self):
        # a Moonraker printer that simply doesn't have filament_detect (stock firmware,
        # or a Klipper printer that isn't a U1 at all) - the actual gate for the feature
        manager, plugin = _makeManager(
            connectionState={
                "connector": "moonraker",
                "host": "192.168.1.120",
                "port": 7125,
            }
        )
        manager._probeFilamentDetect = lambda host, port: False
        manager._fetchPrinterInfo = lambda host, port: {}

        manager.evaluateDetectionChain()
        status = manager.getStatus()

        self.assertTrue(status["chain"][U1RfidManager.U1RfidManager.STAGE_MOONRAKER])
        self.assertFalse(
            status["chain"][U1RfidManager.U1RfidManager.STAGE_FILAMENT_DETECT]
        )
        self.assertFalse(status["supported"])
        self.assertIn("Extended Firmware", status["chainMessage"])

    def test_stageThreeIsInformativeOnlyNeverBlocking(self):
        # a deviating/unknown machine_type must not block the feature as long as
        # filament_detect is present - capability decides, model only informs
        manager, plugin = _makeManager(
            connectionState={
                "connector": "moonraker",
                "host": "10.0.0.5",
                "port": 7125,
            }
        )
        manager._probeFilamentDetect = lambda host, port: True
        manager._fetchPrinterInfo = lambda host, port: {
            "machineType": "Some Other Klipper Printer"
        }

        manager.evaluateDetectionChain()
        status = manager.getStatus()

        self.assertTrue(status["supported"])
        self.assertEqual(
            status["printerInfo"]["machineType"], "Some Other Klipper Printer"
        )

    def test_refreshStartsAndStopsBasedOnChainAndSetting(self):
        manager, plugin = _makeManager(
            settingsEnabled=True,
            connectionState={
                "connector": "moonraker",
                "host": "192.168.1.120",
                "port": 7125,
            },
        )
        manager._probeFilamentDetect = lambda host, port: True
        manager._fetchPrinterInfo = lambda host, port: {}
        started = []
        manager._start = lambda: started.append("start")
        manager._stop = lambda: started.append("stop")

        manager.refresh()
        self.assertEqual(started, ["start"])

    def test_refreshStopsWhenDisabledEvenIfChainPasses(self):
        manager, plugin = _makeManager(
            settingsEnabled=False,
            connectionState={
                "connector": "moonraker",
                "host": "192.168.1.120",
                "port": 7125,
            },
        )
        manager._probeFilamentDetect = lambda host, port: True
        manager._fetchPrinterInfo = lambda host, port: {}
        calls = []
        manager._start = lambda: calls.append("start")
        manager._stop = lambda: calls.append("stop")

        manager.refresh()
        self.assertEqual(calls, ["stop"])


################################################################################################ spool resolution + edge detection


class TestChannelHandling(unittest.TestCase):
    def test_knownUidSelectsSpoolForMatchingChannel(self):
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})

        self.assertEqual(plugin.selectSpoolForToolCalls, [(2, 42)])
        self.assertEqual(plugin._databaseManager.lookedUpCodes, ["A1B2C3D4"])
        pushed = [m for m in plugin.sentMessages if m["action"] == "u1RfidSpoolSelected"]
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["channel"], 2)
        self.assertEqual(pushed[0]["databaseId"], 42)

    def test_sameUidDoesNotFireTwice(self):
        # the edge-detection core: a tag sitting in a channel must not re-trigger the
        # load on every websocket push, only on the 0 -> UID transition
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        channelInfo = {"CARD_UID": [161, 178, 195, 212]}
        manager._handleChannel(2, channelInfo)
        manager._handleChannel(2, channelInfo)
        manager._handleChannel(2, channelInfo)

        self.assertEqual(len(plugin.selectSpoolForToolCalls), 1)

    def test_unknownUidReportsWithoutSelecting(self):
        manager, plugin = _makeManager(spoolsByCode={})
        manager._handleChannel(0, {"CARD_UID": [1, 2, 3, 4], "VENDOR": "Snapmaker"})

        self.assertEqual(plugin.selectSpoolForToolCalls, [])
        pushed = [m for m in plugin.sentMessages if m["action"] == "u1RfidUnknownTag"]
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["uid"], "01020304")
        self.assertEqual(pushed[0]["channel"], 0)

        unknown = manager.getUnknownTags()
        self.assertIn("0", unknown)
        self.assertEqual(unknown["0"]["uid"], "01020304")

    def test_tagRemovalForgetsStateWithoutDeselecting(self):
        # no auto-deselect: the U1 briefly reports "no tag" during a filament change,
        # which must not clear a perfectly valid selection
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})
        callsBeforeRemoval = len(plugin.selectSpoolForToolCalls)

        manager._handleChannel(2, {"CARD_UID": 0})

        self.assertEqual(len(plugin.selectSpoolForToolCalls), callsBeforeRemoval)
        self.assertNotIn(2, manager._lastUidByChannel)

    def test_reinsertingTheSameTagFiresAgainAfterRemoval(self):
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})
        manager._handleChannel(2, {"CARD_UID": 0})
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})

        self.assertEqual(len(plugin.selectSpoolForToolCalls), 2)

    def test_channelsAreIndependent(self):
        manager, plugin = _makeManager(
            spoolsByCode={
                "A1B2C3D4": FakeSpoolModel(42, "PLA Red"),
                "01020304": FakeSpoolModel(43, "PETG Blue"),
            }
        )
        manager._handleChannel(0, {"CARD_UID": [161, 178, 195, 212]})
        manager._handleChannel(2, {"CARD_UID": [1, 2, 3, 4]})

        self.assertEqual(
            sorted(plugin.selectSpoolForToolCalls), [(0, 42), (2, 43)]
        )

    def test_knownUidClearsAnyPriorUnknownEntryForThatChannel(self):
        # teaching a UID (assigning it to a spool's `code`) must make the "unknown tag"
        # popup go away on the next scan, not linger in the settings status forever
        manager, plugin = _makeManager(spoolsByCode={})
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})
        self.assertIn(2, manager._unknownTagByChannel)

        manager._lastUidByChannel.pop(2)  # simulate the tag having been re-scanned
        manager._plugin._databaseManager._spoolsByCode["A1B2C3D4"] = FakeSpoolModel(
            42, "PLA Red"
        )
        manager._handleChannel(2, {"CARD_UID": [161, 178, 195, 212]})

        self.assertNotIn(2, manager._unknownTagByChannel)


################################################################################################ websocket message parsing


class TestHandleMessage(unittest.TestCase):
    def test_notifyStatusUpdatePushIsParsed(self):
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        message = {
            "method": "notify_status_update",
            "params": [
                {
                    "filament_detect": {
                        "info": [
                            {"CARD_UID": 0},
                            {"CARD_UID": 0},
                            {"CARD_UID": [161, 178, 195, 212]},
                            {"CARD_UID": 0},
                        ]
                    }
                }
            ],
        }
        manager._handleMessage(message)
        self.assertEqual(plugin.selectSpoolForToolCalls, [(2, 42)])

    def test_queryResponseResultIsParsed(self):
        # the initial printer.objects.query response has "result" instead of a
        # notify_status_update push, but the same status/filament_detect shape inside it
        manager, plugin = _makeManager(
            spoolsByCode={"A1B2C3D4": FakeSpoolModel(42, "PLA Red")}
        )
        message = {
            "result": {
                "status": {
                    "filament_detect": {
                        "info": [{"CARD_UID": [161, 178, 195, 212]}]
                    }
                }
            }
        }
        manager._handleMessage(message)
        self.assertEqual(plugin.selectSpoolForToolCalls, [(0, 42)])

    def test_messagesWithoutFilamentDetectAreIgnored(self):
        manager, plugin = _makeManager()
        manager._handleMessage({"method": "notify_proc_stat_update", "params": [{}]})
        manager._handleMessage({"result": {"status": {}}})
        manager._handleMessage({})
        self.assertEqual(plugin.selectSpoolForToolCalls, [])
        self.assertEqual(plugin.sentMessages, [])


if __name__ == "__main__":
    unittest.main()
