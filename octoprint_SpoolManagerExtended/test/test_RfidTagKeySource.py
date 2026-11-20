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
from octoprint_SpoolManagerExtended.U1RfidManager import deriveRfidTagKey

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
    getOctoScaleNfcStatus = SpoolManagerAPI.getOctoScaleNfcStatus.__wrapped__

    def __init__(self, databaseManager=None, probe=None):
        self._logger = logging.getLogger("test.rfidtagkeysource")
        self._databaseManager = databaseManager
        # what OctoScale's /nfcprobe answers - for both the write guard and /octoscale/nfc
        self._probe = probe
        self._settings = _Settings()

    def _probeTagOnReader(self):
        return self._probe

    def _getOctoScaleBaseUrlChecked(self):
        return "http://octoscale", None

    def _callOctoScale(self, baseUrl, path, **kwargs):
        return _Response(self._probe), None


class _Settings:
    def get(self, path):
        return "extended"


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


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

    def test_rewriting_a_tag_the_spool_already_has_the_key_of_marks_it_as_own_write(
        self,
    ):
        # the key came from somewhere else (typed in, taught from a read, or before V14) -
        # the teach-in has nothing to save, but the plugin did just write that tag
        key = deriveRfidTagKey(TAG_UID)
        spool = self._spool(rfidTagKey=key, version=3)

        answer = self._teach(spool)

        self.assertFalse(answer["taught"], answer)
        self.assertEqual("unchanged", answer["reason"])
        stored = SpoolModel.get_by_id(spool.databaseId)
        self.assertEqual(key, stored.rfidTagKey)
        self.assertEqual(
            RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE, stored.rfidTagKeySource
        )
        # bookkeeping only: an edit dialog open on this spool must not see a conflict
        self.assertEqual(3, stored.version)

    def test_marking_the_source_needs_the_key_to_still_match(self):
        spool = self._spool(rfidTagKey="DEADBEEF")

        changed = self.databaseManager.markRfidTagKeySource(
            spool.databaseId, "CAFEBABE", RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE
        )

        self.assertEqual(0, changed)
        self.assertIsNone(SpoolModel.get_by_id(spool.databaseId).rfidTagKeySource)

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


class TestAnOwnWriteOnTheReader(_DatabaseTestCase):
    # A TigerTag/OpenPrintTag the plugin wrote itself is exempt from the vendor-tag
    # protections - on the write guard and in what /octoscale/nfc tells the frontend.
    def _probeFor(self, uid, idSource="extendedNoId"):
        return {
            "present": True,
            "uid": uid,
            "idSource": idSource,
            "idParsed": -1,
            "hasExtendedData": True,
            "tagType": "ntag",
        }

    def _api(self, uid=TAG_UID, idSource="extendedNoId"):
        return _Api(self.databaseManager, probe=self._probeFor(uid, idSource))

    def _ownWriteSpool(self, uid=TAG_UID, **fields):
        return self._spool(
            rfidTagKey=deriveRfidTagKey(uid),
            rfidTagKeySource=RfidKeyCollision.RFID_TAG_KEY_SOURCE_OWN_WRITE,
            **fields,
        )

    def _nfcStatus(self, api):
        with self.app.test_request_context():
            return flask.json.loads(api.getOctoScaleNfcStatus().get_data())

    def test_a_tag_the_plugin_wrote_is_no_foreign_payload_to_guard(self):
        self._ownWriteSpool()
        self.assertFalse(self._api()._tagOnReaderHasForeignExtendedPayload())

    def test_a_key_that_was_typed_in_does_not_lift_the_guard(self):
        self._spool(rfidTagKey=deriveRfidTagKey(TAG_UID))
        self.assertTrue(self._api()._tagOnReaderHasForeignExtendedPayload())

    def test_an_unknown_vendor_tag_stays_guarded(self):
        self.assertTrue(self._api()._tagOnReaderHasForeignExtendedPayload())

    def test_a_truncated_uid_never_lifts_the_guard(self):
        # a cascade-level abort reports 3 bytes of a 7-byte UID - see isPlausibleTagUid()
        fragment = TAG_UID[:6]
        self._ownWriteSpool(uid=fragment)
        self.assertTrue(self._api(uid=fragment)._tagOnReaderHasForeignExtendedPayload())

    def test_the_nfc_status_names_the_spool_an_own_write_belongs_to(self):
        spool = self._ownWriteSpool(displayName="Mine")

        status = self._nfcStatus(self._api())

        self.assertTrue(status["ownWrite"])
        self.assertEqual(spool.databaseId, status["ownWriteSpoolId"])
        self.assertEqual("Mine", status["ownWriteSpoolDisplayName"])

    def test_the_nfc_status_of_a_vendor_tag_reports_no_own_write(self):
        self._spool(rfidTagKey=deriveRfidTagKey(TAG_UID))

        status = self._nfcStatus(self._api())

        self.assertFalse(status["ownWrite"])
        self.assertNotIn("ownWriteSpoolId", status)

    def test_an_own_extended_tag_is_not_looked_up(self):
        # only extendedNoId tags need the lookup; anything else keeps its id-based handling
        self._ownWriteSpool()

        status = self._nfcStatus(self._api(idSource="extended"))

        self.assertFalse(status["ownWrite"])


if __name__ == "__main__":
    unittest.main()
