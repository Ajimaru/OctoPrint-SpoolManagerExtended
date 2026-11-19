# coding=utf-8

# Tests for SpoolModel.rfidTagKeySource: set to "ownWrite" by the teach-in after the plugin
# wrote a TigerTag/OpenPrintTag itself, cleared when the key is edited by hand, never taken
# from a client, and turning a foreign-format read into a legitimate match.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_RfidTagKeySource.py

import logging
import unittest

import flask
import peewee

from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.common import RfidKeyCollision
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

# a 7-byte NTAG UID, as a TigerTag presents it; keyed on the whole UID
TAG_UID = "04A1B2C3D4E5F6"
BASE = {
    "displayName": "Taught",
    "vendor": "Vendor",
    "material": "PLA",
    "color": "#FFFFFF",
    "colorName": "White",
    "diameter": 1.75,
}


class _Api(SpoolManagerAPI):
    """The pieces under test only; SpoolManagerAPI.__init__ is deliberately not called."""

    # @no_firstrun_access needs OctoPrint's settings singleton - unwrap to the plain method
    teachOctoScaleRfidTagKey = SpoolManagerAPI.teachOctoScaleRfidTagKey.__wrapped__

    def __init__(self, databaseManager=None):
        self._logger = logging.getLogger("test.rfidtagkeysource")
        self._databaseManager = databaseManager


class _DatabaseTestCase(unittest.TestCase):
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

    def _spool(self, **fields):
        values = dict(displayName="Spool", isActive=True, diameter=1.75, density=1.24)
        values.update(fields)
        return SpoolModel.create(**values)


class TestTeachInMarksAnOwnWrite(_DatabaseTestCase):
    def _teach(self, spool, **extra):
        body = dict({"databaseId": spool.databaseId, "uid": TAG_UID}, **extra)
        with self.app.test_request_context(json=body):
            response = _Api(self.databaseManager).teachOctoScaleRfidTagKey()
        return flask.json.loads(response.get_data())

    def test_the_taught_key_is_recorded_as_the_plugins_own_write(self):
        spool = self._spool()

        answer = self._teach(spool)

        self.assertTrue(answer["taught"], answer)
        stored = SpoolModel.get_by_id(spool.databaseId)
        self.assertEqual(answer["rfidTagKey"], stored.rfidTagKey)
        self.assertEqual(
            RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE, stored.rfidTagKeySource
        )

    def test_a_refused_teach_in_changes_nothing(self):
        spool = self._spool(rfidTagKey="DEADBEEF")

        answer = self._teach(spool)

        self.assertFalse(answer["taught"], answer)
        stored = SpoolModel.get_by_id(spool.databaseId)
        self.assertEqual("DEADBEEF", stored.rfidTagKey)
        self.assertIsNone(stored.rfidTagKeySource)


class TestSavingASpool(unittest.TestCase):
    def _apply(self, spoolModel, payload):
        errors = _Api()._updateSpoolModelFromJSONData(spoolModel, dict(BASE, **payload))
        self.assertEqual([], errors)
        return spoolModel

    def _ownWriteSpool(self):
        spoolModel = SpoolModel()
        spoolModel.rfidTagKey = TAG_UID
        spoolModel.rfidTagKeySource = RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE
        return spoolModel

    def test_editing_the_key_by_hand_forgets_where_it_came_from(self):
        spoolModel = self._apply(self._ownWriteSpool(), {"rfidTagKey": "1234"})
        self.assertIsNone(spoolModel.rfidTagKeySource)

    def test_clearing_the_key_forgets_it_too(self):
        spoolModel = self._apply(self._ownWriteSpool(), {"rfidTagKey": None})
        self.assertIsNone(spoolModel.rfidTagKeySource)

    def test_a_save_that_keeps_the_key_keeps_the_source(self):
        # the edit dialog and a GET -> save round trip send the key back unchanged
        spoolModel = self._apply(self._ownWriteSpool(), {"rfidTagKey": TAG_UID})
        self.assertEqual(
            RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE, spoolModel.rfidTagKeySource
        )

    def test_a_client_cannot_set_the_source(self):
        spoolModel = self._apply(
            SpoolModel(), {"rfidTagKey": TAG_UID, "rfidTagKeySource": "ownWrite"}
        )
        self.assertIsNone(spoolModel.rfidTagKeySource)


class TestDescribingAMatch(_DatabaseTestCase):
    # the read path: a TigerTag's parser id, and the spool its UID key resolved to
    def _match(self, spool):
        return _Api(self.databaseManager)._describeRfidMatch(
            spool.rfidTagKey, tagFormat="tigerTag", matchedSpool=spool
        )

    def test_a_tigertag_the_plugin_wrote_itself_is_no_foreign_tag(self):
        spool = self._spool(
            rfidTagKey=TAG_UID,
            rfidTagKeySource=RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE,
        )
        self.assertEqual({"foreignTag": False, "ambiguous": False}, self._match(spool))

    def test_a_manufacturers_tigertag_sharing_a_key_is(self):
        spool = self._spool(rfidTagKey=TAG_UID)
        self.assertEqual({"foreignTag": True, "ambiguous": False}, self._match(spool))

    def test_an_own_write_still_reports_a_second_spool_on_the_key(self):
        spool = self._spool(
            rfidTagKey=TAG_UID,
            rfidTagKeySource=RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE,
        )
        self._spool(displayName="Other", rfidTagKey=TAG_UID)
        self.assertEqual({"foreignTag": False, "ambiguous": True}, self._match(spool))


if __name__ == "__main__":
    unittest.main()
