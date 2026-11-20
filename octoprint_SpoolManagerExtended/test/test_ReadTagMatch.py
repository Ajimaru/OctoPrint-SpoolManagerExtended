# coding=utf-8

# Tests for how "Read tag" (POST /octoscale/readTag) finds the spool a tag belongs to: the
# spool id written on an OctoScale extended tag first, the tag's UID key second - the order
# the OctoScale firmware uses when it weighs a spool. Exercised through
# _buildReadTagResponse(), the part both the NTAG and the Mifare Classic path share.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_ReadTagMatch.py

import logging
import unittest

import flask
import peewee

from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.common.FilamentTagModel import (
    GenericFilament,
    ScanResult,
    TagType,
)
from octoprint_SpoolManagerExtended.common.FilamentTagReader import TagReadResult
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel
from octoprint_SpoolManagerExtended.U1RfidManager import deriveRfidTagKey

# a 7-byte NTAG UID; keyed on the whole UID
TAG_UID = "04C7D656CB2A81"


class _Api(SpoolManagerAPI):
    """The pieces under test only; SpoolManagerAPI.__init__ is deliberately not called."""

    def __init__(self, databaseManager):
        self._logger = logging.getLogger("test.readtagmatch")
        self._databaseManager = databaseManager


class TestReadTagMatch(unittest.TestCase):
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

    def _keyedSpool(self, **fields):
        return self._spool(rfidTagKey=deriveRfidTagKey(TAG_UID), **fields)

    def _read(self, tagDatabaseId=None, parserId="ntagExtended"):
        filament = GenericFilament(
            parserId,
            None,
            "Vendor",
            "PLA",
            [],
            [0xFF00AA55],
            1.75,
            1000,
            190,
            220,
            60,
            0,
            0,
            None,
        )
        if tagDatabaseId is not None:
            # what the OctoScale extended parsers attach once magic and CRC checked out
            filament.octoscaleExtendedFields = {"databaseId": tagDatabaseId}
        readResult = TagReadResult(data=b"\x00", uid=TAG_UID)
        scanResult = ScanResult(TagType.MIFARE_ULTRALIGHT, bytes.fromhex(TAG_UID))
        with self.app.test_request_context():
            response = _Api(self.databaseManager)._buildReadTagResponse(
                filament, readResult, scanResult, {}
            )
            return flask.json.loads(response.get_data())

    def test_the_spool_id_on_the_tag_wins_over_the_uid_key(self):
        # the case seen live: the tag was written for one spool after its UID had been
        # taught in to another
        written = self._spool(displayName="Written for")
        taught = self._keyedSpool(displayName="Taught in")

        answer = self._read(tagDatabaseId=written.databaseId)

        self.assertEqual(written.databaseId, answer["matchedSpoolId"])
        self.assertEqual("Written for", answer["matchedSpoolDisplayName"])
        self.assertEqual("tagId", answer["matchedBy"])
        self.assertEqual(taught.databaseId, answer["uidMatchedSpoolId"])
        self.assertEqual("Taught in", answer["uidMatchedSpoolDisplayName"])
        # an exact primary-key match carries no key-collision caveat
        self.assertIsNone(answer["match"])

    def test_a_tag_whose_uid_was_never_taught_in_is_still_found(self):
        written = self._spool()

        answer = self._read(tagDatabaseId=written.databaseId)

        self.assertEqual(written.databaseId, answer["matchedSpoolId"])
        self.assertEqual("tagId", answer["matchedBy"])
        self.assertIsNone(answer["uidMatchedSpoolId"])

    def test_id_and_uid_naming_the_same_spool_is_no_conflict(self):
        spool = self._keyedSpool()

        answer = self._read(tagDatabaseId=spool.databaseId)

        self.assertEqual(spool.databaseId, answer["matchedSpoolId"])
        self.assertEqual("tagId", answer["matchedBy"])
        self.assertIsNone(answer["uidMatchedSpoolId"])

    def test_an_id_naming_no_spool_here_falls_back_to_the_uid(self):
        # deleted since, or written from another database
        taught = self._keyedSpool()

        answer = self._read(tagDatabaseId=taught.databaseId + 100)

        self.assertEqual(taught.databaseId, answer["matchedSpoolId"])
        self.assertEqual("uid", answer["matchedBy"])
        self.assertIsNone(answer["uidMatchedSpoolId"])
        self.assertEqual({"foreignTag": False, "ambiguous": False}, answer["match"])

    def test_an_id_naming_a_template_is_not_a_match(self):
        template = self._spool(isTemplate=True)

        answer = self._read(tagDatabaseId=template.databaseId)

        self.assertIsNone(answer["matchedSpoolId"])
        self.assertIsNone(answer["matchedBy"])

    def test_a_vendor_tag_is_still_matched_by_its_uid_alone(self):
        taught = self._keyedSpool()

        answer = self._read(parserId="tigerTag")

        self.assertEqual(taught.databaseId, answer["matchedSpoolId"])
        self.assertEqual("uid", answer["matchedBy"])
        self.assertEqual({"foreignTag": True, "ambiguous": False}, answer["match"])

    def test_no_spool_at_all(self):
        answer = self._read(tagDatabaseId=5)

        self.assertIsNone(answer["matchedSpoolId"])
        self.assertIsNone(answer["matchedBy"])
        self.assertIsNone(answer["uidMatchedSpoolId"])
        self.assertIsNone(answer["match"])


if __name__ == "__main__":
    unittest.main()
