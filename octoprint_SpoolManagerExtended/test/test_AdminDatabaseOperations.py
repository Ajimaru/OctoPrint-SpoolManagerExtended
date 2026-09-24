# coding=utf-8

# Tests for the Storage tab's admin actions on a database other than the active one: CSV
# export and import, delete, copy, and the .db restore.
#
# These used to switch the active database settings for the duration of the action. The
# settings apply to the whole process, so every other request of the instance - the sidebar,
# the spool dialog, the usage a finishing print books - went to the other database in the
# meantime. The probe below stands in for such a request: another thread loads spool 1
# while the action is under way, and has to get the active database's spool 1, not the
# other database's.
#
# "external" is a second SQLite file here - MySQL is not available to the tests. Every
# connection the manager builds for useExternal=True goes to that file. Both files hold a
# spool 1, with names that tell them apart.
#
# Run with:  python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_AdminDatabaseOperations.py

import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

import flask
from peewee import SqliteDatabase

from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.common import CSVExportImporter
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import (
    CURRENT_DATABASE_SCHEME_VERSION,
    MODELS,
    DatabaseManager,
)
from octoprint_SpoolManagerExtended.models.PluginMetaDataModel import (
    PluginMetaDataModel,
)
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

EXTERNAL_SETTINGS_JSON = {
    SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL: True,
    SettingsKeys.SETTINGS_KEY_DATABASE_TYPE: "mysql",
    SettingsKeys.SETTINGS_KEY_DATABASE_HOST: "external.invalid",
    SettingsKeys.SETTINGS_KEY_DATABASE_PORT: 3306,
    SettingsKeys.SETTINGS_KEY_DATABASE_NAME: "spoolmanager",
    SettingsKeys.SETTINGS_KEY_DATABASE_USER: "spoolmanager",
    SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD: "",
}

_app = flask.Flask(__name__)


def _runInOtherThread(function):
    # runs function() in a thread of its own and waits for it; its exception is re-raised
    result = {}

    def target():
        try:
            result["value"] = function()
        except Exception as exception:
            result["error"] = exception

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _createDatabaseFile(path, displayNames):
    database = SqliteDatabase(path)
    with database.bind_ctx(MODELS):
        database.create_tables(MODELS)
        PluginMetaDataModel.create(
            key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION,
            value=CURRENT_DATABASE_SCHEME_VERSION,
        )
        for displayName in displayNames:
            SpoolModel.create(displayName=displayName, material="PLA")
    database.close()


def _query(path, sql):
    # straight through sqlite3, independent of any model binding
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _displayNames(path):
    return sorted(
        row[0] for row in _query(path, "SELECT displayName FROM spo_spoolmodel")
    )


def _integrityCheck(path):
    return _query(path, "PRAGMA integrity_check")[0][0]


class _FakeSettings(object):
    def __init__(self):
        self.values = {SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS: [1]}

    def get(self, keys):
        return self.values.get(keys[0])

    def set(self, keys, value):
        self.values[keys[0]] = value

    def save(self):
        pass


class _FakePlugin(object):
    """Runs the real endpoints and helpers."""

    exportSpoolsData = SpoolManagerAPI.exportSpoolsData.__wrapped__
    deleteDatabase = SpoolManagerAPI.deleteDatabase.__wrapped__
    copyDatabase = SpoolManagerAPI.copyDatabase.__wrapped__
    downloadDatabase = SpoolManagerAPI.downloadDatabase.__wrapped__
    _processCSVUploadAsync = SpoolManagerAPI._processCSVUploadAsync
    _buildDatabaseSettingsFromJson = SpoolManagerAPI._buildDatabaseSettingsFromJson
    _getValueFromJSONOrNone = SpoolManagerAPI._getValueFromJSONOrNone
    _resetSelectedSpools = SpoolManagerAPI._resetSelectedSpools

    def __init__(self, databaseManager):
        self._databaseManager = databaseManager
        self._settings = _FakeSettings()
        self._logger = logging.getLogger("test.adminDatabase.plugin")

    def selectedSpoolIds(self):
        return self._settings.values[
            SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS
        ]


class _TwoDatabasesTestCase(unittest.TestCase):
    externalIsActive = False

    def setUp(self):
        self.baseFolder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.baseFolder, True)
        self.localFile = DatabaseManager.buildDefaultDatabaseFileLocation(
            self.baseFolder
        )
        self.externalFile = os.path.join(self.baseFolder, "external.db")
        _createDatabaseFile(self.localFile, ["local-1"])
        _createDatabaseFile(self.externalFile, ["external-1"])

        settings = DatabaseManager.DatabaseSettings()
        settings.baseFolder = self.baseFolder
        settings.useExternal = self.externalIsActive
        settings.type = EXTERNAL_SETTINGS_JSON[SettingsKeys.SETTINGS_KEY_DATABASE_TYPE]
        settings.host = EXTERNAL_SETTINGS_JSON[SettingsKeys.SETTINGS_KEY_DATABASE_HOST]
        settings.port = EXTERNAL_SETTINGS_JSON[SettingsKeys.SETTINGS_KEY_DATABASE_PORT]
        settings.name = EXTERNAL_SETTINGS_JSON[SettingsKeys.SETTINGS_KEY_DATABASE_NAME]
        settings.user = EXTERNAL_SETTINGS_JSON[SettingsKeys.SETTINGS_KEY_DATABASE_USER]
        settings.password = ""

        self.databaseManager = DatabaseManager(
            logging.getLogger("test.adminDatabase"), False
        )
        self._routeExternalToFile()
        self.databaseManager.initDatabase(settings, lambda *args: None)
        self.addCleanup(self.databaseManager.closeDatabase)
        self.plugin = _FakePlugin(self.databaseManager)
        self.probes = []

    def _routeExternalToFile(self):
        manager = self.databaseManager
        buildLocal = manager._buildDatabaseConnection
        externalFile = self.externalFile

        def build(databaseSettings=None):
            if databaseSettings is None:
                databaseSettings = manager._databaseSettings
            if databaseSettings.useExternal:
                return SqliteDatabase(externalFile)
            return buildLocal(databaseSettings)

        manager._buildDatabaseConnection = build

    def _probeFromOtherThread(self):
        # what any other request of the instance gets right now for spool 1
        spool = _runInOtherThread(lambda: self.databaseManager.loadSpool(1))
        self.probes.append(None if spool is None else spool.displayName)

    def _probeBefore(self, methodName):
        # a method old and new code both call while the action is under way
        original = getattr(self.databaseManager, methodName)

        def probing(*args, **kwargs):
            self._probeFromOtherThread()
            return original(*args, **kwargs)

        setattr(self.databaseManager, methodName, probing)

    def _writeCsv(self, displayNames):
        spools = []
        for displayName in displayNames:
            spool = SpoolModel()
            spool.displayName = displayName
            spool.material = "PETG"
            spools.append(spool)
        path = os.path.join(self.baseFolder, "import.csv")
        with open(path, "w", encoding="utf-8") as csvFile:
            for line in CSVExportImporter.transform2CSV(spools):
                csvFile.write(line)
        return path

    def _importCsv(self, displayNames, importMode, importUseExternal, onStatus=None):
        statuses = []

        def sendStatus(importStatus, lineNumber, backupFilePath, message, errors):
            statuses.append((importStatus, message, list(errors)))
            if onStatus is not None:
                onStatus(importStatus)

        self.plugin._processCSVUploadAsync(
            self._writeCsv(displayNames),
            importMode,
            importUseExternal,
            self.databaseManager,
            sendStatus,
            logging.getLogger("test.adminDatabase.csv"),
        )
        return statuses


class TestExport(_TwoDatabasesTestCase):
    def test_exportReadsTheOtherDatabaseWithoutSwitching(self):
        self._probeBefore("loadAllSpoolsByQuery")

        with _app.test_request_context("/?instance=external"):
            response = self.plugin.exportSpoolsData("CSV")
            csvText = response.get_data(as_text=True)

        self.assertEqual(self.probes, ["local-1"])
        self.assertIn("external-1", csvText)
        self.assertNotIn("local-1", csvText)


class TestFailedExport(_TwoDatabasesTestCase):
    # The live case: exporting the internal database while an external one is active, the
    # internal file on an older scheme. The read failed, the switch back was skipped, and
    # the instance stayed on the internal database until the next restart.
    externalIsActive = True

    def test_failedExportLeavesTheActiveDatabaseAlone(self):
        _query(
            self.localFile, "ALTER TABLE spo_spoolmodel DROP COLUMN dryingTemperature"
        )

        with _app.test_request_context("/?instance=internal"):
            try:
                response = self.plugin.exportSpoolsData("CSV")
            except Exception:
                response = None  # escaped the endpoint

        self.assertTrue(self.databaseManager.getDatabaseSettings().useExternal)
        self.assertEqual(self.databaseManager.loadSpool(1).displayName, "external-1")
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 500)


class TestCsvImport(_TwoDatabasesTestCase):
    def test_importIntoTheOtherDatabaseWithoutSwitching(self):
        def probeOnce(importStatus):
            if importStatus == "running" and not self.probes:
                self._probeFromOtherThread()

        statuses = self._importCsv(
            ["imported-1", "imported-2"],
            SettingsKeys.KEY_IMPORTCSV_MODE_APPEND,
            True,
            probeOnce,
        )

        self.assertEqual(self.probes, ["local-1"])
        self.assertEqual(statuses[-1][0], "finished")
        self.assertEqual(statuses[-1][2], [])
        self.assertEqual(
            _displayNames(self.externalFile),
            ["external-1", "imported-1", "imported-2"],
        )
        self.assertEqual(_displayNames(self.localFile), ["local-1"])

    def test_replacingTheOtherDatabaseKeepsTheSelection(self):
        # the selection holds ids of the active database, which the import does not touch
        self._importCsv(["imported-1"], SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE, True)

        self.assertEqual(self.plugin.selectedSpoolIds(), [1])
        self.assertEqual(_displayNames(self.externalFile), ["imported-1"])
        self.assertEqual(_displayNames(self.localFile), ["local-1"])

    def test_replacingTheActiveDatabaseResetsTheSelection(self):
        statuses = self._importCsv(
            ["imported-1"], SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE, False
        )

        self.assertEqual(statuses[-1][2], [])
        self.assertEqual(self.plugin.selectedSpoolIds(), [])
        self.assertEqual(_displayNames(self.localFile), ["imported-1"])
        self.assertEqual(_displayNames(self.externalFile), ["external-1"])

    def test_failedImportStoresNothingAndSaysSo(self):
        def failingInsert(*args, **kwargs):
            raise RuntimeError("simulated write failure")

        self.databaseManager.insertSpools = failingInsert

        statuses = self._importCsv(
            ["imported-1"], SettingsKeys.KEY_IMPORTCSV_MODE_APPEND, True
        )

        self.assertEqual(statuses[-1][0], "finished")
        self.assertNotEqual(statuses[-1][2], [])
        self.assertEqual(_displayNames(self.externalFile), ["external-1"])

    def test_insertSpoolsIsAllOrNothing(self):
        spools = []
        for displayName in ("imported-1", "imported-2"):
            spool = SpoolModel()
            spool.displayName = displayName
            spool.material = "PETG"
            spools.append(spool)

        def failOnSecond(count):
            if count == 2:
                raise RuntimeError("simulated failure after the second insert")

        externalSettings = self.databaseManager.getDatabaseSettings()
        externalSettings.useExternal = True
        with self.assertRaises(RuntimeError):
            self.databaseManager.insertSpools(spools, externalSettings, failOnSecond)

        self.assertEqual(_displayNames(self.externalFile), ["external-1"])


class TestDelete(_TwoDatabasesTestCase):
    def test_deleteExternalWithoutSwitching(self):
        self._probeBefore("_createDatabaseTables")

        with _app.test_request_context("/", method="POST", json=EXTERNAL_SETTINGS_JSON):
            self.plugin.deleteDatabase("external")

        self.assertEqual(self.probes, ["local-1"])
        self.assertEqual(_displayNames(self.externalFile), [])
        self.assertEqual(
            _query(self.externalFile, "SELECT value FROM spo_pluginmetadatamodel"),
            [(str(CURRENT_DATABASE_SCHEME_VERSION),)],
        )
        self.assertEqual(_displayNames(self.localFile), ["local-1"])


class TestDeleteInternalWithExternalActive(_TwoDatabasesTestCase):
    # The "internal" delete button follows the unsaved radio choice in the Storage tab, so it
    # can be pressed while an external database is active.
    externalIsActive = True

    def test_deleteInternalEmptiesTheLocalFileNotTheActiveDatabase(self):
        localJson = dict(EXTERNAL_SETTINGS_JSON)
        localJson[SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL] = False

        with _app.test_request_context("/", method="POST", json=localJson):
            self.plugin.deleteDatabase("internal")

        self.assertEqual(_displayNames(self.externalFile), ["external-1"])
        self.assertEqual(_displayNames(self.localFile), [])


class TestCopy(_TwoDatabasesTestCase):
    def test_copyWithoutSwitching(self):
        self._probeBefore("_createDatabaseTables")

        with _app.test_request_context("/", method="POST", json=EXTERNAL_SETTINGS_JSON):
            result = self.plugin.copyDatabase().get_json()

        self.assertEqual(self.probes, ["local-1"])
        self.assertEqual(result["metadata"], {"success": True, "copySpoolCount": 1})
        self.assertEqual(_displayNames(self.externalFile), ["local-1"])
        self.assertEqual(_displayNames(self.localFile), ["local-1"])


class TestRestore(_TwoDatabasesTestCase):
    def test_replaceRestoreWaitsForAnotherConnectionsTransaction(self):
        # A plain file copy went underneath the other connection: its commit afterwards
        # wrote its pages into the restored file.
        manager = self.databaseManager
        uploadedFile = os.path.join(self.baseFolder, "uploaded.db")
        _createDatabaseFile(uploadedFile, ["uploaded-1", "uploaded-2", "uploaded-3"])
        writing = threading.Event()

        def writeSlowly():
            manager.connectoToDatabase()
            try:
                with SpoolModel._meta.database.atomic():
                    SpoolModel.create(displayName="writer-1", material="PLA")
                    writing.set()
                    time.sleep(1.0)
            finally:
                manager.closeDatabase()

        writer = threading.Thread(target=writeSlowly)
        writer.start()
        self.assertTrue(writing.wait(5))

        result = manager.restoreFromSQLiteFile(
            uploadedFile, SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE
        )
        writer.join()

        self.assertTrue(result["success"], result["errorMessage"])
        self.assertEqual(_integrityCheck(self.localFile), "ok")
        self.assertEqual(
            _displayNames(self.localFile), ["uploaded-1", "uploaded-2", "uploaded-3"]
        )


class TestCopiesOfTheLocalFile(_TwoDatabasesTestCase):
    def test_backupIsACompleteCopy(self):
        backupPath = self.databaseManager.backupDatabaseFile()

        self.assertIn(
            "-backup-V" + str(CURRENT_DATABASE_SCHEME_VERSION) + "-",
            os.path.basename(backupPath),
        )
        self.assertEqual(_integrityCheck(backupPath), "ok")
        self.assertEqual(_displayNames(backupPath), ["local-1"])

    def test_downloadIsACompleteCopy(self):
        with _app.test_request_context("/"):
            response = self.plugin.downloadDatabase()
            response.direct_passthrough = False
            content = response.get_data()

        snapshotPath = os.path.join(self.baseFolder, "download.db")
        with open(snapshotPath, "wb") as snapshotFile:
            snapshotFile.write(content)
        self.assertEqual(_integrityCheck(snapshotPath), "ok")
        self.assertEqual(_displayNames(snapshotPath), ["local-1"])


if __name__ == "__main__":
    unittest.main()
