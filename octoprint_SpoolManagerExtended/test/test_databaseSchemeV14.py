# coding=utf-8

# Tests for the 13 -> 14 migration (rfidTagKeySource). Same approach as test_databaseSchemeV12:
# a real in-memory SQLite database, because the property under test is what ALTER TABLE does.
# The idempotency test matters most - several OctoPrint instances share one external database
# and each runs the migration in turn.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_databaseSchemeV14.py

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.PluginMetaDataModel import (
    PluginMetaDataModel,
)

V14_COLUMN = "rfidTagKeySource"


class TestDatabaseSchemeV14(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)
        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database
        PluginMetaDataModel.create(
            key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION, value="13"
        )

    def tearDown(self):
        if not self.database.is_closed():
            self.database.close()

    def _columnNames(self):
        return [column.name for column in self.database.get_columns("spo_spoolmodel")]

    def _storedVersion(self):
        return PluginMetaDataModel.get(
            PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION
        ).value

    def _dropV14Column(self):
        # create_tables() built the current model, which has the column - drop it so the
        # migration meets a genuine V13 table
        self.database.execute_sql(
            "ALTER TABLE spo_spoolmodel DROP COLUMN " + V14_COLUMN
        )

    def test_migration_adds_the_column_and_bumps_the_version(self):
        self._dropV14Column()
        self.assertNotIn(V14_COLUMN, self._columnNames())

        self.databaseManager._upgradeFrom13To14()

        self.assertIn(V14_COLUMN, self._columnNames())
        self.assertEqual("14", self._storedVersion())

    def test_second_run_against_the_same_schema_is_a_noop(self):
        self._dropV14Column()
        self.databaseManager._upgradeFrom13To14()

        self.databaseManager._upgradeFrom13To14()

        self.assertEqual(1, self._columnNames().count(V14_COLUMN))
        self.assertEqual("14", self._storedVersion())


if __name__ == "__main__":
    unittest.main()
