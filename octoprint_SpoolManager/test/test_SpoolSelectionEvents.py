# coding=utf-8

# Regression tests for the spool_selected/spool_deselected event-spam fix:
#   https://github.com/mdziekon/OctoPrint-SpoolManager/issues/45
#   https://github.com/WildRikku/OctoPrint-SpoolManager/issues/4
#
# loadSelectedSpools() (a pure read, called on every sidebar poll / client connect /
# file upload check) used to fire spool_selected unconditionally, spamming the log
# every ~5s while idle and on every file upload. _selectSpool() also fired regardless
# of whether the tool's spool actually changed. Both are exercised here against the
# real production methods (bound from the actual classes), not a reimplementation.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManager/test/test_SpoolSelectionEvents.py -v

import logging
import unittest

import peewee

from octoprint_SpoolManager import SpoolmanagerPlugin
from octoprint_SpoolManager.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManager.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManager.models.SpoolModel import SpoolModel


################################################################################################ fakes


class FakeSettings(object):
    def __init__(self):
        self._values = {SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS: []}

    def get(self, keys):
        return self._values.get(keys[0])

    def get_boolean(self, keys):
        return False

    def set(self, keys, value):
        self._values[keys[0]] = value

    def save(self):
        pass


class FakeEventBus(object):
    def __init__(self):
        self.firedEvents = []

    def fire(self, eventName, payload=None):
        self.firedEvents.append((eventName, payload))


class FakePlugin(object):
    """
    Binds the real _selectSpool/_announceSpoolSelectionChange/loadSelectedSpools/
    _sendPayload2EventBus methods from the production classes onto a lightweight
    stand-in, instead of driving the full SpoolmanagerPlugin (which needs OctoPrint's
    plugin loader to inject self._printer, self._plugin_manager, etc).
    """

    _selectSpool = SpoolManagerAPI._selectSpool
    loadSelectedSpools = SpoolManagerAPI.loadSelectedSpools
    _sendPayload2EventBus = SpoolmanagerPlugin._sendPayload2EventBus
    _announceSpoolSelectionChange = SpoolmanagerPlugin._announceSpoolSelectionChange

    def __init__(self, databaseManager):
        self._databaseManager = databaseManager
        self._settings = FakeSettings()
        self._event_bus = FakeEventBus()
        self._logger = logging.getLogger("test.spoolselection")
        self._lastAnnouncedSpoolIds = {}
        self._mqttManager = None
        self.filamentWarningsRequested = []

    def _sendMessageToClient(self, *args, **kwargs):
        pass

    def checkRemainingFilament(self, forToolIndex=None, shouldWarn=True):
        self.filamentWarningsRequested.append(forToolIndex)


class TestSpoolSelectionEvents(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(logging.getLogger("test.dbmanager"), False)
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True
        # loadSpool()/loadSelectedSpools() open/close their own connection per call;
        # give connectoToDatabase()/closeDatabase() a harmless no-op so the shared
        # in-memory connection above stays intact across calls within one test.
        self.databaseManager.connectoToDatabase = lambda *a, **k: None
        self.databaseManager.closeDatabase = lambda *a, **k: None

        self.plugin = FakePlugin(self.databaseManager)

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _create(self, **fields):
        defaults = {"displayName": "Test Spool", "isActive": True, "isTemplate": None}
        defaults.update(fields)
        return SpoolModel.create(**defaults)

    def _eventNames(self):
        return [name for name, _payload in self.plugin._event_bus.firedEvents]

    def test_loadSelectedSpoolsFiresNoEvents(self):
        spool = self._create(displayName="Idle Poll Spool")
        self.plugin._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS],
            [spool.databaseId],
        )

        result = self.plugin.loadSelectedSpools()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].databaseId, spool.databaseId)
        self.assertEqual(self.plugin._event_bus.firedEvents, [])

    def test_selectingDifferentSpoolFiresOneSelectedEvent(self):
        spool = self._create(displayName="Fresh Spool")

        self.plugin._selectSpool(0, spool.databaseId)

        self.assertEqual(
            self._eventNames(), ["plugin_spoolmanager_spool_selected"]
        )

    def test_selectingSameSpoolTwiceFiresOnlyOnce(self):
        spool = self._create(displayName="Repeated RFID Read")

        self.plugin._selectSpool(0, spool.databaseId)
        self.plugin._selectSpool(0, spool.databaseId)

        self.assertEqual(
            self._eventNames(), ["plugin_spoolmanager_spool_selected"]
        )

    def test_clearingOccupiedToolFiresDeselected(self):
        spool = self._create(displayName="To Be Cleared")
        self.plugin._selectSpool(0, spool.databaseId)
        self.plugin._event_bus.firedEvents = []

        self.plugin._selectSpool(0, -1)

        self.assertEqual(
            self._eventNames(), ["plugin_spoolmanager_spool_deselected"]
        )

    def test_clearingAlreadyEmptyToolFiresNothing(self):
        self.plugin._selectSpool(0, -1)

        self.assertEqual(self.plugin._event_bus.firedEvents, [])

    def test_movingSpoolBetweenToolsFiresDeselectThenSelect(self):
        spool = self._create(displayName="Swapped Spool")
        self.plugin._selectSpool(0, spool.databaseId)
        self.plugin._event_bus.firedEvents = []

        self.plugin._selectSpool(1, spool.databaseId)

        self.assertEqual(
            self._eventNames(),
            [
                "plugin_spoolmanager_spool_deselected",
                "plugin_spoolmanager_spool_selected",
            ],
        )
        deselectPayload = self.plugin._event_bus.firedEvents[0][1]
        selectPayload = self.plugin._event_bus.firedEvents[1][1]
        self.assertEqual(deselectPayload["toolId"], 0)
        self.assertEqual(selectPayload["toolId"], 1)

    def test_spoolDeletedUnderneathToolFiresDeselected(self):
        spool = self._create(displayName="Doomed Spool")
        self.plugin._selectSpool(0, spool.databaseId)
        self.plugin._event_bus.firedEvents = []
        deletedId = spool.databaseId
        spool.delete_instance()

        self.plugin._selectSpool(0, deletedId)

        self.assertEqual(
            self._eventNames(), ["plugin_spoolmanager_spool_deselected"]
        )


if __name__ == "__main__":
    unittest.main()
