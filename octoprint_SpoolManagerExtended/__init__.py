# coding=utf-8

import json
import math
import os
import shutil
import socket
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from urllib.parse import quote
from urllib.request import pathname2url

import flask
import octoprint.plugin
from octoprint.access.permissions import Permissions
from octoprint.events import Events
from octoprint.filemanager.destinations import FileDestinations

# FileDestinations.PRINTER only exists on OctoPrint 2.0; on 1.x the attribute is absent and
# reading it raises. The wire value is "printer" in both, so resolve it once here.
PRINTER_DESTINATION = getattr(FileDestinations, "PRINTER", "printer")

from octoprint_SpoolManagerExtended.api import Transformer
from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.common import StringUtils
from octoprint_SpoolManagerExtended.common.EventBusKeys import EventBusKeys
from octoprint_SpoolManagerExtended.common.FilamentDatabaseService import (
    FilamentDatabaseService,
)
from octoprint_SpoolManagerExtended.common import FilamentTagConstants
from octoprint_SpoolManagerExtended.common.TigerTagIdService import TigerTagIdService
from octoprint_SpoolManagerExtended.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManagerExtended.DatabaseManager import (
    DATABASE_FILE_NAME,
    DatabaseManager,
)
from octoprint_SpoolManagerExtended.MqttManager import MqttManager
from octoprint_SpoolManagerExtended.U1RfidManager import U1RfidManager
from octoprint_SpoolManagerExtended.newodometer import NewFilamentOdometer

# sentinel distinguishing "never announced" from "known to be empty (None)" in
# _lastAnnouncedSpoolIds, so the very first deselect of a tool is not swallowed
_SPOOL_SELECTION_NOT_YET_ANNOUNCED = object()

# Identifier this plugin used before it was renamed to "SpoolManagerExtended". Both the
# data folder (~/.octoprint/data/<identifier>/) and the settings namespace
# (plugins.<identifier>) are derived from it, so an existing install is only reachable
# under the old name - see _performLegacyMigration().
LEGACY_IDENTIFIER = "SpoolManager"

# Records what a migration overwrote, so it can be taken back. One file per migration
# kind, so the database and the settings can be undone independently - they are separate
# actions in separate tabs, and undoing one must not silently drop the other's record.
# Their presence is what enables the respective "Undo" button.
LEGACY_UNDO_FILE_NAMES = {
    "database": "legacy-migration-undo-database.json",
    "settings": "legacy-migration-undo-settings.json",
}

# Settings that must not be offered in the comparison dialog: the selected spool ids point
# at database rows and only make sense together with the database itself, and the version
# describes the installed plugin rather than a user choice.
LEGACY_SETTINGS_NOT_MIGRATABLE = frozenset(
    ["selectedSpoolsDatabaseIds", "installed_version"]
)


class SpoolmanagerPlugin(
    SpoolManagerAPI,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.EventHandlerPlugin,
):

    def initialize(self):
        self._logger.info("Start initializing")

        # cache for filament usage parsed from printer-storage 3mf files: (path, plate) -> (fingerprint, filament)
        self._printer3mfFilamentCache = {}
        # cache for per-tool filament usage read from Moonraker: (host, port, path) -> (filament,)
        self._moonrakerFilamentCache = {}
        # guards against booking the sliced usage twice (connectors may fire PRINT_DONE multiple times)
        self._slicedUsageAlreadyBooked = False
        # own wall clock per print job - connectors may report time=0.0 in the PRINT_DONE payload
        self._printJobStartedTimestamp = None

        # DATABASE
        self.databaseConnectionProblemConfirmed = False
        sqlLoggingEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_SQL_LOGGING_ENABLED]
        )
        self._databaseManager = DatabaseManager(self._logger, sqlLoggingEnabled)

        databaseSettings = self._buildDatabaseSettingsFromPluginSettings()

        # init database
        self._databaseManager.initDatabase(databaseSettings, self._sendMessageToClient)

        # OTHER STUFF
        # self._filamentOdometer = None
        # self._filamentOdometer = FilamentOdometer()
        # TODO no idea what this thing is doing in detail self._filamentOdometer.set_g90_extruder(self._settings.getBoolean(["feature", "g90InfluencesExtruder"]))

        self.myFilamentOdometer = NewFilamentOdometer(self._extrusionValuesChanged)
        self.myFilamentOdometer.set_g90_extruder(
            self._settings.get_boolean(["feature", "g90InfluencesExtruder"])
        )

        self._filamentManagerPluginImplementation = None
        self._filamentManagerPluginImplementationState = None

        self._lastPrintState = None

        # last databaseId (or None) announced via spool_selected/spool_deselected per
        # toolIndex, so _announceSpoolSelectionChange() only fires on real transitions
        self._lastAnnouncedSpoolIds = {}

        self.metaDataFilamentLengths = []

        self.alreadyCanceled = False

        # MQTT (read-only publishing, helper is acquired later in on_after_startup)
        self._mqttManager = MqttManager(self, self._logger)
        # Snapmaker U1 RFID self-reporter (read-only spool selection). The detection
        # chain runs later in on_after_startup, when the printer connection is up.
        self._u1RfidManager = U1RfidManager(self, self._logger)
        self._filamentDatabaseService = FilamentDatabaseService(
            self.get_plugin_data_folder(), self._logger, self._plugin_version
        )
        self._tigerTagIdService = TigerTagIdService(
            self.get_plugin_data_folder(),
            self._logger,
            self._plugin_version,
            is_enabled=lambda: self._settings.get_boolean(
                [SettingsKeys.SETTINGS_KEY_TIGERTAG_IDS_AUTO_UPDATE_ENABLED]
            ),
        )
        FilamentTagConstants.setTigerTagIdService(self._tigerTagIdService)

        self._logger.info("Done initializing")
        pass

    def _getLegacyDataFolder(self):
        """
        Path of the data folder left behind under the plugin's previous identifier
        "SpoolManager" (before the rename to "SpoolManagerExtended"), or None if
        there is none. Pure lookup, no side effects.
        """
        legacyDataFolder = os.path.join(
            self._settings.getBaseFolder("data"), LEGACY_IDENTIFIER
        )
        if not os.path.isdir(legacyDataFolder):
            return None
        return legacyDataFolder

    def _isLegacyMigrationAvailable(self):
        """
        Whether there is anything worth migrating from a previous SpoolManager install.

        Deliberately ignores a legacy folder that only holds the SpoolmanDB/TigerTag
        caches: the old plugin recreates those on its own, so treating them as
        "migratable" would show the migration hint forever with nothing to adopt.
        """
        legacyDataFolder = self._getLegacyDataFolder()
        if legacyDataFolder is not None:
            if os.path.isfile(os.path.join(legacyDataFolder, DATABASE_FILE_NAME)):
                return True

        legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER])
        if legacySettings:
            return True

        return False

    def _databaseHoldsSpools(self, databaseFile):
        """
        Whether the SQLite file at databaseFile contains at least one spool.

        Read directly via sqlite3 rather than through the DatabaseManager: the plugin
        keeps its own database open, and this has to inspect a file that may not be the
        one currently connected. Anything unreadable counts as "no spools" - an
        unusable file is not worth guarding against being replaced.
        """
        if not os.path.isfile(databaseFile):
            return False
        try:
            connection = sqlite3.connect(databaseFile)
            try:
                cursor = connection.execute("SELECT COUNT(*) FROM spo_spoolmodel")
                return cursor.fetchone()[0] > 0
            finally:
                connection.close()
        except Exception:
            self._logger.exception(
                "Could not read '%s', treating it as empty" % databaseFile
            )
            return False

    def _readLegacyDatabasePreview(self, databaseFile, spoolLimit=10):
        """
        Summary of what a legacy database holds, for the confirmation dialog: counts,
        scheme version, total remaining weight and the first few spools by name.

        Opened read-only (sqlite3 URI mode=ro): the file still belongs to the old plugin,
        which may well be running, and a preview must not create a journal next to it or
        touch the file in any way.
        """
        preview = {
            "readable": False,
            "spoolCount": 0,
            "templateCount": 0,
            "schemeVersion": None,
            "totalRemainingWeight": 0.0,
            "spools": [],
            "moreSpools": 0,
        }
        if not os.path.isfile(databaseFile):
            return preview

        try:
            uri = "file:%s?mode=ro" % pathname2url(databaseFile)
            connection = sqlite3.connect(uri, uri=True)
            try:
                # regular spools store NULL in isTemplate, not False - see loadSpoolByCode()
                preview["spoolCount"] = connection.execute(
                    "SELECT COUNT(*) FROM spo_spoolmodel WHERE isTemplate IS NULL OR isTemplate = 0"
                ).fetchone()[0]
                preview["templateCount"] = connection.execute(
                    "SELECT COUNT(*) FROM spo_spoolmodel WHERE isTemplate = 1"
                ).fetchone()[0]
                preview["totalRemainingWeight"] = (
                    connection.execute(
                        "SELECT COALESCE(SUM(remainingWeight), 0) FROM spo_spoolmodel "
                        "WHERE isTemplate IS NULL OR isTemplate = 0"
                    ).fetchone()[0]
                    or 0.0
                )
                for row in connection.execute(
                    "SELECT displayName, material, remainingWeight FROM spo_spoolmodel "
                    "WHERE isTemplate IS NULL OR isTemplate = 0 "
                    "ORDER BY databaseId LIMIT ?",
                    (spoolLimit,),
                ):
                    preview["spools"].append(
                        {
                            "displayName": row[0],
                            "material": row[1],
                            "remainingWeight": row[2],
                        }
                    )
                preview["moreSpools"] = max(
                    0, preview["spoolCount"] - len(preview["spools"])
                )
                try:
                    schemeRow = connection.execute(
                        "SELECT value FROM spo_pluginmetadatamodel "
                        "WHERE key = 'databaseSchemeVersion'"
                    ).fetchone()
                    if schemeRow is not None:
                        preview["schemeVersion"] = schemeRow[0]
                except Exception:
                    # a database without the metadata table is still worth previewing
                    pass
                preview["readable"] = True
            finally:
                connection.close()
        except Exception:
            # Not an error worth failing on: an unreadable file simply cannot be
            # summarised, and the dialog says so while still offering the copy.
            self._logger.exception("Could not read legacy database '%s'" % databaseFile)

        return preview

    def _classifyLegacyFile(self, entryName):
        """
        What kind of file an entry in the legacy data folder is, which decides whether the
        dialog preselects it: the database is the point of the exercise, caches rebuild
        themselves (the SpoolmanDB index alone is ~10 MB), backups are the user's call.
        """
        if entryName == DATABASE_FILE_NAME:
            return "database"
        if entryName.endswith("_index.json") or entryName.endswith("_installation_id"):
            return "cache"
        if (
            entryName.startswith("spoolmanager-backup")
            or entryName.startswith("spoolmanager_external_backup")
            or entryName.startswith("SpoolManager-backup")
            or entryName.endswith(".sql")
            or entryName.endswith(".csv")
        ):
            return "backup"
        return "other"

    def _getLegacyFileEntries(self):
        """Entries of the legacy data folder, annotated for the migration dialog."""
        legacyDataFolder = self._getLegacyDataFolder()
        if legacyDataFolder is None:
            return []

        entries = []
        for entryName in sorted(os.listdir(legacyDataFolder)):
            path = os.path.join(legacyDataFolder, entryName)
            kind = self._classifyLegacyFile(entryName)
            try:
                size = (
                    sum(
                        os.path.getsize(os.path.join(root, name))
                        for root, _dirs, files in os.walk(path)
                        for name in files
                    )
                    if os.path.isdir(path)
                    else os.path.getsize(path)
                )
            except OSError:
                size = 0
            entries.append(
                {
                    "name": entryName,
                    "kind": kind,
                    "size": size,
                    "isDirectory": os.path.isdir(path),
                    # only the database is worth taking by default
                    "preselected": kind == "database",
                }
            )
        return entries

    def _getUndoFilePath(self, undoKind):
        return os.path.join(
            self.get_plugin_data_folder(), LEGACY_UNDO_FILE_NAMES[undoKind]
        )

    def _isLegacyMigrationUndoAvailable(self, undoKind):
        return os.path.isfile(self._getUndoFilePath(undoKind))

    def _writeUndoRecord(self, undoKind, replacedFiles, previousSettings):
        """
        Records what a migration overwrote so it can be taken back. One step per kind:
        the previous database migration and the previous settings migration can each be
        undone, but not a whole chain - more levels would just litter the data folder,
        and the legacy folder itself is the real way back.
        """
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "replacedFiles": replacedFiles,
            "previousSettings": previousSettings,
        }
        try:
            with open(self._getUndoFilePath(undoKind), "w") as undoFile:
                json.dump(record, undoFile, indent=2)
        except Exception:
            # Losing the undo record must not fail the migration itself - the data is
            # already copied at this point, and the legacy folder is still intact.
            self._logger.exception("Could not write the migration undo record")

    def _isLegacyMigrationDone(self):
        """
        Whether a migration has already run. Used to retire the banner: the legacy folder
        is deliberately kept (we copy, never move), so its mere presence would keep the
        hint up forever - even for someone who migrated everything on day one.

        An undo brings the banner back, which is the point: at that moment there really
        is something left to migrate again.
        """
        return any(
            self._isLegacyMigrationUndoAvailable(undoKind)
            for undoKind in LEGACY_UNDO_FILE_NAMES
        )

    def _hasLegacySettings(self):
        """
        Whether the old install has a settings namespace at all - the settings migration
        button is pointless without one, even when a legacy database is present.
        """
        return bool(self._settings.global_get(["plugins", LEGACY_IDENTIFIER]))

    def _performLegacyMigration(
        self, overwriteExisting=False, includeSettings=True, fileNames=None
    ):
        """
        Copies data and (optionally) settings of a previous SpoolManager install into this
        plugin's own data folder / settings namespace. Triggered by the user from the
        settings, never automatically - it touches user data.

        The legacy folder is left untouched (copy, not move), so the old install stays
        usable as a fallback. Files that would be overwritten are kept as
        "<name>.pre-migration-<timestamp>" and recorded for undoLegacyMigration().

        fileNames=None copies every entry; a list restricts it to those names.

        Returns a result dict mirroring the shape used by upgradeDatabaseScheme():
        {"success": bool, "errorMessage": str, "conflict": bool, "copiedFiles": int,
         "settingsMigrated": bool, "schemeUpgradeNeeded": bool}
        """

        def failure(errorMessage, conflict=False):
            return {
                "success": False,
                "errorMessage": errorMessage,
                "conflict": conflict,
                "copiedFiles": 0,
                "settingsMigrated": False,
            }

        legacyDataFolder = self._getLegacyDataFolder()
        legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER])

        if legacyDataFolder is None and not legacySettings:
            return failure(
                "No previous SpoolManager installation found. Nothing to migrate."
            )

        newDataFolder = self.get_plugin_data_folder()
        legacyDatabaseFile = (
            os.path.join(legacyDataFolder, DATABASE_FILE_NAME)
            if legacyDataFolder is not None
            else None
        )
        hasLegacyDatabase = legacyDatabaseFile is not None and os.path.isfile(
            legacyDatabaseFile
        )
        # a database only moves when it is actually part of the selection
        databaseIsSelected = hasLegacyDatabase and (
            fileNames is None or DATABASE_FILE_NAME in fileNames
        )

        # Only a database holding actual spools is worth protecting. The plugin creates an
        # empty one on first start, so testing for the file alone would confront every
        # existing user with a data-loss warning that does not apply to them.
        targetDatabaseFile = os.path.join(newDataFolder, DATABASE_FILE_NAME)
        if (
            databaseIsSelected
            and self._databaseHoldsSpools(targetDatabaseFile)
            and not overwriteExisting
        ):
            return failure(
                "This installation already has its own database with spools in it. "
                "Migrating would replace it with the one from the previous "
                "SpoolManager install.",
                conflict=True,
            )

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        replacedFiles = []
        copiedFiles = 0
        try:
            if legacyDataFolder is not None:
                os.makedirs(newDataFolder, exist_ok=True)
                for entryName in os.listdir(legacyDataFolder):
                    if fileNames is not None and entryName not in fileNames:
                        continue
                    sourcePath = os.path.join(legacyDataFolder, entryName)
                    targetPath = os.path.join(newDataFolder, entryName)
                    # keep whatever is about to be replaced, so the undo has something
                    # to put back
                    if os.path.exists(targetPath) and not os.path.isdir(targetPath):
                        backupName = "%s.pre-migration-%s" % (entryName, timestamp)
                        os.rename(targetPath, os.path.join(newDataFolder, backupName))
                        replacedFiles.append(
                            {"name": entryName, "backupName": backupName}
                        )
                    if os.path.isdir(sourcePath):
                        shutil.copytree(sourcePath, targetPath, dirs_exist_ok=True)
                    else:
                        shutil.copy2(sourcePath, targetPath)
                    copiedFiles += 1
                self._logger.info(
                    "Migrated %s entries from '%s' to '%s' (originals kept)"
                    % (copiedFiles, legacyDataFolder, newDataFolder)
                )
        except Exception as e:
            self._logger.exception("Legacy data migration failed")
            return failure("Could not copy the data folder: " + str(e))

        settingsMigrated = False
        previousSettings = {}
        try:
            if includeSettings and legacySettings:
                previousSettings = self._captureSettingsForUndo(legacySettings.keys())
                for key, value in legacySettings.items():
                    self._settings.set([key], value)
                self._settings.save()
                settingsMigrated = True
                self._logger.info(
                    "Migrated settings from 'plugins.%s' to 'plugins.%s'"
                    % (LEGACY_IDENTIFIER, self._identifier)
                )
        except Exception as e:
            self._logger.exception("Legacy settings migration failed")
            return failure("Data was copied, but the settings could not be migrated: " + str(e))

        # Written whenever something was actually migrated, not only when files were
        # replaced: migrating into an empty install overwrites nothing, but it still has
        # to count as done - otherwise the banner would never retire for exactly the
        # users the migration is meant for.
        if copiedFiles or replacedFiles or previousSettings:
            self._writeUndoRecord("database", replacedFiles, previousSettings)

        # The migrated database predates this plugin's scheme version, so an upgrade is
        # the expected next step - reported so the UI can say so instead of looking broken.
        schemeUpgradeNeeded = False
        try:
            if databaseIsSelected:
                schemeUpgradeNeeded = self._databaseManager.isSchemeUpgradeNeeded()
        except Exception:
            self._logger.exception(
                "Could not determine whether a scheme upgrade is needed after migration"
            )

        return {
            "success": True,
            "errorMessage": None,
            "conflict": False,
            "copiedFiles": copiedFiles,
            "settingsMigrated": settingsMigrated,
            "schemeUpgradeNeeded": schemeUpgradeNeeded,
        }

    def _captureSettingsForUndo(self, keys):
        """
        Current value of each key before it is overwritten. A key that has no value of its
        own yet is recorded as None, so the undo removes it again rather than writing a
        value the user never had.
        """
        captured = {}
        for key in keys:
            try:
                captured[key] = self._settings.get([key])
            except Exception:
                captured[key] = None
        return captured

    def _getLegacySettingsKeys(self):
        """
        Every setting the old plugin knows, not just the ones it has stored.

        OctoPrint only writes settings that differ from their default, so a plugin the
        user barely touched stores a handful of keys while knowing dozens - listing only
        the stored ones would hide most of the comparison (currencySymbol and friends
        simply would not appear).

        Read from the old plugin's own SettingsKeys when it is installed; otherwise fall
        back to ours, which is a superset - the old plugin has no key we lack.
        """
        try:
            from octoprint_SpoolManager.common.SettingsKeys import (
                SettingsKeys as LegacySettingsKeys,
            )

            source = LegacySettingsKeys
        except Exception:
            source = SettingsKeys

        return {
            getattr(source, name)
            for name in dir(source)
            if name.startswith("SETTINGS_KEY_")
            and isinstance(getattr(source, name), str)
        }

    def _getLegacySettingsComparison(self):
        """
        Rows for the settings comparison dialog: what the old install has, what this one
        has, and what would apply where nothing is stored.
        """
        legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER]) or {}
        defaults = self.get_settings_defaults()

        # stored values first, then everything else the old plugin knows - a value it
        # never stored still applies over there, and may well differ from ours
        candidateKeys = set(legacySettings.keys()) | self._getLegacySettingsKeys()

        rows = []
        for key in sorted(candidateKeys):
            if key in LEGACY_SETTINGS_NOT_MIGRATABLE:
                continue

            legacyIsStored = key in legacySettings
            # the shared defaults are identical between both plugins, so ours stand in
            # for what the old install actually applies
            legacyValue = (
                legacySettings.get(key) if legacyIsStored else defaults.get(key)
            )

            try:
                currentValue = self._settings.get([key])
            except Exception:
                currentValue = None
            defaultValue = defaults.get(key)
            effectiveValue = currentValue if currentValue is not None else defaultValue

            # a key neither side knows a value for carries no information
            if legacyValue is None and effectiveValue is None:
                continue

            rows.append(
                {
                    "key": key,
                    "legacyValueText": self._formatSettingValue(legacyValue),
                    "legacyIsDefault": not legacyIsStored,
                    "currentValueText": self._formatSettingValue(effectiveValue),
                    "currentIsDefault": currentValue is None,
                    "differs": legacyValue != effectiveValue,
                }
            )
        return rows

    @staticmethod
    def _formatSettingValue(value):
        """Settings as display text, so the dialog does not have to format anything."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, separators=(", ", ": "))
            except Exception:
                return str(value)
        return str(value)

    def _applyLegacySettings(self, keys):
        """Writes only the named keys from the legacy namespace into this plugin's own."""
        legacySettings = self._settings.global_get(["plugins", LEGACY_IDENTIFIER]) or {}
        defaults = self.get_settings_defaults()
        knownKeys = self._getLegacySettingsKeys()

        # A key the old plugin knows but never stored still has a value over there - its
        # default - and the dialog offers it, so applying it has to work too.
        selectedValues = {}
        for key in keys:
            if key in LEGACY_SETTINGS_NOT_MIGRATABLE:
                continue
            if key in legacySettings:
                selectedValues[key] = legacySettings[key]
            elif key in knownKeys and key in defaults:
                selectedValues[key] = defaults[key]

        selected = sorted(selectedValues.keys())
        if not selected:
            return {
                "success": False,
                "errorMessage": "No settings were selected.",
                "appliedCount": 0,
            }

        try:
            previousSettings = self._captureSettingsForUndo(selected)
            for key in selected:
                self._settings.set([key], selectedValues[key])
            self._settings.save()
            self._writeUndoRecord("settings", [], previousSettings)
        except Exception as e:
            self._logger.exception("Applying legacy settings failed")
            return {
                "success": False,
                "errorMessage": "Could not apply the settings: " + str(e),
                "appliedCount": 0,
            }

        self._logger.info(
            "Applied %s setting(s) from 'plugins.%s'" % (len(selected), LEGACY_IDENTIFIER)
        )
        return {"success": True, "errorMessage": None, "appliedCount": len(selected)}

    def _undoLegacyMigration(self, undoKind):
        """
        Puts back what the named migration replaced: the saved files and the settings
        values it overwrote. Keys that had no value before are removed again.

        The two kinds are independent - undoing the settings migration leaves a database
        migration untouched, and the other way round.
        """
        undoFilePath = self._getUndoFilePath(undoKind)
        if not os.path.isfile(undoFilePath):
            return {
                "success": False,
                "errorMessage": "There is nothing to undo.",
                "restoredFiles": 0,
                "restoredSettings": 0,
            }

        try:
            with open(undoFilePath) as undoFile:
                record = json.load(undoFile)
        except Exception as e:
            self._logger.exception("Could not read the migration undo record")
            return {
                "success": False,
                "errorMessage": "Could not read the undo record: " + str(e),
                "restoredFiles": 0,
                "restoredSettings": 0,
            }

        dataFolder = self.get_plugin_data_folder()
        restoredFiles = 0
        try:
            for entry in record.get("replacedFiles", []):
                backupPath = os.path.join(dataFolder, entry["backupName"])
                targetPath = os.path.join(dataFolder, entry["name"])
                if not os.path.isfile(backupPath):
                    continue
                if os.path.exists(targetPath):
                    os.remove(targetPath)
                os.rename(backupPath, targetPath)
                restoredFiles += 1
        except Exception as e:
            self._logger.exception("Restoring the replaced files failed")
            return {
                "success": False,
                "errorMessage": "Could not restore the files: " + str(e),
                "restoredFiles": restoredFiles,
                "restoredSettings": 0,
            }

        restoredSettings = 0
        try:
            previousSettings = record.get("previousSettings", {})
            for key, value in previousSettings.items():
                # None means "had no value of its own" - set(None) makes OctoPrint drop
                # the key again, which is exactly the state we are restoring
                self._settings.set([key], value)
                restoredSettings += 1
            if previousSettings:
                self._settings.save()
        except Exception as e:
            self._logger.exception("Restoring the previous settings failed")
            return {
                "success": False,
                "errorMessage": "Files were restored, but the settings were not: " + str(e),
                "restoredFiles": restoredFiles,
                "restoredSettings": restoredSettings,
            }

        try:
            os.remove(undoFilePath)
        except OSError:
            self._logger.exception("Could not remove the migration undo record")

        self._logger.info(
            "Undid the last migration: %s file(s), %s setting(s) restored"
            % (restoredFiles, restoredSettings)
        )
        return {
            "success": True,
            "errorMessage": None,
            "restoredFiles": restoredFiles,
            "restoredSettings": restoredSettings,
        }

    ################################################################################################### public functions

    def checkRemainingFilament(self, forToolIndex=None, shouldWarn=True):
        """
        Checks if all spools or single spool includes enough filament
        :param forToolIndex check only for the provided toolIndex
        :return: see
        @param shouldWarn: can be set to 'False' if warnings should not be sent to the user
        """
        shouldWarn = shouldWarn and self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH]
        )

        # - check, if spool change in pause-mode

        # - check if new spool fits for current printjob
        selectedSpools = self.loadSelectedSpools()

        requiredWeightResult = self._evaluateRequiredWeight(
            selectedSpools, forToolIndex, shouldWarn
        )
        # "metaDataMissing": metaDataMissing,
        # "warnUser": fromPluginSettings,
        # "attributesMissing": someAttributesMissing,
        # "notEnough": notEnough,
        # "detailedSpoolResult": [
        #               "toolIndex": toolIndex,
        #               "requiredWeight": requiredWeight,
        #               "requiredLength": filamentLength,
        #               "remainingWeight": remainingWeight,
        #               "diameter": diameter,
        #               "density": density,
        #               "notEnough": notEnough,
        #               "spoolSelected": True
        # ]

        # for a single check, don't send the info to the browser
        if forToolIndex is None:
            requiredWeightResult["action"] = "requiredFilamentChanged"
            self._sendDataToClient(requiredWeightResult)

        return requiredWeightResult

    def set_temp_offsets(self, toolIndex, spoolModel):
        toolOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]
        )
        bedOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]
        )
        enclosureOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]
        )

        offset_dict = dict()
        if toolOffsetEnabled and spoolModel is not None:
            if spoolModel.offsetTemperature is not None:
                offset_dict["tool" + str(toolIndex)] = spoolModel.offsetTemperature

        if bedOffsetEnabled and spoolModel is not None:
            if spoolModel.offsetBedTemperature is not None:
                offset_dict["bed"] = spoolModel.offsetBedTemperature

        if enclosureOffsetEnabled and spoolModel is not None:
            if spoolModel.offsetEnclosureTemperature is not None:
                offset_dict["chamber"] = spoolModel.offsetEnclosureTemperature

        if len(offset_dict) != 0:
            self._printer.set_temperature_offset(offset_dict)

    def clear_temp_offsets(self):
        toolOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]
        )
        bedOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]
        )
        enclosureOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]
        )

        offset_dict = dict()
        if toolOffsetEnabled:
            printer_profile = self._printer_profile_manager.get_current_or_default()
            printerProfileToolCount = printer_profile["extruder"]["count"]
            # for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
            for toolIndex in range(printerProfileToolCount):
                # toolIndex should be tool0
                offset_dict["tool" + str(toolIndex)] = 0

        if bedOffsetEnabled:
            offset_dict["bed"] = 0

        if enclosureOffsetEnabled:
            offset_dict["chamber"] = 0

        if len(offset_dict) != 0:
            self._printer.set_temperature_offset(offset_dict)

    ################################################################################################## private functions

    def _sendDataToClient(self, payloadDict):
        self._plugin_manager.send_plugin_message(self._identifier, payloadDict)

    def _sendMessageToClient(self, type, title, message, autoclose=False):
        self._logger.warning("SendToClient: " + type + "#" + title + "#" + message)
        self._sendDataToClient(
            dict(
                action="showPopUp",
                type=type,
                title=title,
                message=message,
                autoclose=autoclose,
            )
        )

    def _sendPayload2EventBus(self, eventKey, eventPayload):

        # Must match what OctoPrint derives from the identifier for register_custom_events,
        # otherwise the events fired here are not the ones registered on the event bus.
        eventName = "plugin_" + self._identifier.lower() + "_" + eventKey
        self._logger.info(
            "Send Event '"
            + eventName
            + "' with payload '"
            + str(eventPayload)
            + "' to event-bus"
        )
        self._event_bus.fire(eventName, payload=eventPayload)

        mqttManager = getattr(self, "_mqttManager", None)
        if mqttManager is not None:
            mqttManager.handleEvent(eventKey, eventPayload)

    def _announceSpoolSelectionChange(self, toolIndex, spoolModel):
        """
        Fire spool_selected/spool_deselected for toolIndex only if the spool assigned
        to it actually changed since the last announcement. Prevents event spam from
        callers that re-select the same spool (e.g. repeated RFID reads).
        """
        newDatabaseId = spoolModel.databaseId if spoolModel is not None else None
        lastDatabaseId = self._lastAnnouncedSpoolIds.get(
            toolIndex, _SPOOL_SELECTION_NOT_YET_ANNOUNCED
        )
        if lastDatabaseId == newDatabaseId:
            return

        self._lastAnnouncedSpoolIds[toolIndex] = newDatabaseId

        if spoolModel is not None:
            eventPayload = {
                "toolId": toolIndex,
                "databaseId": spoolModel.databaseId,
                "spoolName": spoolModel.displayName,
                "material": spoolModel.material,
                "colorName": spoolModel.colorName,
                "remainingWeight": spoolModel.remainingWeight,
            }
            self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_SELECTED, eventPayload)
        else:
            eventPayload = {"toolId": toolIndex, "databaseId": None}
            self._sendPayload2EventBus(
                EventBusKeys.EVENT_BUS_SPOOL_DESELECTED, eventPayload
            )

    def _checkForMissingPluginInfos(self, sendToClient=False):

        pluginInfo = self._getPluginInformation("filamentmanager")
        self._filamentManagerPluginImplementationState = pluginInfo[0]
        self._filamentManagerPluginImplementation = pluginInfo[1]

        self._logger.info(
            "Plugin-State: "
            "filamentmanager=" + self._filamentManagerPluginImplementationState + " "
        )
        pass

    # get the plugin with status information
    # [0] == status-string
    # [1] == implementation of the plugin
    def _getPluginInformation(self, pluginKey):

        status = None
        implementation = None

        if pluginKey in self._plugin_manager.plugins:
            plugin = self._plugin_manager.plugins[pluginKey]
            if plugin is not None:
                if plugin.enabled:
                    status = "enabled"
                    # for OP 1.4.x we need to check against "incompatible"-attribute
                    if hasattr(plugin, "incompatible"):
                        if not plugin.incompatible:
                            implementation = plugin.implementation
                        else:
                            status = "incompatible"
                    else:
                        # OP 1.3.x
                        implementation = plugin.implementation
                    pass
                else:
                    status = "disabled"
        else:
            status = "missing"

        return [status, implementation]

    def _extrusionValuesChanged(self, newExtrusionValues):
        if self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_EXTRUSION_DEBUGGING_ENABLED]
        ):
            self._sendDataToClient(
                dict(
                    action="extrusionValuesChanged", extrusionValues=newExtrusionValues
                )
            )

        pass

    def _getCurrentJobFileLocation(self):
        # Classic job data dict, filled once a file is selected on serial printers
        currentData = self._printer.get_current_data()
        if "job" in currentData:
            jobData = currentData["job"]
            if "file" in jobData:
                fileData = jobData["file"]
                origin = fileData.get("origin")
                path = fileData.get("path")
                if origin is not None and path is not None:
                    return origin, path

        # OctoPrint >= 2.0 job model: connector plugins (e.g. bambu_connector) don't
        # populate the classic job dict ("Cannot load job: printer doesn't support it")
        currentJob = getattr(self._printer, "current_job", None)
        if currentJob is not None:
            origin = getattr(currentJob, "storage", None)
            path = getattr(currentJob, "path", None)
            if origin is not None and path is not None:
                return origin, path

        return None, None

    def _parseFilamentLengthsFromBambu3mf(self, fileOrPath, plate=1):
        # bambu-studio/orca .gcode.3mf containers carry the sliced filament usage in
        # Metadata/slice_info.config: <plate><filament id="1" used_m="1.03" used_g="3.08"/></plate>
        try:
            with zipfile.ZipFile(fileOrPath) as zipFile:
                sliceInfoName = None
                for name in zipFile.namelist():
                    if name.lower() == "metadata/slice_info.config":
                        sliceInfoName = name
                        break
                if sliceInfoName is None:
                    return None
                root = ET.fromstring(zipFile.read(sliceInfoName))
        except Exception as e:
            self._logger.debug("could not parse 3mf '%s': %s" % (fileOrPath, e))
            return None

        try:
            plate = int(plate)
        except (TypeError, ValueError):
            plate = 1

        result = {}
        for plateNode in root.iter("plate"):
            plateIndex = None
            for metaNode in plateNode.findall("metadata"):
                if metaNode.get("key") == "index":
                    try:
                        plateIndex = int(metaNode.get("value"))
                    except (TypeError, ValueError):
                        pass
            if plateIndex is not None and plateIndex != plate:
                continue
            for filamentNode in plateNode.findall("filament"):
                try:
                    toolIndex = int(filamentNode.get("id")) - 1
                    usedMeters = float(filamentNode.get("used_m"))
                except (TypeError, ValueError):
                    continue
                result["tool%d" % toolIndex] = {"length": usedMeters * 1000.0}
            if result:
                break
        return result if result else None

    def _getFilamentMetaData(self, origin, path, plate=1):
        # Printer-storage jobs on a Moonraker printer come with per-tool usage that the
        # connector throws away, so ask Moonraker directly before trusting the metadata
        # below - see _getFilamentFromMoonraker() for why, and
        # https://github.com/OctoPrint/OctoPrint-MoonrakerConnector/issues/4 for when this
        # workaround can be dropped again.
        if origin == PRINTER_DESTINATION and path is not None:
            filament = self._getFilamentFromMoonraker(path)
            if filament is not None:
                return filament

        candidates = [(origin, path)]
        if origin != FileDestinations.LOCAL and path is not None:
            # gcode analysis results only exist in local storage; printer-storage jobs
            # (e.g. bambu connector) reference a file that was uploaded from local
            candidates.append((FileDestinations.LOCAL, path))
            basename = path.rsplit("/", 1)[-1]
            if basename != path:
                candidates.append((FileDestinations.LOCAL, basename))
            # bambu uploads may wrap gcode into a 3mf container ("X.gcode" -> "X.gcode.3mf")
            if basename.endswith(".3mf"):
                candidates.append((FileDestinations.LOCAL, basename[: -len(".3mf")]))

        for candidateOrigin, candidatePath in candidates:
            try:
                metadata = self._file_manager.get_metadata(
                    candidateOrigin, candidatePath
                )
            except Exception:
                metadata = None
            if metadata is None:
                self._logger.debug(
                    "no metadata found for '%s:%s'" % (candidateOrigin, candidatePath)
                )
                continue
            if "analysis" in metadata and "filament" in metadata["analysis"]:
                if (candidateOrigin, candidatePath) != (origin, path):
                    self._logger.info(
                        "filament metadata for job '%s:%s' resolved via fallback '%s:%s'"
                        % (origin, path, candidateOrigin, candidatePath)
                    )
                return metadata["analysis"]["filament"]

        # no analysis metadata anywhere: if a local copy is a 3mf container,
        # extract the sliced filament usage directly from it
        for candidateOrigin, candidatePath in candidates:
            if candidateOrigin != FileDestinations.LOCAL or not candidatePath.endswith(
                ".3mf"
            ):
                continue
            try:
                pathOnDisk = self._file_manager.path_on_disk(
                    FileDestinations.LOCAL, candidatePath
                )
            except Exception:
                continue
            if pathOnDisk is None or not os.path.exists(pathOnDisk):
                continue
            filament = self._parseFilamentLengthsFromBambu3mf(pathOnDisk, plate=plate)
            if filament is not None:
                self._logger.info(
                    "filament usage for job '%s:%s' parsed from 3mf 'local:%s' (plate %s)"
                    % (origin, path, candidatePath, plate)
                )
                return filament

        # last resort: no local copy at all, fetch the 3mf from the printer storage
        if origin == PRINTER_DESTINATION and path.endswith(".3mf"):
            return self._getFilamentFromPrinter3mf(path, plate)
        return None

    def _getFilamentFromMoonraker(self, path):
        """
        Per-tool filament usage for a printer-storage job, read straight from Moonraker.

        OctoPrint-MoonrakerConnector maps a job's filament usage onto tool0 unconditionally
        (`connector.py: {"tool0": AnalysisFilamentUse(length=f.filament_total)}`) and never
        reads Moonraker's per-extruder breakdown - its InternalFile only carries
        `filament_total`. On a multi-tool printer every job therefore looks like it prints
        from tool 0: an Orca job sliced for the Snapmaker U1's slot 4 emits `T3` and
        `filament used [mm] = 0.00, 0.00, 0.00, 21872.80`, yet arrives here as
        `{"tool0": 21872.8}` - so the spool selected for tool 3 reads as "not selected"
        while tool 0 reads as "in use".

        Moonraker itself has the split all along, as `filament_used_mm` in the file
        metadata, so fetch it from there. Returns None whenever anything is missing or
        unusable, which leaves the existing metadata path (and its tool0 value) in charge.

        Reported upstream as
        https://github.com/OctoPrint/OctoPrint-MoonrakerConnector/issues/4 - once the
        connector reports per-tool usage itself, this whole method and its call in
        _getFilamentMetaData() can go: the normal metadata path then already returns the
        right tools. Check the connector version before removing it, since users on an
        older release still need this.
        """
        connectorParams = self._u1RfidManager._getConnectorParams()
        if connectorParams is None:
            # not a Moonraker printer (or OctoPrint 1.x without connection_state)
            return None

        payload = self._u1RfidManager._httpGet(
            connectorParams["host"],
            connectorParams["port"],
            "/server/files/metadata?filename=%s" % quote(path),
        )
        result = (payload or {}).get("result")
        if not isinstance(result, dict):
            return None

        # The request itself always runs - it is a local call and it is what detects a
        # re-slice. Cached is only the parsed result, keyed by the file's identity so
        # re-slicing to the same name invalidates the entry instead of serving stale tools.
        cacheKey = (connectorParams["host"], connectorParams["port"], path)
        fingerprint = (result.get("modified"), result.get("size"))
        cached = self._moonrakerFilamentCache.get(cacheKey)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        usedPerTool = result.get("filament_used_mm")
        if not isinstance(usedPerTool, list) or not usedPerTool:
            # single-extruder slicers omit the breakdown - nothing gained over the
            # connector's own value, so let the normal path handle it
            return None

        # a per-tool diameter list is what turns length into volume; Moonraker reports
        # `filament_diameter` either per tool or as a single value
        diameters = result.get("filament_diameter")
        if not isinstance(diameters, list):
            diameters = [diameters] * len(usedPerTool)

        filament = {}
        for toolIndex, usedLength in enumerate(usedPerTool):
            try:
                usedLength = float(usedLength)
            except (TypeError, ValueError):
                continue
            if usedLength <= 0:
                # tools this job never touches must stay absent, not be reported as 0 -
                # that distinction is what tells allowedToPrint() which tools are in use
                continue
            toolData = {"length": usedLength}
            try:
                radius = float(diameters[toolIndex]) / 2.0
                toolData["volume"] = math.pi * radius * radius * usedLength / 1000.0
            except (TypeError, ValueError, IndexError, ZeroDivisionError):
                pass
            filament["tool%d" % toolIndex] = toolData

        if not filament:
            return None

        self._moonrakerFilamentCache[cacheKey] = (fingerprint, filament)
        self._logger.info(
            "filament usage for job 'printer:%s' read per-tool from Moonraker: %s"
            % (path, {tool: data["length"] for tool, data in filament.items()})
        )
        return filament

    def _getFilamentFromPrinter3mf(self, path, plate):
        connection = getattr(self._printer, "_connection", None)
        if connection is None or not hasattr(connection, "download_printer_file"):
            return None

        # downloading from the printer takes seconds, so cache by file fingerprint
        fingerprint = None
        try:
            printerFile = connection.get_printer_file(path)
            fingerprint = (
                getattr(printerFile, "size", None),
                getattr(printerFile, "date", None),
            )
        except Exception:
            pass

        cacheKey = (path, plate)
        cached = self._printer3mfFilamentCache.get(cacheKey)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        self._sendDataToClient(dict(action="printerFileAnalysisStarted", path=path))
        try:
            try:
                fileObject = connection.download_printer_file(path)
            except Exception as e:
                self._logger.warning(
                    "could not download 'printer:%s' for filament parsing: %s"
                    % (path, e)
                )
                return None

            filament = self._parseFilamentLengthsFromBambu3mf(fileObject, plate=plate)
        finally:
            self._sendDataToClient(
                dict(action="printerFileAnalysisFinished", path=path)
            )
        self._printer3mfFilamentCache[cacheKey] = (fingerprint, filament)
        if filament is not None:
            self._logger.info(
                "filament usage for job 'printer:%s' parsed from downloaded 3mf (plate %s)"
                % (path, plate)
            )
        return filament

    def _readingFilamentMetaData(self):
        filamentLengthPresentInMeta = False
        self.metaDataFilamentLengths = []

        origin, path = self._getCurrentJobFileLocation()
        if origin is None or path is None:
            self._logger.warning(
                "calculating filament aborted because no current job file could be determined"
            )
            return False

        currentJob = getattr(self._printer, "current_job", None)
        plate = getattr(currentJob, "plate", 1) if currentJob is not None else 1

        filamentMetaData = self._getFilamentMetaData(origin, path, plate=plate)
        if filamentMetaData is None:
            self._logger.warning(
                "calculating filament aborted because filament analysis metadata was missing for '%s:%s'"
                % (origin, path)
            )
            return False

        for toolName, toolData in filamentMetaData.items():
            toolIndex = int(toolName[4:])
            self.metaDataFilamentLengths += [0.0] * (
                toolIndex + 1 - len(self.metaDataFilamentLengths)
            )
            self.metaDataFilamentLengths[toolIndex] = toolData["length"]
            filamentLengthPresentInMeta = True

        return filamentLengthPresentInMeta

    def _evaluateRequiredWeight(
        self, selectedSpools, forToolIndex=None, warnUser=False
    ):

        self._readingFilamentMetaData()
        metaDataMissing = len(self.metaDataFilamentLengths) <= 0
        someAttributesMissing = False
        overallNotEnough = False
        requiredWeightResultDict = {
            "metaDataMissing": metaDataMissing,
            "warnUser": warnUser,
            "attributesMissing": someAttributesMissing,
            "notEnough": overallNotEnough,
            "detailedSpoolResult": [],
        }
        if metaDataMissing:
            return requiredWeightResultDict

        # loop over all tools
        for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
            if forToolIndex is not None and forToolIndex != toolIndex:
                continue
            selectedSpool = (
                selectedSpools[toolIndex] if toolIndex < len(selectedSpools) else None
            )

            if selectedSpool is not None:
                diameter = selectedSpool.diameter
                density = selectedSpool.density
                totalWeight = selectedSpool.totalWeight
                usedWeight = selectedSpool.usedWeight

                # need attributes present: diameter, density, totalWeight
                missing_fields = []
                if diameter is None:
                    missing_fields.append("diameter")
                if density is None:
                    missing_fields.append("density")
                if totalWeight is None:
                    missing_fields.append("total weight")
                if usedWeight is None:
                    usedWeight = 0.0

                if missing_fields:
                    if warnUser:
                        self._sendMessageToClient(
                            "warning",
                            "Filament prediction not possible!",
                            "Following fields not set in Spool '%s' (in tool %d): %s"
                            % (
                                selectedSpool.displayName,
                                toolIndex,
                                ", ".join(missing_fields),
                            ),
                        )
                    someAttributesMissing = True
                else:
                    not_a_number_fields = []
                    try:
                        diameter = float(diameter)
                    except ValueError:
                        not_a_number_fields.append("diameter")
                    try:
                        density = float(density)
                    except ValueError:
                        not_a_number_fields.append("density")
                    try:
                        totalWeight = float(totalWeight)
                    except ValueError:
                        not_a_number_fields.append("totalweight")
                    try:
                        usedWeight = float(usedWeight)
                    except ValueError:
                        not_a_number_fields.append("used weight")

                    if not_a_number_fields:
                        if warnUser:
                            self._sendMessageToClient(
                                "warning",
                                "Filament prediction not possible!",
                                "One of the needed fields are not a number in Spool '%s' (in tool %d): %s"
                                % (
                                    selectedSpool.displayName,
                                    toolIndex,
                                    ", ".join(not_a_number_fields),
                                ),
                            )
                        someAttributesMissing = True
                    else:
                        # Benötigtes Gewicht = gewicht(geplante länge, durchmesser, dichte)
                        requiredWeight = self._calculateWeight(
                            filamentLength, diameter, density
                        )

                        # Vorhanden Gewicht = Gesamtgewicht - Verbrauchtes Gewicht
                        # TODO don't calculate here use the value from the database
                        remainingWeight = totalWeight - usedWeight

                        safetyLengthInMM = self._settings.get_int(
                            [SettingsKeys.SETTINGS_KEY_SAFETY_LENGTH]
                        )
                        if safetyLengthInMM != 0:
                            safetyRequiredWeight = self._calculateWeight(
                                safetyLengthInMM, diameter, density
                            )
                            self._logger.info(
                                "safetyWeight '"
                                + str(safetyRequiredWeight)
                                + "' from safetyLengthInMM '"
                                + str(safetyLengthInMM)
                                + "' calculated"
                            )
                            requiredWeight = requiredWeight + safetyRequiredWeight

                        self._logger.info(
                            "tool"
                            + str(toolIndex)
                            + ", requiredWeight '"
                            + str(requiredWeight)
                            + "',  remainingWeight '"
                            + str(remainingWeight)
                            + "'"
                        )

                        notEnough = False
                        if remainingWeight < requiredWeight and requiredWeight > 0:
                            self._logger.info("Filament not enough!")
                            if warnUser:
                                self._sendMessageToClient(
                                    "warning",
                                    "Filament not enough!",
                                    "Required on tool %d: %dg, available from Spool '%s': '%dg'"
                                    % (
                                        toolIndex,
                                        requiredWeight,
                                        selectedSpool.displayName,
                                        remainingWeight,
                                    ),
                                )
                            notEnough = True
                            overallNotEnough = True

                        detailedSpoolResultItem = {
                            "toolIndex": toolIndex,
                            "requiredWeight": requiredWeight,
                            "requiredLength": filamentLength,
                            "remainingWeight": remainingWeight,
                            "diameter": diameter,
                            "density": density,
                            "notEnough": notEnough,
                            "spoolSelected": True,
                            "spoolName": selectedSpool.displayName,
                        }
                        requiredWeightResultDict["detailedSpoolResult"].append(
                            detailedSpoolResultItem
                        )
            else:
                # No selected spool for this tool-index, just create a simple entry
                detailedSpoolResultItem = {
                    "toolIndex": toolIndex,
                    "requiredLength": filamentLength,
                    "spoolSelected": False,
                    "spoolName": "not selected",
                }
                requiredWeightResultDict["detailedSpoolResult"].append(
                    detailedSpoolResultItem
                )
                pass

        requiredWeightResultDict["attributesMissing"] = someAttributesMissing
        requiredWeightResultDict["notEnough"] = overallNotEnough

        return requiredWeightResultDict

    def _calculateWeight(self, length, diameter, density):
        radius = diameter / 2.0
        volume = length * math.pi * (radius * radius) / 1000
        result = volume * density
        return result

    def _buildDatabaseSettingsFromPluginSettings(self):
        databaseSettings = DatabaseManager.DatabaseSettings()
        databaseSettings.useExternal = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL]
        )
        databaseSettings.type = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_TYPE]
        )
        databaseSettings.host = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_HOST]
        )
        databaseSettings.port = self._settings.get_int(
            [SettingsKeys.SETTINGS_KEY_DATABASE_PORT]
        )
        databaseSettings.name = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_NAME]
        )
        databaseSettings.user = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_USER]
        )
        databaseSettings.password = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD]
        )
        pluginDataBaseFolder = self.get_plugin_data_folder()
        databaseSettings.baseFolder = pluginDataBaseFolder
        databaseSettings.fileLocation = (
            self._databaseManager.buildDefaultDatabaseFileLocation(
                databaseSettings.baseFolder
            )
        )

        return databaseSettings

    # common states: STATE_CONNECTING("Connecting"), STATE_OPERATIONAL("Operational"),
    # STATE_STARTING("Startinf..."), STATE_PRINTING("Printing or Sendind"), STATE_CANCELLING("Cancelling"),
    # STATE_PAUSING("Pausing"), STATE_PAUSED("Paused"), STATE_RESUMING("Resuming"), STATE_FINISHING("Finishing"), STATE_CLOSED("Offline")
    # Normal flow:
    # - OPERATIONAL
    # - STARTING
    # - PRINTING
    # - FINISHING
    # - OPERATIONAL

    # Cancel
    # - ...
    # - PRINTING
    # -CANCELLING
    # - OPERATIONAL

    # Pause -> Resume
    # - STARTING
    # - PRINTING
    # - PAUSING
    # - PAUSED
    # - RESUMING
    # - PRINTING
    # - FINISHING
    # - OPERATIONAL

    # Pause -> Restart
    # - PRINTING
    # - PAUSING
    # - PAUSED
    # - STARTING
    # - PRINTING
    # def _on_printer_state_changed(self, payload):
    #   printerState = payload['state_id']
    #   print("######################  " +str(printerState))
    #   if payload['state_id'] == "PRINTING":
    #       if self._lastPrintState == "PAUSED":
    #           # resuming print
    #           self.filamentOdometer.reset_extruded_length()
    #       else:
    #           # starting new print
    #           self.filamentOdometer.reset()
    #       self.odometerEnabled = self._settings.getBoolean(["enableOdometer"])
    #       self.pauseEnabled = self._settings.getBoolean(["autoPause"])
    #       self._logger.debug("Printer State: %s" % payload["state_string"])
    #       self._logger.debug("Odometer: %s" % ("On" if self.odometerEnabled else "Off"))
    #       self._logger.debug("AutoPause: %s" % ("On" if self.pauseEnabled and self.odometerEnabled else "Off"))
    #   elif self._lastPrintState == "PRINTING":
    #       # print state changed from printing => update filament usage
    #       self._logger.debug("Printer State: %s" % payload["state_string"])
    #       if self.odometerEnabled:
    #           self.odometerEnabled = False  # disabled because we don't want to track manual extrusion
    #
    #           self.currentExtrusion = self.filamentOdometer.get_extrusion()
    #
    #   # update last print state
    #   self._lastPrintState = payload['state_id']

    def _on_printJobStarted(self):
        # starting new print

        # self._filamentOdometer.reset()
        self.myFilamentOdometer.reset()
        self._slicedUsageAlreadyBooked = False
        self._printJobStartedTimestamp = time.time()

        reloadTable = False
        selectedSpools = self.loadSelectedSpools()
        self._readingFilamentMetaData()
        for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
            spoolModel = (
                selectedSpools[toolIndex] if toolIndex < len(selectedSpools) else None
            )

            if spoolModel is not None:
                if StringUtils.isEmpty(spoolModel.firstUse):
                    firstUse = datetime.now()
                    spoolModel.firstUse = firstUse
                    self._databaseManager.saveSpool(spoolModel)
                    reloadTable = True
        if reloadTable:
            self._sendDataToClient(dict(action="reloadTable"))

    # assign the current extrusion to the current selected spools

    # connectors can fire spurious PRINT_DONE events seconds after the job kickoff
    # (state bounces); no real print finishes this quickly
    MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE = 60

    def commitOdometerData(self, printStatus=None, printDuration=None):
        reload = False
        slicedLengthsRead = False
        slicedUsageBooked = False
        slicedUsagePlausible = (
            printDuration is None
            or printDuration >= self.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
        )
        selectedSpools = self.loadSelectedSpools()
        for toolIndex, spoolModel in enumerate(selectedSpools):
            if spoolModel is None:
                self._logger.warning(
                    "Tool %d: No spool selected, could not update values after print"
                    % toolIndex
                )
                continue

            # - Last usage datetime
            lastUsage = datetime.now()
            spoolModel.lastUse = lastUsage
            # - Used length
            try:
                allExtrusions = self.myFilamentOdometer.getExtrusionAmount()
                currentExtrusionLength = allExtrusions[toolIndex]
            except (KeyError, IndexError):
                currentExtrusionLength = None

            if (
                (currentExtrusionLength is None or currentExtrusionLength <= 0.0)
                and printStatus == "success"
                and not self._slicedUsageAlreadyBooked
                and slicedUsagePlausible
            ):
                # nothing streamed through octoprint (e.g. printer-storage prints via a
                # connector plugin), so book the sliced filament usage instead
                if not slicedLengthsRead:
                    self._readingFilamentMetaData()
                    slicedLengthsRead = True
                slicedLength = (
                    self.metaDataFilamentLengths[toolIndex]
                    if toolIndex < len(self.metaDataFilamentLengths)
                    else None
                )
                if slicedLength is not None and slicedLength > 0.0:
                    currentExtrusionLength = slicedLength
                    slicedUsageBooked = True
                    self._logger.info(
                        "Tool %d: no extrusion tracked by odometer, using sliced filament usage of %.1fmm instead"
                        % (toolIndex, slicedLength)
                    )

            if currentExtrusionLength is None:
                self._logger.info("Tool %d: No filament extruded" % toolIndex)
                continue
            self._logger.info(
                "Tool %d: Extruded filament length: %s"
                % (toolIndex, str(currentExtrusionLength))
            )
            spoolUsedLength = (
                0.0
                if StringUtils.isEmpty(spoolModel.usedLength)
                else spoolModel.usedLength
            )
            self._logger.info(
                "Tool %d: Current Spool used filament length: %s"
                % (toolIndex, str(spoolUsedLength))
            )
            newUsedLength = spoolUsedLength + currentExtrusionLength
            self._logger.info(
                "Tool %d: New Spool used filament length: %s"
                % (toolIndex, str(newUsedLength))
            )
            spoolModel.usedLength = newUsedLength
            # - Used weight
            diameter = spoolModel.diameter
            density = spoolModel.density
            if diameter is None or density is None:
                self._logger.warning(
                    "Tool %d: Could not update spool weight, because diameter or density not set in spool '%s'"
                    % (toolIndex, spoolModel.displayName)
                )
            else:
                usedWeight = self._calculateWeight(
                    currentExtrusionLength, diameter, density
                )
                spoolUsedWeight = (
                    0.0 if spoolModel.usedWeight is None else spoolModel.usedWeight
                )
                newUsedWeight = spoolUsedWeight + usedWeight
                spoolModel.usedWeight = newUsedWeight
                self._logger.info(
                    "Tool %d: spoolUsedWeight: %s" % (toolIndex, str(spoolUsedWeight))
                )
                self._logger.info(
                    "Tool %d: New spoolUsedWeight: %s" % (toolIndex, str(newUsedWeight))
                )

            self._databaseManager.saveSpool(spoolModel)

            eventPayload = {
                "toolId": toolIndex,
                "databaseId": spoolModel.databaseId,
                "spoolName": spoolModel.displayName,
                "material": spoolModel.material,
                "colorName": spoolModel.colorName,
                "remainingWeight": spoolModel.remainingWeight,
            }
            self._sendPayload2EventBus(
                EventBusKeys.EVENT_BUS_SPOOL_WEIGHT_UPDATED_AFTER_PRINT, eventPayload
            )

            reload = True

        self.myFilamentOdometer.reset_extruded_length()

        if slicedUsageBooked:
            self._slicedUsageAlreadyBooked = True

        if reload:
            self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))

    #### print job finished
    def _on_printJobFinished(self, printStatus, payload):
        printDuration = payload.get("time") if payload else None
        if (
            printDuration is None or printDuration <= 0.0
        ) and self._printJobStartedTimestamp is not None:
            # some connectors (e.g. bambu) always report time=0.0, so use our own clock
            printDuration = time.time() - self._printJobStartedTimestamp
        if (
            printDuration is not None
            and printDuration < self.MINIMUM_PRINT_DURATION_FOR_SLICED_USAGE
        ):
            self._logger.info(
                "print 'finished' after only %.1fs - sliced filament usage will not be booked (spurious event?)"
                % printDuration
            )
        self.commitOdometerData(printStatus=printStatus, printDuration=printDuration)

        # update remaining data in selected spools after a print
        selectedSpools = self.loadSelectedSpools()
        requiredWeightResult = self._evaluateRequiredWeight(selectedSpools, None, False)
        requiredWeightResult["action"] = "requiredFilamentChanged"
        self._sendDataToClient(requiredWeightResult)

        if "paused" != printStatus:
            self.clear_temp_offsets()

    def _on_clientOpened(self, payload):
        # start-workaround https://github.com/foosel/OctoPrint/issues/3400
        # TODO remove workaround
        import time

        time.sleep(3)
        selectedSpoolsAsDicts = []

        # Check if database is available
        # connected = self._databaseManager.reConnectToDatabase()
        # self._logger.info("ClientOpened. Database connected:"+str(connected))

        connectionErrorResult = self._databaseManager.testDatabaseConnection()

        # Don't show already shown message
        if (
            not self.databaseConnectionProblemConfirmed
            and connectionErrorResult is not None
        ):
            databaseErrorMessageDict = (
                self._databaseManager.getCurrentErrorMessageDict()
            )
            # The databaseErrorMessages should always be present in that case.
            if databaseErrorMessageDict is not None:
                self._logger.error(databaseErrorMessageDict)
                self._sendDataToClient(
                    dict(
                        action="showConnectionProblem",
                        type=databaseErrorMessageDict["type"],
                        title=databaseErrorMessageDict["title"],
                        message=databaseErrorMessageDict["message"],
                    )
                )

        # Send plugin storage information
        ## Storage
        if connectionErrorResult is None:
            selectedSpoolsAsDicts = [
                (
                    None
                    if selectedSpool is None
                    else Transformer.transformSpoolModelToDict(selectedSpool)
                )
                for selectedSpool in self.loadSelectedSpools()
            ]

        pluginNotWorking = connectionErrorResult is not None
        self._sendDataToClient(
            dict(
                action="initialData",
                selectedSpools=selectedSpoolsAsDicts,
                isFilamentManagerPluginAvailable=self._filamentManagerPluginImplementation
                is not None,
                pluginNotWorking=pluginNotWorking,
                # the settings keep their buttons as long as anything is migratable; the
                # banner listens to legacyMigrationPending instead
                legacyMigrationAvailable=self._isLegacyMigrationAvailable(),
                legacyMigrationPending=(
                    self._isLegacyMigrationAvailable()
                    and not self._isLegacyMigrationDone()
                ),
                legacyDatabaseUndoAvailable=self._isLegacyMigrationUndoAvailable(
                    "database"
                ),
                legacySettingsUndoAvailable=self._isLegacyMigrationUndoAvailable(
                    "settings"
                ),
                legacySettingsAvailable=self._hasLegacySettings(),
            )
        )
        # data for the sidebar
        self.checkRemainingFilament(shouldWarn=False)
        pass

    def _on_clientClosed(self, payload):
        self.databaseConnectionProblemConfirmed = False

    def _on_file_selectionChanged(self, payload):
        self.checkRemainingFilament()

    pass

    ######################################################################################### PUBLIC IMPLEMENTATION API
    def api_getSelectedSpoolInformations(self):
        """
        Returns the current extruded filament for each tool
        :return: array of spoolData-object ....
        """
        spoolModels = self.loadSelectedSpools()
        result = []
        toolIndex = 0
        while toolIndex < len(spoolModels):
            spoolModel = spoolModels[toolIndex]
            spoolData = None
            if spoolModel is not None:
                spoolData = {
                    "toolIndex": toolIndex,
                    "databaseId": spoolModel.databaseId,
                    "spoolName": spoolModel.displayName,
                    "vendor": spoolModel.vendor,
                    "material": spoolModel.material,
                    "diameter": spoolModel.diameter,
                    "density": spoolModel.density,
                    "colorName": spoolModel.colorName,
                    "color": spoolModel.color,
                    "cost": spoolModel.cost,
                    "weight": spoolModel.totalWeight,
                }
            result.append(spoolData)

            toolIndex += 1
        return result

    def api_getExtrusionAmount(self):
        """
        Returns the current extruded filament for each tool
        :return: array of ....
        """
        return self.myFilamentOdometer.getExtrusionAmount()
        pass

    ######################################################################################### Hooks and public functions

    def on_after_startup(self):
        # check if needed plugins were available
        self._checkForMissingPluginInfos()

        # Announce the spools restored from settings exactly once, so external
        # consumers of spool_selected/spool_deselected learn the current state
        # after a restart without loadSelectedSpools() itself firing on every read.
        for toolIndex, spoolModel in enumerate(self.loadSelectedSpools()):
            self._announceSpoolSelectionChange(toolIndex, spoolModel)

        # MQTT: acquire the OctoPrint-MQTT helper and publish the initial state
        self._mqttManager.initialize()
        if self._mqttManager.isOperational():
            self._mqttManager.publishDiscovery()
            self._mqttManager.publishAllStates()

        # U1 RFID: evaluate the detection chain and start the reader if everything lines
        # up. Never blocks startup - an unreachable U1 just leaves the reader idle.
        self._u1RfidManager.initialize()
        pass

    def on_shutdown(self):
        u1RfidManager = getattr(self, "_u1RfidManager", None)
        if u1RfidManager is not None:
            u1RfidManager.shutdown()

    # Listen to all  g-code which where already sent to the printer (thread: comm.sending_thread)
    def on_sentGCodeHook(
        self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs
    ):

        # TODO maybe later via a queue
        # self._filamentOdometer.parse(gcode, cmd)
        self.myFilamentOdometer.processGCodeLine(cmd)
        # if self.pauseEnabled and self.check_threshold():
        #   self._logger.info("Filament is running out, pausing print")
        #   self._printer.pause_print()
        pass

    def on_event(self, event, payload):

        # if (event != "RegisteredMessageReceived"):
        #   print("*** EVENT: " + event)
        #
        # if ("plugin_spoolmanager" in event):
        #   print(payload)
        #   pass

        if Events.CLIENT_OPENED == event:
            self._on_clientOpened(payload)
            return
        if Events.CLIENT_CLOSED == event:
            self._on_clientClosed(payload)
            return

        # The U1 reader derives its host from the active printer connection, so a
        # connect/disconnect has to re-run the detection chain - otherwise it would keep
        # talking to the previously connected printer.
        if event in (Events.CONNECTED, Events.DISCONNECTED):
            u1RfidManager = getattr(self, "_u1RfidManager", None)
            if u1RfidManager is not None:
                u1RfidManager.refresh()

        elif Events.PRINT_STARTED == event:
            self.alreadyCanceled = False
            self._on_printJobStarted()

        elif Events.PRINT_PAUSED == event:
            self._on_printJobFinished("paused", payload)

        elif Events.PRINT_DONE == event:
            self._on_printJobFinished("success", payload)

        elif Events.PRINT_FAILED == event:
            if not self.alreadyCanceled:
                self._on_printJobFinished("failed", payload)

        elif Events.PRINT_CANCELLED == event:
            self.alreadyCanceled = True
            self._on_printJobFinished("canceled", payload)

        if (
            Events.FILE_SELECTED == event
            or Events.FILE_DESELECTED == event
            or Events.METADATA_ANALYSIS_FINISHED == event
            or Events.UPDATED_FILES == event
        ):
            self._on_file_selectionChanged(payload)
            return

        pass

    def get_settings_restricted_paths(self):
        # Settings that must not be handed to clients without admin rights. OctoPrint keeps
        # these out of the settings payload it serves, so an anonymous or read-only session
        # cannot read them back out of the browser.
        #
        # Both entries are credentials the user supplied, not plugin data: the database
        # password, and the manufacturer keys for reading protected vendor tags. Neither is
        # needed to render anything - the settings dialog shows a status ("set" / "not set"
        # / "does not match") rather than the value itself.
        #
        # This hook did not exist in this plugin before; the database password was public to
        # any logged-in client. Adding the tag keys was the occasion to close that too.
        return {
            "admin": [
                [SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD],
                [SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_KEYS],
            ]
        }

    def on_settings_save(self, data):
        # Enable cleaning up any offsets that are turned off
        oldToolOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]
        )
        oldBedOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]
        )
        oldEnclosureOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]
        )

        # capture old MQTT identity, so retained topics can be cleared if it changes
        oldMqttEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_MQTT_ENABLED]
        )
        oldMqttIdentity = (
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_PREFIX]),
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_TOPIC_BASE]),
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME]),
        )

        # # default save function
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        # Clean up any offsets that are turned off
        newToolOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]
        )
        newBedOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]
        )
        newEnclosureOffsetEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]
        )

        offsetCleanup = False
        offset_dict = dict()
        if not newToolOffsetEnabled and oldToolOffsetEnabled:
            offsetCleanup = True
            offset_dict["tool0"] = 0
        if not newBedOffsetEnabled and oldBedOffsetEnabled:
            offsetCleanup = True
            offset_dict["bed"] = 0
        if not newEnclosureOffsetEnabled and oldEnclosureOffsetEnabled:
            offsetCleanup = True
            offset_dict["chamber"] = 0

        if offsetCleanup:
            self._printer.set_temperature_offset(offset_dict)

        # Update Temperature Offsets
        selectedSpools = self.loadSelectedSpools()
        self._readingFilamentMetaData()
        for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
            selectedSpool = (
                selectedSpools[toolIndex] if toolIndex < len(selectedSpools) else None
            )
            if selectedSpool is not None:
                self.set_temp_offsets(toolIndex, selectedSpool)

        # MQTT: clear retained topics on disable or identity change, republish when enabled
        newMqttEnabled = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_MQTT_ENABLED]
        )
        newMqttIdentity = (
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_PREFIX]),
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_TOPIC_BASE]),
            self._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME]),
        )
        if (oldMqttEnabled and not newMqttEnabled) or (
            oldMqttEnabled and oldMqttIdentity != newMqttIdentity
        ):
            self._mqttManager.clearRetainedTopics()
        if newMqttEnabled:
            self._mqttManager.publishDiscovery()
            self._mqttManager.publishAllStates()

        # U1 RFID: enabling/disabling has to start or stop the reader right away
        u1RfidManager = getattr(self, "_u1RfidManager", None)
        if u1RfidManager is not None:
            u1RfidManager.refresh()

        # In case we are switching between internal and external storage
        databaseSettings = self._buildDatabaseSettingsFromPluginSettings()
        self._databaseManager.assignNewDatabaseSettings(databaseSettings)
        # testResult = self._databaseManager.testDatabaseConnection(databaseSettings)
        # if (testResult != None):
        #   # TODO Send to client
        #   pass

    # explicitly declare the API protection status, will become the default in a future OctoPrint version
    def is_api_protected(self):
        return True

    # to allow the frontend to trigger an update
    def on_api_get(self, request):
        if not Permissions.SETTINGS.can():
            return "Insufficient rights", 403

        if len(request.values) != 0:
            action = request.values["action"]

            if "getDefaultSettings" == action:
                return flask.jsonify(self.get_settings_defaults())

            # because of some race conditions, we can't push the initialDate during client-open event. So we provide the settings on request
            if "additionalSettingsValues" == action:
                return flask.jsonify(
                    {
                        "isFilamentManagerPluginAvailable": self._filamentManagerPluginImplementation
                        is not None,
                        "isMqttPluginAvailable": self._mqttManager.isMqttPluginAvailable(),
                    }
                )

    ##~~ SettingsPlugin mixin
    def get_settings_defaults(self):

        settings = dict(installed_version=self._plugin_version)

        # Not visible
        settings[SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS] = []

        ## General
        settings[SettingsKeys.SETTINGS_KEY_REMINDER_SELECTING_SPOOL] = True
        settings[SettingsKeys.SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED] = True
        settings[SettingsKeys.SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH] = True
        settings[SettingsKeys.SETTINGS_KEY_CURRENCY_SYMBOL] = "€"
        settings[SettingsKeys.SETTINGS_KEY_SAFETY_LENGTH] = 0
        settings[SettingsKeys.SETTINGS_KEY_LENGTH_UNIT] = "mm"
        settings[SettingsKeys.SETTINGS_KEY_WEIGHT_UNIT] = "g"
        settings[SettingsKeys.SETTINGS_KEY_DEFAULT_VIEW_MODE_SIMPLE] = True

        ## Performance
        settings[
            SettingsKeys.SETTINGS_KEY_PERFORMANCE_LAZY_LOAD_SPOOL_SELECTOR_DATA
        ] = False
        settings[SettingsKeys.SETTINGS_KEY_PERFORMANCE_LAZY_LOAD_SPOOL_TABLE] = False

        ## QR-Code
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_ENABLED] = True
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_USE_URL_PREFIX] = False
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_URL_PREFIX] = None
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_FILL_COLOR] = "#008000"
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_BACKGROUND_COLOR] = "#ffffff"
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_WIDTH] = "100"
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_HEIGHT] = "100"
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_LABEL_WIDTH_MM] = "89"
        settings[SettingsKeys.SETTINGS_KEY_QR_CODE_LABEL_HEIGHT_MM] = "36"

        ## Export / Import
        settings[SettingsKeys.SETTINGS_KEY_IMPORT_CSV_MODE] = (
            SettingsKeys.KEY_IMPORTCSV_MODE_APPEND
        )

        ## Temperature
        settings[SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED] = False

        ## OctoScale
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_URL] = ""
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT] = "extended"
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT] = "openSpool"
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_READING_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_VENDOR_TAG_WRITE_ENABLED] = False
        # Empty on purpose: no manufacturer key material ships with this plugin, and the
        # parsers that need one disable themselves until the user supplies it.
        settings[SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_KEYS] = {}

        ## SpoolmanDB-Community
        ## Optional spool fields
        settings[SettingsKeys.SETTINGS_KEY_TD_FIELD_ENABLED] = False

        ## SpoolmanDB-Community
        settings[SettingsKeys.SETTINGS_KEY_SPOOLMANDB_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_SPOOLMANDB_CACHE_TTL_DAYS] = 7

        ## TigerTag id lookup tables (auto-update from TigerTag-SDK-Python)
        # Enabled by default, unlike SpoolmanDB: these tables aren't an optional
        # convenience, they're what TigerTag reading/writing needs to resolve anything
        # beyond the sparse offline fallback snapshot.
        settings[SettingsKeys.SETTINGS_KEY_TIGERTAG_IDS_AUTO_UPDATE_ENABLED] = True

        ## Debugging
        settings[SettingsKeys.SETTINGS_KEY_SQL_LOGGING_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_EXTRUSION_DEBUGGING_ENABLED] = False

        ## MQTT (read-only publishing via the OctoPrint-MQTT plugin)
        # U1 RFID: off by default - without an explicit opt-in no websocket is opened
        settings[SettingsKeys.SETTINGS_KEY_U1RFID_ENABLED] = False

        settings[SettingsKeys.SETTINGS_KEY_MQTT_ENABLED] = False
        settings[SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_ENABLED] = True
        settings[SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_PREFIX] = "homeassistant"
        settings[SettingsKeys.SETTINGS_KEY_MQTT_TOPIC_BASE] = (
            "octoprint/plugin/SpoolManagerExtended"
        )
        # instance-name default stays empty here, because this runs during plugin-init before
        # self._settings is injected; the effective default is filled in on_settings_load
        settings[SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME] = ""
        settings[SettingsKeys.SETTINGS_KEY_MQTT_RETAIN] = True

        ## Database
        ## nested settings are not working, because if only a few attributes are changed it only returns these few attributes, instead the default values + adjusted values
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL] = False
        databaseLocation = DatabaseManager.buildDefaultDatabaseFileLocation(
            self.get_plugin_data_folder()
        )
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_LOCAL_FILELOCATION] = (
            databaseLocation
        )
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_TYPE] = "sqlite"
        # settings[SettingsKeys.SETTINGS_KEY_DATABASE_TYPE] = "postgres"
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_HOST] = "localhost"
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_PORT] = 5432
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_NAME] = "SpoolDatabase"
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_USER] = ""
        settings[SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD] = ""
        # {
        #   "localDatabaseFileLocation": "",
        #   "type": "postgres",
        #   "host": "localhost",
        #   "port": 5432,
        #   "databaseName": "SpoolDatabase",
        #   "user": "Username",
        #   "password": "Example7nEbvTCaXnmnt!39epZbANcPassword"
        # }

        settings["excludedFromTemplateCopy"] = []
        return settings

    def _getDefaultMqttInstanceName(self):
        # the title from OctoPrint Settings -> Appearance, hostname as fallback
        instanceName = self._settings.global_get(["appearance", "name"])
        if instanceName is None or not instanceName.strip():
            instanceName = socket.gethostname()
        return instanceName

    def on_settings_load(self):
        data = octoprint.plugin.SettingsPlugin.on_settings_load(self)
        # prefill the MQTT instance name shown in the settings dialog
        if not (data.get(SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME) or "").strip():
            data[SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME] = (
                self._getDefaultMqttInstanceName()
            )
        return data

    ##~~ TemplatePlugin mixin
    def get_template_configs(self):
        # The template files keep their original "SpoolManager_" prefix after the plugin was
        # renamed to SpoolManagerExtended, so every entry names its template explicitly -
        # otherwise OctoPrint derives the filename from the identifier and finds nothing.
        return [
            dict(type="tab", name="Spools", template="SpoolManager_tab.jinja2"),
            dict(type="settings", custom_bindings=True, name="Spool Manager Extended", template="SpoolManager_settings.jinja2"),
            dict(type="sidebar", name="Spools", icon="life-ring", template="SpoolManager_sidebar.jinja2"),
        ]

    ##~~ AssetPlugin mixin
    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=[
                "js/quill.min.js",
                # Minified 3rd-party assets adopted from mdziekon/OctoPrint-SpoolManager PR #13 (GH-12)
                "js/tinycolor.min.js",
                # Shared constants/helpers, adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10).
                # Load order matters: constants -> utils -> dialogs -> ComponentFactory -> SpoolItem -> consumers
                "js/common/constants.js",
                "js/common/utils.js",
                "js/common/colorPicker.js",
                "js/common/dialogs.js",
                "js/ResetSettingsUtilV3.js",
                "js/ComponentFactory.js",
                "js/TableItemHelper.js",
                "js/SpoolManager-SpoolItem.js",
                "js/SpoolManager.js",
                "js/SpoolManager-APIClient.js",
                "js/SpoolManager-SpoolSelectionTableComp.js",
                "js/SpoolManager-OctoScale.js",
                "js/SpoolManager-EditSpoolDialog.js",
                "js/SpoolManager-AddSpoolWizard.js",
                "js/SpoolManager-ImportDialog.js",
                "js/SpoolManager-DatabaseConnectionProblemDialog.js",
            ],
            css=["css/quill.snow.css", "css/SpoolManager.css"],
            less=["less/SpoolManager.less"],
        )

    ##~~ Softwareupdate hook
    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://docs.octoprint.org/en/master/bundledplugins/softwareupdate.html
        # for details.
        return dict(
            SpoolManagerExtended=dict(
                displayName="SpoolManagerExtended Plugin",
                displayVersion=self._plugin_version,
                # version check: GitHub repository
                type="github_release",
                user="Ajimaru",
                repo="OctoPrint-SpoolManagerExtended",
                current=self._plugin_version,
                # Release channels
                stable_branch=dict(
                    name="Only Release", branch="main", commitish=["main"]
                ),
                prerelease_branches=[
                    dict(
                        name="Release & Testing",
                        branch="testing",
                        commitish=["testing", "main"],
                    )
                ],
                force_base=True,  # undocumented parameter necessary when using a1 version notation
                # update method: pip
                pip="https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended/releases/download/{target_version}/main.zip",
            )
        )

    def register_custom_events(*args, **kwargs):
        return [
            EventBusKeys.EVENT_BUS_SPOOL_WEIGHT_UPDATED_AFTER_PRINT,
            EventBusKeys.EVENT_BUS_SPOOL_WEIGHT_MEASURED,
            EventBusKeys.EVENT_BUS_SPOOL_SELECTED,
            EventBusKeys.EVENT_BUS_SPOOL_DESELECTED,
            EventBusKeys.EVENT_BUS_SPOOL_ADDED,
            EventBusKeys.EVENT_BUS_SPOOL_DELETED,
        ]

    # def message_on_connect(self, comm, script_type, script_name, *args, **kwargs):
    #   print(script_name)
    #   if not script_type == "gcode" or not script_name == "afterPrinterConnected":
    #       return None
    #
    #   prefix = None
    #   postfix = "M117 OctoPrint connected"
    #   variables = dict(myvariable="Hi! I'm a variable!")
    #   return prefix, postfix, variables


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "SpoolManagerExtended Plugin"
__plugin_pythoncompat__ = ">=3.9,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = SpoolmanagerPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.on_sentGCodeHook,
        # "octoprint.comm.protocol.scripts": __plugin_implementation__.message_on_connect
        "octoprint.events.register_custom_events": __plugin_implementation__.register_custom_events,
    }
