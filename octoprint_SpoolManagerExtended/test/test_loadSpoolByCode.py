# coding=utf-8

# Regression test for DatabaseManager.loadSpoolByCode() (the UID -> spool lookup the U1
# RFID self-reporter depends on). Uses a real in-memory SQLite database via peewee, not
# a fake - the bug this guards against is a SQL semantics issue that a fake DB layer
# cannot reproduce.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_loadSpoolByCode.py
# Kept separate from test_DatabaseManager.py, whose setUp() connects to a hardcoded
# absolute path (`/Users/o0632/...`) and fails outright in any other environment - this
# test needs nothing but an in-memory database and must not inherit that failure.

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel


class TestLoadSpoolByCode(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(logging.getLogger("test.dbmanager"), False)
        # Bypass connectoToDatabase() (postgres/mysql/sqlite-file branching, unrelated to
        # what's under test here) and hand the manager an already-open connection, the
        # same way _handleReusableConnection()'s withReusedConnection=True path expects.
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
        # THE bug: isTemplate is a nullable BooleanField, and every spool ever created
        # through the normal UI (wizard/edit dialog) stores NULL there, never False -
        # confirmed against a real spool created via the wizard. A `isTemplate != True`
        # filter evaluates to NULL (not TRUE) for a NULL column in SQL, so it silently
        # excluded EVERY regular spool and the U1 reported every already-taught tag as
        # unknown on every subsequent scan.
        self._create(code="5DD71040", displayName="Snapspeed Green", isTemplate=None)

        result = self.databaseManager.loadSpoolByCode(
            "5DD71040", withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.displayName, "Snapspeed Green")

    def test_spoolWithExplicitFalseIsTemplateIsFound(self):
        self._create(code="ABCDEF01", displayName="Explicit False", isTemplate=False)

        result = self.databaseManager.loadSpoolByCode(
            "ABCDEF01", withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.displayName, "Explicit False")

    def test_templateSpoolIsExcluded(self):
        # a template describes a product, not the physical spool carrying the tag - it
        # must never be what a scanned UID resolves to
        self._create(code="TEMPLATE1", displayName="PLA Template", isTemplate=True)

        result = self.databaseManager.loadSpoolByCode(
            "TEMPLATE1", withReusedConnection=True
        )

        self.assertIsNone(result)

    def test_unknownCodeReturnsNone(self):
        self._create(code="KNOWN001", isTemplate=None)

        result = self.databaseManager.loadSpoolByCode(
            "DOES-NOT-EXIST", withReusedConnection=True
        )

        self.assertIsNone(result)

    def test_emptyOrNoneCodeReturnsNoneWithoutQuerying(self):
        self.assertIsNone(
            self.databaseManager.loadSpoolByCode(None, withReusedConnection=True)
        )
        self.assertIsNone(
            self.databaseManager.loadSpoolByCode("", withReusedConnection=True)
        )
        self.assertIsNone(
            self.databaseManager.loadSpoolByCode("   ", withReusedConnection=True)
        )

    def test_newestMatchWinsIfATagWasAssignedTwice(self):
        # defensive: a UID accidentally copied onto two spools' code field should
        # resolve to the one taught most recently, not silently to an arbitrary one
        self._create(code="DUPLICATE", displayName="Older", isTemplate=None)
        self._create(code="DUPLICATE", displayName="Newer", isTemplate=None)

        result = self.databaseManager.loadSpoolByCode(
            "DUPLICATE", withReusedConnection=True
        )

        self.assertEqual(result.displayName, "Newer")


if __name__ == "__main__":
    unittest.main()
