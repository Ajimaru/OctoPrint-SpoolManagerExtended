# coding=utf-8

# Tests for the 11 -> 12 migration (drying temperature/time and TD). Uses a real in-memory
# SQLite database via peewee: the property under test is what ALTER TABLE actually does,
# which a fake DB layer cannot reproduce.
#
# The idempotency test is the important one. Several OctoPrint instances can share one
# external database, so the migration will be run more than once against the same schema -
# a second run must be a no-op rather than an error.
#
# Run with:  python3 octoprint_SpoolManager/test/test_databaseSchemeV12.py
# (needs peewee - not available in a bare system python, same as the other DB tests here)

import logging
import unittest

import peewee

from octoprint_SpoolManager.DatabaseManager import (
    CURRENT_DATABASE_SCHEME_VERSION,
    MODELS,
    DatabaseManager,
)
from octoprint_SpoolManager.models.PluginMetaDataModel import PluginMetaDataModel

V12_COLUMNS = ("dryingTemperature", "dryingTime", "td")


class TestDatabaseSchemeV12(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database

        PluginMetaDataModel.create(
            key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION, value="11"
        )

    def tearDown(self):
        if not self.database.is_closed():
            self.database.close()

    def _columnNames(self):
        return [column.name for column in self.database.get_columns("spo_spoolmodel")]

    def _dropV12Columns(self):
        # create_tables() built the current model, which already has the V12 columns - drop
        # them again so the migration has something to do, i.e. a genuine V11 table.
        for columnName in V12_COLUMNS:
            self.database.execute_sql(
                "ALTER TABLE spo_spoolmodel DROP COLUMN " + columnName
            )

    def test_current_version_is_twelve(self):
        self.assertEqual(12, CURRENT_DATABASE_SCHEME_VERSION)

    def test_migration_adds_the_three_columns(self):
        self._dropV12Columns()
        for columnName in V12_COLUMNS:
            self.assertNotIn(columnName, self._columnNames())

        self.databaseManager._upgradeFrom11To12()

        for columnName in V12_COLUMNS:
            self.assertIn(columnName, self._columnNames())

    def test_migration_records_the_new_version(self):
        self._dropV12Columns()
        self.databaseManager._upgradeFrom11To12()
        stored = (
            PluginMetaDataModel.select()
            .where(
                PluginMetaDataModel.key
                == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION
            )
            .get()
        )
        self.assertEqual("12", stored.value)

    def test_running_the_migration_twice_is_a_no_op(self):
        # Several OctoPrint instances may share one external database and each will try to
        # migrate it. The second run must not raise "duplicate column name".
        self._dropV12Columns()
        self.databaseManager._upgradeFrom11To12()
        self.databaseManager._upgradeFrom11To12()
        for columnName in V12_COLUMNS:
            self.assertIn(columnName, self._columnNames())

    def test_migration_on_an_already_current_schema_is_a_no_op(self):
        # create_tables() already produced V12 columns - the migration must notice and
        # skip, not fail.
        self.databaseManager._upgradeFrom11To12()
        for columnName in V12_COLUMNS:
            self.assertIn(columnName, self._columnNames())

    def test_existing_rows_survive_the_migration(self):
        from octoprint_SpoolManager.models.SpoolModel import SpoolModel

        SpoolModel.create(displayName="Existing spool", material="PLA")
        self._dropV12Columns()

        self.databaseManager._upgradeFrom11To12()

        spool = SpoolModel.select().where(SpoolModel.displayName == "Existing spool").get()
        self.assertEqual("PLA", spool.material)
        # new columns default to NULL rather than 0 - "not set" must stay distinguishable
        # from "set to zero", which is exactly the distinction the tag mapping relies on
        self.assertIsNone(spool.dryingTemperature)
        self.assertIsNone(spool.dryingTime)
        self.assertIsNone(spool.td)

    def test_td_accepts_a_fractional_value(self):
        from octoprint_SpoolManager.models.SpoolModel import SpoolModel

        # td is REAL, unlike the two integer drying columns - a rounded 1.0 would be wrong.
        spool = SpoolModel.create(displayName="TD spool", td=1.75)
        reloaded = SpoolModel.select().where(SpoolModel.databaseId == spool.databaseId).get()
        self.assertAlmostEqual(1.75, reloaded.td)


if __name__ == "__main__":
    unittest.main()
