# coding=utf-8

# Tests for the "spool sits on a tool this print does not use" hint in /allowedToPrint.
#
# A slicer may map a single-colour job to extruder 1 (T0) while the spool physically sits
# in another slot and is selected for that tool - observed with Orca slicing for the
# Snapmaker U1: the gcode carries `filament used [mm] = 25659.00, 0.00, 0.00, 0.00` and only
# `T0`, so the analysis metadata holds nothing but `tool0`, while the spool is selected for
# tool 3. The resulting "no spool selected for Tool 0" warning is correct but reads like a
# missing selection instead of a slot mix-up.
#
# allowed_to_print() is bound from the real SpoolManagerAPI class, so these run against
# production code rather than a reimplementation.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManager/test/test_SpoolsOnUnusedTools.py -v

import logging
import unittest

import flask
import peewee

from octoprint_SpoolManager.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManager.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManager.models.SpoolModel import SpoolModel


################################################################################################ fakes


class FakeSettings(object):
    def __init__(self):
        self._values = {
            SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS: [],
            SettingsKeys.SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED: True,
            SettingsKeys.SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH: True,
            SettingsKeys.SETTINGS_KEY_REMINDER_SELECTING_SPOOL: True,
            SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED: True,
            SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED: True,
            SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED: True,
        }

    def get(self, keys):
        return self._values.get(keys[0])

    def get_boolean(self, keys):
        return self._values.get(keys[0])

    def set(self, keys, value):
        self._values[keys[0]] = value

    def save(self):
        pass


class FakePrinterProfileManager(object):
    def __init__(self, toolCount):
        self._toolCount = toolCount

    def get_current_or_default(self):
        return {"extruder": {"count": self._toolCount}}


class FakePlugin(object):
    """
    Binds the real allowed_to_print/loadSelectedSpools onto a lightweight stand-in - the
    full SpoolmanagerPlugin needs OctoPrint's plugin loader to inject _printer,
    _file_manager and friends. The filament metadata is injected directly, standing in for
    _readingFilamentMetaData()'s gcode-analysis lookup.
    """

    # @no_firstrun_access needs OctoPrint's global settings singleton, which does not exist
    # outside a running server - unwrap to the plain implementation, that is what we test
    allowed_to_print = SpoolManagerAPI.allowed_to_print.__wrapped__
    loadSelectedSpools = SpoolManagerAPI.loadSelectedSpools

    def __init__(self, databaseManager, toolCount, filamentLengths):
        self._databaseManager = databaseManager
        self._settings = FakeSettings()
        self._logger = logging.getLogger("test.spoolsonunusedtools")
        self._printer_profile_manager = FakePrinterProfileManager(toolCount)
        # what _readingFilamentMetaData() would have derived from the gcode analysis
        self.metaDataFilamentLengths = list(filamentLengths)

    def _readingFilamentMetaData(self):
        return len(self.metaDataFilamentLengths) > 0

    def checkRemainingFilament(self, forToolIndex=None, shouldWarn=True):
        # no spool attributes are exercised here - the tests are about which tools end up
        # in which bucket, not about weight math
        return {
            "metaDataMissing": False,
            "attributesMissing": False,
            "notEnough": False,
            "detailedSpoolResult": [],
        }


################################################################################################ tests


class TestSpoolsOnUnusedTools(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True
        # loadSelectedSpools() opens/closes its own connection per call; keep the shared
        # in-memory connection alive across calls within one test
        self.databaseManager.connectoToDatabase = lambda *a, **k: None
        self.databaseManager.closeDatabase = lambda *a, **k: None

        self.app = flask.Flask(__name__)

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _create(self, **fields):
        defaults = {"displayName": "Test Spool", "isActive": True, "isTemplate": None}
        defaults.update(fields)
        return SpoolModel.create(**defaults)

    def _callAllowedToPrint(self, plugin):
        with self.app.test_request_context():
            response = plugin.allowed_to_print()
        return flask.json.loads(response.get_data())["result"]

    def test_spoolOnUnusedToolIsReported(self):
        # the real-world case: job uses T0 only, spool selected for tool 3
        spool = self._create(displayName="Kingroon White PLA", material="PLA")
        plugin = FakePlugin(self.databaseManager, 4, [25659.0])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, None, None, spool.databaseId],
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(len(result["noSpoolSelected"]), 1)
        self.assertEqual(result["noSpoolSelected"][0]["toolIndex"], 0)
        self.assertEqual(len(result["spoolsOnUnusedTools"]), 1)
        self.assertEqual(result["spoolsOnUnusedTools"][0]["toolIndex"], 3)
        self.assertEqual(
            result["spoolsOnUnusedTools"][0]["spoolName"], "Kingroon White PLA"
        )

    def test_spoolOnUsedToolIsNotReported(self):
        # counter-check: the very same spool, but on a tool the job actually uses. If this
        # still produced a hint, the test above would prove nothing about "unused".
        spool = self._create(displayName="Kingroon White PLA", material="PLA")
        plugin = FakePlugin(self.databaseManager, 4, [25659.0, 100.0, 0.0, 0.0])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, spool.databaseId, None, None],
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(result["spoolsOnUnusedTools"], [])

    def test_emptyUnusedToolIsNotReported(self):
        # an unused tool without a selected spool is nothing to point at
        plugin = FakePlugin(self.databaseManager, 4, [25659.0])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, None, None, None],
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(result["spoolsOnUnusedTools"], [])

    def test_multipleSpoolsOnUnusedToolsAreAllReported(self):
        # backend reports every candidate; deciding that two of them are ambiguous (and
        # showing no hint) is the frontend's job
        spoolA = self._create(displayName="Blau", material="PLA")
        spoolB = self._create(displayName="Kingroon White PLA", material="PLA")
        plugin = FakePlugin(self.databaseManager, 4, [25659.0])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, None, spoolA.databaseId, spoolB.databaseId],
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(
            [item["toolIndex"] for item in result["spoolsOnUnusedTools"]], [2, 3]
        )

    def test_noMetadataReportsNoUnusedTools(self):
        # without filament metadata it is unknown which tools the print uses - every hint
        # would be guesswork, and the "unused" branch is never reached
        spool = self._create(displayName="Kingroon White PLA", material="PLA")
        plugin = FakePlugin(self.databaseManager, 4, [])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, None, None, spool.databaseId],
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(result["spoolsOnUnusedTools"], [])

    def test_hintSuppressedWhenWarningDisabled(self):
        # no "no spool selected" popup means nothing to decorate
        spool = self._create(displayName="Kingroon White PLA", material="PLA")
        plugin = FakePlugin(self.databaseManager, 4, [25659.0])
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [None, None, None, spool.databaseId],
        )
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED], False
        )

        result = self._callAllowedToPrint(plugin)

        self.assertEqual(result["spoolsOnUnusedTools"], [])


if __name__ == "__main__":
    unittest.main()
