# coding=utf-8

import datetime
import json
import os
import logging
import re
import shutil
import sqlite3

from octoprint_SpoolManager.WrappedLoggingHandler import WrappedLoggingHandler
from peewee import *
from playhouse.shortcuts import model_to_dict, dict_to_model

from octoprint_SpoolManager.api import Transformer
from octoprint_SpoolManager.common import StringUtils
from octoprint_SpoolManager.models.BaseModel import BaseModel
from octoprint_SpoolManager.models.PluginMetaDataModel import PluginMetaDataModel
from octoprint_SpoolManager.models.SpoolModel import SpoolModel

# from octoprint_SpoolManager.models.MaterialModel import MaterialModel
# from octoprint_SpoolManager.models.MaterialCharacteristicModel import MaterialCharacteristicModel

FORCE_CREATE_TABLES = False

CURRENT_DATABASE_SCHEME_VERSION = 8

# List all Models
MODELS = [PluginMetaDataModel, SpoolModel]

class DatabaseManager(object):

    class DatabaseSettings:
        # Internal stuff
        baseFolder = ""
        fileLocation = ""
        # External stuff
        useExternal = False
        type = "postgresql" # postgresql,  mysql NOT sqlite
        name = ""
        host = ""
        port = 0
        user = ""
        password = ""

        def __str__(self):
            # mask the password, because this string ends up in octoprint.log
            maskedDict = dict(self.__dict__)
            if (maskedDict.get("password")):
                maskedDict["password"] = "*****"
            return str(maskedDict)

    def __init__(self, parentLogger, sqlLoggingEnabled):
        self.sqlLoggingEnabled = sqlLoggingEnabled
        self._logger = logging.getLogger(parentLogger.name + "." + self.__class__.__name__)
        self._sqlLogger = logging.getLogger(parentLogger.name + "." + self.__class__.__name__ + ".SQL")

        self._database = None
        self._databaseSettings = None
        self._sendDataToClient = None
        self._isConnected = False
        self._currentErrorMessageDict = None
        # True when an external database still has an old scheme (auto-upgrade only runs for local SQLite)
        self._schemeUpgradeNeeded = False

    ################################################################################################## private functions
    # "databaseSettings"] = {
    # "type": "mysql",
    # "host": "localhost",
    # "port": 8080,
    # "databaseName": "SpoolManagerDatabase",
    # "user": "",
    # "password": ""

    def _buildDatabaseConnection(self):
        database = None
        if (self._databaseSettings.useExternal == False):
            # local database`
            database = SqliteDatabase(self._databaseSettings.fileLocation)
        else:
            databaseType = self._databaseSettings.type
            databaseName = self._databaseSettings.name
            host = self._databaseSettings.host
            port = int(self._databaseSettings.port)
            user = self._databaseSettings.user
            password = self._databaseSettings.password
            if ("postgres" == databaseType):
                # Connect to a Postgres database.
                database = PostgresqlDatabase(databaseName,
                                              user=user,
                                              password=password,
                                              host=host,
                                              port=port)
            else:
                # Connect to a MySQL database on network.
                database = MySQLDatabase(databaseName,
                                         user=user,
                                         password=password,
                                         host=host,
                                         port=port)

        return database

    def _createDatabase(self, forceCreateTables):

        if forceCreateTables:
            self._logger.info("Creating new database-tables, because FORCE == TRUE!")
            self._createDatabaseTables()
        else:
            # check, if we need an scheme upgrade
            self._createOrUpgradeSchemeIfNecessary()

        self._logger.info("Database created-check done")

    def _createOrUpgradeSchemeIfNecessary(self):

        self._logger.info("Check if database-scheme upgrade needed...")
        schemeVersionFromDatabaseModel = None
        schemeVersionFromDatabase = None
        try:
            cursor = PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
            result = cursor.value
            if (result != None):
                schemeVersionFromDatabase = int(result[0])
                self._logger.info("Current databasescheme: " + str(schemeVersionFromDatabase))
            else:
                self._logger.warning("Strange, table is found (maybe), but there is no result of the schem version. Try to recreate a new db-scheme")
                self.backupDatabaseFile() # safty first
                self._createDatabaseTables()
                return
            pass
        except Exception as e:
            self._logger.exception(e)
            self.closeDatabase()
            errorMessage = str(e)
            if (
                    # - SQLLite
                    errorMessage.startswith("no such table") or
                    # - Postgres
                    "does not exist" in errorMessage or
                    # - mySQL errorcode=1146
                    "doesn\'t exist" in errorMessage
            ):
                self._createDatabaseTables()
                return
            else:
                self._logger.error(str(e))

        if not schemeVersionFromDatabase == None:
            currentDatabaseSchemeVersion = schemeVersionFromDatabase
            if (currentDatabaseSchemeVersion < CURRENT_DATABASE_SCHEME_VERSION):
                # auto upgrade done only for local database
                if (self._databaseSettings.useExternal == True):
                    self._logger.warning("Scheme upgrade is only done for local database. Use the 'Upgrade database scheme' button in the plugin settings (Storage tab).")
                    self._schemeUpgradeNeeded = True
                    return

                # evautate upgrade steps (from 1-2 , 1...6)
                self._logger.info("We need to upgrade the database scheme from: '" + str(currentDatabaseSchemeVersion) + "' to: '" + str(CURRENT_DATABASE_SCHEME_VERSION) + "'")

                try:
                    self.backupDatabaseFile()
                    self._upgradeDatabase(currentDatabaseSchemeVersion, CURRENT_DATABASE_SCHEME_VERSION)
                except Exception as e:
                    self._logger.error("Error during database upgrade!!!!")
                    self._logger.exception(e)
                    return
                self._logger.info("...Database-scheme successfully upgraded.")
                self._schemeUpgradeNeeded = False
            else:
                self._logger.info("...Database-scheme upgraded not needed.")
                self._schemeUpgradeNeeded = False
        else:
            self._logger.warning("...something was strange. Should not be shwon in log. Check full log")
        pass

    def _upgradeDatabase(self,currentDatabaseSchemeVersion, targetDatabaseSchemeVersion):

        migrationFunctions = [self._upgradeFrom1To2,
                              self._upgradeFrom2To3,
                              self._upgradeFrom3To4,
                              self._upgradeFrom4To5,
                              self._upgradeFrom5To6,
                              self._upgradeFrom6To7,
                              self._upgradeFrom7To8,
                              self._upgradeFrom8To9,
                              self._upgradeFrom9To10
                              ]

        for migrationMethodIndex in range(currentDatabaseSchemeVersion -1, targetDatabaseSchemeVersion -1):
            self._logger.info("Database migration from '" + str(migrationMethodIndex + 1) + "' to '" + str(migrationMethodIndex + 2) + "'")
            migrationFunctions[migrationMethodIndex]()
            pass
        pass

    def _upgradeFrom9To10(self):
        self._logger.info(" Starting 9 -> 10")
        # What is changed:
        # -
        self._passMessageToClient("error", "DatabaseManager",
                                  "Could not upgrade database scheme V1 to V2. See OctoPrint.log for details!")
        self._logger.info(" Successfully 9 -> 10")

    def _upgradeFrom8To9(self):
        self._logger.info(" Starting 8 -> 9")
        # What is changed:
        # -
        self._passMessageToClient("error", "DatabaseManager",
                                  "Could not upgrade database scheme V1 to V2. See OctoPrint.log for details!")
        self._logger.info(" Successfully 8 -> 9")

    def _upgradeFrom7To8(self):
        self._logger.info(" Starting 7 -> 8")
        # What is changed:
        # - batchNumber = CharField(null=True) # since V8
        # Unlike the legacy migrations this one is database-agnostic (local SQLite and external MySQL/PostgreSQL):
        # it uses the already open peewee connection instead of a direct sqlite3 connection.

        # column check makes the migration idempotent (several OctoPrint instances may share one external database)
        columnNames = [column.name for column in self._database.get_columns("spo_spoolmodel")]
        if ("batchNumber" in columnNames):
            self._logger.info("  column 'batchNumber' already present, skipping ALTER TABLE")
        else:
            self._database.execute_sql("ALTER TABLE spo_spoolmodel ADD COLUMN batchNumber VARCHAR(255)")

        PluginMetaDataModel.update(value="8").where(
            PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).execute()

        self._logger.info(" Successfully 7 -> 8")

    def _upgradeFrom6To7(self):
        self._logger.info(" Starting 6 -> 7")
        # What is changed:
        # - Recalculate remaining weight

        self._logger.info("  try to calculate remaining weight.")

        #  Calculate the remaining weight for all current spools
        with self._database.atomic() as transaction:  # Opens new transaction.

            try:
                allSpoolModels = self.loadAllSpoolsByQuery(None)
                if (allSpoolModels != None):
                    for spoolModel in allSpoolModels:
                        totalWeight = spoolModel.totalWeight
                        usedWeight = spoolModel.usedWeight
                        remainingWeight = Transformer.calculateRemainingWeight(usedWeight, totalWeight)
                        if (remainingWeight != None):
                            spoolModel.remainingWeight = remainingWeight
                            spoolModel.save()

                localSchemeVersionFromDatabaseModel = PluginMetaDataModel.get(
                    PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
                localSchemeVersionFromDatabaseModel.value = "7"
                localSchemeVersionFromDatabaseModel.save()

                # do explicit commit
                transaction.commit()
            except:
                # Because this block of code is wrapped with "atomic", a
                # new transaction will begin automatically after the call
                # to rollback().
                transaction.rollback()
                self._logger.exception("Could not calculate remainingWeight during scheme update from 6 To 7:" )

                return
            pass
        self._logger.info(" Successfully 6 -> 7")



    def _upgradeFrom5To6(self):
        self._logger.info(" Starting 5 -> 6")
        # What is changed:
        # - Recalculate remaining weight
        # - offsetTemperature = IntegerField(null=True)  # since V6
        # - offsetBedTemperature = IntegerField(null=True)  # since V6
        # - offsetEnclosureTemperature = IntegerField(null=True)  # since V6

        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        sql = """
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;

            ALTER TABLE 'spo_spoolmodel' ADD 'offsetTemperature' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'offsetBedTemperature' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'offsetEnclosureTemperature' INTEGER;

            UPDATE 'spo_pluginmetadatamodel' SET value=6 WHERE key='databaseSchemeVersion';
        COMMIT;
        PRAGMA foreign_keys=on;
        """
        cursor.executescript(sql)
        connection.close()

        self._logger.info("  try to calculate remaining weight.")
        #  Calculate the remaining weight for all current spools
        with self._database.atomic() as transaction:  # Opens new transaction.
            try:
                allSpoolModels = self.loadAllSpoolsByQuery(None)
                if (allSpoolModels != None):
                    for spoolModel in allSpoolModels:
                        totalWeight = spoolModel.totalWeight
                        usedWeight = spoolModel.usedWeight
                        remainingWeight = Transformer.calculateRemainingWeight(usedWeight, totalWeight)
                        if (remainingWeight != None):
                            spoolModel.remainingWeight = remainingWeight
                            spoolModel.save()
                # do expicit commit
                transaction.commit()
            except Exception as e:
                # Because this block of code is wrapped with "atomic", a
                # new transaction will begin automatically after the call
                # to rollback().
                transaction.rollback()
                self._logger.exception("Could not calculate remainingWeight during scheme update from 5 To 6:" + str(e))

                return
            pass

        self._logger.info(" Successfully 5 -> 6")
        pass

    def _upgradeFrom4To5(self):
        self._logger.info(" Starting 4 -> 5")
        # What is changed:
        # SpoolModel (needed, because last script created a new table and altering was already done
        # - materialCharacteristic = CharField(null=True, index=True) # strong, soft,... # since V4: new
        # - material = CharField(null=True, index=True)	# since V4: added index
        # - vendor = CharField(null=True, index=True) # since V4: added index
        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        self._executeSQLQuietly(cursor, "ALTER TABLE 'spo_spoolmodel' ADD 'updated' DATETIME")
        self._executeSQLQuietly(cursor, "ALTER TABLE 'spo_spoolmodel' ADD 'originator' CHAR(60)")
        self._executeSQLQuietly(cursor, "ALTER TABLE 'spo_spoolmodel' ADD 'materialCharacteristic' VARCHAR(255)")
        self._executeSQLQuietly(cursor, "ALTER TABLE 'spo_spoolmodel' ADD 'isActive' INTEGER")
        self._executeSQLQuietly(cursor, "UPDATE 'spo_spoolmodel' SET isActive=1")
        self._executeSQLQuietly(cursor, "CREATE INDEX spoolmodel_materialCharacteristic ON spo_spoolmodel (materialCharacteristic)")
        self._executeSQLQuietly(cursor, "CREATE INDEX spoolmodel_material ON spo_spoolmodel (material)")
        self._executeSQLQuietly(cursor, "CREATE INDEX spoolmodel_vendor ON spo_spoolmodel (vendor)")

        self._executeSQLQuietly(cursor, "UPDATE 'spo_pluginmetadatamodel' SET value=5 WHERE key='databaseSchemeVersion'")

        # sql = """
        # PRAGMA foreign_keys=off;
        # BEGIN TRANSACTION;
        #
        # 	ALTER TABLE 'spo_spoolmodel' ADD 'updated' DATETIME;
        # 	ALTER TABLE 'spo_spoolmodel' ADD 'originator' CHAR(60);
        # 	ALTER TABLE 'spo_spoolmodel' ADD 'materialCharacteristic' VARCHAR(255);
        # 	ALTER TABLE 'spo_spoolmodel' ADD 'isActive' INTEGER;
        # 	UPDATE 'spo_spoolmodel' SET isActive=1;
        #
        # 	CREATE INDEX spoolmodel_materialCharacteristic ON spo_spoolmodel (materialCharacteristic);
        # 	CREATE INDEX spoolmodel_material ON spo_spoolmodel (material);
        # 	CREATE INDEX spoolmodel_vendor ON spo_spoolmodel (vendor);
        #
        # 	UPDATE 'spo_pluginmetadatamodel' SET value=5 WHERE key='databaseSchemeVersion';
        # COMMIT;
        # PRAGMA foreign_keys=on;
        # """
        # cursor.executescript(sql)

        connection.close()

        self._logger.info(" Successfully 4 -> 5")
        pass

    def _executeSQLQuietly(self, cursor, sqlStatement):
        try:
            cursor.execute(sqlStatement)
        except Exception as e:
            self._logger.error(sqlStatement)
            self._logger.exception(e)

    def _upgradeFrom4To5_HACK(self, sqlStatement):

        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        sql = """
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;
        """

        # ALTER TABLE 'spo_spoolmodel' ADD 'updated' DATETIME;
        # ALTER TABLE 'spo_spoolmodel' ADD 'originator' CHAR(60);
        # ALTER TABLE 'spo_spoolmodel' ADD 'materialCharacteristic' VARCHAR(255);
        # ALTER TABLE 'spo_spoolmodel' ADD 'isActive' INTEGER;
        # UPDATE 'spo_spoolmodel' SET isActive=1;
        #
        # CREATE INDEX spoolmodel_materialCharacteristic ON spo_spoolmodel (materialCharacteristic);
        # CREATE INDEX spoolmodel_material ON spo_spoolmodel (material);
        # CREATE INDEX spoolmodel_vendor ON spo_spoolmodel (vendor);
        #
        # UPDATE 'spo_pluginmetadatamodel' SET value=5 WHERE key='databaseSchemeVersion';

        sql = sql + """
        COMMIT;
        PRAGMA foreign_keys=on;
        """
        cursor.executescript(sql)

        connection.close()

    def _upgradeFrom3To4(self):
        self._logger.info(" Starting 3 -> 4")
        # What is changed:
        # BaseModel, so add to all tables
        # - updated = DateTimeField(default=datetime.datetime.now)
        # - version = SmallIntegerField(null=True)
        # - originator = FixedCharField(null=True, max_length=60)
        # SpoolModel
        # - materialCharacteristic = CharField(null=True, index=True) # strong, soft,... # since V4: new
        # - material = CharField(null=True, index=True)	# since V4: added index
        # - vendor = CharField(null=True, index=True) # since V4: added index
        # - encloser -> rename to enclosureTemperature
        #               ALTER TABLE spo_spoolmodel RENAME COLUMN encloserTemperature to enclosureTemperature; not working
        #               SQLite did not support the ALTER TABLE RENAME COLUMN syntax before version 3.25.0.
        #               see https://www.sqlitetutorial.net/sqlite-rename-column/#:~:text=SQLite%20did%20not%20support%20the,the%20version%20lower%20than%203.25.
        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        # SCHROTT!!!!! zuerst 'ALTER' und dann eine neue Tabelle erstellen ohne die ALTER-Spalten, SUPER !!!!
        sql = """
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;

            ALTER TABLE 'spo_pluginmetadatamodel' ADD 'updated' DATETIME;
            ALTER TABLE 'spo_pluginmetadatamodel' ADD 'version' INTEGER;
            ALTER TABLE 'spo_pluginmetadatamodel' ADD 'originator' CHAR(60);
            UPDATE 'spo_pluginmetadatamodel' SET version=1;

            ALTER TABLE 'spo_spoolmodel' ADD 'updated' DATETIME;
            ALTER TABLE 'spo_spoolmodel' ADD 'originator' CHAR(60);
            ALTER TABLE 'spo_spoolmodel' ADD 'materialCharacteristic' VARCHAR(255);
            ALTER TABLE 'spo_spoolmodel' ADD 'isActive' INTEGER;
            UPDATE 'spo_spoolmodel' SET isActive=1;

            CREATE INDEX spoolmodel_materialCharacteristic ON spo_spoolmodel (materialCharacteristic);
            CREATE INDEX spoolmodel_material ON spo_spoolmodel (material);
            CREATE INDEX spoolmodel_vendor ON spo_spoolmodel (vendor);

            ALTER TABLE 'spo_spoolmodel' RENAME TO 'spo_spoolmodel_old';
            CREATE TABLE "spo_spoolmodel" (
                "databaseId" INTEGER NOT NULL PRIMARY KEY,
                "created" DATETIME NOT NULL,
                "isTemplate" INTEGER,
                "displayName" VARCHAR(255),
                "vendor" VARCHAR(255),
                "material" VARCHAR(255),
                "density" REAL,
                "diameter" REAL,
                "colorName" VARCHAR(255),
                "color" VARCHAR(255),
                "temperature" INTEGER,
                "totalWeight" REAL,
                "usedWeight" REAL,
                "remainingWeight" REAL,
                "usedLength" INTEGER,
                "code" VARCHAR(255),
                "firstUse" DATETIME,
                "lastUse" DATETIME,
                "purchasedFrom" VARCHAR(255),
                "purchasedOn" DATE,
                "cost" REAL,
                "costUnit" VARCHAR(255),
                "labels" TEXT,
                "noteText" TEXT,
                "noteDeltaFormat" TEXT,
                "noteHtml" TEXT,
                'version' INTEGER,
                'diameterTolerance' REAL,
                'spoolWeight' REAL,
                'flowRateCompensation' INTEGER,
                'bedTemperature' INTEGER,
                'enclosureTemperature' INTEGER,
                'totalLength' INTEGER);

                INSERT INTO 'spo_spoolmodel'
                (databaseId, created, isTemplate, displayName, vendor, material, density, diameter, diameter, colorName, color, temperature, totalWeight, usedWeight, remainingWeight, usedLength, code, firstUse, lastUse, purchasedFrom, purchasedOn, cost, costUnit, labels, noteText, noteDeltaFormat, noteHtml, version, diameterTolerance, spoolWeight, flowRateCompensation, bedTemperature, enclosureTemperature, totalLength)
                  SELECT databaseId, created, isTemplate, displayName, vendor, material, density, diameter, diameter, colorName, color, temperature, totalWeight, usedWeight, remainingWeight, usedLength, code, firstUse, lastUse, purchasedFrom, purchasedOn, cost, costUnit, labels, noteText, noteDeltaFormat, noteHtml, version, diameterTolerance, spoolWeight, flowRateCompensation, bedTemperature, encloserTemperature, totalLength
                  FROM 'spo_spoolmodel_old';

                DROP TABLE 'spo_spoolmodel_old';

            UPDATE 'spo_pluginmetadatamodel' SET value=4 WHERE key='databaseSchemeVersion';
        COMMIT;
        PRAGMA foreign_keys=on;
        """
        cursor.executescript(sql)

        connection.close()

        self._logger.info(" Successfully 3 -> 4")
        pass

    def _upgradeFrom2To3(self):
        self._logger.info(" Starting 2 -> 3")
        # What is changed:
        # - version = IntegerField(null=True)  # since V3
        # - diameterTolerance = FloatField(null=True)  # since V3
        # - flowRateCompensation = IntegerField(null=True)  # since V3
        # - bedTemperature = IntegerField(null=True)  # since V3
        # - enclosureTemperature = IntegerField(null=True)  # since V3

        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        sql = """
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;

            ALTER TABLE 'spo_spoolmodel' ADD 'version' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'diameterTolerance' REAL;
            ALTER TABLE 'spo_spoolmodel' ADD 'spoolWeight' REAL;
            ALTER TABLE 'spo_spoolmodel' ADD 'flowRateCompensation' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'bedTemperature' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'encloserTemperature' INTEGER;
            ALTER TABLE 'spo_spoolmodel' ADD 'totalLength' INTEGER;

            UPDATE 'spo_spoolmodel' SET version=1;

            UPDATE 'spo_pluginmetadatamodel' SET value=3 WHERE key='databaseSchemeVersion';
        COMMIT;
        PRAGMA foreign_keys=on;
        """
        cursor.executescript(sql)

        connection.close()

        self._logger.info(" Successfully 2 -> 3")
        pass

    def _upgradeFrom1To2(self):
        self._logger.info(" Starting 1 -> 2")
        # What is changed:
        # - SpoolModel: Add Column remainingWeight (needed fro filtering, sorting)
        connection = sqlite3.connect(self._databaseSettings.fileLocation)
        cursor = connection.cursor()

        sql = """
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;

            ALTER TABLE 'spo_spoolmodel' ADD 'remainingWeight' REAL;

            UPDATE 'spo_pluginmetadatamodel' SET value=2 WHERE key='databaseSchemeVersion';
        COMMIT;
        PRAGMA foreign_keys=on;
        """
        cursor.executescript(sql)

        connection.close()
        self._logger.info("Database 'altered' successfully. Try to calculate remaining weight.")
        #  Calculate the remaining weight for all current spools
        with self._database.atomic() as transaction:  # Opens new transaction.
            try:
                allSpoolModels = self.loadAllSpoolsByQuery(None)
                if (allSpoolModels != None):
                    for spoolModel in allSpoolModels:
                        totalWeight = spoolModel.totalWeight
                        usedWeight = spoolModel.usedWeight
                        remainingWeight = Transformer.calculateRemainingWeight(usedWeight, totalWeight)
                        if (remainingWeight != None):
                            spoolModel.remainingWeight = remainingWeight
                            spoolModel.save()

                # do expicit commit
                transaction.commit()
            except Exception as e:
                # Because this block of code is wrapped with "atomic", a
                # new transaction will begin automatically after the call
                # to rollback().
                transaction.rollback()
                self._logger.exception("Could not upgrade database scheme from 1 To 2:" + str(e))

                self._passMessageToClient("error", "DatabaseManager", "Could not upgrade database scheme V1 to V2. See OctoPrint.log for details!")
            pass

        self._logger.info(" Successfully 1 -> 2")
        pass


    def _createDatabaseTables(self):
        self._logger.info("Creating new database tables for spoolmanager-plugin")
        self._database.connect(reuse_if_open=True)
        self._database.drop_tables(MODELS)
        self._database.create_tables(MODELS)

        PluginMetaDataModel.create(key=PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION, value=CURRENT_DATABASE_SCHEME_VERSION)
        self.closeDatabase()

    def _storeErrorMessage(self, type, title, message, sendErrorPopUp):
        # store current error message
        self._currentErrorMessageDict = {
            "type":type,
            "title":title,
            "message":message
        }
        # send to client, if needed
        if (sendErrorPopUp == True):
            self._passMessageToClient(type, title, message)

    ################################################################################################### public functions
    @staticmethod
    def buildDefaultDatabaseFileLocation(pluginDataBaseFolder):
        databaseFileLocation = os.path.join(pluginDataBaseFolder, "spoolmanager.db")
        return databaseFileLocation

    def initDatabase(self, databaseSettings, sendMessageToClient):

        self._logger.info("Init DatabaseManager")
        self._currentErrorMessageDict = None
        self._passMessageToClient = sendMessageToClient
        self._databaseSettings = databaseSettings

        databaseFileLocation = DatabaseManager.buildDefaultDatabaseFileLocation(databaseSettings.baseFolder)
        self._databaseSettings.fileLocation = databaseFileLocation
        existsDatabaseFile = str(os.path.exists(self._databaseSettings.fileLocation))
        self._logger.info("Databasefile '" +self._databaseSettings.fileLocation+ "' exists: " + existsDatabaseFile)

        if (existsDatabaseFile == False):
            self._createDatabase(FORCE_CREATE_TABLES)
            self.closeDatabase()

        import logging
        logger = logging.getLogger('peewee')
        # we need only the single logger without parent
        logger.parent = None
        # logger.addHandler(logging.StreamHandler())
        # activate SQL logging on PEEWEE side and on PLUGIN side
        # logger.setLevel(logging.DEBUG)
        # self._sqlLogger.setLevel(logging.DEBUG)
        self.showSQLLogging(self.sqlLoggingEnabled)

        wrappedHandler = WrappedLoggingHandler(self._sqlLogger)
        logger.addHandler(wrappedHandler)

        connected = self.connectoToDatabase(sendErrorPopUp=False)
        if (connected == True):
            self._createDatabase(FORCE_CREATE_TABLES)
            self.closeDatabase()

        return self._currentErrorMessageDict

    def assignNewDatabaseSettings(self, databaseSettings):
        self._databaseSettings = databaseSettings

    def getDatabaseSettings(self):
        return self._databaseSettings

    def testDatabaseConnection(self, databaseSettings = None):
        result = None
        backupCurrentDatabaseSettings = None
        try:
            # use provided database settings or default if not provided
            if (databaseSettings != None):
                backupCurrentDatabaseSettings = self._databaseSettings
                self._databaseSettings = databaseSettings

            succesful = self.connectoToDatabase()
            if (succesful == False):
                result = self.getCurrentErrorMessageDict()
        finally:
            try:
                self.closeDatabase()
            except:
                pass # do nothing
            if (backupCurrentDatabaseSettings != None):
                self._databaseSettings = backupCurrentDatabaseSettings

        return result


    def getCurrentErrorMessageDict(self):
        return self._currentErrorMessageDict

    # connect to the current database
    def connectoToDatabase(self, withMetaCheck=False, sendErrorPopUp=True) :
        # reset current errorDict
        self._currentErrorMessageDict = None
        self._isConnected = False

        # build connection
        try:
            if (self.sqlLoggingEnabled):
                self._logger.info("Database connection with...")
                self._logger.info(self._databaseSettings)
            self._database = self._buildDatabaseConnection()

            # connect to Database
            DatabaseManager.db = self._database
            self._database.bind(MODELS)

            self._database.connect()
            if (self.sqlLoggingEnabled):
                self._logger.info("Database connection successful. Checking Scheme versions")
            # TODO do I realy need to check the meta-infos in the connect function
            # schemeVersionFromPlugin = str(CURRENT_DATABASE_SCHEME_VERSION)
            # schemeVersionFromDatabaseModel = str(PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value)
            # if (schemeVersionFromPlugin != schemeVersionFromDatabaseModel):
            # 	errorMessage = "Plugin needs database scheme version: "+str(schemeVersionFromPlugin)+", but database has version: "+str(schemeVersionFromDatabaseModel);
            # 	self._storeErrorMessage("error", "database scheme version", errorMessage , False)
            # 	self._logger.error(errorMessage)
            # 	self._isConnected = False
            # else:
            # 	self._logger.info("...succesfull connected")
            # 	self._isConnected = True
            self._isConnected = True
        except Exception as e:
            errorMessage = str(e)
            self._logger.exception("connectoToDatabase")
            self.closeDatabase()
            # type, title, message
            self._storeErrorMessage("error", "connection problem", errorMessage, sendErrorPopUp)
            return False
        return self._isConnected

    def closeDatabase(self, ) :
        self._currentErrorMessageDict = None
        try:
            self._database.close()
            pass
        except Exception as e:
            pass ## ignore close exception
        self._isConnected = False

    def isConnected(self):
        return self._isConnected

    def isSchemeUpgradeNeeded(self):
        return self._schemeUpgradeNeeded

    # re-reads the scheme version from the database; the startup check goes stale
    # when the (external) database is replaced at runtime, e.g. after a dump restore
    def recheckSchemeUpgradeNeeded(self, withReusedConnection=False):
        def databaseCallMethode():
            schemeVersionFromDatabase = int(str(PluginMetaDataModel.get(
                PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value))
            self._schemeUpgradeNeeded = schemeVersionFromDatabase < CURRENT_DATABASE_SCHEME_VERSION
            return self._schemeUpgradeNeeded

        try:
            if (withReusedConnection == True):
                return databaseCallMethode()
            self.connectoToDatabase(sendErrorPopUp=False)
            try:
                return databaseCallMethode()
            finally:
                self.closeDatabase()
        except Exception:
            # keep the last known state, e.g. when the database is not reachable at all
            return self._schemeUpgradeNeeded

    def showSQLLogging(self, enabled):
        import logging
        logger = logging.getLogger('peewee')

        if (enabled):
            logger.setLevel(logging.DEBUG)
            self._sqlLogger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.ERROR)
            self._sqlLogger.setLevel(logging.ERROR)

    def backupDatabaseFile(self):

        if (self._databaseSettings.useExternal == True):
            self._logger.info("No database backup needed, because we are using an external database.")
        else:
            if (os.path.exists(self._databaseSettings.fileLocation)):
                self._logger.info("Starting database backup")
                now = datetime.datetime.now()
                currentDate = now.strftime("%Y%m%d-%H%M")
                currentSchemeVersion = "unknown"
                try:
                    currentSchemeVersion = PluginMetaDataModel.get(
                        PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION)
                    if (currentSchemeVersion != None):
                        currentSchemeVersion = str(currentSchemeVersion.value)
                except Exception as e:
                    self._logger.exception("Could not read databasescheme version:" + str(e))

                backupDatabaseFilePath = self._databaseSettings.fileLocation[0:-3] + "-backup-V" + currentSchemeVersion + "-" +currentDate+".db"
                # backupDatabaseFileName = "spoolmanager-backup-"+currentDate+".db"
                # backupDatabaseFilePath = os.path.join(backupFolder, backupDatabaseFileName)
                if not os.path.exists(backupDatabaseFilePath):
                    shutil.copy(self._databaseSettings.fileLocation, backupDatabaseFilePath)
                    self._logger.info("Backup of spoolmanager database created '" + backupDatabaseFilePath + "'")
                else:
                    self._logger.warning("Backup of spoolmanager database ('" + backupDatabaseFilePath + "') is already present. No backup created.")
                    return backupDatabaseFilePath
            else:
                self._logger.info("No database backup needed, because there is no databasefile '"+str(self._databaseSettings.fileLocation)+"'")

    def reCreateDatabase(self, databaseSettings = None):
        self._currentErrorMessageDict = None
        self._logger.info("ReCreating Database")
        self._logger.info(databaseSettings)

        backupCurrentDatabaseSettings = None
        if (databaseSettings != None):
            backupCurrentDatabaseSettings = self._databaseSettings
            self._databaseSettings = databaseSettings
        try:
            # - connect to dataabase
            self.connectoToDatabase()

            self._createDatabase(True)

            # - close dataabase
            self.closeDatabase()
        finally:
            # - restore database settings
            if (backupCurrentDatabaseSettings != None):
                self._databaseSettings = backupCurrentDatabaseSettings

    def copySpoolData(self, databaseSettings = None):

        loadResult = False
        copySpoolCount = 0

        backupCurrentDatabaseSettings = None
        if (databaseSettings != None):
            backupCurrentDatabaseSettings = self._databaseSettings
        else:
            # use default settings
            databaseSettings = self._databaseSettings
            backupCurrentDatabaseSettings = self._databaseSettings

        try:
            currentDatabaseType = databaseSettings.type
            currentUseExternal = databaseSettings.useExternal

            # First load meta from local sqlite database
            databaseSettings.type = "sqlite"
            databaseSettings.baseFolder = self._databaseSettings.baseFolder
            databaseSettings.fileLocation = self._databaseSettings.fileLocation
            databaseSettings.useExternal = False
            self._databaseSettings = databaseSettings

            allSpoolDicts = None
            try:
                self.connectoToDatabase( sendErrorPopUp=False)
                # materialize the query result before closing the connection,
                # since peewee's select() is lazy and would otherwise be
                # evaluated after switching to the external database
                allSpoolDicts = [model_to_dict(spool) for spool in SpoolModel.select()]
                self.closeDatabase()
            except Exception as e:
                errorMessage = "local database: " + str(e)
                self._logger.error("Connecting to local database not possible")
                self._logger.exception(e)
                try:
                    self.closeDatabase()
                except Exception:
                    pass  # ignore close exception


            databaseSettings.type = currentDatabaseType
            databaseSettings.useExternal = True
            self._databaseSettings = databaseSettings

            if allSpoolDicts is not None:
                try:
                    self.connectoToDatabase( sendErrorPopUp=False)
                    self._createDatabase(True)
                    for spoolJson in allSpoolDicts:
                        SpoolModel.insert(spoolJson).execute()
                        copySpoolCount = copySpoolCount + 1
                    self.closeDatabase()
                    loadResult = True
                except Exception as e:
                    errorMessage = "database: " + str(e)
                    self._logger.error("Connecting to external database not possible")
                    self._logger.exception(e)
                    try:
                        self.closeDatabase()
                    except Exception:
                        pass  # ignore close exception

        finally:
            # restore orig. databasettings
            if (backupCurrentDatabaseSettings != None):
                self._databaseSettings = backupCurrentDatabaseSettings

        return {
            "success": loadResult,
            "copySpoolCount": copySpoolCount
        }

    ######################################################################################### MYSQL DUMP EXPORT / IMPORT
    # The dump format is self-generated and validated on import:
    # - first line is SQL_DUMP_MARKER
    # - one INSERT per row on a single line (pymysql escaping produces no literal newlines)
    # - databaseId is always the first column (append-import relies on it)
    SQL_DUMP_MARKER = "-- SpoolManager MySQL dump"

    def _isExternalMySQL(self):
        return self._databaseSettings != None and \
               self._databaseSettings.useExternal == True and \
               "mysql" == self._databaseSettings.type

    # builds the dump text, requires an already open MySQL connection (caller handles connect/close)
    def _generateMySQLDumpText(self):

        # raw pymysql connection, needed for proper SQL value escaping
        rawConnection = self._database.connection()

        schemeVersion = str(CURRENT_DATABASE_SCHEME_VERSION)
        pluginVersion = "unknown"
        try:
            schemeVersion = str(PluginMetaDataModel.get(
                PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value)
        except Exception:
            self._logger.warning("Could not read the database scheme version for the dump header, using default.")
        try:
            pluginVersion = str(PluginMetaDataModel.get(
                PluginMetaDataModel.key == PluginMetaDataModel.KEY_PLUGIN_VERSION).value)
        except DoesNotExist:
            # the plugin never writes this metadata key, so it being absent is the normal case
            pass
        except Exception:
            self._logger.warning("Could not read the plugin version for the dump header, using default.")

        now = datetime.datetime.now()
        dumpLines = [
            self.SQL_DUMP_MARKER,
            "-- pluginVersion: " + pluginVersion,
            "-- schemeVersion: " + schemeVersion,
            "-- database: " + str(self._databaseSettings.name),
            "-- exportDate: " + now.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "SET NAMES utf8mb4;",
            ""
        ]

        for model in MODELS:
            tableName = model._meta.table_name

            dumpLines.append("DROP TABLE IF EXISTS `" + tableName + "`;")

            cursor = self._database.execute_sql("SHOW CREATE TABLE `" + tableName + "`")
            createTableStatement = cursor.fetchone()[1]
            dumpLines.append(createTableStatement + ";")
            dumpLines.append("")

            cursor = self._database.execute_sql("SHOW COLUMNS FROM `" + tableName + "`")
            columnNames = [column[0] for column in cursor.fetchall()]
            # make sure databaseId is the first column, the append-import relies on it
            if ("databaseId" in columnNames):
                columnNames.remove("databaseId")
                columnNames.insert(0, "databaseId")
            columnList = ", ".join(["`" + columnName + "`" for columnName in columnNames])

            cursor = self._database.execute_sql("SELECT " + columnList + " FROM `" + tableName + "`")
            for row in cursor.fetchall():
                valueList = ", ".join([rawConnection.escape(value) for value in row])
                dumpLines.append("INSERT INTO `" + tableName + "` (" + columnList + ") VALUES (" + valueList + ");")
            dumpLines.append("")

        return "\n".join(dumpLines)

    def exportMySQLDatabaseDump(self):

        if (self._isExternalMySQL() == False):
            return {
                "success": False,
                "dump": None,
                "errorMessage": "Database dump export is only supported for external MySQL databases."
            }

        try:
            connected = self.connectoToDatabase(sendErrorPopUp=False)
            if (connected == False):
                errorMessage = "Could not connect to the external MySQL database."
                if (self._currentErrorMessageDict != None):
                    errorMessage = errorMessage + " " + str(self._currentErrorMessageDict.get("message", ""))
                return {
                    "success": False,
                    "dump": None,
                    "errorMessage": errorMessage
                }

            return {
                "success": True,
                "dump": self._generateMySQLDumpText(),
                "errorMessage": None
            }
        except Exception as e:
            self._logger.exception("exportMySQLDatabaseDump")
            return {
                "success": False,
                "dump": None,
                "errorMessage": str(e)
            }
        finally:
            self.closeDatabase()

    def upgradeExternalDatabaseScheme(self, createBackupFile=True):
        # user-triggered scheme upgrade for external databases (Settings dialog);
        # local SQLite databases are upgraded automatically during plugin startup.
        # A dump backup is mandatory: either already downloaded by the frontend (createBackupFile=False)
        # or written to the plugin data folder here - without one the upgrade is aborted.
        result = {
            "success": False,
            "fromVersion": None,
            "toVersion": CURRENT_DATABASE_SCHEME_VERSION,
            "backupFilePath": None,
            "errorMessage": None
        }

        if (self._databaseSettings == None or self._databaseSettings.useExternal == False):
            result["errorMessage"] = "Scheme upgrade via settings is only needed for external databases. Local databases are upgraded automatically."
            return result

        try:
            connected = self.connectoToDatabase(sendErrorPopUp=False)
            if (connected == False):
                errorMessage = "Could not connect to the external database."
                if (self._currentErrorMessageDict != None):
                    errorMessage = errorMessage + " " + str(self._currentErrorMessageDict.get("message", ""))
                result["errorMessage"] = errorMessage
                return result

            try:
                currentSchemeVersion = int(PluginMetaDataModel.get(
                    PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value)
            except Exception as e:
                result["errorMessage"] = "Could not read the database scheme version: " + str(e)
                return result

            result["fromVersion"] = currentSchemeVersion

            if (currentSchemeVersion == CURRENT_DATABASE_SCHEME_VERSION):
                result["success"] = True
                return result
            if (currentSchemeVersion > CURRENT_DATABASE_SCHEME_VERSION):
                result["errorMessage"] = "Database scheme version " + str(currentSchemeVersion) + \
                                         " is newer than this plugin supports (" + str(CURRENT_DATABASE_SCHEME_VERSION) + \
                                         "). Please update the plugin instead."
                return result
            if (currentSchemeVersion < 7):
                # the legacy migrations (1..7) use SQLite-specific scripts and can't run against external databases
                result["errorMessage"] = "Upgrades from scheme version " + str(currentSchemeVersion) + \
                                         " are not supported for external databases. Please migrate manually via SQL console."
                return result

            # mandatory backup, the upgrade is aborted if it fails
            if (self._isExternalMySQL() == False):
                result["errorMessage"] = "No dump backup is available for this database type. " \
                                         "Please create a backup manually (e.g. pg_dump) and migrate via SQL console."
                return result
            if (createBackupFile == True):
                try:
                    dumpText = self._generateMySQLDumpText()
                    now = datetime.datetime.now()
                    backupFileName = "spoolmanager_external_backup-V" + str(currentSchemeVersion) + "-" + now.strftime("%Y%m%d-%H%M") + ".sql"
                    backupFilePath = os.path.join(self._databaseSettings.baseFolder, backupFileName)
                    with open(backupFilePath, "w") as backupFile:
                        backupFile.write(dumpText)
                    result["backupFilePath"] = backupFilePath
                    self._logger.info("Created external database backup dump '" + backupFilePath + "'")
                except Exception as e:
                    self._logger.exception("Could not create the backup dump, scheme upgrade aborted")
                    result["errorMessage"] = "Could not create the backup dump, scheme upgrade aborted: " + str(e)
                    return result
            else:
                self._logger.info("Backup dump was already downloaded by the frontend, skipping backup file creation.")

            self._logger.info("Upgrading external database scheme from '" + str(currentSchemeVersion) + "' to '" + str(CURRENT_DATABASE_SCHEME_VERSION) + "'")
            self._upgradeDatabase(currentSchemeVersion, CURRENT_DATABASE_SCHEME_VERSION)
            self._logger.info("...external database scheme successfully upgraded.")
            self._schemeUpgradeNeeded = False
            result["success"] = True
        except Exception as e:
            self._logger.exception("upgradeExternalDatabaseScheme")
            result["errorMessage"] = str(e)
        finally:
            self.closeDatabase()

        return result

    def importMySQLDatabaseDump(self, dumpText, importMode):

        result = {
            "success": False,
            "executedStatementCount": 0,
            "importedSpoolCount": 0,
            "errorMessage": None
        }

        if (self._isExternalMySQL() == False):
            result["errorMessage"] = "Database dump import is only supported for external MySQL databases."
            return result

        # - validate the marker, only self-generated dumps are accepted (see comment above SQL_DUMP_MARKER)
        firstContentLine = None
        for line in dumpText.splitlines():
            if (line.strip() != ""):
                firstContentLine = line.strip()
                break
        if (firstContentLine != self.SQL_DUMP_MARKER):
            result["errorMessage"] = "Not a SpoolManager dump file. Only dumps exported by this plugin can be imported."
            return result

        # - validate the database scheme version (there is no MySQL scheme-upgrade path)
        schemeVersionMatch = re.search(r"^-- schemeVersion: (\d+)$", dumpText, re.MULTILINE)
        if (schemeVersionMatch == None):
            result["errorMessage"] = "Dump file has no schemeVersion header."
            return result
        dumpSchemeVersion = int(schemeVersionMatch.group(1))
        if (dumpSchemeVersion != CURRENT_DATABASE_SCHEME_VERSION):
            result["errorMessage"] = "Dump has database scheme version " + str(dumpSchemeVersion) + \
                                     ", but the plugin requires version " + str(CURRENT_DATABASE_SCHEME_VERSION) + \
                                     ". Export and import with matching plugin versions."
            return result

        statements = self._splitSQLDumpStatements(dumpText)
        if (len(statements) == 0):
            result["errorMessage"] = "Dump file contains no SQL statements."
            return result

        # - validate all statements against the whitelist before executing anything
        allowedTableNames = [model._meta.table_name for model in MODELS]
        for index, statement in enumerate(statements):
            if (self._isAllowedDumpStatement(statement, allowedTableNames) == False):
                result["errorMessage"] = "Statement #" + str(index + 1) + " is not allowed. " \
                                         "Only SET NAMES, DROP/CREATE/INSERT for the SpoolManager tables are accepted."
                return result

        spoolTableName = SpoolModel._meta.table_name
        spoolInsertPrefix = "INSERT INTO `" + spoolTableName + "` ("

        try:
            connected = self.connectoToDatabase(sendErrorPopUp=False)
            if (connected == False):
                errorMessage = "Could not connect to the external MySQL database."
                if (self._currentErrorMessageDict != None):
                    errorMessage = errorMessage + " " + str(self._currentErrorMessageDict.get("message", ""))
                result["errorMessage"] = errorMessage
                return result

            if ("replace" == importMode):
                # DROP/CREATE force implicit commits in MySQL, so no single wrapping transaction is possible
                for index, statement in enumerate(statements):
                    try:
                        self._database.execute_sql(statement)
                        result["executedStatementCount"] = result["executedStatementCount"] + 1
                        if (statement.startswith(spoolInsertPrefix)):
                            result["importedSpoolCount"] = result["importedSpoolCount"] + 1
                    except Exception as e:
                        # only the statement number is logged, INSERT contents could include user data
                        self._logger.exception("importMySQLDatabaseDump: statement #" + str(index + 1) + " failed")
                        result["errorMessage"] = "Statement #" + str(index + 1) + " failed: " + str(e) + \
                                                 ". The database might be partially restored. " \
                                                 "Use 'ReCreate Database' and import again."
                        return result
            else:
                # append: only spool-INSERTs, without databaseId so that new ids are generated
                insertStatements = []
                for statement in statements:
                    if (statement.startswith(spoolInsertPrefix)):
                        rewrittenStatement = self._stripDatabaseIdFromInsertStatement(statement)
                        if (rewrittenStatement == None):
                            result["errorMessage"] = "Unexpected INSERT format, could not strip databaseId."
                            return result
                        insertStatements.append(rewrittenStatement)
                if (len(insertStatements) == 0):
                    result["errorMessage"] = "Dump file contains no spools to append."
                    return result

                with self._database.atomic():
                    for statement in insertStatements:
                        self._database.execute_sql(statement)
                result["executedStatementCount"] = len(insertStatements)
                result["importedSpoolCount"] = len(insertStatements)

            result["success"] = True
            return result
        except Exception as e:
            self._logger.exception("importMySQLDatabaseDump")
            result["errorMessage"] = str(e)
            return result
        finally:
            self.closeDatabase()

    def _splitSQLDumpStatements(self, dumpText):
        # line-based splitting is safe, because the self-generated dump never
        # contains literal newlines inside value literals (pymysql escaping)
        statements = []
        currentLines = []
        for line in dumpText.splitlines():
            strippedLine = line.strip()
            if (len(currentLines) == 0 and (strippedLine == "" or strippedLine.startswith("--"))):
                continue
            currentLines.append(line)
            if (strippedLine.endswith(";")):
                statements.append("\n".join(currentLines).strip())
                currentLines = []
        return statements

    def _isAllowedDumpStatement(self, statement, allowedTableNames):
        if ("SET NAMES utf8mb4;" == statement):
            return True
        for tableName in allowedTableNames:
            if (statement.startswith("DROP TABLE IF EXISTS `" + tableName + "`;")):
                return True
            if (statement.startswith("CREATE TABLE `" + tableName + "` (")):
                return True
            if (statement.startswith("INSERT INTO `" + tableName + "` (")):
                return True
        return False

    def _stripDatabaseIdFromInsertStatement(self, statement):
        # expected single-line format: INSERT INTO `table` (`databaseId`, ...) VALUES (123, ...);
        insertMatch = re.match(r"^INSERT INTO (`[^`]+`) \(`databaseId`, (.+)\) VALUES \(\d+, (.+)\);$", statement)
        if (insertMatch == None):
            return None
        return "INSERT INTO " + insertMatch.group(1) + " (" + insertMatch.group(2) + ") " \
               "VALUES (" + insertMatch.group(3) + ");"

    ################################################################################################ DATABASE OPERATIONS
    def _handleReusableConnection(self, databaseCallMethode, withReusedConnection, methodeNameForLogging, defaultReturnValue=None):
        try:
            if (withReusedConnection == True):
                if (self._isConnected == False):
                    self._logger.error("Database not connected. Check database-settings!")
                    return defaultReturnValue
            else:
                self.connectoToDatabase()
            return databaseCallMethode()
        except Exception as e:
            errorMessage = "Database call error in methode " + methodeNameForLogging
            self._logger.exception(errorMessage)

            # the startup check goes stale when the database is replaced at runtime, so re-evaluate;
            # the connection from this call is still open at this point
            try:
                self.recheckSchemeUpgradeNeeded(withReusedConnection=True)
            except Exception:
                pass

            if (self._schemeUpgradeNeeded == True):
                # most likely cause: the model expects columns the old scheme doesn't have yet
                self._passMessageToClient("error",
                                          "DatabaseManager",
                                          "The database scheme is outdated. "
                                          "Open Plugin Settings -> SpoolManager -> Storage and press 'Upgrade database scheme'!")
            else:
                self._passMessageToClient("error",
                                          "DatabaseManager",
                                          errorMessage + ". See OctoPrint.log for details!")
            return defaultReturnValue
        finally:
            try:
                if (withReusedConnection == False):
                    self._closeDatabase()
            except:
                pass # do nothing
        pass

    def loadDatabaseMetaInformations(self, databaseSettings = None):

        backupCurrentDatabaseSettings = None
        if (databaseSettings != None):
            backupCurrentDatabaseSettings = self._databaseSettings
        else:
            # use default settings
            databaseSettings = self._databaseSettings
            backupCurrentDatabaseSettings = self._databaseSettings
        # filelocation
        # backupname
        # scheme version
        # spoolitem count
        schemeVersionFromPlugin = CURRENT_DATABASE_SCHEME_VERSION
        localSchemeVersionFromDatabaseModel = "-"
        localSpoolItemCount = "-"
        externalSchemeVersionFromDatabaseModel = "-"
        externalSpoolItemCount = "-"
        errorMessage = ""
        loadResult = False
        # - save current DatbaseSettings
        # currentDatabaseSettings = self._databaseSettings
        # currentDatabase = self._database
        # externalConnected = False
        # always read local meta data
        try:
            currentDatabaseType = databaseSettings.type
            currentUseExternal = databaseSettings.useExternal

            # First load meta from local sqlite database
            databaseSettings.type = "sqlite"
            databaseSettings.baseFolder = self._databaseSettings.baseFolder
            databaseSettings.fileLocation = self._databaseSettings.fileLocation
            databaseSettings.useExternal = False
            self._databaseSettings = databaseSettings
            try:
                self.connectoToDatabase( sendErrorPopUp=False)
                localSchemeVersionFromDatabaseModel = PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value
                localSpoolItemCount = self.countSpoolsByQuery()
                self.closeDatabase()
            except Exception as e:
                errorMessage = "local database: " + str(e)
                self._logger.error("Connecting to local database not possible")
                self._logger.exception(e)
                try:
                    self.closeDatabase()
                except Exception:
                    pass  # ignore close exception

            # Use origin Database type to collect the other metadata (if needed)
            databaseSettings.type = currentDatabaseType
            databaseSettings.useExternal = currentUseExternal
            if (databaseSettings.useExternal == True):
                # External DB
                self._databaseSettings = databaseSettings
                self.connectoToDatabase(sendErrorPopUp=False)
                externalSchemeVersionFromDatabaseModel = PluginMetaDataModel.get(PluginMetaDataModel.key == PluginMetaDataModel.KEY_DATABASE_SCHEME_VERSION).value
                externalSpoolItemCount = self.countSpoolsByQuery()
                self.closeDatabase()
            loadResult = True
        except Exception as e:
            errorMessage = str(e)
            self._logger.exception(e)
            try:
                self.closeDatabase()
            except Exception:
                pass #ignore close exception
        finally:
            # restore orig. database settings
            if (backupCurrentDatabaseSettings != None):
                self._databaseSettings = backupCurrentDatabaseSettings

        return {
            "success": loadResult,
            "errorMessage": errorMessage,
            "schemeVersionFromPlugin": schemeVersionFromPlugin,
            "localSchemeVersionFromDatabaseModel": localSchemeVersionFromDatabaseModel,
            "localSpoolItemCount": localSpoolItemCount,
            "externalSchemeVersionFromDatabaseModel": externalSchemeVersionFromDatabaseModel,
            "externalSpoolItemCount": externalSpoolItemCount
        }

    def loadFirstSingleSpool(self, withReusedConnection=False):
        def databaseCallMethode():
            return SpoolModel.select().limit(1)[0]

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadFirstSingleSpool")


    def loadSpool(self, databaseId, withReusedConnection=False):
        def databaseCallMethode():
            try:
                result = SpoolModel.get_by_id(databaseId)
            except DoesNotExist:
                return None
            return result

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadSpool")

    def loadSpoolTemplates(self, withReusedConnection=False):
        def databaseCallMethode():
            return SpoolModel.select().where(SpoolModel.isTemplate == True)

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadSpoolTemplates")

    def getMaxSpoolDatabaseId(self, withReusedConnection=False):
        def databaseCallMethode():
            return SpoolModel.select(fn.MAX(SpoolModel.databaseId)).scalar()

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "getMaxSpoolDatabaseId")

    def loadAllSpoolsByQuery(self, tableQuery = None, withReusedConnection = False):

        def databaseCallMethode():
            if (tableQuery == None):
                return SpoolModel.select().order_by(SpoolModel.created.desc())

            sortColumn = tableQuery["sortColumn"]
            sortOrder = tableQuery["sortOrder"]
            filterName = tableQuery["filterName"]

            if ("selectedPageSize" in tableQuery and StringUtils.to_native_str(tableQuery["selectedPageSize"]) == "all"):
                myQuery = SpoolModel.select()
            else:
                offset = int(tableQuery["from"])
                limit = int(tableQuery["to"])
                myQuery = SpoolModel.select().offset(offset).limit(limit)

            myQuery = self._applyTableQueryFilters(myQuery, tableQuery)

            # mySqlText = myQuery.sql()

            if ("onlyTemplates" in filterName):
                myQuery = myQuery.where( (SpoolModel.isTemplate == True) )
            else:
                if ("noTemplates" in filterName):
                    myQuery = myQuery.where( (SpoolModel.isTemplate == False) | (SpoolModel.isTemplate == None) )
                if ("hideEmptySpools" in filterName):
                    myQuery = myQuery.where( (SpoolModel.remainingWeight > 0) | (SpoolModel.remainingWeight == None))
                if ("hideInactiveSpools" in filterName):
                    myQuery = myQuery.where( (SpoolModel.isActive == True) )

            if ("displayName" == sortColumn):
                if ("desc" == sortOrder):
                    myQuery = myQuery.order_by(fn.Lower(SpoolModel.displayName).desc())
                else:
                    myQuery = myQuery.order_by(fn.Lower(SpoolModel.displayName).asc())
            if ("lastUse" == sortColumn):
                if ("desc" == sortOrder):
                    myQuery = myQuery.order_by(SpoolModel.lastUse.desc())
                else:
                    myQuery = myQuery.order_by(SpoolModel.lastUse.asc())
            if ("firstUse" == sortColumn):
                if ("desc" == sortOrder):
                    myQuery = myQuery.order_by(SpoolModel.firstUse.desc())
                else:
                    myQuery = myQuery.order_by(SpoolModel.firstUse.asc())
            if ("remaining" == sortColumn):
                if ("desc" == sortOrder):
                    myQuery = myQuery.order_by(SpoolModel.remainingWeight.desc())
                else:
                    myQuery = myQuery.order_by(SpoolModel.remainingWeight.asc())
            if ("material" == sortColumn):
                if ("desc" == sortOrder):
                    myQuery = myQuery.order_by(SpoolModel.material.desc())
                else:
                    myQuery = myQuery.order_by(SpoolModel.material.asc())

                    self._logger.info("Quering spools: %s" % myQuery)
            return myQuery

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadAllSpoolsByQuery")

    def _applyTableQueryFilters(self, myQuery, tableQuery):
        if (tableQuery == None):
            return myQuery

        filterName = StringUtils.to_native_str(tableQuery.get("filterName", ""))

        if ("materialFilter" in tableQuery):
            materialFilter = tableQuery["materialFilter"]
            vendorFilter = tableQuery["vendorFilter"]
            colorFilter = tableQuery["colorFilter"]

            # materialFilter
            # u'ABS,PLA'
            # u''
            # u'all'
            materialFilter = StringUtils.to_native_str(materialFilter)
            if (materialFilter != "all"):
                if (StringUtils.isEmpty(colorFilter)):
                    myQuery = myQuery.where( (SpoolModel.material == '') )
                else:
                    allMaterials = materialFilter.split(",")
                    myQuery = myQuery.where(SpoolModel.material.in_(allMaterials))

            # vendorFilter
            # u'MatterMost,TheFactory'
            # u''
            # u'all'
            vendorFilter = StringUtils.to_native_str(vendorFilter)
            if (vendorFilter != "all"):
                if (StringUtils.isEmpty(vendorFilter)):
                    myQuery = myQuery.where( (SpoolModel.vendor == '') )
                else:
                    allVendors = vendorFilter.split(",")
                    myQuery = myQuery.where(SpoolModel.vendor.in_(allVendors))

            # colorFilter
            # u'#ff0000;red,#ff0000;keinRot,#ff0000;deinRot,#ff0000;meinRot,#ffff00;yellow'
            # u''
            # u'all'
            colorFilter = StringUtils.to_native_str(colorFilter)
            if (colorFilter != "all" and StringUtils.isNotEmpty(colorFilter)):
                allColorObjects = colorFilter.split(",")
                allColors = []
                allColorNames = []
                for colorObject in allColorObjects:
                    colorCodeColorName = colorObject.split(";")
                    color = colorCodeColorName[0]
                    colorName = colorCodeColorName[1]
                    allColors.append(color)
                    allColorNames.append(colorName)
                myQuery = myQuery.where(SpoolModel.color.in_(allColors))
                myQuery = myQuery.where(SpoolModel.colorName.in_(allColorNames))

        # Text search (issue #22 / #44 preparation): case-insensitive match across
        # the same user-facing fields as sidebar select plus optional note text.
        textFilter = StringUtils.to_native_str(tableQuery.get("textFilter", "")).strip().lower()
        if (StringUtils.isNotEmpty(textFilter)):
            myQuery = myQuery.where(
                (fn.Lower(SpoolModel.material).contains(textFilter)) |
                (fn.Lower(SpoolModel.vendor).contains(textFilter)) |
                (fn.Lower(SpoolModel.displayName).contains(textFilter)) |
                (fn.Lower(SpoolModel.colorName).contains(textFilter)) |
                (fn.Lower(SpoolModel.noteText).contains(textFilter))
            )

        if ("onlyTemplates" in filterName):
            myQuery = myQuery.where( (SpoolModel.isTemplate == True) )
        else:
            if ("noTemplates" in filterName):
                myQuery = myQuery.where( (SpoolModel.isTemplate == False) | (SpoolModel.isTemplate == None) )
            if ("hideEmptySpools" in filterName):
                myQuery = myQuery.where( (SpoolModel.remainingWeight > 0) | (SpoolModel.remainingWeight == None))
            if ("hideInactiveSpools" in filterName):
                myQuery = myQuery.where( (SpoolModel.isActive == True) )

        return myQuery

    def saveSpool(self, spoolModel, withReusedConnection=False):

        def databaseCallMethode():
            # databaseId = model.get_id()
            # if (databaseId != None):
            # 	# we need to update and we need to make
            # 	spoolModel = self.loadSpool(databaseId)
            # 	if (spoolModel == None):
            # 		self._passMessageToClient("error", "DatabaseManager",
            # 								  "Could not update the Spool, because it is already deleted!")
            # 		return
            # 	else:
            # 		versionFromUI = model.version if model.version != None else 1
            # 		versionFromDatabase = spoolModel.version if spoolModel.version != None else 1
            # 		if (versionFromUI != versionFromDatabase):
            # 			self._passMessageToClient("error", "DatabaseManager",
            # 									  "Could not update the Spool, because someone already modified the spool. Do a reload!")
            # 			return

            with self._database.atomic() as transaction:  # Opens new transaction.
                try:
                    databaseId = spoolModel.databaseId
                    if (databaseId != None):
                        versionFromUI = None
                        # we need to update and we need to make sure nobody else modify the data
                        currentSpoolModel = self.loadSpool(databaseId, withReusedConnection)
                        if (currentSpoolModel == None):
                            self._passMessageToClient("error", "DatabaseManager",
                                                      "Could not update the Spool, because it is already deleted!")
                            return
                        else:
                            versionFromUI = spoolModel.version if spoolModel.version != None else 1
                            versionFromDatabase = currentSpoolModel.version if currentSpoolModel.version != None else 1
                            if (versionFromUI != versionFromDatabase):
                                self._passMessageToClient("error", "DatabaseManager",
                                                          "Could not update the Spool, because someone already modified the spool. Do a manuel reload!")
                                return
                        # okay fits, increate version
                        newVersion = versionFromUI + 1
                        spoolModel.version = newVersion

                    # Not needed any more, we have multi-temlates
                    # if (spoolModel.isTemplate == True):
                    # 	#  remove template flag from last templateSpool
                    # 	SpoolModel.update({SpoolModel.isTemplate: False}).where(SpoolModel.isTemplate == True).execute()

                    spoolModel.save()
                    databaseId = spoolModel.get_id()
                    # do expicit commit
                    transaction.commit()
                except Exception as e:
                    # Because this block of code is wrapped with "atomic", a
                    # new transaction will begin automatically after the call
                    # to rollback().
                    transaction.rollback()
                    self._logger.exception("Could not insert Spool into database")

                    self._passMessageToClient("error", "DatabaseManager", "Could not insert the spool into the database. See OctoPrint.log for details!")
                pass

            return databaseId

        # always recalculate the remaing weight (total - used)
        totalWeight = spoolModel.totalWeight
        usedWeight = spoolModel.usedWeight
        if (totalWeight != None):
            if (usedWeight == None):
                usedWeight = 0.0
            remainingWeight = Transformer.calculateRemainingWeight(usedWeight, totalWeight)
            spoolModel.remainingWeight = remainingWeight

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "saveSpool")

    def countSpoolsByQuery(self, tableQuery=None, withReusedConnection=False):
        def databaseCallMethode():
            myQuery = SpoolModel.select()
            myQuery = self._applyTableQueryFilters(myQuery, tableQuery)
            return myQuery.count()

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "countSpoolsByQuery")

    def loadCatalogVendors(self, withReusedConnection=False):
        def databaseCallMethode():
            result = set()
            result.add("")
            myQuery = SpoolModel.select(SpoolModel.vendor).distinct()
            for spool in myQuery:
                value = spool.vendor
                if (value != None):
                    result.add(value)
            return result;

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadCatalogVendors", set())

    def loadCatalogMaterials(self, withReusedConnection=False):
        def databaseCallMethode():
            result = set()
            myQuery = SpoolModel.select(SpoolModel.material).distinct()
            for spool in myQuery:
                value = spool.material
                if (value != None):
                    result.add(value)
            return result;

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadCatalogMaterials", set())

    def loadCatalogLabels(self, tableQuery, withReusedConnection=False):
        def databaseCallMethode():
            result = set()
            myQuery = SpoolModel.select(SpoolModel.labels).distinct()
            for spool in myQuery:
                value = spool.labels
                if (value != None):
                    spoolLabels = json.loads(value)
                    for singleLabel in spoolLabels:
                        result.add(singleLabel)
            return result

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadCatalogLabels", set())

    def loadCatalogColors(self, withReusedConnection=False):
        def databaseCallMethode():
            result = []
            myQuery = SpoolModel.select(SpoolModel.color, SpoolModel.colorName).distinct()
            for spool in myQuery:
                if (spool.color != None and spool.colorName):
                    colorInfo = {
                        "colorId": spool.color + ";" + spool.colorName,
                        "color": spool.color,
                        "colorName": spool.colorName
                    }
                    result.append(colorInfo)
            return result

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "loadCatalogColors", set())

    def deleteSpool(self, databaseId, withReusedConnection=False):
        def databaseCallMethode():
            with self._database.atomic() as transaction:  # Opens new transaction.
                try:
                    # first delete relations
                    # n = FilamentModel.delete().where(FilamentModel.printJob == databaseId).execute()
                    # n = TemperatureModel.delete().where(TemperatureModel.printJob == databaseId).execute()

                    deleteResult = SpoolModel.delete_by_id(databaseId)
                    if (deleteResult == 0):
                        return None
                    return databaseId
                    pass
                except Exception as e:
                    # Because this block of code is wrapped with "atomic", a
                    # new transaction will begin automatically after the call
                    # to rollback().
                    transaction.rollback()
                    self._logger.exception("Could not delete spool from database:" + str(e))

                    self._passMessageToClient("Spool-DatabaseManager", "Could not delete the spool ('"+ str(databaseId) +"') from the database. See OctoPrint.log for details!")
                    return None
                pass

        return self._handleReusableConnection(databaseCallMethode, withReusedConnection, "deleteSpool")

