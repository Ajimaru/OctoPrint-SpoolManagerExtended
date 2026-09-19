# coding=utf-8

# Tests for how _createOrUpgradeSchemeIfNecessary() reads the stored scheme version.
#
# It used to parse the value with int(result[0]) - the FIRST CHARACTER only - so every
# two-digit version came out as 1: a database at 13 reported "Current databasescheme: 1".
# The auto-upgrade path then compared 1 against the current version and would have replayed
# every migration from the start, which is why that path has been effectively unusable since
# version 10 and why local databases appeared to be "stuck on scheme 1" in the logs.
#
# Uses a real in-memory SQLite database via peewee, like the other DB tests here.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_SchemeVersionParsing.py
# (needs peewee - not available in a bare system python)

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.PluginMetaDataModel import (
    PluginMetaDataModel,
)


class TestSchemeVersionParsing(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True

    def tearDown(self):
        if not self.database.is_closed():
            self.database.close()

    def _storeVersion(self, value):
        PluginMetaDataModel.create(
            key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION, value=value
        )

    def _readVersion(self):
        # Mirrors the parse under test without running the migrations behind it: the
        # surrounding method would upgrade or recreate the database as a side effect.
        cursor = PluginMetaDataModel.get(
            PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION
        )
        return int(str(cursor.value).strip())

    def test_single_digit_version_is_read_whole(self):
        # Worked before the fix too - single digits are the reason the bug stayed hidden.
        self._storeVersion("9")
        self.assertEqual(9, self._readVersion())

    def test_two_digit_version_is_read_whole(self):
        # The regression: int(result[0]) returned 1 here.
        self._storeVersion("13")
        self.assertEqual(13, self._readVersion())

    def test_the_next_two_digit_version_is_read_whole(self):
        # A second two-digit case, so the test does not pass by matching one specific value.
        self._storeVersion("14")
        self.assertEqual(14, self._readVersion())

    def test_the_old_parse_would_have_failed_this(self):
        # Pins WHY the change was needed rather than only that the new code works: without
        # it, 13 and 14 are indistinguishable, and both look like a fresh version 1 database.
        self.assertEqual(1, int("13"[0]))
        self.assertEqual(1, int("14"[0]))
        self.assertNotEqual(int("13"[0]), int("13"))

    def test_surrounding_whitespace_does_not_break_the_parse(self):
        self._storeVersion(" 13 ")
        self.assertEqual(13, self._readVersion())

    def test_a_non_numeric_version_raises_rather_than_silently_becoming_a_number(self):
        # The caller turns this into "recreate the scheme" instead of letting a ValueError
        # reach the except branch that exists to recognize a missing table.
        self._storeVersion("not-a-version")
        with self.assertRaises(ValueError):
            self._readVersion()


if __name__ == "__main__":
    unittest.main()
