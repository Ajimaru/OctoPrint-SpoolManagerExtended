# coding=utf-8

# Tests for /allowedToPrint when the job's filament usage is unknown.
#
# Seen for a file on a serial printer's SD card, which has no analysis: the dialog said
# "There is no spool selected for Tool 0 despite it being used (maybe) by this print"
# while a spool was selected for tool 0. Without metadata there is no detailedSpoolResult,
# and the tool landed in noSpoolSelected whether it had a spool or not.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_AllowedToPrintWithoutMetadata.py -v

import logging
import unittest

import flask
import peewee

from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

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
    # @no_firstrun_access needs OctoPrint's global settings singleton - unwrap to the
    # plain implementation, see test_SpoolsOnUnusedTools.py
    allowed_to_print = SpoolManagerAPI.allowed_to_print.__wrapped__
    loadSelectedSpools = SpoolManagerAPI.loadSelectedSpools

    def __init__(self, databaseManager, toolCount):
        self._databaseManager = databaseManager
        self._settings = FakeSettings()
        self._logger = logging.getLogger("test.allowedtoprintwithoutmetadata")
        self._printer_profile_manager = FakePrinterProfileManager(toolCount)
        self.metaDataFilamentLengths = []

    def _readingFilamentMetaData(self):
        # no analysis for the job file
        return False

    def checkRemainingFilament(self, forToolIndex=None, shouldWarn=True):
        # what _evaluateRequiredWeight() returns without metadata
        return {
            "metaDataMissing": True,
            "attributesMissing": False,
            "notEnough": False,
            "detailedSpoolResult": [],
        }


################################################################################################ tests


class TestAllowedToPrintWithoutMetadata(unittest.TestCase):
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

        self.app = flask.Flask(__name__)

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _callAllowedToPrint(self, plugin):
        with self.app.test_request_context():
            response = plugin.allowed_to_print()
        return flask.json.loads(response.get_data())

    def test_toolWithASpoolIsNotReportedAsWithout(self):
        spool = SpoolModel.create(
            displayName="Selected Spool", isActive=True, isTemplate=None
        )
        plugin = FakePlugin(self.databaseManager, 1)
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [spool.databaseId],
        )

        response = self._callAllowedToPrint(plugin)

        self.assertEqual(response["result"]["noSpoolSelected"], [])
        # the frontend still learns that the usage is unknown
        self.assertTrue(response["metaDataMissing"])

    def test_toolWithoutASpoolIsStillReported(self):
        # "despite it being used (maybe)": without metadata a tool without a spool might
        # be used, and that warning stays
        selected = SpoolModel.create(
            displayName="Selected Spool", isActive=True, isTemplate=None
        )
        plugin = FakePlugin(self.databaseManager, 2)
        plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [selected.databaseId, None],
        )

        response = self._callAllowedToPrint(plugin)

        self.assertEqual(
            [item["toolIndex"] for item in response["result"]["noSpoolSelected"]], [1]
        )


if __name__ == "__main__":
    unittest.main()
