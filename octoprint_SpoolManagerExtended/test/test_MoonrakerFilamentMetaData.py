# coding=utf-8

# Tests for reading per-tool filament usage straight from Moonraker.
#
# OctoPrint-MoonrakerConnector maps a job's usage onto tool0 unconditionally
# (connector.py: {"tool0": AnalysisFilamentUse(length=f.filament_total)}) and never reads
# Moonraker's per-extruder breakdown. On the Snapmaker U1 an Orca job sliced for slot 4
# emits `T3` and `filament used [mm] = 0.00, 0.00, 0.00, 21872.80`, but reaches the plugin
# as {"tool0": 21872.8} - so the spool selected for tool 3 reads as "not selected".
#
# Every assertion below therefore checks for tool3, a value the connector's tool0-only
# metadata cannot produce: if the fallback silently stopped working, the tests fail rather
# than passing on the old value.
#
# Upstream bug: https://github.com/OctoPrint/OctoPrint-MoonrakerConnector/issues/4
# If it gets fixed, these tests go away together with _getFilamentFromMoonraker().
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_MoonrakerFilamentMetaData.py -v

import logging
import unittest

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin

# PRINTER only exists on OctoPrint 2.0; the test env may run 1.x, so use
# the wire values the production code compares against
PRINTER = "printer"
LOCAL = "local"


# real /server/files/metadata payload of the U1 job, trimmed to the fields in play
MOONRAKER_METADATA = {
    "result": {
        "filename": "OcroScale_Part_Top_PLA_1h54m.gcode",
        "modified": 1787729414.2328074,
        "size": 3768241,
        "filament_total": 21872.8,
        "filament_used_mm": [0.0, 0.0, 0.0, 21872.8],
        "filament_diameter": [1.75, 1.75, 1.75, 1.75],
    }
}

# what the connector puts into OctoPrint's metadata for the very same file
CONNECTOR_ANALYSIS = {"analysis": {"filament": {"tool0": {"length": 21872.8}}}}


class FakeU1RfidManager(object):
    def __init__(self, connectorParams, payload):
        self._connectorParams = connectorParams
        self._payload = payload
        self.requestedPaths = []

    def _getConnectorParams(self):
        return self._connectorParams

    def _httpGet(self, host, port, path, timeoutSeconds=5):
        self.requestedPaths.append(path)
        return self._payload


class FakeFileManager(object):
    def __init__(self, metadata=None):
        self._metadata = metadata

    def get_metadata(self, origin, path):
        return self._metadata

    def path_on_disk(self, origin, path):
        return None


class FakePlugin(object):
    """
    Binds the real metadata resolution onto a stand-in - the full plugin needs OctoPrint's
    loader to inject _printer/_file_manager.
    """

    _getFilamentMetaData = SpoolmanagerPlugin._getFilamentMetaData
    _getFilamentFromMoonraker = SpoolmanagerPlugin._getFilamentFromMoonraker
    _parseFilamentLengthsFromBambu3mf = (
        SpoolmanagerPlugin._parseFilamentLengthsFromBambu3mf
    )
    _getFilamentFromPrinter3mf = SpoolmanagerPlugin._getFilamentFromPrinter3mf

    def __init__(self, connectorParams, payload, fileManagerMetadata=None):
        self._logger = logging.getLogger("test.moonrakerfilament")
        self._u1RfidManager = FakeU1RfidManager(connectorParams, payload)
        self._file_manager = FakeFileManager(fileManagerMetadata)
        self._printer = None
        self._printer3mfFilamentCache = {}
        self._moonrakerFilamentCache = {}


MOONRAKER_PARAMS = {"host": "192.168.1.120", "port": 7125, "apikey": None}


class TestMoonrakerFilamentMetaData(unittest.TestCase):
    def test_perToolUsageIsReadFromMoonraker(self):
        plugin = FakePlugin(MOONRAKER_PARAMS, MOONRAKER_METADATA)

        filament = plugin._getFilamentFromMoonraker(
            "OcroScale_Part_Top_PLA_1h54m.gcode"
        )

        # tool3, not tool0 - exactly what the connector's metadata cannot express
        self.assertEqual(sorted(filament.keys()), ["tool3"])
        self.assertEqual(filament["tool3"]["length"], 21872.8)
        self.assertIn("volume", filament["tool3"])

    def test_moonrakerWinsOverConnectorToolZeroMetadata(self):
        # the whole point: the connector metadata is present and says tool0, and the
        # Moonraker answer has to take precedence over it
        plugin = FakePlugin(
            MOONRAKER_PARAMS, MOONRAKER_METADATA, fileManagerMetadata=CONNECTOR_ANALYSIS
        )

        filament = plugin._getFilamentMetaData(
            PRINTER, "OcroScale_Part_Top_PLA_1h54m.gcode"
        )

        self.assertEqual(sorted(filament.keys()), ["tool3"])

    def test_unusedToolsAreAbsentNotZero(self):
        # allowedToPrint() decides "tool is used by this print" by presence in the dict,
        # so a 0.0 entry would mark every tool as used
        plugin = FakePlugin(MOONRAKER_PARAMS, MOONRAKER_METADATA)

        filament = plugin._getFilamentFromMoonraker(
            "OcroScale_Part_Top_PLA_1h54m.gcode"
        )

        for absentTool in ("tool0", "tool1", "tool2"):
            self.assertNotIn(absentTool, filament)

    def test_multiToolJobKeepsEveryUsedTool(self):
        payload = {
            "result": dict(
                MOONRAKER_METADATA["result"],
                filament_used_mm=[100.0, 0.0, 250.0, 21872.8],
            )
        }
        plugin = FakePlugin(MOONRAKER_PARAMS, payload)

        filament = plugin._getFilamentFromMoonraker("multi.gcode")

        self.assertEqual(sorted(filament.keys()), ["tool0", "tool2", "tool3"])
        self.assertEqual(filament["tool2"]["length"], 250.0)

    def test_nonMoonrakerPrinterFallsBackToConnectorMetadata(self):
        # a bambu/serial printer has no connector params - the existing path must stay
        plugin = FakePlugin(None, None, fileManagerMetadata=CONNECTOR_ANALYSIS)

        filament = plugin._getFilamentMetaData(
            PRINTER, "OcroScale_Part_Top_PLA_1h54m.gcode"
        )

        self.assertEqual(filament, {"tool0": {"length": 21872.8}})
        self.assertEqual(plugin._u1RfidManager.requestedPaths, [])

    def test_localJobDoesNotQueryMoonraker(self):
        # local files carry a real gcode analysis; no reason to ask the printer
        plugin = FakePlugin(
            MOONRAKER_PARAMS, MOONRAKER_METADATA, fileManagerMetadata=CONNECTOR_ANALYSIS
        )

        filament = plugin._getFilamentMetaData(LOCAL, "local.gcode")

        self.assertEqual(filament, {"tool0": {"length": 21872.8}})
        self.assertEqual(plugin._u1RfidManager.requestedPaths, [])

    def test_missingBreakdownFallsBackToConnectorMetadata(self):
        # single-extruder slicers omit filament_used_mm
        payload = {
            "result": {
                "modified": 1.0,
                "size": 2,
                "filament_total": 21872.8,
            }
        }
        plugin = FakePlugin(
            MOONRAKER_PARAMS, payload, fileManagerMetadata=CONNECTOR_ANALYSIS
        )

        filament = plugin._getFilamentMetaData(
            PRINTER, "single.gcode"
        )

        self.assertEqual(filament, {"tool0": {"length": 21872.8}})

    def test_unreachableMoonrakerFallsBackToConnectorMetadata(self):
        # _httpGet returns None on any failure - must not break job handling
        plugin = FakePlugin(
            MOONRAKER_PARAMS, None, fileManagerMetadata=CONNECTOR_ANALYSIS
        )

        filament = plugin._getFilamentMetaData(
            PRINTER, "OcroScale_Part_Top_PLA_1h54m.gcode"
        )

        self.assertEqual(filament, {"tool0": {"length": 21872.8}})

    def test_scalarDiameterIsAccepted(self):
        payload = {
            "result": dict(MOONRAKER_METADATA["result"], filament_diameter=1.75)
        }
        plugin = FakePlugin(MOONRAKER_PARAMS, payload)

        filament = plugin._getFilamentFromMoonraker("scalar.gcode")

        self.assertEqual(sorted(filament.keys()), ["tool3"])
        self.assertIn("volume", filament["tool3"])

    def test_lengthSurvivesUnusableDiameter(self):
        payload = {
            "result": dict(MOONRAKER_METADATA["result"], filament_diameter="n/a")
        }
        plugin = FakePlugin(MOONRAKER_PARAMS, payload)

        filament = plugin._getFilamentFromMoonraker("baddia.gcode")

        self.assertEqual(filament["tool3"]["length"], 21872.8)
        self.assertNotIn("volume", filament["tool3"])

    def test_pathIsUrlEncoded(self):
        plugin = FakePlugin(MOONRAKER_PARAMS, MOONRAKER_METADATA)

        plugin._getFilamentFromMoonraker("sub folder/a&b.gcode")

        self.assertEqual(
            plugin._u1RfidManager.requestedPaths,
            ["/server/files/metadata?filename=sub%20folder/a%26b.gcode"],
        )

    def test_resliceInvalidatesCachedResult(self):
        payload = {"result": dict(MOONRAKER_METADATA["result"])}
        plugin = FakePlugin(MOONRAKER_PARAMS, payload)

        first = plugin._getFilamentFromMoonraker("job.gcode")
        self.assertEqual(sorted(first.keys()), ["tool3"])

        # same name, re-sliced to another slot: new modified/size must win over the cache
        payload["result"] = dict(
            payload["result"],
            modified=1787799999.0,
            size=4000000,
            filament_used_mm=[500.0, 0.0, 0.0, 0.0],
        )

        second = plugin._getFilamentFromMoonraker("job.gcode")

        self.assertEqual(sorted(second.keys()), ["tool0"])
        self.assertEqual(second["tool0"]["length"], 500.0)


if __name__ == "__main__":
    unittest.main()
