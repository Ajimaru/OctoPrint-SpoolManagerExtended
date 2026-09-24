# coding=utf-8

# Tests for DatabaseManager's connection handling across threads.
#
# Observed on a live instance: saving a spool in the edit dialog answered "modified elsewhere"
# (HTTP 409) although nothing had changed. A request running in parallel - the dialog's
# OctoScale NFC poll - opened and closed a connection of its own in the middle of the save.
# "Connected" was a single flag for all threads, and every connect built and bound a new
# database object, so the saving thread was told "Database not connected" and saveSpool()
# returned None, which the API reported as a version conflict.
#
# The other thread is always run to completion with join() between two steps of the test
# thread. That makes the interleaving deterministic: what matters is that another thread's
# complete connect/close lands in the middle of this thread's work, not how a scheduler
# happens to line the two up.
#
# A real SQLite file on purpose: ":memory:" is private to a single connection, and the point
# here is exactly that every thread has a connection of its own.
#
# Run with:  python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_DatabaseConnectionThreads.py

import copy
import logging
import os
import shutil
import tempfile
import threading
import unittest

import peewee

from octoprint_SpoolManagerExtended.DatabaseManager import (
    SAVE_OUTCOME_DATABASE_ERROR,
    SAVE_OUTCOME_DELETED,
    SAVE_OUTCOME_SAVED,
    SAVE_OUTCOME_VERSION_CONFLICT,
    DatabaseManager,
)
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel


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


class _DatabaseManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.baseFolder = tempfile.mkdtemp()
        self.databaseSettings = DatabaseManager.DatabaseSettings()
        self.databaseSettings.useExternal = False
        self.databaseSettings.baseFolder = self.baseFolder
        self.clientMessages = []
        self.databaseManager = DatabaseManager(
            logging.getLogger("test.connectionThreads"), False
        )
        self.databaseManager.initDatabase(self.databaseSettings, self._clientMessage)

    def tearDown(self):
        try:
            self.databaseManager.closeDatabase()
        finally:
            shutil.rmtree(self.baseFolder, ignore_errors=True)

    def _clientMessage(self, *args):
        self.clientMessages.append(args)

    def _createSpool(self, displayName="Test spool"):
        spoolModel = SpoolModel()
        spoolModel.displayName = displayName
        spoolModel.material = "PLA"
        databaseId = self.databaseManager.saveSpool(spoolModel)
        self.assertIsNotNone(databaseId)
        return databaseId


class TestConnectionStatePerThread(_DatabaseManagerTestCase):
    def test_otherThreadsCloseDoesNotDisconnectThisThread(self):
        # The observed failure: this thread holds its connection for a save while another
        # thread runs a complete non-reused call - connect, query, close - in between.
        manager = self.databaseManager
        databaseId = self._createSpool()

        manager.connectoToDatabase()
        spoolModel = manager.loadSpool(databaseId, withReusedConnection=True)
        self.assertIsNotNone(spoolModel)

        _runInOtherThread(lambda: manager.loadSpool(databaseId))

        self.assertTrue(manager.isConnected())
        spoolModel.displayName = "Renamed"
        savedDatabaseId = manager.saveSpool(
            spoolModel, withReusedConnection=True, suppressConflictMessage=True
        )
        manager.closeDatabase()

        self.assertEqual(savedDatabaseId, databaseId)
        self.assertEqual(manager.getLastSaveOutcome(), SAVE_OUTCOME_SAVED)
        self.assertEqual(manager.loadSpool(databaseId).displayName, "Renamed")

    def test_otherThreadsCallDoesNotRebindTheModels(self):
        # Rebinding on every connect pointed this thread's queries at an object it had never
        # connected, in the middle of its own work.
        manager = self.databaseManager
        databaseId = self._createSpool()

        manager.connectoToDatabase()
        boundBefore = SpoolModel._meta.database
        _runInOtherThread(lambda: manager.loadSpool(databaseId))

        self.assertIs(SpoolModel._meta.database, boundBefore)
        manager.closeDatabase()

    def test_nestedConnectInTheSameThreadKeepsTheConnection(self):
        manager = self.databaseManager
        manager.connectoToDatabase()
        connection = SpoolModel._meta.database.connection()

        self.assertTrue(manager.connectoToDatabase())
        self.assertIs(SpoolModel._meta.database.connection(), connection)
        manager.closeDatabase()

    def test_leftoverConnectionIsReplacedOnConnect(self):
        # A query after closeDatabase() reopens a connection through peewee's autoconnect,
        # and nobody ever closes that one. The next connect must not hand it out again.
        manager = self.databaseManager
        self._createSpool()
        manager.connectoToDatabase()
        manager.closeDatabase()

        SpoolModel.select().count()
        database = SpoolModel._meta.database
        self.assertFalse(database.is_closed())
        leftover = database.connection()

        manager.connectoToDatabase()
        self.assertIsNot(database.connection(), leftover)
        manager.closeDatabase()
        self.assertTrue(database.is_closed())

    def test_changedSettingsBindANewDatabase(self):
        manager = self.databaseManager
        manager.connectoToDatabase()
        firstDatabase = SpoolModel._meta.database
        manager.closeDatabase()

        otherFolder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, otherFolder, True)
        otherSettings = copy.copy(self.databaseSettings)
        otherSettings.fileLocation = os.path.join(otherFolder, "other.db")
        manager.assignNewDatabaseSettings(otherSettings)

        manager.connectoToDatabase()
        self.assertIsNot(SpoolModel._meta.database, firstDatabase)
        self.assertEqual(SpoolModel._meta.database.database, otherSettings.fileLocation)
        manager.closeDatabase()

    def test_settingsChangedInPlaceAreNoticed(self):
        # some callers switch the database by changing the live settings object's fields
        manager = self.databaseManager
        manager.connectoToDatabase()
        firstDatabase = SpoolModel._meta.database
        manager.closeDatabase()

        otherFolder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, otherFolder, True)
        self.databaseSettings.fileLocation = os.path.join(otherFolder, "other.db")

        manager.connectoToDatabase()
        self.assertIsNot(SpoolModel._meta.database, firstDatabase)
        manager.closeDatabase()


class TestMetaInformations(_DatabaseManagerTestCase):
    def test_metaInformationsLeaveLiveSettingsAndBindingAlone(self):
        # With no argument (the Storage tab) this used to rewrite the live settings to
        # "local sqlite" for the local half and connect through the shared binding, so any
        # request in that window - writes included - went to the local file.
        manager = self.databaseManager
        self._createSpool()
        manager.connectoToDatabase()
        boundBefore = SpoolModel._meta.database
        manager.closeDatabase()

        liveSettings = manager._databaseSettings
        # an external database nobody listens for: the local half still has to be read
        liveSettings.useExternal = True
        liveSettings.type = "mysql"
        liveSettings.host = "127.0.0.1"
        liveSettings.port = 1
        liveSettings.name = "spoolmanager"
        liveSettings.user = "spoolmanager"
        liveSettings.password = ""
        settingsBefore = dict(vars(liveSettings))

        seenSettings = []
        originalConnect = manager.connectoToDatabase

        def recordingConnect(*args, **kwargs):
            seenSettings.append(
                (manager._databaseSettings.useExternal, manager._databaseSettings.type)
            )
            return originalConnect(*args, **kwargs)

        manager.connectoToDatabase = recordingConnect
        result = manager.loadDatabaseMetaInformations()

        self.assertEqual(seenSettings, [])
        self.assertEqual(dict(vars(liveSettings)), settingsBefore)
        self.assertIs(SpoolModel._meta.database, boundBefore)
        self.assertEqual(result["localSpoolItemCount"], 1)
        self.assertEqual(result["externalSpoolItemCount"], "-")
        self.assertFalse(result["success"])
        self.assertNotEqual(result["errorMessage"], "")

    def test_metaInformationsForTheActiveLocalDatabase(self):
        self._createSpool()
        self._createSpool("Second spool")

        result = self.databaseManager.loadDatabaseMetaInformations()

        self.assertTrue(result["success"])
        self.assertEqual(result["localSpoolItemCount"], 2)
        self.assertEqual(
            str(result["localSchemeVersionFromDatabaseModel"]),
            str(result["schemeVersionFromPlugin"]),
        )


class TestSaveOutcome(_DatabaseManagerTestCase):
    def test_versionConflict(self):
        manager = self.databaseManager
        databaseId = self._createSpool()
        stale = manager.loadSpool(databaseId)
        fresh = manager.loadSpool(databaseId)
        fresh.displayName = "first"
        self.assertEqual(manager.saveSpool(fresh), databaseId)

        stale.displayName = "second"
        self.assertIsNone(manager.saveSpool(stale, suppressConflictMessage=True))
        self.assertEqual(manager.getLastSaveOutcome(), SAVE_OUTCOME_VERSION_CONFLICT)

    def test_deletedSpool(self):
        manager = self.databaseManager
        databaseId = self._createSpool()
        spoolModel = manager.loadSpool(databaseId)
        manager.deleteSpool(databaseId)

        self.assertIsNone(manager.saveSpool(spoolModel, suppressConflictMessage=True))
        self.assertEqual(manager.getLastSaveOutcome(), SAVE_OUTCOME_DELETED)

    def test_noConnectionIsADatabaseError(self):
        manager = self.databaseManager
        databaseId = self._createSpool()
        spoolModel = manager.loadSpool(databaseId)
        manager.closeDatabase()

        self.assertIsNone(manager.saveSpool(spoolModel, withReusedConnection=True))
        self.assertEqual(manager.getLastSaveOutcome(), SAVE_OUTCOME_DATABASE_ERROR)

    def test_failedWriteIsNotReportedAsSaved(self):
        # Used to answer with the spool's id: the caller took a failed write for a stored
        # one and adopted the version bumped for it, so its next save hit a false conflict.
        manager = self.databaseManager
        databaseId = self._createSpool()
        spoolModel = manager.loadSpool(databaseId)
        versionInDatabase = spoolModel.version

        def failingSave(*args, **kwargs):
            raise RuntimeError("simulated write failure")

        spoolModel.save = failingSave

        self.assertIsNone(manager.saveSpool(spoolModel))
        self.assertEqual(manager.getLastSaveOutcome(), SAVE_OUTCOME_DATABASE_ERROR)
        self.assertEqual(manager.loadSpool(databaseId).version, versionInDatabase)


class TestErrorMessagePerThread(_DatabaseManagerTestCase):
    # A failed connect stores its error, and the caller reads it back afterwards (the
    # connection-problem dialog on client open, dump export/import, scheme upgrade). Every
    # connect and close resets the stored error, so with one value for all threads another
    # request cleared or replaced it in between.

    ACCESS_DENIED = "Access denied. Check the user name and password."

    def _failConnects(self, exceptionForThisThread, exceptionForOtherThreads=None):
        # Only the connect fails, the database file is fine - so a thread that is not told
        # to fail really connects.
        database = self.databaseManager._getOrBuildBoundDatabase()
        realConnect = database.connect
        thisThread = threading.current_thread()

        def connect(*args, **kwargs):
            if threading.current_thread() is thisThread:
                raise exceptionForThisThread
            if exceptionForOtherThreads is not None:
                raise exceptionForOtherThreads
            return realConnect(*args, **kwargs)

        database.connect = connect
        self.addCleanup(delattr, database, "connect")

    def test_otherThreadsConnectDoesNotClearThisThreadsError(self):
        manager = self.databaseManager
        databaseId = self._createSpool()
        self._failConnects(peewee.OperationalError("Access denied for user"))

        self.assertFalse(manager.connectoToDatabase(sendErrorPopUp=False))
        self.assertIsNotNone(_runInOtherThread(lambda: manager.loadSpool(databaseId)))

        errorMessageDict = manager.getCurrentErrorMessageDict()
        self.assertIsNotNone(errorMessageDict)
        self.assertEqual(errorMessageDict["message"], self.ACCESS_DENIED)

    def test_otherThreadsErrorDoesNotReplaceThisThreadsError(self):
        manager = self.databaseManager
        self._failConnects(
            peewee.OperationalError("Access denied for user"),
            peewee.OperationalError("Connection refused"),
        )

        self.assertFalse(manager.connectoToDatabase(sendErrorPopUp=False))
        self.assertFalse(
            _runInOtherThread(lambda: manager.connectoToDatabase(sendErrorPopUp=False))
        )

        self.assertEqual(
            manager.getCurrentErrorMessageDict()["message"], self.ACCESS_DENIED
        )


if __name__ == "__main__":
    unittest.main()
