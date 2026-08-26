# coding=utf-8

# This file has two halves:
#
# 1. TestLoadSpool - real, self-contained tests for DatabaseManager.loadSpool(), run by
#    the suite. They use an in-memory SQLite database, the same way test_loadSpoolByCode.py
#    does, and need nothing from the environment.
#
# 2. TestDatabase - manual developer scripts against real postgres/mysql/sqlite instances,
#    kept from the original project. Every method there is deliberately prefixed with
#    `_test_` so pytest does NOT collect it: setUp() connects to a hardcoded absolute path
#    from another machine and the settings carry plaintext credentials. Do not "fix" those
#    underscores - dropping one is what made test_loadSingleSpool fail in every checkout
#    since upstream commit 998822b.

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended import DatabaseManager
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel


class _RecordingHandler(logging.Handler):
    """
    Captures ERROR records so a test can tell "loadSpool() returned None because the
    spool does not exist" from "loadSpool() returned None because it swallowed an
    exception" - _handleReusableConnection() returns defaultReturnValue for both.
    """

    def __init__(self):
        logging.Handler.__init__(self)
        self.records = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.records.append(record)


class TestLoadSpool(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.logger = logging.getLogger("test.loadSpool")
        self.logHandler = _RecordingHandler()
        self.logger.addHandler(self.logHandler)

        self.databaseManager = DatabaseManager(self.logger, False)
        # Bypass connectoToDatabase() (postgres/mysql/sqlite-file branching, unrelated to
        # what's under test) and hand the manager an already-open connection, the way
        # _handleReusableConnection()'s withReusedConnection=True path expects.
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True

    def tearDown(self):
        self.logger.removeHandler(self.logHandler)
        self.database.drop_tables(MODELS)
        self.database.close()

    def _create(self, **fields):
        defaults = {"displayName": "Test Spool", "isActive": True}
        defaults.update(fields)
        return SpoolModel.create(**defaults)

    def _assertNothingLogged(self):
        self.assertEqual(
            [record.getMessage() for record in self.logHandler.records],
            [],
            "loadSpool() logged an error - the None it returned is a swallowed "
            "exception, not a clean 'not found'",
        )

    def test_existingSpoolIsLoadedById(self):
        spool = self._create(displayName="Kingroon White PLA")

        result = self.databaseManager.loadSpool(
            spool.databaseId, withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.databaseId, spool.databaseId)
        self.assertEqual(result.displayName, "Kingroon White PLA")

    def test_unknownDatabaseIdReturnsNoneWithoutError(self):
        # loadSpool() catches DoesNotExist and returns None. Guarded explicitly because
        # _handleReusableConnection() also returns None on an unexpected exception, so
        # the return value alone cannot tell the two apart.
        self._create(displayName="The Only Spool")

        result = self.databaseManager.loadSpool(999999, withReusedConnection=True)

        self.assertIsNone(result)
        self._assertNothingLogged()

    def test_databaseIdMayBeAString(self):
        # SpoolManagerAPI passes ids straight through from URL parameters, so they arrive
        # as strings - the original script called loadSpool("9") for exactly that reason.
        spool = self._create(displayName="String Id Spool")

        result = self.databaseManager.loadSpool(
            str(spool.databaseId), withReusedConnection=True
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.databaseId, spool.databaseId)

    def test_deletedSpoolReturnsNone(self):
        spool = self._create(displayName="Doomed Spool")
        deletedId = spool.databaseId
        spool.delete_instance()

        result = self.databaseManager.loadSpool(deletedId, withReusedConnection=True)

        self.assertIsNone(result)
        self._assertNothingLogged()


class TestDatabase(unittest.TestCase):

    sqliteDatabaseSettings = DatabaseManager.DatabaseSettings()
    sqliteDatabaseSettings.useExternal = False
    sqliteDatabaseSettings.baseFolder = (
        "/Users/o0632/Library/Application Support/OctoPrint/data/SpoolManager/"
    )

    postgresDatabaseSettings = DatabaseManager.DatabaseSettings()
    postgresDatabaseSettings.type = "postgres"
    postgresDatabaseSettings.host = "localhost"
    postgresDatabaseSettings.port = 5432
    postgresDatabaseSettings.name = "spoolmanagerdb"
    postgresDatabaseSettings.user = "Olli"
    postgresDatabaseSettings.password = "illO"

    mysqlDatabaseSettings = DatabaseManager.DatabaseSettings()
    mysqlDatabaseSettings.type = "mysql"
    mysqlDatabaseSettings.host = "localhost"
    mysqlDatabaseSettings.port = 3306
    mysqlDatabaseSettings.name = "spoolmanagerdb"
    mysqlDatabaseSettings.user = "Olli"
    mysqlDatabaseSettings.password = "illO"

    def setUp(self):
        self.init_database()

    def _clientOutput(self, type, title, message):
        print("**********************************************")
        print("Type:" + type)
        print("Title:" + title)
        print("Message:" + message)
        print("**********************************************")

    def init_database(self):
        logging.basicConfig(level=logging.DEBUG)
        self.testLogger = logging.getLogger("testLogger")
        logging.info("Start Database-Test")
        self.databaseManager = DatabaseManager(self.testLogger, True)

    ##################################################################################################   SQLITE CONNECTION
    def _test_connectToSQLite(self):
        self.testLogger.info("--------------------- SQLITE CONNECTION")
        self.databaseManager.initDatabase(
            self.sqliteDatabaseSettings, self._clientOutput
        )
        self.assertTrue(
            self.databaseManager.testDatabaseConnection() is None,
            "No Database connection",
        )
        self.testLogger.info("--------------------- SQLITE CONNECTION - DONE")

    ##################################################################################################   POSTGRES CONNECTION
    def _test_connectToPostgres(self):
        self.testLogger.info("--------------------- POSTGRESS CONNECTION")
        self.databaseManager.initDatabase(
            self.postgresDatabaseSettings, self._clientOutput
        )
        self.assertTrue(
            self.databaseManager.testDatabaseConnection() is None,
            "No Database connection",
        )
        self.testLogger.info("--------------------- POSTGRESS CONNECTION - DONE")

    ##################################################################################################   MYSQL CONNECTION
    def _test_connectToMySQL(self):
        self.testLogger.info("--------------------- MYSQL CONNECTION")
        self.databaseManager.initDatabase(
            self.mysqlDatabaseSettings, self._clientOutput
        )
        self.databaseManager.connectoToDatabase()
        self.assertTrue(
            self.databaseManager.testDatabaseConnection() is None,
            "No Database connection",
        )
        self.testLogger.info("--------------------- MYSQL CONNECTION - DONE")

    ##################################################################################################   LOAD META DATA
    def _test_readMetadata(self):

        self.databaseManager.initDatabase(
            self.sqliteDatabaseSettings, self._clientOutput
        )
        # self.databaseManager.initDatabase(self.postgresDatabaseSettings, self._clientOutput)
        # self.databaseManager.initDatabase(self.mysqlDatabaseSettings, self._clientOutput)
        metadata = self.databaseManager.loadDatabaseMetaInformations()
        print(metadata)

    ##################################################################################################   CREATE DATABASE
    def _test_createDatabase(self):

        self.databaseManager.initDatabase(
            self.postgresDatabaseSettings, self._clientOutput
        )
        self.databaseManager.reCreateDatabase(self.postgresDatabaseSettings)
        metadata = self.databaseManager.loadDatabaseMetaInformations()
        print(metadata)
        allSpoolModels = self.databaseManager.loadAllSpoolsByQuery()
        self.assertEqual(
            0, len(allSpoolModels), "Database not reCreated. Still spools inside"
        )

    ##################################################################################################   REUSABEL CONNECTION
    def _test_handleReusableConnectionl(self):

        self.databaseManager.initDatabase(
            self.postgresDatabaseSettings, self._clientOutput
        )
        self.databaseManager.connectoToDatabase()
        spool = self.databaseManager.loadSpool(1, withReusedConnection=True)
        import time

        time.sleep(3)
        print(spool.displayName)

        allSpoolModels = self.databaseManager.loadAllSpoolsByQuery(
            withReusedConnection=True
        )
        print(len(allSpoolModels))
        import time

        time.sleep(3)

        if allSpoolModels is not None:
            for spoolModel in allSpoolModels:
                print(spoolModel.displayName)

        self.databaseManager.closeDatabase()

    ##################################################################################################   LOAD SINGLE SPOOL
    # Superseded by TestLoadSpool above; kept as a manual script like its siblings.
    def _test_loadSingleSpool(self):

        self.databaseManager.initDatabase(
            self.sqliteDatabaseSettings, self._clientOutput
        )
        spool = self.databaseManager.loadSpool("9")
        # import time
        # time.sleep(3)
        print(spool.displayName)

    ##################################################################################################   LOAD ALL SPOOLS
    def _test_loadAllSpools(self):

        self.databaseManager.initDatabase(
            self.sqliteDatabaseSettings, self._clientOutput
        )

        tableQuery = {
            "from": 0,
            "to": 100,
            "sortColumn": "remaining",
            "sortOrder": "asc",
            "filterName": "all",
            "materialFilter": "ABS,PLA",
            "vendorFilter": "all",
            "colorFilter": "#ff0000;red,#ff0000;keinRot",
        }

        allSpoolModels = self.databaseManager.loadAllSpoolsByQuery(tableQuery)
        print(len(allSpoolModels))
        # import time
        # time.sleep(3)

        if allSpoolModels is not None:
            for spoolModel in allSpoolModels:
                displayName = spoolModel.displayName
                color = spoolModel.color + " " + spoolModel.colorName
                material = spoolModel.material
                print(
                    "Spool:'"
                    + displayName
                    + "' Color:'"
                    + color
                    + "' Material:'"
                    + material
                    + "'"
                )

    ##################################################################################################   SAVE SPOOL
    def _test_saveSpool(self):
        spoolModel = SpoolModel()
        spoolModel.displayName = "TESTSPOOL - Number1"

        self.databaseManager.initDatabase(
            self.postgresDatabaseSettings, self._clientOutput
        )
        databaseId = self.databaseManager.saveSpool(spoolModel)
        print(databaseId)
        self.assertTrue(databaseId is not None, "Spool not saved")

        spoolModel = self.databaseManager.loadSpool(databaseId)
        self.assertTrue(spoolModel is not None, "Spool not loaded")
        self.assertEqual(
            "TESTSPOOL - Number1", spoolModel.displayName, "Spool not saved"
        )

    ##################################################################################################   DELETE SPOOL
    def _test_deleteSpool(self):
        self.databaseManager.initDatabase(
            self.postgresDatabaseSettings, self._clientOutput
        )
        databaseId = 3
        print(databaseId)

        deletedDatabaseId = self.databaseManager.deleteSpool(databaseId)
        self.assertEqual(databaseId, deletedDatabaseId, "Spool not deleted")

    ##################################################################################################   DELETE SPOOL
    def _test_materialModels(self):
        self.databaseManager.initDatabase(
            self.sqliteDatabaseSettings, self._clientOutput
        )


if __name__ == "__main__":
    print("Start DatabaseManager Test")
    unittest.main()
    print("Finished")
