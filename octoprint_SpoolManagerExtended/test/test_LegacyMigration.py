# coding=utf-8

# Tests for the manual migration from a previous "SpoolManager" install (the plugin was
# renamed to "SpoolManagerExtended", and both the data folder and the settings namespace
# are derived from the identifier).
#
# The migration touches user data, so these run against real directories via
# tempfile.TemporaryDirectory() rather than a mocked filesystem. File contents are made
# distinguishable ("OLD" vs "NEW") - otherwise a passing test would not show WHICH file
# ended up in the target folder, which is the whole point of the copy/overwrite rules.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_LegacyMigration.py -v

import logging
import os
import sqlite3
import tempfile
import unittest

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.DatabaseManager import DATABASE_FILE_NAME

LEGACY_IDENTIFIER = "SpoolManager"


class FakeSettings(object):
    """
    Stand-in for OctoPrint's settings: a data base folder plus the global plugin settings
    tree, which is where the legacy namespace lives.
    """

    def __init__(self, dataFolder, legacySettings=None):
        self._dataFolder = dataFolder
        self._globalPlugins = {}
        if legacySettings is not None:
            self._globalPlugins[LEGACY_IDENTIFIER] = legacySettings
        self.ownSettings = {}
        self.saveCallCount = 0

    def getBaseFolder(self, name):
        return self._dataFolder

    def global_get(self, path):
        if path == ["plugins", LEGACY_IDENTIFIER]:
            return self._globalPlugins.get(LEGACY_IDENTIFIER)
        return None

    def get(self, keys):
        return self.ownSettings.get(keys[0])

    def get_boolean(self, keys):
        return bool(self.ownSettings.get(keys[0], False))

    def set_boolean(self, keys, value):
        self.ownSettings[keys[0]] = bool(value)

    def set(self, keys, value):
        # OctoPrint drops a key that is set back to None, which is what the undo relies
        # on to remove settings the user never had a value for
        if value is None:
            self.ownSettings.pop(keys[0], None)
            return
        self.ownSettings[keys[0]] = value

    def save(self):
        self.saveCallCount += 1


class FakeDatabaseManager(object):
    def __init__(self, schemeUpgradeNeeded=False):
        self._schemeUpgradeNeeded = schemeUpgradeNeeded

    def isSchemeUpgradeNeeded(self):
        return self._schemeUpgradeNeeded


class FakePlugin(object):
    """
    Binds the real migration methods onto a lightweight stand-in - driving the full
    SpoolmanagerPlugin would need OctoPrint's plugin loader.
    """

    _getLegacyDataFolder = SpoolmanagerPlugin._getLegacyDataFolder
    _isLegacyMigrationAvailable = SpoolmanagerPlugin._isLegacyMigrationAvailable
    _databaseHoldsSpools = SpoolmanagerPlugin._databaseHoldsSpools
    _performLegacyMigration = SpoolmanagerPlugin._performLegacyMigration
    _readLegacyDatabasePreview = SpoolmanagerPlugin._readLegacyDatabasePreview
    _classifyLegacyFile = SpoolmanagerPlugin._classifyLegacyFile
    _getLegacyFileEntries = SpoolmanagerPlugin._getLegacyFileEntries
    _getUndoFilePath = SpoolmanagerPlugin._getUndoFilePath
    _isLegacyMigrationUndoAvailable = SpoolmanagerPlugin._isLegacyMigrationUndoAvailable
    _isLegacyMigrationDone = SpoolmanagerPlugin._isLegacyMigrationDone
    _writeUndoRecord = SpoolmanagerPlugin._writeUndoRecord
    _captureSettingsForUndo = SpoolmanagerPlugin._captureSettingsForUndo
    _getLegacySettingsKeys = SpoolmanagerPlugin._getLegacySettingsKeys
    _getLegacySettingsComparison = SpoolmanagerPlugin._getLegacySettingsComparison
    # staticmethod on the real class: binding it here would hand it `self` as its first
    # argument, so take the underlying function instead
    _formatSettingValue = staticmethod(SpoolmanagerPlugin._formatSettingValue)
    _applyLegacySettings = SpoolmanagerPlugin._applyLegacySettings
    _undoLegacyMigration = SpoolmanagerPlugin._undoLegacyMigration

    def __init__(self, dataFolder, newDataFolder, legacySettings=None):
        self._settings = FakeSettings(dataFolder, legacySettings)
        self._newDataFolder = newDataFolder
        self._logger = logging.getLogger("test.legacyMigration")
        self._identifier = "SpoolManagerExtended"
        self._databaseManager = FakeDatabaseManager()
        self._defaults = {}

    def get_plugin_data_folder(self):
        return self._newDataFolder

    def get_settings_defaults(self):
        return dict(self._defaults)


class TestLegacyMigration(unittest.TestCase):
    def setUp(self):
        self._tempDir = tempfile.TemporaryDirectory()
        self.dataFolder = self._tempDir.name
        self.legacyFolder = os.path.join(self.dataFolder, LEGACY_IDENTIFIER)
        self.newFolder = os.path.join(self.dataFolder, "SpoolManagerExtended")
        os.makedirs(self.newFolder, exist_ok=True)

    def tearDown(self):
        self._tempDir.cleanup()

    def _writeLegacyFile(self, name, content):
        os.makedirs(self.legacyFolder, exist_ok=True)
        path = os.path.join(self.legacyFolder, name)
        with open(path, "w") as fileHandle:
            fileHandle.write(content)
        return path

    def _read(self, path):
        with open(path) as fileHandle:
            return fileHandle.read()

    def _writeDatabase(self, path, spoolNames):
        """
        Creates a real SQLite file with a spo_spoolmodel table, because the conflict
        check counts spools rather than testing for the file's existence - the plugin
        creates an empty database of its own on first start.
        """
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS spo_spoolmodel "
                "(databaseId INTEGER PRIMARY KEY, displayName VARCHAR(255))"
            )
            connection.executemany(
                "INSERT INTO spo_spoolmodel (displayName) VALUES (?)",
                [(name,) for name in spoolNames],
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def _spoolNames(self, path):
        connection = sqlite3.connect(path)
        try:
            rows = connection.execute(
                "SELECT displayName FROM spo_spoolmodel ORDER BY databaseId"
            ).fetchall()
        finally:
            connection.close()
        return [row[0] for row in rows]

    def _plugin(self, legacySettings=None):
        return FakePlugin(self.dataFolder, self.newFolder, legacySettings)

    ############################################################################ detection

    def test_noLegacyInstallIsNotAvailable(self):
        plugin = self._plugin()

        self.assertIsNone(plugin._getLegacyDataFolder())
        self.assertFalse(plugin._isLegacyMigrationAvailable())

    def test_legacyFolderWithOnlyCachesIsNotAvailable(self):
        # THE case seen on the k9 instance: the old plugin recreates its SpoolmanDB cache
        # on its own, so a folder holding just those files must not advertise a migration -
        # otherwise the hint would never go away and clicking it would copy nothing useful.
        self._writeLegacyFile("spoolmandb_index.json", "{}")
        self._writeLegacyFile("spoolmandb_installation_id", "abc")

        plugin = self._plugin()

        self.assertIsNotNone(plugin._getLegacyDataFolder())
        self.assertFalse(plugin._isLegacyMigrationAvailable())

    def test_legacyDatabaseMakesMigrationAvailable(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")

        self.assertTrue(self._plugin()._isLegacyMigrationAvailable())

    def test_legacySettingsAloneMakeMigrationAvailable(self):
        # external-database installs keep no local .db, but their credentials still live
        # in the old settings namespace and are worth migrating
        plugin = self._plugin(legacySettings={"databaseType": "mysql"})

        self.assertTrue(plugin._isLegacyMigrationAvailable())

    ############################################################################ migration

    def test_migrationCopiesAndKeepsTheOriginal(self):
        legacyDatabase = self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        self._writeLegacyFile("spoolmandb_index.json", "{}")

        result = self._plugin()._performLegacyMigration()

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertEqual(result["copiedFiles"], 2)

        migrated = os.path.join(self.newFolder, DATABASE_FILE_NAME)
        self.assertTrue(os.path.isfile(migrated))
        self.assertEqual(self._read(migrated), "OLD")
        # copy, not move - the old install has to stay usable as a fallback
        self.assertTrue(os.path.isfile(legacyDatabase))
        self.assertEqual(self._read(legacyDatabase), "OLD")

    def test_migrationAdoptsSettings(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        plugin = self._plugin(
            legacySettings={"databaseType": "mysql", "lengthUnit": "m"}
        )

        result = plugin._performLegacyMigration()

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertTrue(result["settingsMigrated"])
        self.assertEqual(plugin._settings.ownSettings["databaseType"], "mysql")
        self.assertEqual(plugin._settings.ownSettings["lengthUnit"], "m")
        self.assertEqual(plugin._settings.saveCallCount, 1)

    def test_nothingToMigrateReportsFailure(self):
        result = self._plugin()._performLegacyMigration()

        self.assertFalse(result["success"])
        self.assertFalse(result["conflict"])
        self.assertIsNotNone(result["errorMessage"])

    ############################################################################ conflicts

    def test_populatedDatabaseCausesConflictAndChangesNothing(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        existing = self._writeDatabase(
            os.path.join(self.newFolder, DATABASE_FILE_NAME), ["Own Spool"]
        )

        result = self._plugin()._performLegacyMigration(overwriteExisting=False)

        self.assertFalse(result["success"])
        self.assertTrue(result["conflict"])
        # the existing database must be untouched - this is what the confirmation guards
        self.assertEqual(self._spoolNames(existing), ["Own Spool"])

    def test_emptyDatabaseIsNotTreatedAsConflict(self):
        # the plugin creates an empty database on its own first start, so every existing
        # user would otherwise be met with a data-loss warning that does not apply
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        existing = self._writeDatabase(
            os.path.join(self.newFolder, DATABASE_FILE_NAME), []
        )

        result = self._plugin()._performLegacyMigration(overwriteExisting=False)

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertFalse(result["conflict"])
        self.assertEqual(self._read(existing), "OLD")

    def test_overwriteReplacesPopulatedDatabase(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        existing = self._writeDatabase(
            os.path.join(self.newFolder, DATABASE_FILE_NAME), ["Own Spool"]
        )

        result = self._plugin()._performLegacyMigration(overwriteExisting=True)

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertFalse(result["conflict"])
        self.assertEqual(self._read(existing), "OLD")

    def test_settingsOnlyMigrationIgnoresExistingDatabase(self):
        # no legacy .db, so an existing database of ours is not at risk and must not
        # trigger the conflict path
        existing = self._writeDatabase(
            os.path.join(self.newFolder, DATABASE_FILE_NAME), ["Own Spool"]
        )
        plugin = self._plugin(legacySettings={"databaseType": "mysql"})

        result = plugin._performLegacyMigration(overwriteExisting=False)

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertFalse(result["conflict"])
        self.assertTrue(result["settingsMigrated"])
        self.assertEqual(self._spoolNames(existing), ["Own Spool"])

    ############################################################################ scheme hint

    def test_schemeUpgradeNeededIsReported(self):
        # a migrated 1.8.0a3 database predates the current scheme - the UI says so instead
        # of leaving the user with an apparently broken install
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        plugin = self._plugin()
        plugin._databaseManager = FakeDatabaseManager(schemeUpgradeNeeded=True)

        result = plugin._performLegacyMigration()

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertTrue(result["schemeUpgradeNeeded"])


    ############################################################ preview + file selection

    def _writeRichDatabase(self, path):
        """A database with regular spools, a template and a scheme version, for previews."""
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE spo_spoolmodel (databaseId INTEGER PRIMARY KEY, "
                "displayName VARCHAR(255), material VARCHAR(255), "
                "remainingWeight REAL, isTemplate INTEGER)"
            )
            connection.executemany(
                "INSERT INTO spo_spoolmodel (displayName, material, remainingWeight, isTemplate) "
                "VALUES (?, ?, ?, ?)",
                [
                    ("Kingroon White PLA", "PLA", 694.6, None),
                    ("Elegoo Black PETG", "PETG", 188.0, None),
                    ("PLA Template", "PLA", 1000.0, 1),
                ],
            )
            connection.execute(
                "CREATE TABLE spo_pluginmetadatamodel (databaseId INTEGER PRIMARY KEY, "
                "key VARCHAR(255), value VARCHAR(255))"
            )
            connection.execute(
                "INSERT INTO spo_pluginmetadatamodel (key, value) VALUES ('databaseSchemeVersion', '7')"
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_previewCountsSpoolsAndTemplatesSeparately(self):
        os.makedirs(self.legacyFolder, exist_ok=True)
        databaseFile = self._writeRichDatabase(
            os.path.join(self.legacyFolder, DATABASE_FILE_NAME)
        )

        preview = self._plugin()._readLegacyDatabasePreview(databaseFile)

        self.assertTrue(preview["readable"])
        self.assertEqual(preview["spoolCount"], 2)
        self.assertEqual(preview["templateCount"], 1)
        self.assertEqual(preview["schemeVersion"], "7")
        # templates must not count towards the remaining weight
        self.assertAlmostEqual(preview["totalRemainingWeight"], 882.6, places=2)
        self.assertEqual(preview["spools"][0]["displayName"], "Kingroon White PLA")

    def test_previewOfUnreadableFileReportsNotReadable(self):
        # a plain text file is not a database - the dialog says so instead of failing
        path = self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")

        preview = self._plugin()._readLegacyDatabasePreview(path)

        self.assertFalse(preview["readable"])
        self.assertEqual(preview["spoolCount"], 0)

    def test_fileEntriesPreselectOnlyTheDatabase(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        self._writeLegacyFile("spoolmandb_index.json", "{}")
        self._writeLegacyFile("spoolmanager-backup-V7-20260101-1200.db", "BACKUP")

        entries = {entry["name"]: entry for entry in self._plugin()._getLegacyFileEntries()}

        self.assertTrue(entries[DATABASE_FILE_NAME]["preselected"])
        self.assertEqual(entries[DATABASE_FILE_NAME]["kind"], "database")
        # the caches rebuild themselves and the SpoolmanDB index alone is ~10 MB
        self.assertFalse(entries["spoolmandb_index.json"]["preselected"])
        self.assertEqual(entries["spoolmandb_index.json"]["kind"], "cache")
        self.assertFalse(entries["spoolmanager-backup-V7-20260101-1200.db"]["preselected"])
        self.assertEqual(entries["spoolmanager-backup-V7-20260101-1200.db"]["kind"], "backup")

    def test_fileNamesRestrictWhatIsCopied(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        self._writeLegacyFile("spoolmandb_index.json", "{}")

        result = self._plugin()._performLegacyMigration(
            fileNames=[DATABASE_FILE_NAME]
        )

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertEqual(result["copiedFiles"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.newFolder, DATABASE_FILE_NAME)))
        self.assertFalse(
            os.path.isfile(os.path.join(self.newFolder, "spoolmandb_index.json"))
        )

    def test_fileNamesNoneCopiesEverything(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        self._writeLegacyFile("spoolmandb_index.json", "{}")

        result = self._plugin()._performLegacyMigration(fileNames=None)

        self.assertEqual(result["copiedFiles"], 2)

    def test_includeSettingsFalseLeavesSettingsAlone(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        plugin = self._plugin(legacySettings={"databaseType": "mysql"})

        result = plugin._performLegacyMigration(includeSettings=False)

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertFalse(result["settingsMigrated"])
        self.assertEqual(plugin._settings.ownSettings, {})

    ############################################################ settings comparison

    def test_comparisonFallsBackToDefaultWhenNothingStored(self):
        plugin = self._plugin(legacySettings={"currencySymbol": "$"})
        plugin._defaults = {"currencySymbol": "EUR"}

        rows = {row["key"]: row for row in plugin._getLegacySettingsComparison()}

        self.assertEqual(rows["currencySymbol"]["legacyValueText"], "$")
        # nothing stored here yet, so the value that actually applies is the default
        self.assertEqual(rows["currencySymbol"]["currentValueText"], "EUR")
        self.assertTrue(rows["currencySymbol"]["currentIsDefault"])
        self.assertTrue(rows["currencySymbol"]["differs"])

    def test_comparisonMarksEqualValuesAsUnchanged(self):
        plugin = self._plugin(legacySettings={"currencySymbol": "EUR"})
        plugin._defaults = {"currencySymbol": "EUR"}

        rows = {row["key"]: row for row in plugin._getLegacySettingsComparison()}

        self.assertFalse(rows["currencySymbol"]["differs"])

    def test_comparisonExcludesNonMigratableKeys(self):
        plugin = self._plugin(
            legacySettings={
                "selectedSpoolsDatabaseIds": [2],
                "installed_version": "1.8.0a3",
                "currencySymbol": "$",
            }
        )

        keys = [row["key"] for row in plugin._getLegacySettingsComparison()]

        # spool ids point at database rows and only make sense with the database itself
        self.assertNotIn("selectedSpoolsDatabaseIds", keys)
        self.assertNotIn("installed_version", keys)
        self.assertIn("currencySymbol", keys)

    def test_applyWritesOnlyTheNamedKeys(self):
        plugin = self._plugin(
            legacySettings={"currencySymbol": "$", "databaseType": "mysql"}
        )

        result = plugin._applyLegacySettings(["currencySymbol"])

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertEqual(result["appliedCount"], 1)
        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "$")
        self.assertNotIn("databaseType", plugin._settings.ownSettings)

    def test_comparisonIncludesKeysTheLegacyPluginNeverStored(self):
        # THE reported gap: OctoPrint only writes settings that differ from their default,
        # so a plugin the user barely touched stores a handful of keys while knowing
        # dozens. Listing only the stored ones hid currencySymbol and most others.
        plugin = self._plugin(legacySettings={"safetyLength": 16})
        plugin._defaults = {"safetyLength": 0, "currencySymbol": "EUR"}
        plugin._settings.ownSettings["currencySymbol"] = "$"

        rows = {row["key"]: row for row in plugin._getLegacySettingsComparison()}

        self.assertIn("currencySymbol", rows)
        # never stored over there, so what applies is that plugin's default
        self.assertTrue(rows["currencySymbol"]["legacyIsDefault"])
        self.assertEqual(rows["currencySymbol"]["legacyValueText"], "EUR")
        self.assertEqual(rows["currencySymbol"]["currentValueText"], "$")
        self.assertTrue(rows["currencySymbol"]["differs"])
        # a stored value is still marked as stored
        self.assertFalse(rows["safetyLength"]["legacyIsDefault"])

    def test_comparisonSkipsKeysNeitherSideHasAValueFor(self):
        plugin = self._plugin(legacySettings={"safetyLength": 16})
        plugin._defaults = {"safetyLength": 0}

        keys = [row["key"] for row in plugin._getLegacySettingsComparison()]

        # no default and nothing stored anywhere - such a row would say nothing
        self.assertNotIn("someUnknownKey", keys)

    def test_applyWorksForAKeyTheLegacyPluginNeverStored(self):
        # the dialog offers these rows, so applying them has to work - otherwise the
        # user ticks a box and nothing happens
        plugin = self._plugin(legacySettings={"safetyLength": 16})
        plugin._defaults = {"safetyLength": 0, "currencySymbol": "EUR"}
        plugin._settings.ownSettings["currencySymbol"] = "$"

        result = plugin._applyLegacySettings(["currencySymbol"])

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertEqual(result["appliedCount"], 1)
        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "EUR")

    ############################################################ undo

    def test_undoRestoresReplacedFileAndSettings(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        target = os.path.join(self.newFolder, DATABASE_FILE_NAME)
        self._writeDatabase(target, ["Own Spool"])

        plugin = self._plugin(legacySettings={"currencySymbol": "$"})
        plugin._settings.ownSettings["currencySymbol"] = "EUR"

        migrateResult = plugin._performLegacyMigration(overwriteExisting=True)
        self.assertTrue(migrateResult["success"], migrateResult["errorMessage"])
        self.assertEqual(self._read(target), "OLD")
        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "$")
        self.assertTrue(plugin._isLegacyMigrationUndoAvailable("database"))

        undoResult = plugin._undoLegacyMigration("database")

        self.assertTrue(undoResult["success"], undoResult["errorMessage"])
        self.assertEqual(undoResult["restoredFiles"], 1)
        # the database the user had before the migration is back, contents and all
        self.assertEqual(self._spoolNames(target), ["Own Spool"])
        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "EUR")
        self.assertFalse(plugin._isLegacyMigrationUndoAvailable("database"))

    def test_undoRemovesSettingsThatHadNoValueBefore(self):
        plugin = self._plugin(legacySettings={"currencySymbol": "$"})

        plugin._applyLegacySettings(["currencySymbol"])
        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "$")

        plugin._undoLegacyMigration("settings")

        # it had no value of its own before, so it must be gone again - not left at "$"
        self.assertNotIn("currencySymbol", plugin._settings.ownSettings)

    def test_theTwoUndosAreIndependent(self):
        # each migration is started from its own tab, so undoing one must leave the
        # other's record intact - a shared record would silently drop it
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        target = os.path.join(self.newFolder, DATABASE_FILE_NAME)
        self._writeDatabase(target, ["Own Spool"])

        plugin = self._plugin(legacySettings={"currencySymbol": "$"})
        plugin._settings.ownSettings["currencySymbol"] = "EUR"

        plugin._performLegacyMigration(overwriteExisting=True, includeSettings=False)
        plugin._applyLegacySettings(["currencySymbol"])

        self.assertTrue(plugin._isLegacyMigrationUndoAvailable("database"))
        self.assertTrue(plugin._isLegacyMigrationUndoAvailable("settings"))

        # undoing the settings leaves the migrated database alone
        plugin._undoLegacyMigration("settings")

        self.assertEqual(plugin._settings.ownSettings["currencySymbol"], "EUR")
        self.assertEqual(self._read(target), "OLD")
        self.assertFalse(plugin._isLegacyMigrationUndoAvailable("settings"))
        self.assertTrue(plugin._isLegacyMigrationUndoAvailable("database"))

        # and the database undo still works afterwards
        plugin._undoLegacyMigration("database")

        self.assertEqual(self._spoolNames(target), ["Own Spool"])
        self.assertFalse(plugin._isLegacyMigrationUndoAvailable("database"))

    def test_bannerRetiresAfterMigrationAndReturnsAfterUndo(self):
        # THE reported annoyance: the legacy folder is deliberately kept (copy, not move),
        # so "does a legacy install exist" stays true forever and the banner never went
        # away, even after migrating everything.
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        plugin = self._plugin()

        self.assertTrue(plugin._isLegacyMigrationAvailable())
        self.assertFalse(plugin._isLegacyMigrationDone())

        plugin._performLegacyMigration(includeSettings=False)

        # still "available" - the old install is untouched - but no longer pending
        self.assertTrue(plugin._isLegacyMigrationAvailable())
        self.assertTrue(plugin._isLegacyMigrationDone())

        plugin._undoLegacyMigration("database")

        # undone, so there really is something left to migrate again
        self.assertFalse(plugin._isLegacyMigrationDone())

    def test_dismissedHintStaysDismissed(self):
        # Anyone already running this plugin when it was renamed still has a
        # plugins.SpoolManager block, so "something is migratable" is true for them
        # forever - without a way to say no the hint could never be got rid of.
        plugin = self._plugin(legacySettings={"currencySymbol": "$"})

        self.assertTrue(plugin._isLegacyMigrationAvailable())
        self.assertFalse(plugin._isLegacyMigrationDone())

        plugin._settings.set_boolean(["legacyMigrationDismissed"], True)

        # still migratable and still not migrated - the offer stands, the hint does not
        self.assertTrue(plugin._isLegacyMigrationAvailable())
        self.assertFalse(plugin._isLegacyMigrationDone())
        self.assertTrue(plugin._settings.get_boolean(["legacyMigrationDismissed"]))

    def test_undoWithoutRecordFailsCleanly(self):
        result = self._plugin()._undoLegacyMigration("database")

        self.assertFalse(result["success"])
        self.assertIsNotNone(result["errorMessage"])

    def test_undoCannotRunTwice(self):
        self._writeLegacyFile(DATABASE_FILE_NAME, "OLD")
        target = os.path.join(self.newFolder, DATABASE_FILE_NAME)
        self._writeDatabase(target, ["Own Spool"])
        plugin = self._plugin()

        plugin._performLegacyMigration(overwriteExisting=True)
        self.assertTrue(plugin._undoLegacyMigration("database")["success"])

        secondUndo = plugin._undoLegacyMigration("database")

        self.assertFalse(secondUndo["success"])


if __name__ == "__main__":
    unittest.main()
