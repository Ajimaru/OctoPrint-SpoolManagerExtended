# coding=utf-8

import base64
import datetime
import json
import os
import re
import shutil
import tempfile
import threading
from io import BytesIO  # for handling byte strings
from math import pi as PI

import flask
import octoprint.plugin
import qrcode
from flask import Response, abort, jsonify, make_response, request, send_file
from markupsafe import escape
from octoprint.access.permissions import Permissions
from octoprint.server.util.flask import no_firstrun_access

from octoprint_SpoolManager import DatabaseManager
from octoprint_SpoolManager.U1RfidManager import (
    deriveRfidTagKey,
    isPlausibleTagUid,
    normalizeCardUid,
)
from octoprint_SpoolManager.api import Transformer
from octoprint_SpoolManager.common import (
    CSVExportImporter,
    FilamentTagKeys,
    FilamentTagModel,
    FilamentTagParsers,
    FilamentTagReader,
    FilamentTagToSpool,
    OctoScaleUrl,
    OpenPrintTag,
    RfidTeachIn,
    StringUtils,
    TagFormats,
)
from octoprint_SpoolManager.common.EventBusKeys import EventBusKeys
from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManager.models.SpoolModel import SpoolModel


class SpoolManagerAPI(octoprint.plugin.BlueprintPlugin):
    def is_blueprint_csrf_protected(self):
        return True

    def _sendCSVUploadStatusToClient(
        self,
        importStatus,
        currenLineNumber,
        backupFilePath,
        successMessages,
        errorCollection,
    ):

        self._sendDataToClient(
            dict(
                action="csvImportStatus",
                importStatus=importStatus,
                currenLineNumber=currenLineNumber,
                backupFilePath=backupFilePath,
                successMessages=successMessages,
                errorCollection=errorCollection,
            )
        )

    # Human readable labels for validation error messages, keyed by JSON field name
    _FIELD_LABELS = {
        "displayName": "Displayname",
        "vendor": "Vendor",
        "material": "Material",
        "colorName": "Color",
        "color": "Color code",
        "finish": "Finish",
        "code": "Code",
        "batchNumber": "Batch number",
        "purchasedFrom": "Purchased from",
        "costUnit": "Cost unit",
        "noteText": "Note",
        "noteHtml": "Note",
        "labels": "Labels",
        "noteDeltaFormat": "Note",
        "density": "Density",
        "diameter": "Diameter",
        "diameterTolerance": "Diameter tolerance",
        "flowRateCompensation": "Flow rate compensation",
        "temperature": "Tool temperature",
        "minTemperature": "Tool temperature (min)",
        "maxTemperature": "Tool temperature (max)",
        "bedTemperature": "Bed temperature",
        "minBedTemperature": "Bed temperature (min)",
        "maxBedTemperature": "Bed temperature (max)",
        "enclosureTemperature": "Enclosure temperature",
        "dryingTemperature": "Drying temperature",
        "dryingTime": "Drying time",
        "td": "Transmission distance",
        "offsetTemperature": "Offset tool temperature",
        "offsetBedTemperature": "Offset bed temperature",
        "offsetEnclosureTemperature": "Offset enclosure temperature",
        "totalWeight": "Filament amount (initial)",
        "spoolWeight": "Empty spool weight",
        "grossWeight": "Measured weight",
        "remainingWeight": "Filament amount (remaining)",
        "totalLength": "Filament length (initial)",
        "usedLength": "Filament length (used)",
        "usedWeight": "Filament amount (used)",
        "cost": "Cost",
        "firstUseKO": "First use",
        "lastUseKO": "Last use",
        "purchasedOnKO": "Purchased on",
    }

    def _fieldLabel(self, key):
        return self._FIELD_LABELS.get(key, key)

    def _spoolman_db_is_enabled(self):
        return self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_SPOOLMANDB_ENABLED]
        )

    def _spoolman_db_ttl_days(self):
        value = self._settings.get_int(
            [SettingsKeys.SETTINGS_KEY_SPOOLMANDB_CACHE_TTL_DAYS]
        )
        return min(max(value, 1), 30)

    def _spoolman_db_disabled_response(self):
        return flask.jsonify(
            {
                "enabled": False,
                "status": "disabled",
                "error": "SpoolmanDB integration is disabled in the plugin settings.",
            }
        )

    @octoprint.plugin.BlueprintPlugin.route("/spoolmanDbVendors", methods=["GET"])
    @no_firstrun_access
    def spoolmanDbVendors(self):
        if not self._spoolman_db_is_enabled():
            return self._spoolman_db_disabled_response()
        vendors, status = self._filamentDatabaseService.vendors(
            self._spoolman_db_ttl_days()
        )
        return flask.jsonify({"enabled": True, "vendors": vendors, "cache": status})

    @octoprint.plugin.BlueprintPlugin.route("/spoolmanDbMaterials", methods=["GET"])
    @no_firstrun_access
    def spoolmanDbMaterials(self):
        if not self._spoolman_db_is_enabled():
            return self._spoolman_db_disabled_response()
        vendor = request.args.get("vendor", "").strip()
        if not vendor:
            return flask.jsonify({"enabled": True, "materials": [], "cache": {"status": "fresh"}})
        materials, status = self._filamentDatabaseService.materials(
            vendor, self._spoolman_db_ttl_days()
        )
        return flask.jsonify({"enabled": True, "materials": materials, "cache": status})

    @octoprint.plugin.BlueprintPlugin.route("/spoolmanDbProducts", methods=["GET"])
    @no_firstrun_access
    def spoolmanDbProducts(self):
        if not self._spoolman_db_is_enabled():
            return self._spoolman_db_disabled_response()
        vendor = request.args.get("vendor", "").strip()
        material = request.args.get("material", "").strip()
        if not vendor or not material:
            return flask.jsonify({"enabled": True, "products": [], "cache": {"status": "fresh"}})
        products, status = self._filamentDatabaseService.products(
            vendor, material, self._spoolman_db_ttl_days()
        )
        return flask.jsonify({"enabled": True, "products": products, "cache": status})

    @octoprint.plugin.BlueprintPlugin.route("/spoolmanDbRefresh", methods=["POST"])
    @no_firstrun_access
    def spoolmanDbRefresh(self):
        if not Permissions.SETTINGS.can():
            return "Insufficient rights", 403
        if not self._spoolman_db_is_enabled():
            return self._spoolman_db_disabled_response()
        cache, status = self._filamentDatabaseService.ensure_index(
            self._spoolman_db_ttl_days(), force=True
        )
        return flask.jsonify({"enabled": True, "cache": status, "available": cache is not None})

    def _updateSpoolModelFromJSONData(self, spoolModel, jsonData):
        # collects human readable validation errors; a non-empty list aborts the save with HTTP 400
        validationErrors = []

        spoolModel.version = self._toIntFromJSONOrNone(
            "version", jsonData, validationErrors
        )
        # if statement is needed because assigning None is alos detected as an dirtyField
        if self._getValueFromJSONOrNone("databaseId", jsonData) is not None:
            spoolModel.databaseId = self._toIntFromJSONOrNone(
                "databaseId", jsonData, validationErrors, minValue=1
            )

        spoolModel.isTemplate = self._getValueFromJSONOrNone("isTemplate", jsonData)
        spoolModel.isActive = self._getValueFromJSONOrNone("isActive", jsonData)
        spoolModel.displayName = self._toStringFromJSONOrNone(
            "displayName", jsonData, validationErrors
        )
        spoolModel.vendor = self._toStringFromJSONOrNone(
            "vendor", jsonData, validationErrors
        )
        spoolModel.material = self._toStringFromJSONOrNone(
            "material", jsonData, validationErrors
        )
        spoolModel.density = self._toFloatFromJSONOrNone(
            "density", jsonData, validationErrors, minValue=0
        )
        spoolModel.diameter = self._toFloatFromJSONOrNone(
            "diameter", jsonData, validationErrors, minValue=0
        )
        spoolModel.diameterTolerance = self._toFloatFromJSONOrNone(
            "diameterTolerance", jsonData, validationErrors, minValue=0
        )
        spoolModel.colorName = self._toStringFromJSONOrNone(
            "colorName", jsonData, validationErrors
        )
        spoolModel.color = self._toStringFromJSONOrNone(
            "color", jsonData, validationErrors
        )
        spoolModel.finish = self._toStringFromJSONOrNone(
            "finish", jsonData, validationErrors
        )
        spoolModel.flowRateCompensation = self._toIntFromJSONOrNone(
            "flowRateCompensation", jsonData, validationErrors, minValue=0
        )
        spoolModel.temperature = self._toIntFromJSONOrNone(
            "temperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.minTemperature = self._toIntFromJSONOrNone(
            "minTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.maxTemperature = self._toIntFromJSONOrNone(
            "maxTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.bedTemperature = self._toIntFromJSONOrNone(
            "bedTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.minBedTemperature = self._toIntFromJSONOrNone(
            "minBedTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.maxBedTemperature = self._toIntFromJSONOrNone(
            "maxBedTemperature", jsonData, validationErrors, minValue=0
        )
        self._validateTemperatureRangePair(
            spoolModel.minTemperature,
            spoolModel.maxTemperature,
            "minTemperature",
            "maxTemperature",
            validationErrors,
        )
        self._validateTemperatureRangePair(
            spoolModel.minBedTemperature,
            spoolModel.maxBedTemperature,
            "minBedTemperature",
            "maxBedTemperature",
            validationErrors,
        )
        spoolModel.enclosureTemperature = self._toIntFromJSONOrNone(
            "enclosureTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.dryingTemperature = self._toIntFromJSONOrNone(
            "dryingTemperature", jsonData, validationErrors, minValue=0
        )
        spoolModel.dryingTime = self._toIntFromJSONOrNone(
            "dryingTime", jsonData, validationErrors, minValue=0
        )
        spoolModel.td = self._toFloatFromJSONOrNone(
            "td", jsonData, validationErrors, minValue=0
        )
        spoolModel.offsetTemperature = self._toIntFromJSONOrNone(
            "offsetTemperature", jsonData, validationErrors
        )
        spoolModel.offsetBedTemperature = self._toIntFromJSONOrNone(
            "offsetBedTemperature", jsonData, validationErrors
        )
        spoolModel.offsetEnclosureTemperature = self._toIntFromJSONOrNone(
            "offsetEnclosureTemperature", jsonData, validationErrors
        )
        spoolModel.totalWeight = self._toFloatFromJSONOrNone(
            "totalWeight", jsonData, validationErrors, minValue=0
        )
        spoolModel.spoolWeight = self._toFloatFromJSONOrNone(
            "spoolWeight", jsonData, validationErrors, minValue=0
        )
        spoolModel.remainingWeight = self._toFloatFromJSONOrNone(
            "remainingWeight", jsonData, validationErrors, minValue=0
        )
        spoolModel.totalLength = self._toIntFromJSONOrNone(
            "totalLength", jsonData, validationErrors, minValue=0
        )
        spoolModel.usedLength = self._toIntFromJSONOrNone(
            "usedLength", jsonData, validationErrors, minValue=0
        )
        spoolModel.usedWeight = self._toFloatFromJSONOrNone(
            "usedWeight", jsonData, validationErrors, minValue=0
        )
        spoolModel.code = self._toStringFromJSONOrNone(
            "code", jsonData, validationErrors
        )
        spoolModel.rfidTagKey = self._toStringFromJSONOrNone(
            "rfidTagKey", jsonData, validationErrors
        )
        spoolModel.batchNumber = self._toStringFromJSONOrNone(
            "batchNumber", jsonData, validationErrors
        )

        # spoolModel.firstUse = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("firstUse", jsonData))
        # spoolModel.lastUse = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("lastUse", jsonData))
        # spoolModel.purchasedOn = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("purchasedOn", jsonData))
        spoolModel.firstUse = self._toDateTimeFromJSONOrNone(
            "firstUseKO", jsonData, validationErrors
        )
        spoolModel.lastUse = self._toDateTimeFromJSONOrNone(
            "lastUseKO", jsonData, validationErrors
        )
        spoolModel.purchasedOn = self._toDateTimeFromJSONOrNone(
            "purchasedOnKO", jsonData, validationErrors
        )

        spoolModel.purchasedFrom = self._toStringFromJSONOrNone(
            "purchasedFrom", jsonData, validationErrors
        )
        spoolModel.cost = self._toFloatFromJSONOrNone(
            "cost", jsonData, validationErrors, minValue=0
        )
        spoolModel.costUnit = self._toStringFromJSONOrNone(
            "costUnit", jsonData, validationErrors
        )

        # TextField payloads: sanity cap at the MySQL TEXT limit so all backends behave the same
        maxTextLength = 65535

        # fall back to an empty list rather than dumping None: that would store the *string*
        # "null", and loadCatalogLabels() does json.loads() + iterates the result, so a single
        # such row breaks the label catalog (and with it the spool search) for the whole table.
        # The edit dialog always sends an array, so this only bites API clients that omit it.
        labels = self._getValueFromJSONOrNone("labels", jsonData)
        if labels is None:
            labels = []
        labelsJson = json.dumps(labels)
        if len(labelsJson) > maxTextLength:
            validationErrors.append(
                self._fieldLabel("labels")
                + " must not be longer than "
                + str(maxTextLength)
                + " characters"
            )
        spoolModel.labels = labelsJson

        spoolModel.noteText = self._toStringFromJSONOrNone(
            "noteText", jsonData, validationErrors, maxLength=maxTextLength
        )
        noteDeltaFormatJson = json.dumps(
            self._getValueFromJSONOrNone("noteDeltaFormat", jsonData)
        )
        if len(noteDeltaFormatJson) > maxTextLength:
            validationErrors.append(
                self._fieldLabel("noteDeltaFormat")
                + " must not be longer than "
                + str(maxTextLength)
                + " characters"
            )
        spoolModel.noteDeltaFormat = noteDeltaFormatJson
        spoolModel.noteHtml = self._toStringFromJSONOrNone(
            "noteHtml", jsonData, validationErrors, maxLength=maxTextLength
        )

        # required-field checks (mirrors the client-side rules so a direct API call cannot bypass them),
        # but only for real spools - templates are allowed to be incomplete
        if not spoolModel.isTemplate:
            if StringUtils.isEmpty(spoolModel.displayName):
                validationErrors.append("Displayname must not be empty")
            if StringUtils.isEmpty(spoolModel.colorName):
                validationErrors.append("Color must not be empty")

        return validationErrors

    def _getValueFromJSONOrNone(self, key, json):
        if key in json:
            return json[key]
        return None

    def _toStringFromJSONOrNone(self, key, json, validationErrors=None, maxLength=255):
        # defense-in-depth: rejects oversized strings before they reach the database layer
        value = self._getValueFromJSONOrNone(key, json)
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if (
            maxLength is not None
            and len(value) > maxLength
            and validationErrors is not None
        ):
            validationErrors.append(
                self._fieldLabel(key)
                + " must not be longer than "
                + str(maxLength)
                + " characters"
            )
        return value

    def _toFloatFromJSONOrNone(self, key, json, validationErrors=None, minValue=None):
        value = self._getValueFromJSONOrNone(key, json)
        if value is not None:
            if StringUtils.isNotEmpty(value):
                try:
                    value = float(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error(
                        "could not transform value '"
                        + str(value)
                        + "' for key '"
                        + key
                        + "' to float:"
                        + errorMessage
                    )
                    if validationErrors is not None:
                        validationErrors.append(
                            self._fieldLabel(key) + " must be a number"
                        )
                    value = None
                else:
                    if (
                        minValue is not None
                        and value < minValue
                        and validationErrors is not None
                    ):
                        validationErrors.append(
                            self._fieldLabel(key)
                            + " must not be less than "
                            + str(minValue)
                        )
            else:
                value = None
        return value

    def _toIntFromJSONOrNone(self, key, json, validationErrors=None, minValue=None):
        value = self._getValueFromJSONOrNone(key, json)
        if value is not None:
            if StringUtils.isNotEmpty(value):
                try:
                    value = int(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error(
                        "could not transform value '"
                        + str(value)
                        + "' for key '"
                        + key
                        + "' to int:"
                        + errorMessage
                    )
                    if validationErrors is not None:
                        validationErrors.append(
                            self._fieldLabel(key) + " must be a whole number"
                        )
                    value = None
                else:
                    if (
                        minValue is not None
                        and value < minValue
                        and validationErrors is not None
                    ):
                        validationErrors.append(
                            self._fieldLabel(key)
                            + " must not be less than "
                            + str(minValue)
                        )
            else:
                value = None
        return value

    def _validateTemperatureRangePair(
        self, minValue, maxValue, minKey, maxKey, validationErrors
    ):
        if (minValue is None) != (maxValue is None):
            validationErrors.append(
                self._fieldLabel(minKey)
                + " and "
                + self._fieldLabel(maxKey)
                + " must both be set or both left empty"
            )
        elif minValue is not None and maxValue is not None and minValue > maxValue:
            validationErrors.append(
                self._fieldLabel(minKey)
                + " must not be greater than "
                + self._fieldLabel(maxKey)
            )

    def _toDateTimeFromJSONOrNone(self, key, json, validationErrors=None):
        value = self._getValueFromJSONOrNone(key, json)
        try:
            return StringUtils.transformFromIsoToDateTimeOrNone(value)
        except Exception as e:
            errorMessage = str(e)
            self._logger.error(
                "could not transform value '"
                + str(value)
                + "' for key '"
                + key
                + "' to datetime:"
                + errorMessage
            )
            if validationErrors is not None:
                validationErrors.append(
                    self._fieldLabel(key) + " has an invalid date format"
                )
            return None

    # def _formatDateOrNone(self, dateValue):
    #   if dateValue != None:
    #       return dateValue.strftime('%d.%m.%Y %H:%M')
    #   return None
    # def _formatDateOrNone(self, dateValue):
    #   if dateValue != None:
    #       return datetime.strptime(str(dateValue), '%d.%m.%Y %H:%M')
    #   return None

    def loadSelectedSpools(self):
        spoolModelList = []
        databaseIds = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS]
        )

        for toolIndex, databaseId in enumerate(databaseIds):
            spoolModel = None
            if databaseId is not None:
                self._databaseManager.connectoToDatabase()
                spoolModel = self._databaseManager.loadSpool(databaseId)
                self._databaseManager.closeDatabase()
                if spoolModel is None:
                    self._logger.warning(
                        "Last selected Spool for Tool %d from plugin-settings not found in database. Maybe deleted in the meantime."
                        % toolIndex
                    )
            spoolModelList.append(spoolModel)
            # No event fired here on purpose: this is a pure read, called on every
            # sidebar poll, client (re)connect and file upload check. Firing
            # spool_selected here caused it to spam every ~5s while idle and to fire
            # on file uploads with no actual selection change (mdziekon #45,
            # WildRikku #4). Real selection changes are announced from _selectSpool()
            # via _announceSpoolSelectionChange().

        return spoolModelList

    def _createSpoolModelFromLegacy(self, allSpoolLegacyList):
        allSpoolModels = list()
        for spoolDict in allSpoolLegacyList:
            spoolModel = SpoolModel()

            nameUnicode = spoolDict["name"]
            usedWeightFloat = spoolDict["used"]
            totalWeightFloat = spoolDict["weight"]
            costFloat = spoolDict["cost"]
            profileDict = spoolDict["profile"]
            diameterFloat = profileDict["diameter"]
            materialUnicode = profileDict["material"]
            vendorUnicode = profileDict["vendor"]
            densityFloat = profileDict["density"]

            spoolModel.displayName = nameUnicode
            spoolModel.vendor = vendorUnicode

            spoolModel.material = materialUnicode
            spoolModel.density = densityFloat
            spoolModel.diameter = diameterFloat
            spoolModel.cost = costFloat
            spoolModel.costUnit = (
                self._filamentManagerPluginImplementation._settings.get(
                    ["currencySymbol"]
                )
            )
            spoolModel.totalWeight = totalWeightFloat
            spoolModel.usedWeight = usedWeightFloat

            spoolModel.usedLength = self._calculateUsedLength(
                spoolModel.usedWeight, spoolModel.density, spoolModel.diameter
            )

            allSpoolModels.append(spoolModel)

        return allSpoolModels

    def _calculateUsedLength(self, usedWeight, density, diameter):
        if diameter is None or density is None or usedWeight is None:
            self._logger.info(
                "Could not calculate used length because some values (usedWeigth, density, diameter) were missing"
            )
            return None
        radius = diameter / 2.0
        volume = (usedWeight) / density
        # length = volume / cross-section. The divisor needs the parentheses: without them
        # Python evaluates left to right and multiplies by the radii instead of dividing,
        # which produced lengths ~1.7x too small. Cross-check: 1000 g of PLA-ish filament
        # (density 1.04, diameter 1.75) yields 399761 mm, matching the totalLength the rest
        # of the plugin computes for such a spool.
        length = (volume * 1000) / (PI * radius * radius)
        lengthRounded = int(round(length))
        return lengthRounded

    def _resetSelectedSpools(self):
        self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS], [])
        self._settings.save()

    def _selectSpool(self, toolIndex, databaseId):
        # three cases
        #  1. databaseId != -1 toolIndex != -1  select spool for toool  ||
        #  2. databaseId == -1 toolIndex != -1  remove spool from tool  |
        #  3. databaseId != -1 toolIndex == -1  remove tool from spool  ||

        databaseIds = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS]
        )

        spoolModel = None
        if databaseId != -1:
            spoolModel = self._databaseManager.loadSpool(databaseId)
            if spoolModel is not None:
                self._logger.info(
                    "Store selected spool %s for tool %d in settings."
                    % (spoolModel.displayName, toolIndex)
                )
                # assign model to selected toolId
                if toolIndex != -1:
                    databaseIds = databaseIds + [None] * (
                        toolIndex + 1 - len(databaseIds)
                    )  # pad list to the needed length
                    idx = 0
                    for selectedSpoolDBId in databaseIds:
                        if selectedSpoolDBId == databaseId:
                            databaseIds[idx] = None
                            # check if tool changed, if yes inform user about the switch
                            if idx != toolIndex:
                                # spool was already assigned and is now used for different tool
                                self._sendMessageToClient(
                                    "warning",
                                    "Spool swapped",
                                    "Spool '"
                                    + spoolModel.displayName
                                    + "' was switched from Tool "
                                    + str(idx)
                                    + " to Tool "
                                    + str(toolIndex),
                                    autoclose=True,
                                )
                                self._announceSpoolSelectionChange(idx, None)
                            pass
                        else:
                            databaseIds[idx] = selectedSpoolDBId
                        idx = idx + 1
                    # assign new spool selection to the tool
                    databaseIds[toolIndex] = databaseId
                    self._announceSpoolSelectionChange(toolIndex, spoolModel)

                else:
                    # spool present, but no toolId -> remove spool from current toolIndex
                    i = 0
                    while i < len(databaseIds):
                        if databaseIds[i] == databaseId:
                            databaseIds[i] = None
                            self._announceSpoolSelectionChange(i, None)
                            break
                        i += 1
                    pass
            else:
                self._logger.warning(
                    "Selected Spool with id %d for tool %d not in database anymore. Maybe deleted in the meantime."
                    % (databaseId, toolIndex)
                )
                # remove spool from current toolIndex
                # (the missing "i += 1" used to make this loop spin forever whenever the
                #  requested id was not present in databaseIds, hanging the request thread)
                i = 0
                while i < len(databaseIds):
                    if databaseIds[i] == databaseId:
                        databaseIds[i] = None
                        self._announceSpoolSelectionChange(i, None)
                        break
                    i += 1
        else:
            if toolIndex == -1:
                self._logger.warn(
                    "databaseId and toolId is -1. This should not happen, strange!!!"
                )
                return None

            # remove current spool from toolIndex
            if toolIndex < len(databaseIds):
                databaseIds[toolIndex] = None
                self._announceSpoolSelectionChange(toolIndex, None)

        self._settings.set(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS], databaseIds
        )
        self._settings.save()

        # only check filament for the spool that was changed, as to not spam the user with warnings (for a specific toolIndex)
        if spoolModel is not None and toolIndex != -1:
            self.checkRemainingFilament(toolIndex)

        return spoolModel

    ################################################### APIs

    @octoprint.plugin.BlueprintPlugin.route("/sampleCSV", methods=["GET"])
    def sampleCSV(self):

        allSpoolModels = list()

        spoolModel = CSVExportImporter.createSampleSpoolModel()
        allSpoolModels.append(spoolModel)
        return Response(
            CSVExportImporter.transform2CSV(allSpoolModels),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=SpoolManager-SAMPLE.csv"
            },
        )

    ##############################################################################################   ALLOWED TO PRINT
    @octoprint.plugin.BlueprintPlugin.route("/allowedToPrint", methods=["GET"])
    @no_firstrun_access
    def allowed_to_print(self):

        checkForSelectedSpool = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED]
        )
        checkForFilamentLength = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH]
        )
        reminderSelectingSpool = self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_REMINDER_SELECTING_SPOOL]
        )

        spoolModels = self.loadSelectedSpools()
        # define variables for missing data here because we might have multiple tools and one tool with missing data
        # is enough to cause problems
        metaOrAttributesMissing = False
        metaDataMissing = False
        attributesMissing = False
        result = {
            "noSpoolSelected": [],
            "filamentNotEnough": [],
            "reminderSpoolSelection": [],
        }

        filamentLengthPresentInMeta = self._readingFilamentMetaData()
        printer_profile = self._printer_profile_manager.get_current_or_default()
        printerProfileToolCount = printer_profile["extruder"]["count"]
        # for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
        for toolIndex in range(printerProfileToolCount):
            # we go over the filamentlength because those are what matters for this print
            if filamentLengthPresentInMeta:
                if toolIndex >= len(self.metaDataFilamentLengths):
                    # if this tool is not used (no filaLenght) in this print, everything is fine
                    continue

            spoolModel = (
                spoolModels[toolIndex] if toolIndex < len(spoolModels) else None
            )

            infoData = {
                "toolIndex": toolIndex,
                "spoolName": (
                    spoolModel.displayName if spoolModel else "(no spool selected)"
                ),
                "material": spoolModel.material if spoolModel else "",
                "remainingWeight": spoolModel.remainingWeight if spoolModel else "",
                "toolOffset": spoolModel.offsetTemperature if spoolModel else "",
                "bedOffset": spoolModel.offsetBedTemperature if spoolModel else "",
                "enclosureOffset": (
                    spoolModel.offsetEnclosureTemperature if spoolModel else ""
                ),
            }

            requiredWeightResult = self.checkRemainingFilament(
                toolIndex, shouldWarn=False
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

            # TODO: remove combined attribute - possibly API breaking change
            if (
                requiredWeightResult["metaDataMissing"]
                or requiredWeightResult["attributesMissing"]
            ):
                metaOrAttributesMissing = True
            metaDataMissing = metaDataMissing or requiredWeightResult["metaDataMissing"]
            attributesMissing = (
                attributesMissing or requiredWeightResult["attributesMissing"]
            )

            detailedSpoolResult = None
            if (
                "detailedSpoolResult" in requiredWeightResult
                and len(requiredWeightResult["detailedSpoolResult"]) > 0
            ):
                detailedSpoolResult = requiredWeightResult["detailedSpoolResult"][0]

            if (
                spoolModel is not None
                and detailedSpoolResult is not None
                and detailedSpoolResult["spoolSelected"]
            ):
                if detailedSpoolResult["requiredLength"] > 0:
                    if detailedSpoolResult["notEnough"]:
                        # if not enough or needed amount could not calculated
                        result["filamentNotEnough"].append(infoData)
                    # add every spool for reminding, if more the 0gr is needed
                    result["reminderSpoolSelection"].append(infoData)
            elif checkForSelectedSpool:
                if detailedSpoolResult is not None:
                    if detailedSpoolResult["requiredLength"] > 0:
                        result["noSpoolSelected"].append(infoData)
                else:
                    result["noSpoolSelected"].append(infoData)

            #   if (metaNotPresent or
            #       attributesMissing or
            #       notEnough
            #   ):
            #       # if not enough or needed amount could not calculated
            #       result['filamentNotEnough'].append(infoData)
            #       if (metaNotPresent or
            #           attributesMissing):
            #           metaOrAttributesMissing = True
            #
            #   # add every spool for reminding
            #   result['reminderSpoolSelection'].append(infoData)
            # elif checkForSelectedSpool:
            #   # if no metatdata is present we cant check if this tool is needed, so we cant inform the user that a selection is missing
            #   if (filamentLengthPresentInMeta == True):
            #       result['noSpoolSelected'].append(infoData)

        # check if the user want a popup
        if not checkForFilamentLength:
            result["filamentNotEnough"] = []

        if not reminderSelectingSpool:
            # no popup, because turned off by user
            result["reminderSpoolSelection"] = []

        return flask.jsonify(
            {
                "result": result,
                "metaOrAttributesMissing": metaOrAttributesMissing,  # deprecated
                "metaDataMissing": metaDataMissing,
                "attributesMissing": attributesMissing,
                "toolOffsetEnabled": self._settings.get_boolean(
                    [SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]
                ),
                "bedOffsetEnabled": self._settings.get_boolean(
                    [SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]
                ),
                "enclosureOffsetEnabled": self._settings.get_boolean(
                    [SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]
                ),
            }
        )

    #############################################################################################  START PRINT CONFIRMED
    @octoprint.plugin.BlueprintPlugin.route("/startPrintConfirmed", methods=["GET"])
    @no_firstrun_access
    def start_print_confirmed(self):
        spoolModels = self.loadSelectedSpools()
        printer_profile = self._printer_profile_manager.get_current_or_default()
        printerProfileToolCount = printer_profile["extruder"]["count"]
        # for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
        for toolIndex in range(printerProfileToolCount):
            spoolModel = (
                spoolModels[toolIndex] if toolIndex < len(spoolModels) else None
            )
            if spoolModel is not None:
                # - assign temp-offset here, because after the print is started (event: ) it is too late Events.PRINT_STARTED
                try:
                    self.set_temp_offsets(toolIndex, spoolModel)
                except Exception as e:
                    self._logger.exception(
                        "Temperature offsets for Spool '"
                        + str(spoolModel.displayName)
                        + "' failed to set!"
                    )
                    self._sendMessageToClient(
                        "warning",
                        "Temperature offsets for Spool '"
                        + str(spoolModel.displayName)
                        + "' failed to set!",
                        str(e),
                    )

        return flask.jsonify({"result": "goForIt"})

    #####################################################################################################   SELECT SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/selectSpool", methods=["PUT"])
    @no_firstrun_access
    def select_spool(self):
        jsonData = request.json

        databaseId = self._toIntFromJSONOrNone("databaseId", jsonData)
        toolIndex = self._toIntFromJSONOrNone("toolIndex", jsonData)

        if self._printer.is_printing():
            # changing a spool mid-print? we want to know
            commitCurrentSpoolValues = self._getValueFromJSONOrNone(
                "commitCurrentSpoolValues", jsonData
            )
            if commitCurrentSpoolValues is None:
                self._logger.warning(
                    "selectSpool endpoint called mid-print without commitCurrentState parameter - this shouldn't happen"
                )
                abort(409)

            if commitCurrentSpoolValues:
                self._logger.info("commitCurrentSpoolValues == True")
                self.commitOdometerData()

        spoolModel = self._selectSpool(toolIndex, databaseId)

        spoolModelAsDict = None
        if spoolModel is not None:
            spoolModelAsDict = Transformer.transformSpoolModelToDict(spoolModel)

        try:
            self.set_temp_offsets(toolIndex, spoolModel)
        except Exception as e:
            self._sendMessageToClient(
                "warning", "Temperature offsets failed to set!", str(e)
            )

        self.checkRemainingFilament()

        return flask.jsonify({"selectedSpool": spoolModelAsDict})

    #####################################################################################################   LOAD SINGLE SPOOL

    @octoprint.plugin.BlueprintPlugin.route("/spool/<int:databaseId>", methods=["GET"])
    @no_firstrun_access
    def getSpoolById(self, databaseId):
        spoolModel = self._databaseManager.loadSpool(databaseId)

        if spoolModel is None:
            abort(404)

        return flask.jsonify(
            {"spool": Transformer.transformSpoolModelToDict(spoolModel)}
        )

    @octoprint.plugin.BlueprintPlugin.route("/spool/byCode/<string:code>", methods=["GET"])
    @no_firstrun_access
    def getSpoolByCode(self, code):
        # Resolves a spool by its `code` field (an RFID tag UID, e.g. a foreign/manufacturer
        # tag such as a Snapmaker U1 tag) instead of databaseId. Mirrors getSpoolById's
        # response shape so callers (e.g. OctoScale) can treat both lookups the same way.
        # Matching itself lives in DatabaseManager.loadSpoolByCode() (also used to be U1's
        # lookup) - this is just the HTTP-facing twin of getSpoolById above.
        spoolModel = self._databaseManager.loadSpoolByCode(code)

        if spoolModel is None:
            # Fallback: `code` is deliberately no longer set from an RFID UID (see
            # U1RfidManager.deriveRfidTagKey()'s PRELIMINARY collision note - a Snapmaker
            # spool's two physical tags report different full UIDs, so U1RfidManager now
            # matches on the last-4-hex-chars rfidTagKey instead). A caller here (e.g.
            # OctoScale) may still pass a full tag UID it just scanned; try the same
            # derivation before giving up, so spools taught in via the U1 flow remain
            # resolvable through this endpoint too.
            rfidTagKey = deriveRfidTagKey(code)
            if rfidTagKey:
                spoolModel = self._databaseManager.loadSpoolByRfidTagKey(rfidTagKey)

        if spoolModel is None:
            abort(404)

        return flask.jsonify(
            {"spool": Transformer.transformSpoolModelToDict(spoolModel)}
        )

    #####################################################################################################   MEASURED WEIGHT (SCALE)

    # Translate a gross reading from a scale (spool core + filament) into the fields the database
    # actually stores. Two things make this less obvious than it looks:
    #  - remainingWeight is *derived*: DatabaseManager.saveSpool() recomputes it as
    #    totalWeight - usedWeight on every save, so assigning it here would be thrown away.
    #    The measurement therefore has to be written through usedWeight.
    #  - a mis-tared or overloaded scale must never push negative weights into the database,
    #    hence the clamping below.
    # Returns the resolved remaining weight, or None if the spool lacks the reference values.
    def _applyMeasuredGrossWeight(
        self, spoolModel, grossWeight, spoolWeightOverride, validationErrors
    ):
        spoolWeight = (
            spoolWeightOverride
            if spoolWeightOverride is not None
            else spoolModel.spoolWeight
        )
        if spoolWeight is None:
            # without the empty spool weight a gross reading carries no usable information
            validationErrors.append(
                self._fieldLabel("spoolWeight")
                + " is not set for this spool, so a gross weight cannot be interpreted"
            )
            return None
        if spoolModel.totalWeight is None:
            validationErrors.append(
                self._fieldLabel("totalWeight")
                + " is not set for this spool, so a gross weight cannot be interpreted"
            )
            return None

        spoolModel.spoolWeight = spoolWeight

        remainingWeight = grossWeight - spoolWeight
        if remainingWeight < 0:
            # scale not tared, or the wrong spool weight stored - clamp instead of storing nonsense
            self._logger.warning(
                "Measured gross weight %s g is below the empty spool weight %s g - clamping remaining filament to 0."
                % (str(grossWeight), str(spoolWeight))
            )
            remainingWeight = 0.0
        if remainingWeight > spoolModel.totalWeight:
            # more filament than the spool ever held - clamp so usedWeight cannot go negative
            self._logger.warning(
                "Measured remaining filament %s g exceeds the initial amount %s g - clamping to the initial amount."
                % (str(remainingWeight), str(spoolModel.totalWeight))
            )
            remainingWeight = spoolModel.totalWeight

        spoolModel.usedWeight = spoolModel.totalWeight - remainingWeight

        # keep the length fields in step with the weights, otherwise the UI shows a spool as
        # 38% used by weight and 0% used by length at the same time. Needs density+diameter;
        # if either is missing the helper logs and returns None, and we leave the old value alone.
        usedLength = self._calculateUsedLength(
            spoolModel.usedWeight, spoolModel.density, spoolModel.diameter
        )
        if usedLength is not None:
            spoolModel.usedLength = usedLength

        return remainingWeight

    # Tool index this spool is currently loaded into, or None. Only used to enrich the event
    # payload so MQTT/HA can republish the tool state for a spool that is currently in use.
    def _findSelectedToolIndexForSpool(self, databaseId):
        databaseIds = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS]
        )
        if databaseIds is None:
            return None
        for toolIndex, selectedDatabaseId in enumerate(databaseIds):
            if selectedDatabaseId == databaseId:
                return toolIndex
        return None

    @octoprint.plugin.BlueprintPlugin.route(
        "/spool/<int:databaseId>/measuredWeight", methods=["PUT"]
    )
    @no_firstrun_access
    def updateMeasuredWeight(self, databaseId):
        # Write back a weight measured by an external scale (e.g. an ESP32 + load cell).
        # Deliberately *not* handled by /saveSpool: that route is a full replace built for the
        # edit dialog and nulls every field the caller omits, which a scale has no way to supply.
        self._logger.info(
            "API update measured weight for spool with database id '"
            + str(databaseId)
            + "'"
        )
        jsonData = request.json
        if jsonData is None:
            return make_response(
                jsonify({"validationErrors": ["Request body must be JSON"]}), 400
            )

        validationErrors = []
        grossWeight = self._toFloatFromJSONOrNone(
            "grossWeight", jsonData, validationErrors, minValue=0
        )
        # optional: correct the stored empty spool weight in the same call, handy when a
        # brand new spool is weighed for the first time
        spoolWeightOverride = self._toFloatFromJSONOrNone(
            "spoolWeight", jsonData, validationErrors, minValue=0
        )
        if grossWeight is None and not validationErrors:
            validationErrors.append(
                self._fieldLabel("grossWeight") + " must not be empty"
            )
        if validationErrors:
            self._logger.warning(
                "Update measured weight rejected, validation errors: "
                + str(validationErrors)
            )
            return make_response(jsonify({"validationErrors": validationErrors}), 400)

        self._databaseManager.connectoToDatabase()
        spoolModel = self._databaseManager.loadSpool(
            databaseId, withReusedConnection=True
        )
        if spoolModel is None:
            self._databaseManager.closeDatabase()
            abort(404)

        remainingWeight = self._applyMeasuredGrossWeight(
            spoolModel, grossWeight, spoolWeightOverride, validationErrors
        )
        if validationErrors:
            self._databaseManager.closeDatabase()
            self._logger.warning(
                "Update measured weight rejected, validation errors: "
                + str(validationErrors)
            )
            return make_response(jsonify({"validationErrors": validationErrors}), 400)

        # a scale calling this gets the 409 below; a popup in the browser would be noise for
        # a conflict the user did not cause and cannot act on from here
        savedDatabaseId = self._databaseManager.saveSpool(
            spoolModel, withReusedConnection=True, suppressConflictMessage=True
        )
        self._databaseManager.closeDatabase()

        if savedDatabaseId is None:
            # saveSpool returns None on a version conflict or a deleted row - without this
            # check we would answer 200 while nothing was written.
            self._logger.warning(
                "Could not store measured weight for spool with database id '"
                + str(databaseId)
                + "'"
            )
            return make_response(
                jsonify(
                    {
                        "error": "Could not store the measured weight, the spool was modified or deleted in the meantime."
                    }
                ),
                409,
            )

        eventPayload = {
            "databaseId": spoolModel.databaseId,
            "spoolName": spoolModel.displayName,
            "material": spoolModel.material,
            "colorName": spoolModel.colorName,
            "grossWeight": grossWeight,
            "remainingWeight": remainingWeight,
            "usedWeight": spoolModel.usedWeight,
        }
        toolIndex = self._findSelectedToolIndexForSpool(spoolModel.databaseId)
        if toolIndex is not None:
            # spool is currently loaded -> MQTT can republish this tool's state
            eventPayload["toolId"] = toolIndex
        self._sendPayload2EventBus(
            EventBusKeys.EVENT_BUS_SPOOL_WEIGHT_MEASURED, eventPayload
        )

        # data for the sidebar
        self.checkRemainingFilament()

        # Push a live update to all connected OctoPrint clients so an already-open
        # SpoolManager UI reflects the OctoScale weight update without a manual tab switch.
        # This endpoint is called by an external scale, not the dialog's own JS, so there
        # is no client-side state update happening on its own (unlike the edit dialog path).
        self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))

        return flask.jsonify(
            {"spool": Transformer.transformSpoolModelToDict(spoolModel)}
        )

    #####################################################################################################   CREATE SPOOL (SCALE)

    @octoprint.plugin.BlueprintPlugin.route("/spool", methods=["POST"])
    @no_firstrun_access
    def createSpool(self):
        # Create a spool and answer with its new database id, so an external device (scale, NFC
        # writer) can put that id on a tag right away. /saveSpool could create spools too, but it
        # answers with an empty body - the caller would have to guess the id it just created.
        self._logger.info("API create spool")
        jsonData = request.json
        if jsonData is None:
            return make_response(
                jsonify({"validationErrors": ["Request body must be JSON"]}), 400
            )

        spoolModel = SpoolModel()
        # a create is exactly the full-replace case this mapper was written for, so it also
        # applies the same required-field rules as the edit dialog
        validationErrors = self._updateSpoolModelFromJSONData(spoolModel, jsonData)
        # ignore any client supplied id/version, this row does not exist yet
        spoolModel.databaseId = None
        spoolModel.version = None

        # totalLength is derived from totalWeight in the edit dialog's JS and only ever reaches
        # the backend as a submitted field. An API client has no such conversion, so derive it
        # here when it was not supplied - otherwise every length based display stays empty.
        if spoolModel.totalLength is None:
            totalLength = self._calculateUsedLength(
                spoolModel.totalWeight, spoolModel.density, spoolModel.diameter
            )
            if totalLength is not None:
                spoolModel.totalLength = totalLength

        # optional convenience: let a scale send its gross reading directly instead of
        # pre-calculating usedWeight itself
        grossWeight = self._toFloatFromJSONOrNone(
            "grossWeight", jsonData, validationErrors, minValue=0
        )
        if grossWeight is not None and not validationErrors:
            self._applyMeasuredGrossWeight(
                spoolModel, grossWeight, None, validationErrors
            )

        if validationErrors:
            self._logger.warning(
                "Create spool rejected, validation errors: " + str(validationErrors)
            )
            return make_response(jsonify({"validationErrors": validationErrors}), 400)

        self._databaseManager.connectoToDatabase()
        savedDatabaseId = self._databaseManager.saveSpool(
            spoolModel, withReusedConnection=True
        )
        if savedDatabaseId is None:
            self._databaseManager.closeDatabase()
            self._logger.error("Could not create spool")
            return make_response(jsonify({"error": "Could not create the spool."}), 500)

        # resolve display name variables ({id} is only known after the initial save), but never inside templates
        if not spoolModel.isTemplate:
            if self._resolveDisplayNameVariables(spoolModel):
                self._databaseManager.saveSpool(spoolModel, withReusedConnection=True)
        self._databaseManager.closeDatabase()

        eventPayload = {
            "databaseId": spoolModel.databaseId,
            "spoolName": spoolModel.displayName,
            "material": spoolModel.material,
            "colorName": spoolModel.colorName,
            "remainingWeight": spoolModel.remainingWeight,
        }
        self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_ADDED, eventPayload)

        # data for the sidebar
        self.checkRemainingFilament()

        # Push a live update to all connected OctoPrint clients, same reasoning as
        # updateMeasuredWeight above - this is an external-device endpoint (scale/NFC writer).
        self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))

        return make_response(
            jsonify(
                {
                    "databaseId": spoolModel.databaseId,
                    "spool": Transformer.transformSpoolModelToDict(spoolModel),
                }
            ),
            201,
        )

    #####################################################################################################   OCTOSCALE PROXY

    # OctoScale is an ESP32 based scale with an NFC reader, reachable over plain HTTP on the local
    # network. The browser cannot talk to it directly: OctoPrint is frequently served over HTTPS
    # (mixed content) and the device sends no CORS headers. So every call is proxied here.
    #
    # Device API (see the OctoScale firmware): GET /version, /weight, /tare, /nfcprobe,
    # POST /nfcwritespool, GET /nfcwritestatus. /version, /weight and /tare answer plain
    # text; /nfcprobe, /nfcwritestatus and the /nfcwritespool response are JSON.
    #
    # NOTE: there is no "/nfc" endpoint on the device - an earlier version of this plugin
    # called one and always got HTTP 404 (confirmed live). /nfcprobe is what actually
    # exists and reports the tag currently on the reader (present/type/uid/parsed id).
    # Writing was always asynchronous on the firmware side too: /nfcwriteid (legacy,
    # id-only) and /nfcwritespool (extended fields) both return 202 immediately and the
    # result must be polled via /nfcwritestatus - this plugin previously treated the write
    # call as synchronous, which never actually reflected a real success/failure.

    # Requests to the device can occasionally take several seconds (measured: 0.02s to 5.0s for
    # /weight while a tag is being polled). Timeouts below ~8s therefore report a connection
    # failure for requests that actually succeed, which is what a short timeout looked like in
    # practice. Kept generous rather than clever - a slow answer is still a correct answer.
    #
    # The cause of that spread is NOT core contention, contrary to what this comment claimed
    # before: the device is a dual-core ESP32-S3 (platformio.ini: esp32-s3-devkitc-1) whose NFC
    # and scale tasks are pinned to core 0 while the web server runs in loop() on core 1, so they
    # do not compete for a core at all. The firmware's own /weight handler merely reads a variable
    # the scale task already filled in. The real cause is unconfirmed - WiFi power-save or TCP
    # handling are the likelier candidates - so the generous timeout stays as a safety margin.
    OCTOSCALE_TIMEOUT_SECONDS = 8.0

    def _getOctoScaleBaseUrl(self):
        # Returns (baseUrl, errorResponse). errorResponse is None when OctoScale is usable.
        if not self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_ENABLED]
        ):
            return (
                None,
                make_response(
                    jsonify(
                        {
                            "success": False,
                            "error": "OctoScale is not enabled in the SpoolManager settings.",
                        }
                    ),
                    409,
                ),
            )

        baseUrl = self._settings.get([SettingsKeys.SETTINGS_KEY_OCTOSCALE_URL])
        baseUrl = self._normalizeOctoScaleUrl(baseUrl)
        if baseUrl is None:
            return (
                None,
                make_response(
                    jsonify(
                        {
                            "success": False,
                            "error": "No OctoScale address configured in the SpoolManager settings.",
                        }
                    ),
                    409,
                ),
            )
        return (baseUrl, None)

    def _normalizeOctoScaleUrl(self, baseUrl):
        # Implementation lives in common/OctoScaleUrl.py so it can be unit-tested without
        # flask/OctoPrint; kept as a method here so all call sites stay unchanged.
        return OctoScaleUrl.normalizeOctoScaleUrl(baseUrl)

    def _callOctoScale(self, baseUrl, path, timeout=None, method="GET", json=None):
        # Returns (response, errorMessage). Never raises - every transport problem comes back
        # as a message the UI can show next to the weight readout.
        # /nfcwritespool needs a POST with a JSON body (the field write, not just a start
        # signal) - method/json are only used by that caller, everything else keeps
        # defaulting to the plain GETs the device otherwise expects.
        import requests

        url = baseUrl + path
        try:
            response = requests.request(
                method,
                url,
                json=json,
                timeout=(
                    timeout if timeout is not None else self.OCTOSCALE_TIMEOUT_SECONDS
                ),
            )
        except requests.exceptions.Timeout:
            return (None, "OctoScale did not answer in time (" + url + ")")
        except requests.exceptions.RequestException as e:
            return (None, "Could not reach OctoScale: " + str(e))

        # /nfcwritespool and /nfcwriteid answer 202 "started" for an accepted async write -
        # that is success, not an error, so 2xx is accepted wholesale rather than just 200.
        if response.status_code < 200 or response.status_code >= 300:
            # A 409 is not a transport failure but a decision by the device: it refuses to
            # overwrite a tag it recognized as foreign, or another RF job is already
            # running. Those answers carry a structured JSON body the caller needs (error,
            # retryable, overridable) - so hand the response along instead of dropping it.
            # Everything else stays a plain message, unchanged.
            errorMessage = "OctoScale answered with HTTP " + str(response.status_code)
            if response.status_code == 409:
                return (response, errorMessage)
            return (None, errorMessage)
        return (response, None)

    def _octoScaleFloatOrError(self, response):
        try:
            return (float(response.text.strip()), None)
        except (ValueError, AttributeError):
            return (
                None,
                "OctoScale sent an unreadable value: '" + str(response.text)[:80] + "'",
            )

    @octoprint.plugin.BlueprintPlugin.route(
        "/octoscale/testConnection", methods=["PUT"]
    )
    @no_firstrun_access
    def testOctoScaleConnection(self):
        # Takes the address from the request body, not from the stored settings, so the user can
        # test what they just typed without saving first (same idea as testDatabaseConnection).
        jsonData = request.json
        baseUrl = None
        if jsonData is not None:
            baseUrl = jsonData.get("octoScaleUrl")
        if baseUrl is None or not str(baseUrl).strip():
            baseUrl = self._settings.get([SettingsKeys.SETTINGS_KEY_OCTOSCALE_URL])

        baseUrl = self._normalizeOctoScaleUrl(baseUrl)
        if baseUrl is None:
            return flask.jsonify(
                {"success": False, "error": "Please enter the OctoScale address first."}
            )

        response, errorMessage = self._callOctoScale(baseUrl, "/version")
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        return flask.jsonify({"success": True, "version": response.text.strip()})

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/weight", methods=["GET"])
    @no_firstrun_access
    def getOctoScaleWeight(self):
        # Polled roughly once per second while a weighing panel is open, so it stays quiet in the log.
        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        response, errorMessage = self._callOctoScale(baseUrl, "/weight")
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        grams, errorMessage = self._octoScaleFloatOrError(response)
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        return flask.jsonify({"success": True, "grams": grams})

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/tare", methods=["POST"])
    @no_firstrun_access
    def tareOctoScale(self):
        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        self._logger.info("Taring OctoScale")
        response, errorMessage = self._callOctoScale(baseUrl, "/tare")
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        return flask.jsonify({"success": True})

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/nfc", methods=["GET"])
    @no_firstrun_access
    def getOctoScaleNfcStatus(self):
        # Talks to /nfcprobe (there is no "/nfc" endpoint on the device, see the class
        # comment above). /nfcprobe answers roughly:
        # {debug, ready, present, type, typeName, uid, idParsed, idText, flowWouldUse,
        #  tagType, capacityBytes, writeFormat, formatLabel, hasExtendedData, extended}
        # idParsed/idText hold the spool id already on the tag (idParsed is -1 / idText is
        # "" for a blank tag or a tag with no parseable id). tagType/capacityBytes/
        # writeFormat/formatLabel/hasExtendedData/extended are newer fields older firmware
        # may not send yet - all are read with .get() and degrade gracefully to "unknown"/
        # the legacy format. NOTE: "typeName" is the coarse protocol class the firmware
        # reports ("NFC-A"/"NFC-V"/"no tag"), NOT a human label for the specific tag -
        # "formatLabel" is the human-readable one ("Mifare Classic 1K", "NTAG215", ...).
        # "extended", when present, mirrors the same field set _buildFullSpoolPayload()
        # writes (see TagFormats.py) with whatever subset actually fit on the tag - used
        # by the UI to show a before/after diff when re-writing a tag that already
        # belongs to the target spool, instead of just warning that data exists.
        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        response, errorMessage = self._callOctoScale(baseUrl, "/nfcprobe")
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        try:
            nfcData = response.json()
        except ValueError:
            return flask.jsonify(
                {"success": False, "error": "OctoScale sent an unreadable NFC status"}
            )

        existingSpoolId = None
        idParsed = nfcData.get("idParsed")
        if isinstance(idParsed, int) and idParsed >= 0:
            existingSpoolId = idParsed
        else:
            rawIdText = nfcData.get("idText")
            if rawIdText is not None and str(rawIdText).strip().isdigit():
                existingSpoolId = int(str(rawIdText).strip())

        tagType = nfcData.get("tagType") or "unknown"
        nfcvFormatSetting = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT]
        )
        ntagFormatSetting = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT]
        )

        result = {
            "success": True,
            "ready": nfcData.get("ready"),
            "present": nfcData.get("present"),
            "uid": nfcData.get("uid"),
            "spoolId": existingSpoolId,
            "tagType": tagType,
            "tagTypeName": nfcData.get("typeName"),
            "capacityBytes": nfcData.get("capacityBytes"),
            "writeFormat": nfcData.get("writeFormat")
            or TagFormats.formatForTagType(tagType, nfcvFormatSetting, ntagFormatSetting),
            "formatLabel": nfcData.get("formatLabel"),
            "hasExtendedData": nfcData.get("hasExtendedData") or False,
            "extended": nfcData.get("extended") or None,
            # "empty" | "foreign" | "" - what the firmware makes of the data already on the
            # tag. "foreign" means the tag carries data in a format OctoScale does not
            # recognize (most likely another vendor's), which a write would destroy. Only
            # reported for Mifare Classic and NTAG; NFC-V has no page reader on the normal
            # poll path and always answers "", so the frontend keeps its own heuristic as a
            # fallback (see isPossiblyForeignTag in SpoolManager-OctoScale.js). Read with
            # .get() like every other newer field: older firmware simply omits it.
            "occupancy": nfcData.get("occupancy") or "",
        }

        # Resolve the id already on the tag to a name, so the UI can warn with something
        # meaningful ("this tag belongs to <name>") before overwriting it.
        if existingSpoolId is not None:
            existingSpool = self._databaseManager.loadSpool(existingSpoolId)
            result["spoolDisplayName"] = (
                existingSpool.displayName if existingSpool is not None else None
            )

        return flask.jsonify(result)

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/writeTag", methods=["POST"])
    @no_firstrun_access
    def writeOctoScaleTag(self):
        # Fires the write and returns as soon as OctoScale accepts it (202) - the actual
        # write+verify happens on the device's own task and is polled via
        # getOctoScaleWriteStatus below. Treating this call as synchronous (the previous
        # implementation) never reflected a real result, since the firmware already
        # answered immediately.
        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        jsonData = request.json
        if jsonData is None:
            return make_response(
                jsonify({"success": False, "error": "Request body must be JSON"}), 400
            )

        databaseId = jsonData.get("databaseId")
        if databaseId is None or not str(databaseId).strip().isdigit():
            return make_response(
                jsonify(
                    {"success": False, "error": "A numeric databaseId is required"}
                ),
                400,
            )
        databaseId = int(str(databaseId).strip())

        spoolModel = self._databaseManager.loadSpool(databaseId)
        if spoolModel is None:
            abort(404)

        # The format is not requested by the caller: the firmware picks it from the tag
        # actually on the reader (Mifare Classic -> extended, NTAG -> OpenSpool, anything
        # else -> id-only) and reports back which one it used. We always send the full
        # field set; the firmware ignores what it can't use for the tag it sees.
        #
        # The one exception is NFC-V: it has three possible formats
        # (extended/OpenSpool/OpenPrintTag), and which one to use is a global user
        # preference (SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT), not something the firmware can
        # infer from the tag alone - so it's passed explicitly. NTAG213/215/216 mirror this
        # with their own independent preference (SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT,
        # "preferredNtagFormat") - openSpool or extended, extended silently rejected by the
        # firmware on a too-small NTAG213. The firmware ignores whichever of these two
        # fields doesn't apply to the tag actually on the reader.
        payload = TagFormats.getTagFormat(
            TagFormats.TAG_FORMAT_OCTOSCALE_EXTENDED
        )["buildPayload"](spoolModel)

        nfcvFormatSetting = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT]
        )
        if nfcvFormatSetting not in TagFormats.NFCV_FORMAT_SETTING_TO_TAG_FORMAT:
            # Guards against a stale/hand-edited setting reaching the firmware as an
            # unrecognized string; "extended" is the long-standing default/fallback.
            self._logger.warning(
                "Unknown "
                + SettingsKeys.SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT
                + " value '"
                + str(nfcvFormatSetting)
                + "', falling back to 'extended'"
            )
            nfcvFormatSetting = "extended"
        payload["preferredNfcvFormat"] = nfcvFormatSetting

        ntagFormatSetting = self._settings.get(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT]
        )
        if ntagFormatSetting not in TagFormats.NTAG_FORMAT_SETTING_TO_TAG_FORMAT:
            # Same guard as above; "openSpool" is the NTAG default/fallback.
            self._logger.warning(
                "Unknown "
                + SettingsKeys.SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT
                + " value '"
                + str(ntagFormatSetting)
                + "', falling back to 'openSpool'"
            )
            ntagFormatSetting = "openSpool"
        payload["preferredNtagFormat"] = ntagFormatSetting

        # Set once the user confirmed overwriting a tag the firmware flagged as foreign.
        # Without it the device keeps refusing with 409 - the confirmation happens in the
        # UI, but the decision has to reach the firmware for its own guard to step aside.
        if jsonData.get("force") is True:
            payload["force"] = True

        self._logger.info(
            "Writing NFC tag for spool with database id '"
            + str(databaseId)
            + "', preferredNfcvFormat='"
            + str(nfcvFormatSetting)
            + "', preferredNtagFormat='"
            + str(ntagFormatSetting)
            + "'"
        )
        response, errorMessage = self._callOctoScale(
            baseUrl, "/nfcwritespool", method="POST", json=payload, timeout=8.0
        )
        if errorMessage is not None:
            return flask.jsonify(
                self._describeOctoScaleWriteRefusal(response, errorMessage)
            )

        return flask.jsonify({"success": True, "databaseId": databaseId, "pending": True})

    # The firmware answers 409 for two very different situations, both with a structured
    # JSON body: it refuses to overwrite a tag it recognized as foreign ("foreign tag",
    # overridable with force=true), or another RF job is already running ("write in
    # progress", transient). They must be told apart by the "error" field and NOT by the
    # status code - one is a protection the user may consciously override, the other simply
    # needs a retry. Without this the UI showed the bare "OctoScale answered with HTTP 409",
    # which reads like a defect rather than the deliberate safeguard it is.
    def _describeOctoScaleWriteRefusal(self, response, fallbackMessage):
        result = {"success": False, "error": fallbackMessage}
        if response is None:
            return result

        try:
            body = response.json()
        except ValueError:
            return result
        if not isinstance(body, dict):
            return result

        message = body.get("message") or body.get("error")
        if message:
            result["error"] = str(message)
        # Passed through so the frontend can offer the right next step: an overridable
        # refusal gets a confirm-and-retry, a retryable one just needs another attempt.
        result["refusal"] = body.get("error") or None
        result["retryable"] = body.get("retryable") is True
        result["overridable"] = body.get("overridable") is True
        occupancy = body.get("occupancy")
        if occupancy:
            result["occupancy"] = str(occupancy)
        return result

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/writeStatus", methods=["GET"])
    @no_firstrun_access
    def getOctoScaleWriteStatus(self):
        # Proxies /nfcwritestatus: {pending, done, ok, error/msg, format, bytesWritten,
        # droppedFields, warning}. The device self-clears "done" once it has been read once, so
        # the frontend must stop polling as soon as done=true comes back (see
        # SpoolManager-OctoScale.js).
        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        response, errorMessage = self._callOctoScale(baseUrl, "/nfcwritestatus")
        if errorMessage is not None:
            return flask.jsonify({"success": False, "error": errorMessage})

        try:
            statusData = response.json()
        except ValueError:
            return flask.jsonify(
                {"success": False, "error": "OctoScale sent an unreadable write status"}
            )

        return flask.jsonify(
            {
                "success": True,
                "pending": statusData.get("pending"),
                "done": statusData.get("done"),
                "ok": statusData.get("ok"),
                "error": statusData.get("error") or statusData.get("msg"),
                "format": statusData.get("format"),
                "bytesWritten": statusData.get("bytesWritten"),
                "droppedFields": statusData.get("droppedFields"),
                # Fields the chosen format has no place for at all - as opposed to
                # droppedFields, which are those that were cut for lack of room. The
                # distinction matters to the user: a dropped field fits on a bigger tag, an
                # unsupported one only in a different format. Without this the write
                # reported plain success while silently leaving data behind (OpenSpool has
                # a fixed 12-key schema and can lose up to 15 of our fields).
                "unsupportedFields": statusData.get("unsupportedFields"),
                "warning": statusData.get("warning") or None,
                # UID of the tag actually written, used to auto-teach-in rfidTagKey after an
                # OpenPrintTag write (see /octoscale/teachRfidTagKey below). Older firmware
                # doesn't send this field yet - absence here just means teach-in falls back
                # to the UID last seen via /octoscale/nfc on the frontend side.
                "uid": statusData.get("uid"),
            }
        )

    def _buildTagKeyStore(self):
        return FilamentTagKeys.FilamentTagKeyStore(
            self._settings.get([SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_KEYS])
        )

    def _readMifareClassicTag(self, reader, scanResult):
        """Try each Classic parser with its own sector keys until one authenticates.

        Unlike the NTAG path there is no single dump every parser can share: the sectors are
        protected, and which keys open them depends on the vendor. So this is one read per
        candidate, stopping at the first that both authenticates and recognizes the content.

        Snapmaker derives a *different* key for each of the 16 sectors, which the current
        firmware contract cannot express in one call - it takes one key A for the whole tag.
        Those parsers are therefore read sector by sector and the results stitched back into
        a full-size image, so the parsers keep seeing absolute offsets into a 1K dump. If the
        firmware later accepts per-sector keys, only _readClassicWithKeys() changes.
        """
        attempted = []
        lastError = None
        lastRetryable = False
        keyStore = self._buildTagKeyStore()

        for descriptor in FilamentTagParsers.parsersForTagClass(
            FilamentTagModel.TagType.MIFARE_CLASSIC_1K
        ):
            parser = FilamentTagParsers.instantiateParser(descriptor, keyStore)
            attempted.append(descriptor["id"])

            keys = None
            if hasattr(parser, "authenticationKeys"):
                keys = parser.authenticationKeys(scanResult)
                if keys is None:
                    # The parser disabled itself (no key configured). Skipping here rather
                    # than filtering the registry keeps upstream's self-disabling behaviour.
                    continue

            readResult = self._readClassicWithKeys(
                reader, keys, descriptor.get("sectors")
            )
            if not readResult.ok:
                lastError = readResult.error
                lastRetryable = readResult.retryable
                continue

            try:
                filament = parser.parseTag(scanResult, readResult.data)
            except Exception:
                self._logger.exception(
                    "Parser '%s' raised while reading a Mifare Classic tag",
                    descriptor["id"],
                )
                continue

            if filament is not None:
                return self._buildReadTagResponse(
                    filament,
                    readResult,
                    scanResult,
                    {"attemptedParsers": attempted, "parserId": descriptor["id"]},
                )

        # Nothing claimed it. A failed authentication is the normal outcome for a tag whose
        # vendor is not supported yet, so this is not an error - just an unrecognized tag.
        self._logger.info(
            "Read a Mifare Classic tag uid='%s' that no parser recognized (tried: %s)",
            scanResult.uidHex,
            ", ".join(attempted) or "none",
        )
        return flask.jsonify(
            {
                "success": True,
                "parsed": False,
                "uid": scanResult.uidHex,
                "error": "This tag's format was not recognized.",
                "retryable": lastRetryable,
                "diagnostics": {
                    "attemptedParsers": attempted,
                    "parserId": None,
                    "tagType": "mifareClassic1k",
                    "error": lastError,
                },
            }
        )

    def _readClassicWithKeys(self, reader, keys, sectors):
        """Read a Classic tag with whatever keys its parser supplies.

        The firmware takes key A as a 16-entry array indexed by sector and rejects any other
        length outright, so a per-sector key set goes over in a single request - no need to
        read sector by sector. Verified against a real Snapmaker tag: 16/16 sectors
        authenticated in one call, 2816 ms.
        """
        if keys is None:
            # Factory-key parsers (Qidi): let the firmware use its default.
            return reader.readRaw(sectors=sectors)

        if isinstance(keys, str):
            # One key for the whole tag - expanded to the 16 entries the firmware wants.
            keys = [keys] * FilamentTagReader.SECTORS_PER_CLASSIC_1K

        return reader.readRaw(keyA=list(keys), sectors=sectors)

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/tagKeyStatus", methods=["GET"])
    @no_firstrun_access
    def getOctoScaleTagKeyStatus(self):
        # Per-key status for the settings dialog: "missing", "invalid" or "ok". Never the key
        # itself - the values are restricted (see get_settings_restricted_paths) and there is
        # no reason to send them back to a browser.
        #
        # This exists because without it a mistyped key is undiagnosable: a wrong key and no
        # key at all both end in a parser that silently never claims a tag. Upstream OpenRFID
        # only logs that; a settings dialog for end users has to say it out loud.
        keyStore = FilamentTagKeys.FilamentTagKeyStore(
            self._settings.get([SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_KEYS])
        )
        statuses = keyStore.statuses()

        return flask.jsonify(
            {
                "success": True,
                "statuses": statuses,
                # Which parsers are actually usable right now, so the dialog can say
                # "Bambu: needs a key" instead of leaving the user to work it out.
                "parsers": [
                    {
                        "id": descriptor["id"],
                        "label": descriptor["label"],
                        "requiresKey": descriptor.get("requiresKey", False),
                        "keyName": descriptor.get("keyName"),
                        "available": (
                            not descriptor.get("requiresKey", False)
                            or statuses.get(descriptor.get("keyName"))
                            == FilamentTagKeys.STATUS_OK
                        ),
                    }
                    for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values()
                ],
            }
        )

    @octoprint.plugin.BlueprintPlugin.route("/octoscale/readTag", methods=["POST"])
    @no_firstrun_access
    def readOctoScaleTag(self):
        # Reads a vendor tag (Bambu, Anycubic, Elegoo, ...) off the reader and returns the
        # spool fields it describes. Nothing is written to the database here: the answer is
        # a suggestion the user confirms in the wizard or the edit dialog.
        #
        # Only ever triggered by an explicit user action, never by the background poll - a
        # raw read costs the device up to a couple of seconds and blocks its RF hardware
        # for that time, which would be a poor trade for someone who only wants to write.
        if not self._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_OCTOSCALE_TAG_READING_ENABLED]
        ):
            return make_response(
                jsonify(
                    {
                        "success": False,
                        "error": "Reading vendor RFID tags is not enabled in the SpoolManager settings.",
                    }
                ),
                409,
            )

        baseUrl, errorResponse = self._getOctoScaleBaseUrl()
        if errorResponse is not None:
            return errorResponse

        reader = FilamentTagReader.OctoScaleTagReader(
            self._callOctoScale, baseUrl, self._logger
        )

        scanResult = reader.probe()
        if scanResult is None:
            return flask.jsonify(
                {
                    "success": False,
                    "error": "No tag on the reader.",
                    "retryable": True,
                }
            )

        # NTAG is one keyless page walk that every parser then gets a go at. Mifare Classic
        # cannot work that way: each parser needs its own sector keys, so it takes one read
        # per candidate until one authenticates.
        if scanResult.tag_type == FilamentTagModel.TagType.MIFARE_CLASSIC_1K:
            return self._readMifareClassicTag(reader, scanResult)

        readResult = reader.readRaw()
        if not readResult.ok:
            self._logger.warning(
                "Could not read the tag from OctoScale: "
                + str(readResult.error)
                + " (retryable="
                + str(readResult.retryable)
                + ")"
            )
            return flask.jsonify(
                {
                    "success": False,
                    "error": readResult.error or "Could not read the tag.",
                    "retryable": readResult.retryable,
                    "diagnostics": readResult.toDiagnostics(),
                }
            )

        # Guard against a truncated UID before anything is derived from it - the reader can
        # report success on a partial anticollision result (see isPlausibleTagUid).
        uid = readResult.uid or scanResult.uidHex
        if not isPlausibleTagUid(uid):
            self._logger.warning(
                "Discarding a tag read with an implausible UID '"
                + str(uid)
                + "' ("
                + str(len(uid) // 2 if uid else 0)
                + " bytes) - the reader most likely aborted anticollision"
            )
            return flask.jsonify(
                {
                    "success": False,
                    "error": "The reader returned an incomplete tag ID. Please place the tag back on the reader and try again.",
                    "retryable": True,
                    "diagnostics": readResult.toDiagnostics(),
                }
            )

        # A UID that changed between the probe and the read means a different tag is on the
        # reader now, so the bytes belong to neither with any certainty. Compared upper-case
        # on both sides: uidHex is normalized, the device's string is not, and a case
        # difference alone must not look like a swapped tag.
        probeUid = (scanResult.uidHex or "").upper()
        readUid = (readResult.uid or "").upper()
        if readUid and probeUid and readUid != probeUid:
            self._logger.warning(
                "Discarding a tag read: the tag changed mid-read (probe='"
                + str(scanResult.uidHex)
                + "', read='"
                + str(readResult.uid)
                + "')"
            )
            return flask.jsonify(
                {
                    "success": False,
                    "error": "The tag changed while it was being read. Please try again.",
                    "retryable": True,
                    "diagnostics": readResult.toDiagnostics(),
                }
            )

        filament, parseDiagnostics = FilamentTagParsers.parseTagData(
            scanResult, readResult.data, keyStore=self._buildTagKeyStore()
        )

        rfidTagKey = None
        normalizedUid = normalizeCardUid(uid) if uid else None
        if normalizedUid:
            rfidTagKey = deriveRfidTagKey(normalizedUid)

        diagnostics = dict(readResult.toDiagnostics())
        diagnostics.update(parseDiagnostics)

        if filament is None:
            # The interesting failure: bytes arrived but no parser claimed them. Log which
            # ones were tried and how much data came back, so the next step is a comparison
            # against the tag's actual content rather than guesswork.
            self._logger.info(
                "Read vendor tag uid='"
                + str(normalizedUid)
                + "': no parser recognized it (tried: "
                + ", ".join(parseDiagnostics.get("attemptedParsers") or ["none"])
                + ", bytes="
                + str(len(readResult.data) if readResult.data else 0)
                + ")"
            )
            return flask.jsonify(
                {
                    "success": True,
                    "parsed": False,
                    "uid": normalizedUid,
                    "rfidTagKey": rfidTagKey,
                    "error": "This tag's format was not recognized.",
                    "diagnostics": diagnostics,
                }
            )

        return self._buildReadTagResponse(
            filament, readResult, scanResult, parseDiagnostics
        )

    def _buildReadTagResponse(
        self, filament, readResult, scanResult, parseDiagnostics
    ):
        """The success payload for a recognized tag - shared by the NTAG and Classic paths.

        Both branches must answer in exactly the same shape: the frontend has one code path
        for the result and cannot tell which kind of tag produced it.
        """
        uid = readResult.uid or scanResult.uidHex
        normalizedUid = normalizeCardUid(uid) if uid else None
        rfidTagKey = deriveRfidTagKey(normalizedUid) if normalizedUid else None

        diagnostics = dict(readResult.toDiagnostics())
        diagnostics.update(parseDiagnostics)
        diagnostics.update(
            FilamentTagToSpool.diagnosticsFor(filament, normalizedUid, rfidTagKey)
        )

        # A vendor tag carries no SpoolManager id, so an already-known spool can only be
        # found by the tag's own UID - the same path OpenPrintTag and the Snapmaker U1 use.
        matchedSpool = None
        if rfidTagKey:
            try:
                matchedSpool = self._databaseManager.loadSpoolByRfidTagKey(rfidTagKey)
            except Exception:
                self._logger.exception(
                    "Could not look up a spool for rfidTagKey '" + str(rfidTagKey) + "'"
                )

        parserDescriptor = FilamentTagParsers.getParser(filament.source_processor)

        # Logged because this endpoint was previously silent: a read that reached the device
        # and came back 200 left no trace in the plugin log at all, so "did the button even
        # work?" could only be answered from tornado's access log.
        self._logger.info(
            "Read vendor tag uid='"
            + str(normalizedUid)
            + "', recognized by parser '"
            + str(filament.source_processor)
            + "'"
        )

        return flask.jsonify(
            {
                "success": True,
                "parsed": True,
                "parserId": filament.source_processor,
                "parserLabel": (
                    parserDescriptor["label"] if parserDescriptor else None
                ),
                "fields": FilamentTagToSpool.genericFilamentToSpoolFields(
                    filament, normalizedUid
                ),
                "uid": normalizedUid,
                "rfidTagKey": rfidTagKey,
                "matchedSpoolId": (
                    matchedSpool.databaseId if matchedSpool is not None else None
                ),
                "matchedSpoolDisplayName": (
                    matchedSpool.displayName if matchedSpool is not None else None
                ),
                "diagnostics": diagnostics,
            }
        )

    @octoprint.plugin.BlueprintPlugin.route(
        "/octoscale/teachRfidTagKey", methods=["POST"]
    )
    @no_firstrun_access
    def teachOctoScaleRfidTagKey(self):
        # Auto-teach-in after a successful OpenPrintTag write: OPT tags carry no database id
        # (see the TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG docstring), so reading one back
        # falls to a UID lookup (GET /spool/byCode/<uid>) that resolves via rfidTagKey - the
        # same mechanism used for Snapmaker U1 tags. Without this endpoint the user would
        # have to copy the UID into the spool's Serial number/rfidTagKey by hand.
        #
        # Deliberately a separate endpoint rather than a side effect of the writeStatus poll
        # above: that endpoint is a pure proxy polled repeatedly while pending, a write
        # side-effect inside a polling GET would be wrong, and the device self-clears "done"
        # after one read so a retry there would silently lose the chance to teach in.
        #
        # Never overwrites silently - mirrors the warning deriveRfidTagKey()'s own docstring
        # prescribes for the U1 flow: an existing, different key or a collision with another
        # spool is reported back rather than applied, unless the caller passes force=true.
        jsonData = request.json
        if jsonData is None:
            return make_response(
                jsonify({"success": False, "error": "Request body must be JSON"}), 400
            )

        databaseId = jsonData.get("databaseId")
        if databaseId is None or not str(databaseId).strip().isdigit():
            return make_response(
                jsonify(
                    {"success": False, "error": "A numeric databaseId is required"}
                ),
                400,
            )
        databaseId = int(str(databaseId).strip())
        force = bool(jsonData.get("force"))

        spoolModel = self._databaseManager.loadSpool(databaseId)
        if spoolModel is None:
            abort(404)

        normalizedUid = normalizeCardUid(jsonData.get("uid"))

        # A truncated UID must never be taught in. The reader reports success even when its
        # anticollision aborts halfway, and the resulting fragment derives a different key
        # than the same tag's full UID - which would be written straight to the database
        # here (this endpoint saves without a further confirmation step), binding the spool
        # to a key the tag never presents again. deriveRfidTagKey() does not catch it: it
        # only needs 4 hex characters, and a fragment has more. Refused rather than forced,
        # since force=true is meant for overriding a *known* key, not a broken read.
        if normalizedUid and not isPlausibleTagUid(normalizedUid):
            self._logger.warning(
                "Refusing rfidTagKey teach-in for spool "
                + str(databaseId)
                + ": implausible tag UID '"
                + str(normalizedUid)
                + "' ("
                + str(len(normalizedUid) // 2)
                + " bytes) - the reader most likely aborted anticollision"
            )
            return flask.jsonify(
                {
                    "success": True,
                    "taught": False,
                    "reason": RfidTeachIn.REASON_NO_UID,
                    "error": "The reader reported an incomplete tag ID, so no tag key was stored. Please place the tag on the reader again.",
                    "retryable": True,
                }
            )

        newKey = deriveRfidTagKey(normalizedUid)

        conflictingSpool = None
        if newKey:
            conflictingSpool = self._databaseManager.loadSpoolByRfidTagKey(newKey)

        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey=newKey,
            existingKeyOnTargetSpool=spoolModel.rfidTagKey,
            conflictingSpoolId=(
                conflictingSpool.databaseId if conflictingSpool is not None else None
            ),
            targetSpoolId=databaseId,
            force=force,
        )

        if not shouldSave:
            self._logger.info(
                "OpenPrintTag rfidTagKey teach-in skipped for spool "
                + str(databaseId)
                + " (reason: "
                + reason
                + ")"
            )
            response = {"success": True, "taught": False, "reason": reason}
            if reason == RfidTeachIn.REASON_EXISTING_KEY_DIFFERS:
                response["existingKey"] = spoolModel.rfidTagKey
                response["newKey"] = newKey
            elif reason == RfidTeachIn.REASON_COLLISION:
                response["conflictingSpoolId"] = conflictingSpool.databaseId
                response["conflictingSpoolDisplayName"] = getattr(
                    conflictingSpool, "displayName", None
                )
                response["newKey"] = newKey
            return flask.jsonify(response)

        spoolModel.rfidTagKey = newKey
        self._databaseManager.saveSpool(spoolModel)
        self._logger.info(
            "OpenPrintTag rfidTagKey teach-in: spool "
            + str(databaseId)
            + " -> rfidTagKey '"
            + str(newKey)
            + "'"
        )
        return flask.jsonify({"success": True, "taught": True, "rfidTagKey": newKey})

    #####################################################################################################   OPENPRINTTAG

    @octoprint.plugin.BlueprintPlugin.route(
        "/spool/<int:databaseId>/openPrintTagPayload", methods=["GET"]
    )
    @no_firstrun_access
    def getOpenPrintTagPayload(self, databaseId):
        # Read-only preview of what an OpenPrintTag for this spool would contain (issue #56).
        # Writing is not possible yet, see the notes in common/OpenPrintTag.py, so this exists so
        # the mapping can be checked against real spools and an NFC-V capable writer can be
        # developed against a stable payload endpoint.
        spoolModel = self._databaseManager.loadSpool(databaseId)
        if spoolModel is None:
            abort(404)

        fields = OpenPrintTag.spoolModelToFields(spoolModel)
        # Should always be empty now that FIELD_KEY_MAP is complete - a non-empty result here
        # means a field was added to spoolModelToFields() without a matching key, i.e. a bug.
        unresolvedFields = OpenPrintTag.getUnresolvedFieldNames(fields)
        droppedFields = OpenPrintTag.getDroppedFieldNames(spoolModel)
        truncatedFields = OpenPrintTag.getTruncatedFieldNames(spoolModel)

        payloadBase64 = None
        encodingError = None
        if not unresolvedFields:
            try:
                payloadBase64 = base64.b64encode(
                    OpenPrintTag.buildTagPayload(spoolModel)
                ).decode("ascii")
            except (OpenPrintTag.UnresolvedFieldKeyError, ValueError) as e:
                encodingError = str(e)

        return flask.jsonify(
            {
                "success": True,
                "databaseId": databaseId,
                # Unwrapped for JSON; the encoder above still saw the Float32 markers.
                "fields": OpenPrintTag.fieldsForJson(fields),
                "payloadBase64": payloadBase64,
                "encodingComplete": payloadBase64 is not None,
                "unresolvedFields": unresolvedFields,
                # Values this spool has that the OpenPrintTag specification has no field for
                # at all (colorName, remainingWeight, enclosureTemperature, serial number,
                # batchNumber, purchasedOn) - they will not survive a write to this format.
                "droppedFields": droppedFields,
                # material_name/brand_name shortened to fit the spec's byte caps (63/31 UTF-8
                # bytes) - the firmware hard-fails on overflow rather than truncating, so this
                # flags a mismatch between what the preview shows and what a real write does.
                "truncatedFields": truncatedFields,
                "error": encodingError,
                "notes": (
                    "The OpenPrintTag integer key map has an internal inconsistency - please "
                    "report this."
                    if unresolvedFields
                    else None
                ),
            }
        )

    #####################################################################################################   U1 RFID

    # Status of the U1 RFID reader: the detection chain stage by stage (so the settings
    # UI can show exactly which one failed), the derived host and the last seen tags.
    @octoprint.plugin.BlueprintPlugin.route("/u1Rfid/status", methods=["GET"])
    @no_firstrun_access
    def getU1RfidStatus(self):
        manager = getattr(self, "_u1RfidManager", None)
        if manager is None:
            return flask.jsonify({"supported": False, "chainMessage": "not initialized"})
        manager.evaluateDetectionChain()
        return flask.jsonify(manager.getStatus())

    # "Test connection" button: re-runs the chain and lists all channels with their tags,
    # so it is immediately visible whether tags are read at all.
    @octoprint.plugin.BlueprintPlugin.route("/u1Rfid/test", methods=["POST"])
    @no_firstrun_access
    def testU1RfidConnection(self):
        manager = getattr(self, "_u1RfidManager", None)
        if manager is None:
            return flask.jsonify({"ok": False, "message": "U1 RFID support not initialized"})
        return flask.jsonify(manager.testConnection())

    # Last unknown tag UIDs per channel - feeds the "take over last U1 UID" button in the
    # edit dialog, so a dismissed popup does not lose the UID.
    @octoprint.plugin.BlueprintPlugin.route("/u1Rfid/unknownTags", methods=["GET"])
    @no_firstrun_access
    def getU1RfidUnknownTags(self):
        manager = getattr(self, "_u1RfidManager", None)
        if manager is None:
            return flask.jsonify({})
        return flask.jsonify(manager.getUnknownTags())

    #####################################################################################################   SELECT SPOOL (SHARED CORE)

    # Shared selection core for every "something scanned a spool" trigger: the QR route
    # and the U1 RFID reader both funnel through here, so both inherit the mid-print
    # guard, the tool clamping and the live UI push. Deliberately transport-agnostic -
    # it returns a status instead of a redirect, and the QR route turns that into one.
    #
    # Returns {"status": ..., "spoolModel": ..., "toolIndex": ...} with status being one
    # of "selected", "printing", "notfound" or "invalid".
    def selectSpoolForTool(self, toolIndex, databaseId, source="request"):
        try:
            databaseId = int(databaseId)
        except (TypeError, ValueError):
            return {"status": "invalid", "spoolModel": None, "toolIndex": toolIndex}

        if self._printer.is_printing():
            # not doing this mid-print since we can't ask the user what to do.
            # The selection is still refused, but the caller gets a status it can explain
            # to the user. See
            # https://github.com/mdziekon/OctoPrint-SpoolManager/issues/41 (@mdziekon)
            self._logger.info(
                "%s requested spool %d while printing - selection refused."
                % (source, databaseId)
            )
            return {"status": "printing", "spoolModel": None, "toolIndex": toolIndex}

        # Check existence up front and bail out *before* touching the selection: for an
        # unknown id _selectSpool() treats the situation as "the spool stored for this tool
        # vanished" and clears the tool's slot, which would deselect whatever is currently
        # loaded just because someone scanned a stale QR code.
        if self._databaseManager.loadSpool(databaseId) is None:
            self._logger.warning(
                "%s referenced spool id %d, which is not in the database."
                % (source, databaseId)
            )
            return {"status": "notfound", "spoolModel": None, "toolIndex": toolIndex}

        # Clamp to the printer profile's tool count instead of aborting: a typo in the
        # URL prefix (or a QR printed for a printer with more tools) must not raise a 500
        # or write into a slot that doesn't exist - it just falls back to tool 0.
        printer_profile = self._printer_profile_manager.get_current_or_default()
        printerProfileToolCount = printer_profile["extruder"]["count"]
        if toolIndex < 0 or toolIndex >= printerProfileToolCount:
            self._logger.warning(
                "%s requested tool %d, but the printer profile only has %d tool(s) - falling back to tool 0."
                % (source, toolIndex, printerProfileToolCount)
            )
            toolIndex = 0

        spoolModel = self._selectSpool(toolIndex, databaseId)

        if spoolModel is None:
            # spool existed a moment ago but is gone now (deleted between check and here)
            return {"status": "notfound", "spoolModel": None, "toolIndex": toolIndex}

        # Push a live update to all connected OctoPrint clients so an already-open
        # SpoolManager UI reflects the selection without a manual refresh. The internal
        # /selectSpool (PUT) path updates its own client-side state after the response;
        # these triggers have no such client, so without this message other open UIs
        # (e.g. a desktop browser) stay stale until reloaded.
        self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))
        return {"status": "selected", "spoolModel": spoolModel, "toolIndex": toolIndex}

    #####################################################################################################   SELECT SPOOL BY QR

    # Redirect back to the plugin tab, carrying the outcome of the QR selection so the
    # frontend can tell the user what happened. The status goes into a *real* query string
    # in front of the "#": a "?" inside the fragment (…-spoolId<id>?spmQrStatus=…) throws
    # OctoPrint's own startup code off while it restores the active tab from the hash, which
    # leaves the UI stuck on "Loading OctoPrint's UI". The frontend reads it back from
    # window.location.search once on load.
    def _buildQRCodeRedirect(self, databaseId, status):
        redirectURL = (
            flask.url_for("index", _external=True)
            + "?spmQrStatus="
            + status
            + "#tab_plugin_SpoolManager-spoolId"
            + str(databaseId)
        )
        # 302 (not 307): this is a plain GET, and preserving the method serves no purpose
        # here while behaving oddly in some QR-scanner in-app browsers.
        return flask.redirect(redirectURL, 302)

    @octoprint.plugin.BlueprintPlugin.route(
        "/selectSpoolByQRCode/<string:databaseId>", methods=["GET"]
    )
    @no_firstrun_access
    def selectSpoolByQRCode(self, databaseId):  # noqa: C901
        self._logger.info("API select spool by QR code" + str(databaseId))

        if "qrPreviewId" == databaseId:
            # Just pick a single spool
            spoolModel = self._databaseManager.loadFirstSingleSpool()
            if spoolModel is None:
                # empty database - nothing to preview
                abort(404)
            databaseId = spoolModel.databaseId

        # the route binds databaseId as a string, but the selected-spool settings hold ints.
        # without this cast the comparisons in _selectSpool() never match, so the
        # "spool already assigned to another tool" handling silently never runs.
        # Must happen before the is_printing() check below, so the redirect built there
        # already carries a clean numeric id (especially for the qrPreviewId case).
        try:
            databaseId = int(databaseId)
        except (TypeError, ValueError):
            databaseId = None
        if databaseId is None:
            # no usable spool id -> no meaningful tab to redirect to
            abort(400)

        # Optional "?tool=<N>" query param decides which tool/extruder the spool is loaded
        # into. Without it we keep the historical behaviour (tool 0), so existing QR codes
        # that only carry the spool id still work unchanged. The target tool is baked into
        # the scanned URL because a QR scan is a plain server-side GET - the server has no
        # other way to learn which extruder the user means.
        toolIndex = 0
        toolParam = request.args.get("tool")
        if toolParam is not None:
            try:
                toolIndex = int(toolParam)
            except (TypeError, ValueError):
                toolIndex = 0

        result = self.selectSpoolForTool(toolIndex, databaseId, source="QR code")
        return self._buildQRCodeRedirect(databaseId, result["status"])

    #####################################################################################################   GENERATE QR FOR SPOOL
    @octoprint.plugin.BlueprintPlugin.route(
        "/generateQRCode/<string:databaseId>", methods=["GET"]
    )
    @no_firstrun_access
    def generateSpoolQRCode(self, databaseId):

        if (
            databaseId == "qrPreviewId"
            or self._databaseManager.loadSpool(databaseId) is not None
        ):
            self._logger.info(
                "API generate QR code for Spool with databaseId: " + str(databaseId)
            )

            requestParameters = request.args

            fillColor = None
            backgroundColor = None
            if (
                "fillColor" in requestParameters
                and "backgroundColor" in requestParameters
            ):
                fillColor = requestParameters["fillColor"]
                backgroundColor = requestParameters["backgroundColor"]
            else:
                fillColor = self._settings.get(
                    [SettingsKeys.SETTINGS_KEY_QR_CODE_FILL_COLOR]
                )
                backgroundColor = self._settings.get(
                    [SettingsKeys.SETTINGS_KEY_QR_CODE_BACKGROUND_COLOR]
                )

            # verify color codes
            from PIL import ImageColor

            if fillColor.startswith("#"):
                fillColor = ImageColor.getcolor(fillColor, "RGB")
            if backgroundColor.startswith("#"):
                backgroundColor = ImageColor.getcolor(backgroundColor, "RGB")

            # windowLocation = request.args.get("windowlocation")
            from PIL import Image

            imageFileLocation = self._basefolder + "/static/images/SPMByOlli.png"
            olliImage = Image.open(imageFileLocation)  # .crop((175, 90, 235, 150))

            # https://note.nkmk.me/en/python-pillow-qrcode/
            qrMaker = qrcode.QRCode(
                border=4, error_correction=qrcode.constants.ERROR_CORRECT_H
            )

            # spoolSelectionUrl = flask.url_for("plugin.SpoolManager.selectSpoolByQRCode", _external=True, _scheme="https", databaseId=databaseId)
            spoolSelectionUrl = None

            useURLPrefix = None
            qrCodeUrlPrefix = None
            if "useURLPrefix" in requestParameters:
                useURLPrefix = True
                qrCodeUrlPrefix = requestParameters["urlPrefix"]

            if useURLPrefix is None:
                useURLPrefix = self._settings.get_boolean(
                    [SettingsKeys.SETTINGS_KEY_QR_CODE_USE_URL_PREFIX]
                )

            if useURLPrefix:
                if qrCodeUrlPrefix is None:
                    qrCodeUrlPrefix = self._settings.get(
                        [SettingsKeys.SETTINGS_KEY_QR_CODE_URL_PREFIX]
                    )

                spoolSelectionUrl = (
                    qrCodeUrlPrefix
                    + "/plugin/SpoolManager/selectSpoolByQRCode/"
                    + databaseId
                )
            else:
                spoolSelectionUrl = flask.url_for(
                    "plugin.SpoolManager.selectSpoolByQRCode",
                    _external=True,
                    databaseId=databaseId,
                )

            qrMaker.add_data(spoolSelectionUrl)
            qrMaker.make(
                fit=True,
            )

            img_qr_big = qrMaker.make_image(
                fill_color=fillColor, back_color=backgroundColor
            ).convert("RGB")
            pos = (
                (img_qr_big.size[0] - olliImage.size[0]) // 2,
                (img_qr_big.size[1] - olliImage.size[1]) // 2,
            )
            img_qr_big.paste(olliImage, pos)

            # img_qr_big.save('data/dst/qr_lena2.png')
            #
            #
            #
            # # qrImage = qrMaker.make_image(fill_color="darkgreen", back_color="white")
            # qrImage = qrMaker.make_image(fill_color=fillColor, back_color=backgroundColor)

            qr_io = BytesIO()
            # qrImage.save(qr_io, 'JPEG', quality=100)
            img_qr_big.save(qr_io, "JPEG", quality=100)
            qr_io.seek(0)

            return send_file(qr_io, mimetype="image/jpeg")
        else:
            abort(404)

    # python twin of window.spmSpoolColorCss in SpoolManager.js, because this view is rendered server-side
    def _buildSpoolColorCss(self, colorValue):
        if colorValue is None:
            return ""
        colorValue = str(colorValue).strip()
        if colorValue.lower() == "rainbow":
            return "linear-gradient(135deg, #ff2d2d 0%, #ff9a00 20%, #ffe600 40%, #16c172 60%, #2f7bff 80%, #a044ff 100%)"
        checkerboard = (
            "repeating-conic-gradient(#c8c8c8 0% 25%, #ffffff 0% 50%) 50% / 8px 8px"
        )
        if colorValue.lower() == "transparent":
            return checkerboard
        transparent = False
        if colorValue.lower().startswith("transparent:"):
            transparent = True
            colorValue = colorValue[len("transparent:") :]
        # only accept hex colors, the value ends up in a style-attribute
        if (
            re.match(r"^#[0-9a-fA-F]{3,8}(;#[0-9a-fA-F]{3,8}){0,2}$", colorValue)
            is None
        ):
            return ""
        colors = colorValue.split(";")
        if transparent:
            # semi-opaque tint layered over the checkerboard (8-digit hex alpha)
            stops = []
            step = 100.0 / len(colors)
            for i, color in enumerate(colors):
                tinted = color + "8c" if (len(color) == 7) else color
                stops.append("%s %.1f%%" % (tinted, i * step))
                stops.append("%s %.1f%%" % (tinted, (i + 1) * step))
            return "linear-gradient(135deg, %s), %s" % (", ".join(stops), checkerboard)
        if len(colors) == 1:
            return colorValue
        stops = []
        step = 100.0 / len(colors)
        for i, color in enumerate(colors):
            stops.append("%s %.1f%%" % (color, i * step))
            stops.append("%s %.1f%%" % (color, (i + 1) * step))
        return "linear-gradient(135deg, %s)" % ", ".join(stops)

    @octoprint.plugin.BlueprintPlugin.route(
        "/generateQRCodeView/<string:databaseId>", methods=["GET"]
    )
    @no_firstrun_access
    def generateSpoolQRCodeHTMLView(self, databaseId):
        htmlContent = ""
        spoolModel = self._databaseManager.loadSpool(databaseId)
        if spoolModel is not None:
            self._logger.info("Generate HTML iew for QR-Code")
            qrCodeImageUrl = flask.url_for(
                "plugin.SpoolManager.generateSpoolQRCode", databaseId=databaseId
            )
            colorCss = self._buildSpoolColorCss(spoolModel.color)
            colorHtml = ""
            if colorCss != "":
                colorName = spoolModel.colorName if spoolModel.colorName else ""
                # value ends up in a html-attribute
                colorName = re.sub(r"[^\w\s#,()-]", "", colorName)
                colorHtml = (
                    "<h3>Spoolcolor: <span title='"
                    + colorName
                    + "' style=\"display:inline-block;"
                    "width:0.9em;height:0.9em;border:1px solid #808080;border-radius:3px;"
                    "vertical-align:baseline;background:"
                    + colorCss
                    + '"></span> '
                    + colorName
                    + "</h3>"
                )
            finishHtml = ""
            if spoolModel.finish:
                # value ends up in html
                safeFinish = re.sub(r"[^\w\s#,()-]", "", str(spoolModel.finish))
                finishHtml = "<h3>Spoolfinish: " + safeFinish + "</h3>"
            htmlContent = (
                "<div class='spm-label-text'>"
                "<h3>Database Id: " + str(spoolModel.databaseId) + "</h3>"
                "<h3>Spoolname: "
                + str(escape(spoolModel.displayName or ""))
                + "</h3>"
                + colorHtml
                + finishHtml
                + "</div>"
                "<img loading='lazy' src='" + qrCodeImageUrl + "' />"
                "<button class='spm-print-btn' onclick='window.print()'>Print Label</button>"
            )
        else:
            htmlContent = "<h3>Spool with database Id not found</h3>"

        # Label printing (print button, @page label size) based on ideas from
        # https://github.com/mdziekon/OctoPrint-SpoolManager/issues/47 (ScottGibb)
        # and PRs mdziekon#54 / dojohnso#59 (reimplemented, not merged)
        # label size for printing, from settings (fallback: Dymo 99012 address label)
        def _labelDimension(settingsKey, defaultValue):
            try:
                value = float(self._settings.get([settingsKey]))
                if value <= 0:
                    return defaultValue
                return value
            except (TypeError, ValueError):
                return defaultValue

        labelWidthMM = _labelDimension(
            SettingsKeys.SETTINGS_KEY_QR_CODE_LABEL_WIDTH_MM, 89.0
        )
        labelHeightMM = _labelDimension(
            SettingsKeys.SETTINGS_KEY_QR_CODE_LABEL_HEIGHT_MM, 36.0
        )
        labelWidth = "%g" % labelWidthMM
        labelHeight = "%g" % labelHeightMM
        qrMaxHeight = "%g" % max(labelHeightMM - 4.0, 1.0)

        qrCodeStyle = (
            "<style>"
            "body{display:flex;flex-direction:column;align-items:center;"
            "justify-content:center;text-align:center;}"
            "img{max-width:100%;}"
            "h3{font-size:2em;margin:0.2em 0;}"
            ".spm-print-btn{margin-top:12px;padding:6px 18px;font-size:1.1em;cursor:pointer;}"
            "@page{size:" + labelWidth + "mm " + labelHeight + "mm;margin:0;}"
            "@media print{"
            ".spm-print-btn{display:none;}"
            "body{margin:0;height:"
            + labelHeight
            + "mm;flex-direction:row;justify-content:flex-start;"
            "align-items:center;gap:2mm;text-align:left;}"
            ".spm-label-text{order:2;overflow:hidden;}"
            "img{max-height:" + qrMaxHeight + "mm;width:auto;order:1;margin-left:2mm;}"
            "h3{font-size:8pt;margin:0;}"
            "}"
            "</style>"
        )

        qrCodeHTMLViewTemplate = (
            ""
            "<html>"
            "<head><link rel='icon' href='data:,'>"
            + qrCodeStyle
            + "</head>"
            + htmlContent
            + "</html>"
            ""
        )

        return Response(
            qrCodeHTMLViewTemplate,
            mimetype="text/html",
            # headers={'Content-Disposition': 'attachment; filename='+reportType+'PrintJobReport-Template.jinja2'}
        )

    ######################################################################################   UPLOAD CSV FILE (in Thread)

    @octoprint.plugin.BlueprintPlugin.route("/importCSV", methods=["POST"])
    @no_firstrun_access
    def importSpoolData(self):

        input_name = "file"
        input_upload_path = (
            input_name
            + "."
            + self._settings.global_get(["server", "uploads", "pathSuffix"])
        )

        if input_upload_path in flask.request.values:

            # Determine which database the import should run against. The actual database switch
            # (and its restore) happens INSIDE the worker thread - not here - so the active
            # settings stay switched for the whole import and are reliably restored afterwards.
            # Doing it in the request thread would race: the restore would fire immediately while
            # the worker is still importing against the (already restored) original database.
            importUseExternal = flask.request.form["externalDatabaseGroup"] == "true"

            importMode = flask.request.form["importCSVMode"]
            # file was uploaded
            sourceLocation = flask.request.values[input_upload_path]

            # because we process in seperate thread we need to create our own temp file, the uploaded temp file will be deleted after this request-call
            archive = tempfile.NamedTemporaryFile(delete=False)
            archive.close()
            shutil.copy(sourceLocation, archive.name)
            sourceLocation = archive.name

            thread = threading.Thread(
                target=self._processCSVUploadAsync,
                args=(
                    sourceLocation,
                    importMode,
                    importUseExternal,
                    self._databaseManager,
                    self._sendCSVUploadStatusToClient,
                    self._logger,
                ),
            )
            thread.daemon = True
            thread.start()

            # targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
            # os.rename(sourceLocation, targetLocation)
            pass
        else:
            return flask.make_response(
                "Invalid request, neither a file nor a path of a file to restore provided",
                400,
            )

        return flask.jsonify(started=True)

    def _processCSVUploadAsync(
        self,
        path,
        importCSVMode,
        importUseExternal,
        databaseManager,
        sendCSVUploadStatusToClient,
        logger,
    ):
        errorCollection = list()

        # Switch the active database to the requested instance for the whole duration of the
        # import and restore the original settings in the finally block. getDatabaseSettings()
        # returns a copy, so backupDatabaseSettings is an independent snapshot and the restore
        # actually takes effect (see the alias trap fixed in DatabaseManager.getDatabaseSettings).
        backupDatabaseSettings = databaseManager.getDatabaseSettings()
        importDatabaseSettings = databaseManager.getDatabaseSettings()
        importDatabaseSettings.useExternal = importUseExternal
        databaseManager.assignNewDatabaseSettings(importDatabaseSettings)

        try:
            # - parsing
            # - backup
            # - append or replace

            def updateParsingStatus(lineNumber):
                # importStatus, currenLineNumber, backupFilePath,  successMessages, errorCollection
                sendCSVUploadStatusToClient(
                    "running", lineNumber, "", "", errorCollection
                )

            resultOfSpools = CSVExportImporter.parseCSV(
                path, updateParsingStatus, errorCollection, logger
            )

            if len(errorCollection) != 0:
                successMessage = (
                    "Some error(s) occurs during parsing! No spools imported!"
                )
                # importStatus, currenLineNumber, backupFilePath,  successMessages, errorCollection
                sendCSVUploadStatusToClient(
                    "finished", "", "", successMessage, errorCollection
                )
                return

            importModeText = "append"
            backupDatabaseFilePath = None
            if len(resultOfSpools) > 0:
                # we could import some jobs

                # - backup
                backupDatabaseFilePath = databaseManager.backupDatabaseFile()

                # - import mode append/replace
                if SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importCSVMode:
                    # delete old database and init a clean database
                    databaseManager.reCreateDatabase()
                    # reset selected spool
                    self._resetSelectedSpools()

                    importModeText = "fully replaced"

                # - insert all printjobs in database
                currentSpoolNumber = 0
                for spool in resultOfSpools:
                    currentSpoolNumber = currentSpoolNumber + 1
                    updateParsingStatus(currentSpoolNumber)

                    remainingWeight = Transformer.calculateRemainingWeight(
                        spool.usedWeight, spool.totalWeight
                    )
                    if remainingWeight is not None:
                        spool.remainingWeight = remainingWeight
                        # spool.save()

                    spool.isActive = True

                    databaseManager.saveSpool(spool)
                pass
            else:
                errorCollection.append("Nothing to import!")

            successMessage = ""
            if len(errorCollection) == 0:
                successMessage = (
                    "All data is successful "
                    + importModeText
                    + " with "
                    + str(len(resultOfSpools))
                    + " spools."
                )
            else:
                successMessage = "Some error(s) occurs! Maybe you need to manually rollback the database!"
            logger.info(successMessage)
            sendCSVUploadStatusToClient(
                "finished", "", backupDatabaseFilePath, successMessage, errorCollection
            )
        finally:
            databaseManager.assignNewDatabaseSettings(backupDatabaseSettings)
        pass

    def _buildDatabaseSettingsFromJson(self, jsonData):

        # DatabaseSettings is a nested class of DatabaseManager, not a module-level
        # one: `DatabaseManager` here is the MODULE, so it has to be addressed via
        # the class (module.DatabaseManager.DatabaseSettings).
        databaseSettings = DatabaseManager.DatabaseManager.DatabaseSettings()
        databaseSettings.useExternal = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL, jsonData
        )
        databaseSettings.type = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_TYPE, jsonData
        )
        databaseSettings.host = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_HOST, jsonData
        )
        databaseSettings.port = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_PORT, jsonData
        )
        databaseSettings.name = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_NAME, jsonData
        )
        databaseSettings.user = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_USER, jsonData
        )
        databaseSettings.password = self._getValueFromJSONOrNone(
            SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD, jsonData
        )

        return databaseSettings

    #######################################################################################   DOWNLOAD DATABASE-FILE
    @octoprint.plugin.BlueprintPlugin.route("/downloadDatabase", methods=["GET"])
    @no_firstrun_access
    def downloadDatabase(self):
        return send_file(
            self._databaseManager.getDatabaseSettings().fileLocation,
            mimetype="application/octet-stream",
            download_name="spoolmanager.db",
            as_attachment=True,
        )

    #######################################################################################   CREATE LOCAL DB BACKUP
    # creates a .db file copy of the local database WITHOUT migrating; the frontend calls this first,
    # downloads the file via /downloadDatabaseBackup, and only then triggers the scheme upgrade
    # (mirrors the external "download dump first, abort on failure" behaviour)
    @octoprint.plugin.BlueprintPlugin.route("/createDatabaseBackup", methods=["PUT"])
    @no_firstrun_access
    def createDatabaseBackup(self):
        backupResult = self._databaseManager.createLocalDatabaseBackup()
        if not backupResult["success"]:
            return flask.make_response(
                "Database backup failed: " + str(backupResult["errorMessage"]), 400
            )
        return flask.jsonify(
            {"backupFileName": os.path.basename(backupResult["backupFilePath"])}
        )

    #######################################################################################   DOWNLOAD LOCAL DB BACKUP
    # serves the .db backup file created by /createDatabaseBackup (in the plugin data folder)
    @octoprint.plugin.BlueprintPlugin.route("/downloadDatabaseBackup", methods=["GET"])
    @no_firstrun_access
    def downloadDatabaseBackup(self):
        backupFileName = flask.request.args.get("fileName")
        if backupFileName is None or backupFileName == "":
            return flask.make_response("No backup file name provided.", 400)

        # only allow a plain file name inside the plugin data folder (no path traversal)
        if backupFileName != os.path.basename(backupFileName):
            return flask.make_response("Invalid backup file name.", 400)

        baseFolder = self._databaseManager.getDatabaseSettings().baseFolder
        backupFilePath = os.path.join(baseFolder, backupFileName)
        if not os.path.isfile(backupFilePath):
            return flask.make_response("Backup file not found.", 404)

        return send_file(
            backupFilePath,
            mimetype="application/octet-stream",
            download_name=backupFileName,
            as_attachment=True,
        )

    #######################################################################################   PRE-IMPORT BACKUP
    # Creates a safety backup of the CURRENTLY ACTIVE database BEFORE an import runs and stores it
    # in the plugin data folder. The frontend calls this first, downloads the file(s) via
    # /downloadDatabaseBackup, and only triggers the actual import after a successful backup+download
    # (mirrors the scheme-upgrade "backup first, abort on failure" guarantee).
    #
    # Backup formats:
    #   - internal SQLite : .db (file copy) AND .csv (best-effort)
    #   - external MySQL  : .sql (dump)     AND .csv (best-effort)
    # The mandatory backup (.db / .sql) must succeed; the additional .csv is best-effort and does
    # not block the import if it fails.
    @octoprint.plugin.BlueprintPlugin.route("/createImportBackup", methods=["PUT"])
    @no_firstrun_access
    def createImportBackup(self):
        databaseSettings = self._databaseManager.getDatabaseSettings()
        if databaseSettings is None:
            return flask.make_response("No database settings available.", 400)

        useExternal = databaseSettings.useExternal
        baseFolder = databaseSettings.baseFolder
        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M%S")

        # The mandatory backup (.db / .sql) must be created and downloaded, otherwise the import is
        # aborted. The optional .csv is best-effort: if its creation OR its download fails, the import
        # still proceeds (a failed optional download must NOT block the import - browsers can also
        # throttle multiple auto-downloads).
        mandatoryBackupFile = None
        optionalBackupFiles = []

        try:
            # --- mandatory backup ---
            if not useExternal:
                # internal SQLite: copy the .db file (backupDatabaseFile already stores it in baseFolder)
                backupResult = self._databaseManager.createLocalDatabaseBackup()
                if not backupResult["success"]:
                    return flask.make_response(
                        "Database backup failed: " + str(backupResult["errorMessage"]),
                        400,
                    )
                mandatoryBackupFile = os.path.basename(backupResult["backupFilePath"])
            else:
                # external database: a full .sql dump is only available for MySQL
                if not self._isExternalMySQLConfigured():
                    return flask.make_response(
                        "A full backup before import is only available for external MySQL databases. Save the storage settings first.",
                        400,
                    )
                dumpResult = self._databaseManager.exportMySQLDatabaseDump()
                if not dumpResult["success"]:
                    return flask.make_response(
                        "Database dump backup failed: "
                        + str(dumpResult["errorMessage"]),
                        400,
                    )
                sqlFileName = "SpoolManager-backup-external-" + currentDate + ".sql"
                with open(os.path.join(baseFolder, sqlFileName), "w") as sqlFile:
                    sqlFile.write(dumpResult["dump"])
                mandatoryBackupFile = sqlFileName

            # --- best-effort .csv backup (does not block the import if it fails) ---
            # This can legitimately fail when the ACTIVE database still has an outdated scheme
            # (e.g. "no such column: batchNumber" if a restored .db was not upgraded yet). The
            # mandatory .db/.sql backup above already protects the data, so we only warn here and
            # let the import proceed - no scary full traceback for an expected best-effort miss.
            try:
                allSpoolModels = list(self._databaseManager.loadAllSpoolsByQuery(None))
                instanceName = "external" if useExternal else "internal"
                csvFileName = (
                    "SpoolManager-backup-" + instanceName + "-" + currentDate + ".csv"
                )
                with open(os.path.join(baseFolder, csvFileName), "w") as csvFile:
                    for csvline in CSVExportImporter.transform2CSV(allSpoolModels):
                        csvFile.write(csvline)
                optionalBackupFiles.append(csvFileName)
            except Exception as csvError:
                self._logger.warning(
                    "Best-effort CSV backup before import skipped (import still allowed, "
                    "mandatory backup was created): " + str(csvError)
                )

        except Exception as e:
            self._logger.exception("createImportBackup")
            return flask.make_response("Backup before import failed: " + str(e), 400)

        return flask.jsonify(
            {
                "mandatoryBackupFile": mandatoryBackupFile,
                "optionalBackupFiles": optionalBackupFiles,
            }
        )

    ##############################################################################   EXPORT / IMPORT MYSQL DATABASE DUMP
    # both routes work on the SAVED storage settings and are only available for external MySQL databases

    def _isExternalMySQLConfigured(self):
        databaseSettings = self._databaseManager.getDatabaseSettings()
        return (
            databaseSettings is not None
            and databaseSettings.useExternal
            and "mysql" == databaseSettings.type
        )

    @octoprint.plugin.BlueprintPlugin.route("/exportDatabaseDump", methods=["GET"])
    @no_firstrun_access
    def exportDatabaseDump(self):

        if not self._isExternalMySQLConfigured():
            return flask.make_response(
                "Database dump export is only available for external MySQL databases. Save the storage settings first.",
                400,
            )

        exportResult = self._databaseManager.exportMySQLDatabaseDump()
        if not exportResult["success"]:
            return flask.make_response(
                "Database dump export failed: " + str(exportResult["errorMessage"]), 400
            )

        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M")
        fileName = "SpoolManager-mysql-" + currentDate + ".sql"

        return Response(
            exportResult["dump"],
            mimetype="application/sql",
            headers={"Content-Disposition": "attachment; filename=" + fileName},
        )

    @octoprint.plugin.BlueprintPlugin.route("/importDatabaseDump", methods=["POST"])
    @no_firstrun_access
    def importDatabaseDump(self):

        if not self._isExternalMySQLConfigured():
            return flask.make_response(
                "Database dump import is only available for external MySQL databases. Save the storage settings first.",
                400,
            )

        input_name = "file"
        input_upload_path = (
            input_name
            + "."
            + self._settings.global_get(["server", "uploads", "pathSuffix"])
        )
        if input_upload_path not in flask.request.values:
            return flask.make_response("Invalid request, no dump file provided", 400)

        importMode = flask.request.form.get("importMode")
        if importMode not in (
            SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE,
            SettingsKeys.KEY_IMPORTCSV_MODE_APPEND,
        ):
            return flask.make_response("Invalid import mode", 400)

        sourceLocation = flask.request.values[input_upload_path]
        try:
            with open(sourceLocation, "r", encoding="utf-8") as dumpFile:
                dumpText = dumpFile.read()
        except UnicodeDecodeError:
            return flask.make_response("Dump file is not UTF-8 encoded", 400)

        importResult = self._databaseManager.importMySQLDatabaseDump(
            dumpText, importMode
        )

        if (
            importResult["success"]
            and SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importMode
        ):
            # same behaviour as the CSV replace-import
            self._resetSelectedSpools()

        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify(
            {
                "success": importResult["success"],
                "errorMessage": importResult["errorMessage"],
                "executedStatementCount": importResult["executedStatementCount"],
                "importedSpoolCount": importResult["importedSpoolCount"],
                "metadata": metaDataResult,
            }
        )

    #######################################################################################   RESTORE LOCAL .db FILE
    # Restores the local SQLite database from an uploaded .db file (replace = whole-file swap,
    # append = insert the uploaded spools). Only available for the internal SQLite database.
    # The frontend creates+downloads the pre-import backup first (createImportBackup).
    @octoprint.plugin.BlueprintPlugin.route("/importDatabaseFile", methods=["POST"])
    @no_firstrun_access
    def importDatabaseFile(self):

        databaseSettings = self._databaseManager.getDatabaseSettings()
        if databaseSettings is None or databaseSettings.useExternal:
            return flask.make_response(
                "A .db restore is only available for the local SQLite database.", 400
            )

        input_name = "file"
        input_upload_path = (
            input_name
            + "."
            + self._settings.global_get(["server", "uploads", "pathSuffix"])
        )
        if input_upload_path not in flask.request.values:
            return flask.make_response("Invalid request, no .db file provided", 400)

        importMode = flask.request.form.get("importMode")
        if importMode not in (
            SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE,
            SettingsKeys.KEY_IMPORTCSV_MODE_APPEND,
        ):
            return flask.make_response("Invalid import mode", 400)

        sourceLocation = flask.request.values[input_upload_path]

        restoreResult = self._databaseManager.restoreFromSQLiteFile(
            sourceLocation, importMode
        )

        if (
            restoreResult["success"]
            and SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importMode
        ):
            # same behaviour as the CSV/SQL replace-import
            self._resetSelectedSpools()

        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify(
            {
                "success": restoreResult["success"],
                "errorMessage": restoreResult["errorMessage"],
                "importedSpoolCount": restoreResult["importedSpoolCount"],
                "metadata": metaDataResult,
            }
        )

    #######################################################################################   DELETE DATABASE
    @octoprint.plugin.BlueprintPlugin.route(
        "/deleteDatabase/<string:databaseType>", methods=["POST"]
    )
    @no_firstrun_access
    def deleteDatabase(self, databaseType):

        databaseSettings = None
        if databaseType == "external":
            jsonData = request.json
            databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)
            databaseSettings.useExternal = True

        self._databaseManager.reCreateDatabase(databaseSettings)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify({"metadata": metaDataResult})

    #######################################################################################   COPY DATABASE
    @octoprint.plugin.BlueprintPlugin.route("/copyDatabase", methods=["POST"])
    @no_firstrun_access
    def copyDatabase(self):
        # metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        jsonData = request.json
        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)
        metaDataResult = self._databaseManager.copySpoolData(databaseSettings)

        return flask.jsonify({"metadata": metaDataResult})

    #######################################################################################   LOAD DATABASE METADATA
    @octoprint.plugin.BlueprintPlugin.route("/loadDatabaseMetaData", methods=["GET"])
    @no_firstrun_access
    def loadDatabaseMetaData(self):

        # databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify({"metadata": metaDataResult})

    #######################################################################################   DATABASE INFO (IDENTITY, NO CREDENTIALS)
    @octoprint.plugin.BlueprintPlugin.route("/databaseInfo", methods=["GET"])
    @no_firstrun_access
    def getDatabaseInfo(self):
        # Returns a DB IDENTITY (no password!) so an external client (e.g. OctoScale)
        # can detect which OctoPrint instances share the same spool DB: same dbId ==
        # shared DB == interchangeable as a lookup fallback for each other.
        databaseSettings = self._databaseManager.getDatabaseSettings()

        dbId = None
        isExternal = False
        if databaseSettings is not None:
            isExternal = databaseSettings.useExternal
            if isExternal:
                host = (databaseSettings.host or "").strip()
                # A DB host of localhost/127.0.0.1 is only unique *within* this machine.
                # To make the dbId comparable across OctoPrint hosts, resolve it to the
                # address this very request came in on (i.e. how this instance is
                # reachable from outside). Two instances on the SAME machine sharing one
                # local DB then still produce the SAME dbId; instances on DIFFERENT
                # machines each with their own local DB get DIFFERENT ones.
                if host.lower() in ("localhost", "127.0.0.1", "::1", ""):
                    host = flask.request.host.split(":")[0]
                # host:port/name identifies the external DB uniquely (without credentials)
                dbId = "%s://%s:%s/%s" % (
                    databaseSettings.type,
                    host,
                    databaseSettings.port,
                    databaseSettings.name,
                )

        return flask.jsonify(
            {
                "external": isExternal,
                "dbId": dbId,  # None -> local SQLite or unknown, never share with others
            }
        )

    #######################################################################################   UPGRADE DATABASE SCHEME
    @octoprint.plugin.BlueprintPlugin.route("/upgradeDatabaseScheme", methods=["PUT"])
    @no_firstrun_access
    def upgradeDatabaseScheme(self):

        jsonData = request.json if request.is_json else {}
        # the frontend downloads the backup dump via /exportDatabaseDump before triggering the upgrade;
        # without that flag a backup file is written to the plugin data folder instead
        backupDownloaded = self._getValueFromJSONOrNone("backupDownloaded", jsonData)

        upgradeResult = self._databaseManager.upgradeExternalDatabaseScheme(
            createBackupFile=(not backupDownloaded)
        )
        # fresh metadata so the frontend can update the scheme version badges
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        if upgradeResult["success"]:
            # refresh spool table and sidebar in all connected clients of this instance
            self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))

        return flask.jsonify({"result": upgradeResult, "metadata": metaDataResult})

    #######################################################################################   TEST DATABASE CONNECTION
    @octoprint.plugin.BlueprintPlugin.route("/testDatabaseConnection", methods=["PUT"])
    @no_firstrun_access
    def testDatabaseConnection(self):

        jsonData = request.json

        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)

        # databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(
            databaseSettings
        )

        return flask.jsonify({"metadata": metaDataResult})

    ###############################################################################  CONFIRM DATABASE CONNECTION PROBLEM
    @octoprint.plugin.BlueprintPlugin.route(
        "/confirmDatabaseProblemMessage", methods=["PUT"]
    )
    @no_firstrun_access
    def confirmDatabaseConnectionProblem(self):

        self.databaseConnectionProblemConfirmed = True

        # return flask.jsonify({
        #   "metadata": metaDataResult
        # })
        return flask.jsonify()

    ###########################################################################################   EXPORT DATABASE as CSV
    @octoprint.plugin.BlueprintPlugin.route(
        "/exportSpools/<string:exportType>", methods=["GET"]
    )
    @no_firstrun_access
    def exportSpoolsData(self, exportType):

        databaseSettings = self._databaseManager.getDatabaseSettings()
        backupDatabaseSettings = self._databaseManager.getDatabaseSettings()

        if exportType == "CSV":

            if flask.request.values["instance"] == "external":
                databaseSettings.useExternal = True
            else:
                databaseSettings.useExternal = False

            self._databaseManager.assignNewDatabaseSettings(databaseSettings)

            # Materialize the lazy peewee query with list(...) BEFORE restoring the database
            # settings - otherwise transform2CSV would iterate (and run the SQL) after the
            # restore, i.e. against the wrong (restored) database.
            allSpoolModels = list(self._databaseManager.loadAllSpoolsByQuery(None))

            self._databaseManager.assignNewDatabaseSettings(backupDatabaseSettings)

            now = datetime.datetime.now()
            currentDate = now.strftime("%Y%m%d-%H%M")
            fileName = "SpoolManager-" + currentDate + ".csv"

            csv = []
            for csvline in CSVExportImporter.transform2CSV(allSpoolModels):
                csv.append(csvline)

            return Response(
                csv,
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=" + fileName},
            )

        else:
            if exportType == "legacyFilamentManager":
                allSpoolLegacyList = (
                    self._filamentManagerPluginImplementation.filamentManager.get_all_spools()
                )
                if allSpoolLegacyList is not None:

                    allSpoolModelList = self._createSpoolModelFromLegacy(
                        allSpoolLegacyList
                    )

                    now = datetime.datetime.now()
                    currentDate = now.strftime("%Y%m%d-%H%M")
                    fileName = "FilamentManager-" + currentDate + ".csv"

                    return Response(
                        CSVExportImporter.transform2CSV(allSpoolModelList),
                        mimetype="text/csv",
                        headers={
                            "Content-Disposition": "attachment; filename=" + fileName
                        },
                    )

                pass

            print("BOOOMM not supported type")
        pass

    ###################################################################################   INVENTORY REPORT (PDF/CSV/XLSX)
    @octoprint.plugin.BlueprintPlugin.route("/exportInventoryReport", methods=["GET"])
    @no_firstrun_access
    def exportInventoryReport(self):
        from octoprint_SpoolManager.common import InventoryReport

        reportFormat = flask.request.values.get("format", "pdf").lower()
        if reportFormat not in ("pdf", "csv", "xlsx"):
            return flask.make_response(
                "Unsupported report format: " + reportFormat, 400
            )

        # Build a table query from the current tab filter/sort state (passed as query params).
        # Force "all" so the report covers every filtered spool, not just the current page.
        # Note: we run against the currently active database (like the normal tab load /
        # loadSpoolsByQuery does) - we must NOT switch the database instance here, otherwise
        # the connection is left pointing at the wrong (e.g. empty/outdated internal) database.
        tableQuery = dict(flask.request.values)
        tableQuery["selectedPageSize"] = "all"

        # loadAllSpoolsByQuery returns a lazy peewee query; materialize it now, while the
        # request/DB context is still valid, so the formatters get a plain list.
        allSpoolModels = list(self._databaseManager.loadAllSpoolsByQuery(tableQuery))

        databaseSettings = self._databaseManager.getDatabaseSettings()
        instanceName = self._settings.global_get(["appearance", "name"])
        dbContextInfo = InventoryReport.build_database_context_info(
            databaseSettings, instanceName
        )

        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M")
        baseName = "SpoolManager-Inventory-" + currentDate

        if reportFormat == "csv":
            payload = InventoryReport.build_inventory_report_csv(
                allSpoolModels, dbContextInfo
            )
            mimetype = "text/csv"
            fileName = baseName + ".csv"
        elif reportFormat == "xlsx":
            payload = InventoryReport.build_inventory_report_xlsx(
                allSpoolModels, dbContextInfo
            ).getvalue()
            mimetype = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            fileName = baseName + ".xlsx"
        else:
            payload = InventoryReport.build_inventory_report_pdf(
                allSpoolModels, dbContextInfo
            ).getvalue()
            mimetype = "application/pdf"
            fileName = baseName + ".pdf"

        return Response(
            payload,
            mimetype=mimetype,
            headers={"Content-Disposition": "attachment; filename=" + fileName},
        )

    ##################################################################################################   LOAD ALL SPOOLS
    @octoprint.plugin.BlueprintPlugin.route("/loadSpoolsByQuery", methods=["GET"])
    @no_firstrun_access
    def loadAllSpoolsByQuery(self):

        self._logger.debug("API Load all spool")
        # sp1 = SpoolModel()
        # sp1.displayName = "Spool No.1"
        # sp1.vendor = "Janbex"
        # sp1.material = "ABS"
        # sp1.color = "#00dd00"
        # sp1.density = 123.23
        # sp1.diameter = 432.12
        # sp1.temperature = 221
        # sp1.firstUse = datetime.datetime(2019, 5, 17)
        # sp1.lastUse = datetime.datetime(2019, 6, 4)
        # sp1.remainingWeight = 1234
        # sp1.weight = 2000
        # sp1.usedPercentage = str(1234.0 / (2000.0 / 100.0))
        # sp1.usedLength = 32
        # sp1.code = "XS-28787-HKH-234"
        # sp1.purchasedOn = datetime.datetime(2018, 4, 3)
        # sp1.purchasedFrom = "http://www.amazon.de/eorjoeiirjfoiejfoijeroffjeroeoidj"
        # sp1.cost = 3.14
        #
        # sp2 = SpoolModel()
        # sp2.displayName = "Spool No.2"
        # sp2.vendor = "Plastic Joe"
        # sp2.material = "PETG"
        #
        # allSpools = [sp1,sp2]

        tableQuery = flask.request.values

        try:
            return self._loadAllSpoolsByQueryResponse(tableQuery)
        except Exception:
            # e.g. outdated database scheme: the model expects columns the database doesn't have yet.
            # Return a valid (empty) response instead of a 500, so the frontend can show the
            # scheme-upgrade hint (schemeUpgradeNeeded) in the tab and sidebar.
            self._logger.exception("loadSpoolsByQuery failed")
            schemeUpgradeNeeded = self._databaseManager.recheckSchemeUpgradeNeeded()
            return flask.jsonify(
                {
                    "databaseConnectionProblem": not schemeUpgradeNeeded,
                    "templateSpools": [],
                    "catalogs": {
                        "vendors": [],
                        "materials": [],
                        "colors": [],
                        "labels": [],
                    },
                    "totalItemCount": 0,
                    "databaseItemCount": 0,
                    "allSpools": [],
                    "schemeUpgradeNeeded": schemeUpgradeNeeded,
                }
            )

    ##################################################################################################   LOAD SELECTED SPOOLS
    # Attribution @mdziekon, PR #8: selected spools are fetched separately from the spools list,
    # so the sidebar can show the current selection without pulling the whole selector dataset.
    @octoprint.plugin.BlueprintPlugin.route("/loadSelectedSpools", methods=["GET"])
    @no_firstrun_access
    def getSelectedSpools(self):

        self._logger.debug("API Load selected spools")

        try:
            selectedSpoolsAsDicts = [
                (
                    None
                    if selectedSpool is None
                    else Transformer.transformSpoolModelToDict(selectedSpool)
                )
                for selectedSpool in self.loadSelectedSpools()
            ]
        except Exception:
            # e.g. outdated database scheme, see loadAllSpoolsByQuery
            self._logger.exception("loadSelectedSpools failed")
            selectedSpoolsAsDicts = []

        return flask.jsonify({"selectedSpools": selectedSpoolsAsDicts})

    def _loadAllSpoolsByQueryResponse(self, tableQuery):
        allSpools = self._databaseManager.loadAllSpoolsByQuery(tableQuery)
        totalItemCount = self._databaseManager.countSpoolsByQuery(tableQuery)
        databaseItemCount = self._databaseManager.countAllSpools()

        # allSpoolsAsDict = self._transformAllSpoolModelsToDict(allSpools)
        allSpoolsAsDict = Transformer.transformAllSpoolModelsToDict(allSpools)

        # load all catalogs: vendors, materials, labels, [colors]
        # The catalogs are deliberately NOT filtered by tableQuery - the filter
        # dropdowns have to offer every existing value, not just the ones in the
        # current page. Passing tableQuery here used to land in the methods'
        # `withReusedConnection` parameter (a non-empty dict is truthy), which made
        # them skip connecting and log "Database not connected" on every tab load.
        vendors = list(self._databaseManager.loadCatalogVendors())
        materials = list(self._databaseManager.loadCatalogMaterials())
        labels = list(self._databaseManager.loadCatalogLabels(tableQuery))
        colors = list(self._databaseManager.loadCatalogColors())

        materials = self._addAdditionalMaterials(materials)

        # sort catalogs alphabetically (case-insensitive) for the filter/edit dropdowns, see issue #23
        vendors = sorted(vendors, key=lambda item: item.lower())
        materials = sorted(materials, key=lambda item: item.lower())
        labels = sorted(labels, key=lambda item: item.lower())
        colors = sorted(colors, key=lambda item: item["colorName"].lower())

        allTemplateSpools = self._databaseManager.loadSpoolTemplates()
        allTemplateSpoolsAsDict = Transformer.transformAllSpoolModelsToDict(
            allTemplateSpools
        )

        # if (allTemplateSpools != None):
        #   for spool in allTemplateSpools:
        #       tempateSpoolAsDict = Transformer.transformSpoolModelToDict(spool)
        #       break

        catalogs = {
            "vendors": vendors,
            "materials": materials,
            "colors": colors,
            "labels": labels,
        }
        # catalogs = {
        #   "materials": ["", "ABS", "PLA", "PETG"],
        #   "colors": ["", "#123", "#456"],
        #   "labels": ["", "good", "bad"]
        # }
        schemeUpgradeNeeded = self._databaseManager.isSchemeUpgradeNeeded()
        if schemeUpgradeNeeded:
            # queries succeed again, so another OctoPrint instance sharing the database
            # may have performed the upgrade in the meantime - re-evaluate the stale flag
            schemeUpgradeNeeded = self._databaseManager.recheckSchemeUpgradeNeeded()

        return flask.jsonify(
            {
                # "databaseConnectionProblem": self._databaseManager.isConnected() == False,
                "templateSpools": allTemplateSpoolsAsDict,
                "catalogs": catalogs,
                "totalItemCount": totalItemCount,
                "databaseItemCount": databaseItemCount,
                "allSpools": allSpoolsAsDict,
                "schemeUpgradeNeeded": schemeUpgradeNeeded,
            }
        )

    def _addAdditionalMaterials(self, databaseMaterials):

        # Material list and density values derived from SpoolmanDB-Community
        # https://github.com/Icezaza2543/SpoolmanDB-Community (maintained fork of
        # https://github.com/Donkie/SpoolmanDB) - Copyright (c) 2024 Donkie, MIT License
        # Curated subset: common base polymers plus popular CF/GF variants and TPU shore
        # grades; exotic brand materials and fine-grained fill variants are left out.
        # NOTE: the long-established short names (TPU, PC, PP, POM, FPE, PLA_plus, PC_ABS)
        # are kept on purpose so spools created with older plugin versions keep matching.
        allMeterials = [
            "PLA",
            "PLA+",
            "PLA_plus",
            "PLA-CF",
            "ABS",
            "ABS+",
            "ABS-T",
            "ABS-CF",
            "ASA",
            "ASA-CF",
            "PETG",
            "PETG-CF",
            "PCTG",
            "NYLON",
            "PA6",
            "PA11",
            "PA12",
            "PA-CF",
            "PA6-CF",
            "PA12-CF",
            "TPU",
            "TPU-85A",
            "TPU-90A",
            "TPU-95A",
            "TPE",
            "Flexible (TPE 32D)",
            "Flexible (TPE 88A)",
            "FPE",
            "PC",
            "PC_ABS",
            "PC/PBT",
            "PC-CF",
            "Wood",
            "Carbon Fiber",
            "HIPS",
            "PVA",
            "PVB",
            "BVOH",
            "PP",
            "PP-CF",
            "PP-GF",
            "POM",
            "PMMA",
            "PET",
            "PET-CF",
            "PBT",
            "PPS",
            "PPS-CF",
            "PVDF",
            "PEI (Ultem)",
            "PEKK",
            "PEEK",
            "PEEK-CF",
            "PPSU",
        ]
        for currentMaterial in allMeterials:
            if (
                currentMaterial.upper() not in databaseMaterials
                and currentMaterial.lower() not in databaseMaterials
            ):
                databaseMaterials.append(currentMaterial)
        return databaseMaterials

    ##################################################################################################   NEXT SPOOL ID
    @octoprint.plugin.BlueprintPlugin.route("/nextSpoolId", methods=["GET"])
    @no_firstrun_access
    def loadNextSpoolId(self):
        # prospective databaseId of the next created spool, used for the {id} display name variable preview
        maxDatabaseId = self._databaseManager.getMaxSpoolDatabaseId()
        nextSpoolId = 1 if maxDatabaseId is None else maxDatabaseId + 1
        return flask.jsonify({"nextSpoolId": nextSpoolId})

    def _resolveDisplayNameVariables(self, spoolModel):
        # replaces variables like {material}-{color}-{id} in the display name with the spool's field values,
        # see https://github.com/WildRikku/OctoPrint-SpoolManager/issues/49
        displayName = spoolModel.displayName
        if StringUtils.isEmpty(displayName) or "{" not in displayName:
            return False

        def asText(value):
            return "" if value is None else str(value)

        replacements = {
            "{id}": asText(spoolModel.databaseId),
            "{material}": asText(spoolModel.material),
            "{color}": asText(spoolModel.colorName),
            "{vendor}": asText(spoolModel.vendor),
            "{diameter}": asText(spoolModel.diameter),
            "{weight}": StringUtils.formatInt(spoolModel.totalWeight),
            "{code}": asText(spoolModel.code),
            "{batch}": asText(spoolModel.batchNumber),
        }
        newDisplayName = StringUtils.multiple_replace(displayName, replacements)
        if newDisplayName != displayName:
            spoolModel.displayName = newDisplayName
            return True
        return False

    #######################################################################################################   SAVE SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/saveSpool", methods=["PUT"])
    @no_firstrun_access
    def saveSpool(self):
        self._logger.info("API Save spool")
        jsonData = request.json

        validationErrors = []
        databaseId = None
        if self._getValueFromJSONOrNone("databaseId", jsonData) is not None:
            databaseId = self._toIntFromJSONOrNone(
                "databaseId", jsonData, validationErrors, minValue=1
            )
            if validationErrors:
                # reject a non-numeric id before it reaches the database layer
                self._logger.warning(
                    "Save spool rejected, validation errors: " + str(validationErrors)
                )
                return make_response(
                    jsonify({"validationErrors": validationErrors}), 400
                )
        self._databaseManager.connectoToDatabase()
        if databaseId is not None:
            self._logger.info(
                "Load spool for update with database id '" + str(databaseId) + "'"
            )
            spoolModel = self._databaseManager.loadSpool(
                databaseId, withReusedConnection=True
            )
            if spoolModel is None:
                # the row is gone - answering 200 here would let the dialog close as if the
                # edit had been stored, and saveSpool(None) below would fail anyway
                self._databaseManager.closeDatabase()
                self._logger.warning(
                    "Save spool failed. Inital loading not possible, maybe already deleted."
                )
                return make_response(
                    jsonify(
                        {
                            "conflict": "deleted",
                            "error": "This spool no longer exists, it was deleted in the meantime.",
                        }
                    ),
                    409,
                )
            else:
                validationErrors = self._updateSpoolModelFromJSONData(
                    spoolModel, jsonData
                )
        else:
            self._logger.info("Create new spool")
            spoolModel = SpoolModel()
            validationErrors = self._updateSpoolModelFromJSONData(spoolModel, jsonData)

        # reject invalid input instead of silently dropping it (e.g. letters in the Cost field)
        if validationErrors:
            self._databaseManager.closeDatabase()
            self._logger.warning(
                "Save spool rejected, validation errors: " + str(validationErrors)
            )
            return make_response(jsonify({"validationErrors": validationErrors}), 400)

        # the conflict is reported below as a 409 that the edit dialog turns into a proper
        # choice, so the generic socket popup would only be a second error to dismiss
        newDatabaseId = self._databaseManager.saveSpool(
            spoolModel, withReusedConnection=True, suppressConflictMessage=True
        )

        if newDatabaseId is None:
            # saveSpool signals a version conflict only via a socket message and returns None.
            # Answering 200 here made the dialog close as if everything had been stored, so the
            # user silently lost the edit - now the client gets a 409 plus the current server
            # state and can offer to reload or overwrite.
            currentSpoolModel = (
                self._databaseManager.loadSpool(databaseId, withReusedConnection=True)
                if databaseId is not None
                else None
            )
            self._databaseManager.closeDatabase()
            self._logger.warning(
                "Save spool failed for database id '"
                + str(databaseId)
                + "', concurrent modification."
            )
            responseBody = {
                "conflict": "version",
                "error": "This spool was modified elsewhere while you were editing it.",
            }
            if currentSpoolModel is not None:
                responseBody["spool"] = Transformer.transformSpoolModelToDict(
                    currentSpoolModel
                )
            return make_response(jsonify(responseBody), 409)

        # resolve display name variables ({id} is only known after the initial save), but never inside templates
        if databaseId is None and not spoolModel.isTemplate:
            if self._resolveDisplayNameVariables(spoolModel):
                self._databaseManager.saveSpool(spoolModel, withReusedConnection=True)

        self._databaseManager.closeDatabase()

        if databaseId is None:
            # New spool was created
            eventPayload = {
                "databaseId": spoolModel.databaseId,
                "spoolName": spoolModel.displayName,
                "material": spoolModel.material,
                "colorName": spoolModel.colorName,
                "remainingWeight": spoolModel.remainingWeight,
            }
            self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_ADDED, eventPayload)

        # data for the sidebar
        self.checkRemainingFilament()

        return flask.jsonify()

    #####################################################################################################   DELETE SPOOL
    @octoprint.plugin.BlueprintPlugin.route(
        "/deleteSpool/<int:databaseId>", methods=["DELETE"]
    )
    @no_firstrun_access
    def deleteSpool(self, databaseId):
        self._logger.info("API Delete spool with database id '" + str(databaseId) + "'")
        databaseId = self._databaseManager.deleteSpool(databaseId)
        if databaseId is not None:
            eventPayload = {"databaseId": databaseId}
            self._sendPayload2EventBus(
                EventBusKeys.EVENT_BUS_SPOOL_DELETED, eventPayload
            )

        return flask.jsonify()
