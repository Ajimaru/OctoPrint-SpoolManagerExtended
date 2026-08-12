# coding=utf-8

# Regression test for DatabaseManager.loadSpoolByRfidTagKey() - the stable-key lookup
# U1RfidManager uses to resolve a scanned tag to a spool. Uses a real in-memory SQLite
# database via peewee, not a fake, following the same pattern as test_loadSpoolByCode.py
# (whose isTemplate NULL-handling bug a fake DB layer could not have caught either).
#
# Run with:  python3 octoprint_SpoolManager/test/test_loadSpoolByRfidTagKey.py

import logging
import unittest

import peewee

from octoprint_SpoolManager.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManager.models.SpoolModel import SpoolModel


class TestLoadSpoolByRfidTagKey(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(logging.getLogger("test.dbmanager"), False)
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _create(self, **fields):
        defaults = {"displayName": "Test Spool", "isActive": True}
        defaults.update(fields)
        return SpoolModel.create(**defaults)

    def test_regularSpoolWithNullIsTemplateIsFound(self):
        # same isTemplate-NULL pitfall as loadSpoolByCode() - regular spools store NULL,
        # never False, so the filter must be (isTemplate == False) | (isTemplate == None)
        self._create(rfidTagKey="1040", displayName="Snapspeed Green", isTemplate=None)

        result = self.databaseManager.loadSpoolByRfidTagKey(
            "1040", withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.displayName, "Snapspeed Green")

    def test_spoolWithExplicitFalseIsTemplateIsFound(self):
        self._create(rfidTagKey="ABCD", displayName="Explicit False", isTemplate=False)

        result = self.databaseManager.loadSpoolByRfidTagKey(
            "ABCD", withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.displayName, "Explicit False")

    def test_templateSpoolIsExcluded(self):
        self._create(rfidTagKey="ABCD", displayName="PLA Template", isTemplate=True)

        result = self.databaseManager.loadSpoolByRfidTagKey(
            "ABCD", withReusedConnection=True
        )

        self.assertIsNone(result)

    def test_unknownRfidTagKeyReturnsNone(self):
        self._create(rfidTagKey="1234", isTemplate=None)

        result = self.databaseManager.loadSpoolByRfidTagKey(
            "FFFF", withReusedConnection=True
        )

        self.assertIsNone(result)

    def test_emptyOrNoneRfidTagKeyReturnsNoneWithoutQuerying(self):
        self.assertIsNone(
            self.databaseManager.loadSpoolByRfidTagKey(None, withReusedConnection=True)
        )
        self.assertIsNone(
            self.databaseManager.loadSpoolByRfidTagKey("", withReusedConnection=True)
        )
        self.assertIsNone(
            self.databaseManager.loadSpoolByRfidTagKey("   ", withReusedConnection=True)
        )

    def test_newestMatchWinsOnCollision(self):
        # PRELIMINARY: the 16-bit key space makes a collision between two DIFFERENT
        # physical spools possible (see SpoolModel.rfidTagKey's docstring), not just the
        # deliberate two-tags-per-spool case this feature targets. Resolve the same way
        # loadSpoolByCode() resolves an accidental duplicate: newest wins.
        self._create(rfidTagKey="1040", displayName="Older", isTemplate=None)
        self._create(rfidTagKey="1040", displayName="Newer", isTemplate=None)

        result = self.databaseManager.loadSpoolByRfidTagKey(
            "1040", withReusedConnection=True
        )

        self.assertEqual(result.displayName, "Newer")

    def test_codeFieldIsIndependentOfRfidTagKey(self):
        # rfidTagKey must not be confused with / fall back to `code` - a spool may carry
        # its own unrelated serial number there (the reason this is a separate field)
        self._create(
            code="MY-OWN-SERIAL-0001", rfidTagKey="1040", displayName="Green", isTemplate=None
        )

        byRfidTagKey = self.databaseManager.loadSpoolByRfidTagKey(
            "1040", withReusedConnection=True
        )
        byCode = self.databaseManager.loadSpoolByCode(
            "MY-OWN-SERIAL-0001", withReusedConnection=True
        )

        self.assertEqual(byRfidTagKey.displayName, "Green")
        self.assertEqual(byCode.displayName, "Green")
        self.assertIsNone(
            self.databaseManager.loadSpoolByRfidTagKey(
                "MY-OWN-SERIAL-0001", withReusedConnection=True
            )
        )


if __name__ == "__main__":
    unittest.main()
