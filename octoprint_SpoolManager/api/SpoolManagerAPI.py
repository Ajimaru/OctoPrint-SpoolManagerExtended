# coding=utf-8

import logging

import octoprint.plugin
import datetime
import flask
from flask import jsonify, request, make_response, Response, send_file, abort
import json
import shutil
import tempfile
import threading
import qrcode
import re
from io import BytesIO     # for handling byte strings
from math import pi as PI

from octoprint_SpoolManager import DatabaseManager
from octoprint_SpoolManager.models.SpoolModel import SpoolModel
from octoprint_SpoolManager.common import StringUtils, CSVExportImporter
from octoprint_SpoolManager.api import Transformer
from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys
from octoprint_SpoolManager.common.EventBusKeys import EventBusKeys

class SpoolManagerAPI(octoprint.plugin.BlueprintPlugin):
    def is_blueprint_csrf_protected(self):
        return True

    def _sendCSVUploadStatusToClient(self, importStatus, currenLineNumber, backupFilePath,  successMessages, errorCollection):

        self._sendDataToClient(dict(action="csvImportStatus",
                                    importStatus = importStatus,
                                    currenLineNumber = currenLineNumber,
                                    backupFilePath = backupFilePath,
                                    successMessages=successMessages,
                                    errorCollection = errorCollection
                                    )
                               )

    # Human readable labels for validation error messages, keyed by JSON field name
    _FIELD_LABELS = {
        "displayName": "Displayname",
        "colorName": "Color",
        "density": "Density",
        "diameter": "Diameter",
        "diameterTolerance": "Diameter tolerance",
        "flowRateCompensation": "Flow rate compensation",
        "temperature": "Tool temperature",
        "bedTemperature": "Bed temperature",
        "enclosureTemperature": "Enclosure temperature",
        "offsetTemperature": "Offset tool temperature",
        "offsetBedTemperature": "Offset bed temperature",
        "offsetEnclosureTemperature": "Offset enclosure temperature",
        "totalWeight": "Filament amount (initial)",
        "spoolWeight": "Empty spool weight",
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

    def _updateSpoolModelFromJSONData(self, spoolModel, jsonData):
        # collects human readable validation errors; a non-empty list aborts the save with HTTP 400
        validationErrors = []

        spoolModel.version = self._toIntFromJSONOrNone("version", jsonData, validationErrors)
        # if statement is needed because assigning None is alos detected as an dirtyField
        if (self._getValueFromJSONOrNone("databaseId", jsonData) != None):
            spoolModel.databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)

        spoolModel.isTemplate = self._getValueFromJSONOrNone("isTemplate", jsonData)
        spoolModel.isActive = self._getValueFromJSONOrNone("isActive", jsonData)
        spoolModel.displayName = self._getValueFromJSONOrNone("displayName", jsonData)
        spoolModel.vendor = self._getValueFromJSONOrNone("vendor", jsonData)
        spoolModel.material = self._getValueFromJSONOrNone("material", jsonData)
        spoolModel.density = self._toFloatFromJSONOrNone("density", jsonData, validationErrors, minValue=0)
        spoolModel.diameter = self._toFloatFromJSONOrNone("diameter", jsonData, validationErrors, minValue=0)
        spoolModel.diameterTolerance = self._toFloatFromJSONOrNone("diameterTolerance", jsonData, validationErrors, minValue=0)
        spoolModel.colorName = self._getValueFromJSONOrNone("colorName", jsonData)
        spoolModel.color = self._getValueFromJSONOrNone("color", jsonData)
        spoolModel.finish = self._getValueFromJSONOrNone("finish", jsonData)
        spoolModel.flowRateCompensation = self._toIntFromJSONOrNone("flowRateCompensation", jsonData, validationErrors, minValue=0)
        spoolModel.temperature = self._toIntFromJSONOrNone("temperature", jsonData, validationErrors, minValue=0)
        spoolModel.bedTemperature = self._toIntFromJSONOrNone("bedTemperature", jsonData, validationErrors, minValue=0)
        spoolModel.enclosureTemperature = self._toIntFromJSONOrNone("enclosureTemperature", jsonData, validationErrors, minValue=0)
        spoolModel.offsetTemperature = self._toIntFromJSONOrNone("offsetTemperature", jsonData, validationErrors)
        spoolModel.offsetBedTemperature = self._toIntFromJSONOrNone("offsetBedTemperature", jsonData, validationErrors)
        spoolModel.offsetEnclosureTemperature = self._toIntFromJSONOrNone("offsetEnclosureTemperature", jsonData, validationErrors)
        spoolModel.totalWeight = self._toFloatFromJSONOrNone("totalWeight", jsonData, validationErrors, minValue=0)
        spoolModel.spoolWeight = self._toFloatFromJSONOrNone("spoolWeight", jsonData, validationErrors, minValue=0)
        spoolModel.remainingWeight = self._toFloatFromJSONOrNone("remainingWeight", jsonData, validationErrors, minValue=0)
        spoolModel.totalLength = self._toIntFromJSONOrNone("totalLength", jsonData, validationErrors, minValue=0)
        spoolModel.usedLength = self._toIntFromJSONOrNone("usedLength", jsonData, validationErrors, minValue=0)
        spoolModel.usedWeight = self._toFloatFromJSONOrNone("usedWeight", jsonData, validationErrors, minValue=0)
        spoolModel.code = self._getValueFromJSONOrNone("code", jsonData)
        spoolModel.batchNumber = self._getValueFromJSONOrNone("batchNumber", jsonData)

        # spoolModel.firstUse = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("firstUse", jsonData))
        # spoolModel.lastUse = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("lastUse", jsonData))
        # spoolModel.purchasedOn = StringUtils.transformToDateTimeOrNone(self._getValueFromJSONOrNone("purchasedOn", jsonData))
        spoolModel.firstUse = self._toDateTimeFromJSONOrNone("firstUseKO", jsonData, validationErrors)
        spoolModel.lastUse = self._toDateTimeFromJSONOrNone("lastUseKO", jsonData, validationErrors)
        spoolModel.purchasedOn = self._toDateTimeFromJSONOrNone("purchasedOnKO", jsonData, validationErrors)

        spoolModel.purchasedFrom = self._getValueFromJSONOrNone("purchasedFrom", jsonData)
        spoolModel.cost = self._toFloatFromJSONOrNone("cost", jsonData, validationErrors, minValue=0)
        spoolModel.costUnit = self._getValueFromJSONOrNone("costUnit", jsonData)

        spoolModel.labels = json.dumps(self._getValueFromJSONOrNone("labels", jsonData))

        spoolModel.noteText = self._getValueFromJSONOrNone("noteText", jsonData)
        spoolModel.noteDeltaFormat = json.dumps(self._getValueFromJSONOrNone("noteDeltaFormat", jsonData))
        spoolModel.noteHtml = self._getValueFromJSONOrNone("noteHtml", jsonData)

        # required-field checks (mirrors the client-side rules so a direct API call cannot bypass them),
        # but only for real spools - templates are allowed to be incomplete
        if (spoolModel.isTemplate != True):
            if (StringUtils.isEmpty(spoolModel.displayName)):
                validationErrors.append("Displayname must not be empty")
            if (StringUtils.isEmpty(spoolModel.colorName)):
                validationErrors.append("Color must not be empty")

        return validationErrors


    def _getValueFromJSONOrNone(self, key, json):
        if key in json:
            return json[key]
        return None

    def _toFloatFromJSONOrNone(self, key, json, validationErrors=None, minValue=None):
        value = self._getValueFromJSONOrNone(key, json)
        if (value != None):
            if (StringUtils.isNotEmpty(value)):
                try:
                    value = float(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error("could not transform value '"+str(value)+"' for key '"+key+"' to float:" + errorMessage)
                    if (validationErrors is not None):
                        validationErrors.append(self._fieldLabel(key) + " must be a number")
                    value = None
                else:
                    if (minValue is not None and value < minValue and validationErrors is not None):
                        validationErrors.append(self._fieldLabel(key) + " must not be less than " + str(minValue))
            else:
                value = None
        return value

    def _toIntFromJSONOrNone(self, key, json, validationErrors=None, minValue=None):
        value = self._getValueFromJSONOrNone(key, json)
        if (value != None):
            if (StringUtils.isNotEmpty(value)):
                try:
                    value = int(value)
                except Exception as e:
                    errorMessage = str(e)
                    self._logger.error("could not transform value '"+str(value)+"' for key '"+key+"' to int:" + errorMessage)
                    if (validationErrors is not None):
                        validationErrors.append(self._fieldLabel(key) + " must be a whole number")
                    value = None
                else:
                    if (minValue is not None and value < minValue and validationErrors is not None):
                        validationErrors.append(self._fieldLabel(key) + " must not be less than " + str(minValue))
            else:
                value = None
        return value

    def _toDateTimeFromJSONOrNone(self, key, json, validationErrors=None):
        value = self._getValueFromJSONOrNone(key, json)
        try:
            return StringUtils.transformFromIsoToDateTimeOrNone(value)
        except Exception as e:
            errorMessage = str(e)
            self._logger.error("could not transform value '"+str(value)+"' for key '"+key+"' to datetime:" + errorMessage)
            if (validationErrors is not None):
                validationErrors.append(self._fieldLabel(key) + " has an invalid date format")
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
        databaseIds = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS])

        for toolIndex, databaseId in enumerate(databaseIds):
            spoolModel = None
            if (databaseId != None):
                self._databaseManager.connectoToDatabase()
                spoolModel = self._databaseManager.loadSpool(databaseId)
                self._databaseManager.closeDatabase()
                if (spoolModel == None):
                    self._logger.warning(
                        "Last selected Spool for Tool %d from plugin-settings not found in database. Maybe deleted in the meantime." % toolIndex)
            spoolModelList.append(spoolModel)
            if (spoolModel != None):
                eventPayload = {
                    "toolId": toolIndex,
                    "databaseId": spoolModel.databaseId,
                    "spoolName": spoolModel.displayName,
                    "material": spoolModel.material,
                    "colorName": spoolModel.colorName,
                    "remainingWeight": spoolModel.remainingWeight
                }
                self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_SELECTED, eventPayload)

        return spoolModelList

    def _createSpoolModelFromLegacy(self, allSpoolLegacyList):
        allSpoolModels = list()
        for spoolDict in allSpoolLegacyList:
            spoolModel = SpoolModel()

            spoolIdInt = spoolDict["id"]
            nameUnicode = spoolDict["name"]
            usedWeightFloat = spoolDict["used"]
            totalWeightFloat = spoolDict["weight"]
            tempOffsetInt = spoolDict["temp_offset"]
            costFloat = spoolDict["cost"]
            profileDict = spoolDict["profile"]
            profileIdInt = profileDict["id"]
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
            spoolModel.costUnit = self._filamentManagerPluginImplementation._settings.get(["currencySymbol"])
            spoolModel.totalWeight = totalWeightFloat
            spoolModel.usedWeight = usedWeightFloat

            spoolModel.usedLength = self._calculateUsedLength(spoolModel.usedWeight, spoolModel.density, spoolModel.diameter)

            allSpoolModels.append(spoolModel)

        return allSpoolModels

    def _calculateUsedLength(self, usedWeight, density, diameter):
        if (diameter == None or density == None or usedWeight == None):
            self._logger.info("Could not calculate used length because some values (usedWeigth, density, diameter) were missing")
            return None
        radius = diameter / 2.0
        volume = (usedWeight) / density
        length = (volume * 1000) / PI * radius * radius
        lengthRounded = int(round(length))
        return lengthRounded;

    def _resetSelectedSpools(self):
        self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS], [])
        self._settings.save()

    def _selectSpool(self, toolIndex, databaseId):
        # three cases
        #  1. databaseId != -1 toolIndex != -1  select spool for toool  ||
        #  2. databaseId == -1 toolIndex != -1  remove spool from tool  |
        #  3. databaseId != -1 toolIndex == -1  remove tool from spool  ||

        databaseIds = self._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS])

        spoolModel = None
        if (databaseId != -1):
            spoolModel = self._databaseManager.loadSpool(databaseId)
            if (spoolModel != None):
                self._logger.info(
                    "Store selected spool %s for tool %d in settings." %
                    (spoolModel.displayName, toolIndex)
                )
                # assign model to selected toolId
                if (toolIndex != -1):
                    databaseIds = databaseIds + [None] * (toolIndex + 1 - len(databaseIds))  # pad list to the needed length
                    idx = 0;
                    for selectedSpoolDBId in databaseIds:
                        if (selectedSpoolDBId == databaseId):
                            databaseIds[idx] = None
                            # check if tool changed, if yes inform user about the switch
                            if (idx != toolIndex):
                                # spool was already assigned and is now used for different tool
                                self._sendMessageToClient("warning",
                                                          "Spool swapped",
                                                          "Spool '"+spoolModel.displayName+"' was switched from Tool "+str(idx)+" to Tool "+str(toolIndex),
                                                          autoclose=True)
                            pass
                        else:
                            databaseIds[idx] = selectedSpoolDBId
                        idx = idx + 1
                    # assign new spool selection to the tool
                    databaseIds[toolIndex] = databaseId
                    eventPayload = {
                        "toolId": toolIndex,
                        "databaseId": spoolModel.databaseId,
                        "spoolName": spoolModel.displayName,
                        "material": spoolModel.material,
                        "colorName": spoolModel.colorName,
                        "remainingWeight": spoolModel.remainingWeight
                    }
                    self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_SELECTED, eventPayload)

                else:
                    # spool present, but no toolId -> remove spool from current toolIndex
                    i = 0
                    while i < len(databaseIds):
                        if (databaseIds[i] == databaseId):
                            databaseIds[i] = None
                            eventPayload = {
                                "toolId": i,
                                "databaseId": spoolModel.databaseId,
                                "spoolName": spoolModel.displayName,
                                "material": spoolModel.material,
                                "colorName": spoolModel.colorName,
                                "remainingWeight": spoolModel.remainingWeight
                            }
                            self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_DESELECTED, eventPayload)
                            break
                        i += 1
                    pass
            else:
                self._logger.warning(
                    "Selected Spool with id %d for tool %d not in database anymore. Maybe deleted in the meantime." %
                    (databaseId, toolIndex)
                )
                # remove spool from current toolIndex
                i = 0
                while i < len(databaseIds):
                    if (databaseIds[i] == databaseId):
                        databaseIds[i] = None
        else:
            if (toolIndex == -1):
                self._logger.warn("databaseId and toolId is -1. This should not happen, strange!!!")
                return None

            # remove current spool from toolIndex
            if (toolIndex < len(databaseIds)):
                databaseIds[toolIndex] = None
                eventPayload = {
                    "toolId": toolIndex,
                    "databaseId": None
                }
                self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_DESELECTED, eventPayload)

        self._settings.set([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS], databaseIds)
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
        return Response(CSVExportImporter.transform2CSV(allSpoolModels),
                        mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=SpoolManager-SAMPLE.csv'})

    ##############################################################################################   ALLOWED TO PRINT
    @octoprint.plugin.BlueprintPlugin.route("/allowedToPrint", methods=["GET"])
    def allowed_to_print(self):

        checkForSelectedSpool = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED])
        checkForFilamentLength = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH])
        reminderSelectingSpool = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_REMINDER_SELECTING_SPOOL])

        spoolModels = self.loadSelectedSpools()
        # define variables for missing data here because we might have multiple tools and one tool with missing data
        # is enough to cause problems
        metaOrAttributesMissing = False
        metaDataMissing = False
        attributesMissing = False
        result = {
            'noSpoolSelected': [],
            'filamentNotEnough': [],
            'reminderSpoolSelection': [],
        }

        filamentLengthPresentInMeta = self._readingFilamentMetaData()
        printer_profile = self._printer_profile_manager.get_current_or_default()
        printerProfileToolCount = printer_profile['extruder']['count']
        # for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
        for toolIndex in range(printerProfileToolCount):
            # we go over the filamentlength because those are what matters for this print
            if filamentLengthPresentInMeta:
                if toolIndex >= len(self.metaDataFilamentLengths):
                    # if this tool is not used (no filaLenght) in this print, everything is fine
                    continue

            spoolModel = spoolModels[toolIndex] if toolIndex < len(spoolModels) else None

            infoData = {
                "toolIndex": toolIndex,
                "spoolName": spoolModel.displayName if spoolModel else '(no spool selected)',
                "material": spoolModel.material if spoolModel else '',
                "remainingWeight": spoolModel.remainingWeight if spoolModel else '',
                "toolOffset": spoolModel.offsetTemperature if spoolModel else '',
                "bedOffset": spoolModel.offsetBedTemperature if spoolModel else '',
                "enclosureOffset": spoolModel.offsetEnclosureTemperature if spoolModel else ''
            }

            requiredWeightResult = self.checkRemainingFilament(toolIndex, shouldWarn=False)
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
            if (requiredWeightResult["metaDataMissing"] == True or requiredWeightResult["attributesMissing"] == True):
                metaOrAttributesMissing = True
            metaDataMissing = metaDataMissing or requiredWeightResult["metaDataMissing"]
            attributesMissing = attributesMissing or requiredWeightResult["attributesMissing"]

            detailedSpoolResult = None
            if ("detailedSpoolResult" in requiredWeightResult and len(requiredWeightResult["detailedSpoolResult"]) > 0):
                detailedSpoolResult = requiredWeightResult["detailedSpoolResult"][0]

            if spoolModel is not None and detailedSpoolResult is not None and detailedSpoolResult["spoolSelected"] == True:
                if (detailedSpoolResult["requiredLength"] > 0):
                    if (detailedSpoolResult["notEnough"] == True):
                        # if not enough or needed amount could not calculated
                        result['filamentNotEnough'].append(infoData)
                    # add every spool for reminding, if more the 0gr is needed
                    result['reminderSpoolSelection'].append(infoData)
            elif checkForSelectedSpool:
                if (detailedSpoolResult is not None):
                    if (detailedSpoolResult["requiredLength"] > 0):
                        result['noSpoolSelected'].append(infoData)
                else:
                    result['noSpoolSelected'].append(infoData)

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
        if (checkForFilamentLength == False):
            result['filamentNotEnough'] = []

        if (reminderSelectingSpool == False):
            # no popup, because turned off by user
            result['reminderSpoolSelection'] = []


        return flask.jsonify({
            "result": result,
            "metaOrAttributesMissing": metaOrAttributesMissing,  # deprecated
            "metaDataMissing": metaDataMissing,
            "attributesMissing": attributesMissing,
            "toolOffsetEnabled": self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_TOOL_OFFSET_ENABLED]),
            "bedOffsetEnabled": self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_BED_OFFSET_ENABLED]),
            "enclosureOffsetEnabled": self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED]),
        })


    #############################################################################################  START PRINT CONFIRMED
    @octoprint.plugin.BlueprintPlugin.route("/startPrintConfirmed", methods=["GET"])
    def start_print_confirmed(self):
        spoolModels = self.loadSelectedSpools()
        printer_profile = self._printer_profile_manager.get_current_or_default()
        printerProfileToolCount = printer_profile['extruder']['count']
        # for toolIndex, filamentLength in enumerate(self.metaDataFilamentLengths):
        for toolIndex in range(printerProfileToolCount):
            spoolModel = spoolModels[toolIndex] if toolIndex < len(spoolModels) else None
            if (spoolModel != None):
                # - assign temp-offset here, because after the print is started (event: ) it is too late Events.PRINT_STARTED
                try:
                    self.set_temp_offsets(toolIndex, spoolModel)
                except Exception as e:
                    self._logger.exception("Temperature offsets for Spool '"+str(spoolModel.displayName)+"' failed to set!")
                    self._sendMessageToClient("warning", "Temperature offsets for Spool '"+str(spoolModel.displayName)+"' failed to set!", str(e))

        return flask.jsonify({
            "result": "goForIt"
        })


    #####################################################################################################   SELECT SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/selectSpool", methods=["PUT"])
    def select_spool(self):
        jsonData = request.json

        databaseId = self._toIntFromJSONOrNone("databaseId", jsonData)
        toolIndex = self._toIntFromJSONOrNone("toolIndex", jsonData)

        if self._printer.is_printing():
            # changing a spool mid-print? we want to know
            commitCurrentSpoolValues = self._getValueFromJSONOrNone("commitCurrentSpoolValues", jsonData)
            if commitCurrentSpoolValues is None:
                self._logger.warning("selectSpool endpoint called mid-print without commitCurrentState parameter - this shouldn't happen")
                abort(409)

            if commitCurrentSpoolValues:
                self._logger.info("commitCurrentSpoolValues == True")
                self.commitOdometerData()

        spoolModel = self._selectSpool(toolIndex, databaseId)

        spoolModelAsDict = None
        if (spoolModel != None):
            spoolModelAsDict = Transformer.transformSpoolModelToDict(spoolModel)

        try:
            self.set_temp_offsets(toolIndex, spoolModel)
        except Exception as e:
            self._sendMessageToClient("warning", "Temperature offsets failed to set!", str(e))

        self.checkRemainingFilament()

        return flask.jsonify({
                                "selectedSpool": spoolModelAsDict
                            })

    #####################################################################################################   SELECT SPOOL BY QR

    from octoprint.server.util.flask import no_firstrun_access, restricted_access
    @octoprint.plugin.BlueprintPlugin.route("/selectSpoolByQRCode/<string:databaseId>", methods=["GET"])
    @no_firstrun_access
    def selectSpoolByQRCode(self, databaseId):
        self._logger.info("API select spool by QR code" + str(databaseId))

        if self._printer.is_printing():
            # not doing this mid-print since we can't ask the user what to do
            abort(409)
            return

        spoolModel = None

        if ("qrPreviewId" == databaseId):
            #Just pick a single spool
            spoolModel = self._databaseManager.loadFirstSingleSpool();
            databaseId = spoolModel.databaseId

        # TODO QR-Code pre-select always tool0 and then the edit-dialog is shown. Better approach: show dialog and the user could choose
        spoolModel = self._selectSpool(0, databaseId)

        spoolModelAsDict = None
        if (spoolModel != None):
            spoolModelAsDict = Transformer.transformSpoolModelToDict(spoolModel)
            #Take us back to the SpoolManager plugin tab
            redirectURLWithSpoolSelection = flask.url_for("index", _external=True)+"#tab_plugin_SpoolManager-spoolId"+str(databaseId)
            return flask.redirect(redirectURLWithSpoolSelection,307)
        else:
            abort(404)

    #####################################################################################################   GENERATE QR FOR SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/generateQRCode/<string:databaseId>", methods=["GET"])
    def generateSpoolQRCode(self, databaseId):

        if (databaseId == "qrPreviewId" or self._databaseManager.loadSpool(databaseId) is not None):
            self._logger.info("API generate QR code for Spool with databaseId: " +str(databaseId))

            requestParameters = request.args

            fillColor = None
            backgroundColor = None
            if ("fillColor" in requestParameters and "backgroundColor" in requestParameters):
                fillColor = requestParameters["fillColor"]
                backgroundColor = requestParameters["backgroundColor"]
            else:
                fillColor = self._settings.get([SettingsKeys.SETTINGS_KEY_QR_CODE_FILL_COLOR])
                backgroundColor = self._settings.get([SettingsKeys.SETTINGS_KEY_QR_CODE_BACKGROUND_COLOR])

            # verify color codes
            from PIL import ImageColor
            if (fillColor.startswith("#")):
                fillColor = ImageColor.getcolor(fillColor, "RGB")
            if (backgroundColor.startswith("#")):
                backgroundColor = ImageColor.getcolor(backgroundColor, "RGB")

            # windowLocation = request.args.get("windowlocation")
            from PIL import Image
            imageFileLocation = self._basefolder + "/static/images/SPMByOlli.png"
            olliImage = Image.open(imageFileLocation)#.crop((175, 90, 235, 150))

            # https://note.nkmk.me/en/python-pillow-qrcode/
            qrMaker = qrcode.QRCode(
                border=4,
                error_correction=qrcode.constants.ERROR_CORRECT_H
            )

            # spoolSelectionUrl = flask.url_for("plugin.SpoolManager.selectSpoolByQRCode", _external=True, _scheme="https", databaseId=databaseId)
            spoolSelectionUrl = None

            useURLPrefix = None
            qrCodeUrlPrefix = None
            if ("useURLPrefix" in requestParameters):
                useURLPrefix = True
                qrCodeUrlPrefix = requestParameters["urlPrefix"]

            if (useURLPrefix == None):
                useURLPrefix = self._settings.get_boolean([SettingsKeys.SETTINGS_KEY_QR_CODE_USE_URL_PREFIX])

            if (useURLPrefix):
                if (qrCodeUrlPrefix == None):
                    qrCodeUrlPrefix = self._settings.get([SettingsKeys.SETTINGS_KEY_QR_CODE_URL_PREFIX])

                spoolSelectionUrl = qrCodeUrlPrefix + "/plugin/SpoolManager/selectSpoolByQRCode/"+databaseId
            else:
                spoolSelectionUrl = flask.url_for("plugin.SpoolManager.selectSpoolByQRCode", _external=True, databaseId=databaseId)

            qrMaker.add_data(spoolSelectionUrl)
            qrMaker.make(fit=True, )


            img_qr_big = qrMaker.make_image(fill_color=fillColor, back_color=backgroundColor).convert('RGB')
            pos = ((img_qr_big.size[0] - olliImage.size[0]) // 2, (img_qr_big.size[1] - olliImage.size[1]) // 2)
            img_qr_big.paste(olliImage, pos)

            # img_qr_big.save('data/dst/qr_lena2.png')
            #
            #
            #
            # # qrImage = qrMaker.make_image(fill_color="darkgreen", back_color="white")
            # qrImage = qrMaker.make_image(fill_color=fillColor, back_color=backgroundColor)

            qr_io = BytesIO()
            # qrImage.save(qr_io, 'JPEG', quality=100)
            img_qr_big.save(qr_io, 'JPEG', quality=100)
            qr_io.seek(0)

            return send_file(qr_io, mimetype='image/jpeg')
        else:
            abort(404)


    # python twin of window.spmSpoolColorCss in SpoolManager.js, because this view is rendered server-side
    def _buildSpoolColorCss(self, colorValue):
        if (colorValue is None):
            return ""
        colorValue = str(colorValue).strip()
        if (colorValue.lower() == "rainbow"):
            return "linear-gradient(135deg, #ff2d2d 0%, #ff9a00 20%, #ffe600 40%, #16c172 60%, #2f7bff 80%, #a044ff 100%)"
        checkerboard = "repeating-conic-gradient(#c8c8c8 0% 25%, #ffffff 0% 50%) 50% / 8px 8px"
        if (colorValue.lower() == "transparent"):
            return checkerboard
        transparent = False
        if (colorValue.lower().startswith("transparent:")):
            transparent = True
            colorValue = colorValue[len("transparent:"):]
        # only accept hex colors, the value ends up in a style-attribute
        if (re.match(r"^#[0-9a-fA-F]{3,8}(;#[0-9a-fA-F]{3,8}){0,2}$", colorValue) is None):
            return ""
        colors = colorValue.split(";")
        if (transparent):
            # semi-opaque tint layered over the checkerboard (8-digit hex alpha)
            stops = []
            step = 100.0 / len(colors)
            for i, color in enumerate(colors):
                tinted = color + "8c" if (len(color) == 7) else color
                stops.append("%s %.1f%%" % (tinted, i * step))
                stops.append("%s %.1f%%" % (tinted, (i + 1) * step))
            return "linear-gradient(135deg, %s), %s" % (", ".join(stops), checkerboard)
        if (len(colors) == 1):
            return colorValue
        stops = []
        step = 100.0 / len(colors)
        for i, color in enumerate(colors):
            stops.append("%s %.1f%%" % (color, i * step))
            stops.append("%s %.1f%%" % (color, (i + 1) * step))
        return "linear-gradient(135deg, %s)" % ", ".join(stops)


    @octoprint.plugin.BlueprintPlugin.route("/generateQRCodeView/<string:databaseId>", methods=["GET"])
    def generateSpoolQRCodeHTMLView(self, databaseId):
        htmlContent = ""
        spoolModel = self._databaseManager.loadSpool(databaseId)
        if (spoolModel is not None):
            self._logger.info("Generate HTML iew for QR-Code")
            qrCodeImageUrl = flask.url_for("plugin.SpoolManager.generateSpoolQRCode", databaseId=databaseId)
            colorCss = self._buildSpoolColorCss(spoolModel.color)
            colorHtml = ""
            if (colorCss != ""):
                colorName = spoolModel.colorName if spoolModel.colorName else ""
                # value ends up in a html-attribute
                colorName = re.sub(r"[^\w\s#,()-]", "", colorName)
                colorHtml = "<h3>Spoolcolor: <span title='" + colorName + "' style=\"display:inline-block;" \
                            "width:0.9em;height:0.9em;border:1px solid #808080;border-radius:3px;" \
                            "vertical-align:baseline;background:" + colorCss + "\"></span> " + colorName + "</h3>"
            finishHtml = ""
            if (spoolModel.finish):
                # value ends up in html
                safeFinish = re.sub(r"[^\w\s#,()-]", "", str(spoolModel.finish))
                finishHtml = "<h3>Spoolfinish: " + safeFinish + "</h3>"
            htmlContent = \
                        "<h3>Database Id: " + str(spoolModel.databaseId) + "</h3>" \
                        "<h3>Spoolname: " + spoolModel.displayName + "</h3>" \
                        + colorHtml \
                        + finishHtml + \
                        "<img loading='lazy' src='" + qrCodeImageUrl + "' />"
        else:
            htmlContent = "<h3>Spool with database Id not found</h3>"

        qrCodeHTMLViewTemplate = ""\
                                "<html>" \
                                "<head><link rel='icon' href='data:,'></head>" \
                                + htmlContent +\
                                "</html>" \
                                ""

        return Response(
                        qrCodeHTMLViewTemplate,
                        mimetype='text/html',
                        # headers={'Content-Disposition': 'attachment; filename='+reportType+'PrintJobReport-Template.jinja2'}
                        )


    ######################################################################################   UPLOAD CSV FILE (in Thread)

    @octoprint.plugin.BlueprintPlugin.route("/importCSV", methods=["POST"])
    def importSpoolData(self):

        input_name = "file"
        input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

        if input_upload_path in flask.request.values:

            databaseSettings = self._databaseManager.getDatabaseSettings()
            backupDatabaseSettings = self._databaseManager.getDatabaseSettings()

            if (flask.request.form["externalDatabaseGroup"] == "true"):
                databaseSettings.useExternal = True
            else:
                databaseSettings.useExternal = False

            self._databaseManager.assignNewDatabaseSettings(databaseSettings)

            importMode = flask.request.form["importCSVMode"]
            # file was uploaded
            sourceLocation = flask.request.values[input_upload_path]

            # because we process in seperate thread we need to create our own temp file, the uploaded temp file will be deleted after this request-call
            archive = tempfile.NamedTemporaryFile(delete=False)
            archive.close()
            shutil.copy(sourceLocation, archive.name)
            sourceLocation = archive.name

            thread = threading.Thread(target=self._processCSVUploadAsync,
                                      args=(sourceLocation,
                                            importMode,
                                            self._databaseManager,
                                            self._sendCSVUploadStatusToClient,
                                            self._logger))
            thread.daemon = True
            thread.start()

            self._databaseManager.assignNewDatabaseSettings(backupDatabaseSettings)

            # targetLocation = self._cameraManager.buildSnapshotFilenameLocation(snapshotFilename, False)
            # os.rename(sourceLocation, targetLocation)
            pass
        else:
            return flask.make_response("Invalid request, neither a file nor a path of a file to restore provided", 400)


        return flask.jsonify(started=True)


    def _processCSVUploadAsync(self, path, importCSVMode, databaseManager, sendCSVUploadStatusToClient, logger):
        errorCollection = list()

        # - parsing
        # - backup
        # - append or replace

        def updateParsingStatus(lineNumber):
            # importStatus, currenLineNumber, backupFilePath,  successMessages, errorCollection
            sendCSVUploadStatusToClient("running", lineNumber, "", "", errorCollection)

        resultOfSpools = CSVExportImporter.parseCSV(path, updateParsingStatus, errorCollection, logger)

        if (len(errorCollection) != 0):
            successMessage = "Some error(s) occurs during parsing! No spools imported!"
            # importStatus, currenLineNumber, backupFilePath,  successMessages, errorCollection
            sendCSVUploadStatusToClient("finished", "", "", successMessage, errorCollection)
            return

        importModeText = "append"
        backupDatabaseFilePath = None
        if (len(resultOfSpools) > 0):
            # we could import some jobs

            # - backup
            backupDatabaseFilePath = databaseManager.backupDatabaseFile()

            # - import mode append/replace
            if (SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importCSVMode):
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

                remainingWeight = Transformer.calculateRemainingWeight(spool.usedWeight, spool.totalWeight)
                if (remainingWeight != None):
                    spool.remainingWeight = remainingWeight
                    # spool.save()

                spool.isActive = True

                databaseManager.saveSpool(spool)
            pass
        else:
            errorCollection.append("Nothing to import!")

        successMessage = ""
        if (len(errorCollection) == 0):
            successMessage = "All data is successful " + importModeText + " with " + str(len(resultOfSpools)) + " spools."
        else:
            successMessage = "Some error(s) occurs! Maybe you need to manually rollback the database!"
        logger.info(successMessage)
        sendCSVUploadStatusToClient("finished", "", backupDatabaseFilePath,  successMessage, errorCollection)
        pass

    def _buildDatabaseSettingsFromJson(self, jsonData):

        databaseSettings = DatabaseManager.DatabaseSettings()
        databaseSettings.useExternal =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_USE_EXTERNAL, jsonData)
        databaseSettings.type =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_TYPE, jsonData)
        databaseSettings.host =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_HOST, jsonData)
        databaseSettings.port =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_PORT, jsonData)
        databaseSettings.name =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_NAME, jsonData)
        databaseSettings.user =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_USER, jsonData)
        databaseSettings.password =  self._getValueFromJSONOrNone(SettingsKeys.SETTINGS_KEY_DATABASE_PASSWORD, jsonData)

        return databaseSettings


    #######################################################################################   DOWNLOAD DATABASE-FILE
    @octoprint.plugin.BlueprintPlugin.route("/downloadDatabase", methods=["GET"])
    def downloadDatabase(self):
        return send_file(self._databaseManager.getDatabaseSettings().fileLocation,
                         mimetype='application/octet-stream',
                         download_name='spoolmanager.db',
                         as_attachment=True)


    ##############################################################################   EXPORT / IMPORT MYSQL DATABASE DUMP
    # both routes work on the SAVED storage settings and are only available for external MySQL databases

    def _isExternalMySQLConfigured(self):
        databaseSettings = self._databaseManager.getDatabaseSettings()
        return databaseSettings != None and \
               databaseSettings.useExternal == True and \
               "mysql" == databaseSettings.type

    @octoprint.plugin.BlueprintPlugin.route("/exportDatabaseDump", methods=["GET"])
    def exportDatabaseDump(self):

        if (self._isExternalMySQLConfigured() == False):
            return flask.make_response("Database dump export is only available for external MySQL databases. Save the storage settings first.", 400)

        exportResult = self._databaseManager.exportMySQLDatabaseDump()
        if (exportResult["success"] == False):
            return flask.make_response("Database dump export failed: " + str(exportResult["errorMessage"]), 400)

        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M")
        fileName = "SpoolManager-mysql-" + currentDate + ".sql"

        return Response(exportResult["dump"],
                        mimetype='application/sql',
                        headers={'Content-Disposition': 'attachment; filename=' + fileName})

    @octoprint.plugin.BlueprintPlugin.route("/importDatabaseDump", methods=["POST"])
    def importDatabaseDump(self):

        if (self._isExternalMySQLConfigured() == False):
            return flask.make_response("Database dump import is only available for external MySQL databases. Save the storage settings first.", 400)

        input_name = "file"
        input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])
        if (input_upload_path not in flask.request.values):
            return flask.make_response("Invalid request, no dump file provided", 400)

        importMode = flask.request.form.get("importMode")
        if (importMode not in (SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE, SettingsKeys.KEY_IMPORTCSV_MODE_APPEND)):
            return flask.make_response("Invalid import mode", 400)

        sourceLocation = flask.request.values[input_upload_path]
        try:
            with open(sourceLocation, "r", encoding="utf-8") as dumpFile:
                dumpText = dumpFile.read()
        except UnicodeDecodeError:
            return flask.make_response("Dump file is not UTF-8 encoded", 400)

        importResult = self._databaseManager.importMySQLDatabaseDump(dumpText, importMode)

        if (importResult["success"] == True and SettingsKeys.KEY_IMPORTCSV_MODE_REPLACE == importMode):
            # same behaviour as the CSV replace-import
            self._resetSelectedSpools()

        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify({
            "success": importResult["success"],
            "errorMessage": importResult["errorMessage"],
            "executedStatementCount": importResult["executedStatementCount"],
            "importedSpoolCount": importResult["importedSpoolCount"],
            "metadata": metaDataResult
        })

    #######################################################################################   DELETE DATABASE
    @octoprint.plugin.BlueprintPlugin.route("/deleteDatabase/<string:databaseType>", methods=["POST"])
    def deleteDatabase(self, databaseType):

        databaseSettings = None
        if (databaseType == "external"):
            jsonData = request.json
            databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)
            databaseSettings.useExternal = True

        self._databaseManager.reCreateDatabase(databaseSettings)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify({
            "metadata": metaDataResult
        })

    #######################################################################################   COPY DATABASE
    @octoprint.plugin.BlueprintPlugin.route("/copyDatabase", methods=["POST"])
    def copyDatabase(self):
        # metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        jsonData = request.json
        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)
        metaDataResult = self._databaseManager.copySpoolData(databaseSettings)

        return flask.jsonify({
            "metadata": metaDataResult
        })

    #######################################################################################   LOAD DATABASE METADATA
    @octoprint.plugin.BlueprintPlugin.route("/loadDatabaseMetaData", methods=["GET"])
    def loadDatabaseMetaData(self):

        # databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        return flask.jsonify({
            "metadata": metaDataResult
        })

    #######################################################################################   UPGRADE DATABASE SCHEME
    @octoprint.plugin.BlueprintPlugin.route("/upgradeDatabaseScheme", methods=["PUT"])
    def upgradeDatabaseScheme(self):

        jsonData = request.json if request.is_json else {}
        # the frontend downloads the backup dump via /exportDatabaseDump before triggering the upgrade;
        # without that flag a backup file is written to the plugin data folder instead
        backupDownloaded = self._getValueFromJSONOrNone("backupDownloaded", jsonData) == True

        upgradeResult = self._databaseManager.upgradeExternalDatabaseScheme(createBackupFile=(backupDownloaded == False))
        # fresh metadata so the frontend can update the scheme version badges
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(None)

        if (upgradeResult["success"] == True):
            # refresh spool table and sidebar in all connected clients of this instance
            self._sendDataToClient(dict(action="reloadTable and sidebarSpools"))

        return flask.jsonify({
            "result": upgradeResult,
            "metadata": metaDataResult
        })

    #######################################################################################   TEST DATABASE CONNECTION
    @octoprint.plugin.BlueprintPlugin.route("/testDatabaseConnection", methods=["PUT"])
    def testDatabaseConnection(self):

        jsonData = request.json

        databaseSettings = self._buildDatabaseSettingsFromJson(jsonData)

        # databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)
        metaDataResult = self._databaseManager.loadDatabaseMetaInformations(databaseSettings)

        return flask.jsonify({
            "metadata": metaDataResult
        })

    ###############################################################################  CONFIRM DATABASE CONNECTION PROBLEM
    @octoprint.plugin.BlueprintPlugin.route("/confirmDatabaseProblemMessage", methods=["PUT"])
    def confirmDatabaseConnectionProblem(self):

        self.databaseConnectionProblemConfirmed = True

        # return flask.jsonify({
        #   "metadata": metaDataResult
        # })
        return flask.jsonify()

    ###########################################################################################   EXPORT DATABASE as CSV
    @octoprint.plugin.BlueprintPlugin.route("/exportSpools/<string:exportType>", methods=["GET"])
    def exportSpoolsData(self, exportType):

        databaseSettings = self._databaseManager.getDatabaseSettings()
        backupDatabaseSettings = self._databaseManager.getDatabaseSettings()

        if exportType == "CSV":

            if (flask.request.values["instance"] == "external"):
                databaseSettings.useExternal = True
            else:
                databaseSettings.useExternal = False

            self._databaseManager.assignNewDatabaseSettings(databaseSettings)

            allSpoolModels = self._databaseManager.loadAllSpoolsByQuery(None)

            self._databaseManager.assignNewDatabaseSettings(backupDatabaseSettings)

            now = datetime.datetime.now()
            currentDate = now.strftime("%Y%m%d-%H%M")
            fileName = "SpoolManager-" + currentDate + ".csv"

            csv = []
            for csvline in CSVExportImporter.transform2CSV(allSpoolModels):
                csv.append(csvline)

            return Response(csv,
                            mimetype='text/csv',
                            headers={'Content-Disposition': 'attachment; filename=' + fileName})

        else:
            if (exportType == "legacyFilamentManager"):
                allSpoolLegacyList = self._filamentManagerPluginImplementation.filamentManager.get_all_spools()
                if (allSpoolLegacyList != None):

                    allSpoolModelList = self._createSpoolModelFromLegacy(allSpoolLegacyList)

                    now = datetime.datetime.now()
                    currentDate = now.strftime("%Y%m%d-%H%M")
                    fileName = "FilamentManager-" + currentDate + ".csv"

                    return Response(CSVExportImporter.transform2CSV(allSpoolModelList),
                                    mimetype='text/csv',
                                    headers={'Content-Disposition': 'attachment; filename='+fileName})

                pass

            print("BOOOMM not supported type")
        pass

    ###################################################################################   INVENTORY REPORT (PDF/CSV/XLSX)
    @octoprint.plugin.BlueprintPlugin.route("/exportInventoryReport", methods=["GET"])
    def exportInventoryReport(self):
        from octoprint_SpoolManager.common import InventoryReport

        reportFormat = flask.request.values.get("format", "pdf").lower()
        if (reportFormat not in ("pdf", "csv", "xlsx")):
            return flask.make_response("Unsupported report format: " + reportFormat, 400)

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

        now = datetime.datetime.now()
        currentDate = now.strftime("%Y%m%d-%H%M")
        baseName = "SpoolManager-Inventory-" + currentDate

        if (reportFormat == "csv"):
            payload = InventoryReport.build_inventory_report_csv(allSpoolModels)
            mimetype = 'text/csv'
            fileName = baseName + ".csv"
        elif (reportFormat == "xlsx"):
            payload = InventoryReport.build_inventory_report_xlsx(allSpoolModels).getvalue()
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            fileName = baseName + ".xlsx"
        else:
            payload = InventoryReport.build_inventory_report_pdf(allSpoolModels).getvalue()
            mimetype = 'application/pdf'
            fileName = baseName + ".pdf"

        return Response(payload,
                        mimetype=mimetype,
                        headers={'Content-Disposition': 'attachment; filename=' + fileName})



    ##################################################################################################   LOAD ALL SPOOLS
    @octoprint.plugin.BlueprintPlugin.route("/loadSpoolsByQuery", methods=["GET"])
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
            return flask.jsonify({
                                    "databaseConnectionProblem": schemeUpgradeNeeded == False,
                                    "templateSpools": [],
                                    "catalogs": {
                                        "vendors": [],
                                        "materials": [],
                                        "colors": [],
                                        "labels": []
                                    },
                                    "totalItemCount": 0,
                                    "allSpools": [],
                                    "selectedSpools": [],
                                    "schemeUpgradeNeeded": schemeUpgradeNeeded
                                })

    def _loadAllSpoolsByQueryResponse(self, tableQuery):
        allSpools = self._databaseManager.loadAllSpoolsByQuery(tableQuery)
        totalItemCount = self._databaseManager.countSpoolsByQuery(tableQuery)

        # allSpoolsAsDict = self._transformAllSpoolModelsToDict(allSpools)
        allSpoolsAsDict = Transformer.transformAllSpoolModelsToDict(allSpools)

        # load all catalogs: vendors, materials, labels, [colors]
        vendors = list(self._databaseManager.loadCatalogVendors(tableQuery))
        materials = list(self._databaseManager.loadCatalogMaterials(tableQuery))
        labels = list(self._databaseManager.loadCatalogLabels(tableQuery))
        colors = list(self._databaseManager.loadCatalogColors(tableQuery))

        materials = self._addAdditionalMaterials(materials)

        # sort catalogs alphabetically (case-insensitive) for the filter/edit dropdowns, see issue #23
        vendors = sorted(vendors, key=lambda item: item.lower())
        materials = sorted(materials, key=lambda item: item.lower())
        labels = sorted(labels, key=lambda item: item.lower())
        colors = sorted(colors, key=lambda item: item["colorName"].lower())

        tempateSpoolAsDict = None
        allTemplateSpools = self._databaseManager.loadSpoolTemplates()
        allTemplateSpoolsAsDict = Transformer.transformAllSpoolModelsToDict(allTemplateSpools)

        # if (allTemplateSpools != None):
        #   for spool in allTemplateSpools:
        #       tempateSpoolAsDict = Transformer.transformSpoolModelToDict(spool)
        #       break

        catalogs = {
            "vendors": vendors,
            "materials": materials,
            "colors": colors,
            "labels": labels
        }
        # catalogs = {
        #   "materials": ["", "ABS", "PLA", "PETG"],
        #   "colors": ["", "#123", "#456"],
        #   "labels": ["", "good", "bad"]
        # }
        selectedSpoolsAsDicts = [
            (None if selectedSpool is None else Transformer.transformSpoolModelToDict(selectedSpool))
            for selectedSpool in self.loadSelectedSpools()
        ]

        schemeUpgradeNeeded = self._databaseManager.isSchemeUpgradeNeeded()
        if (schemeUpgradeNeeded == True):
            # queries succeed again, so another OctoPrint instance sharing the database
            # may have performed the upgrade in the meantime - re-evaluate the stale flag
            schemeUpgradeNeeded = self._databaseManager.recheckSchemeUpgradeNeeded()

        return flask.jsonify({
                                # "databaseConnectionProblem": self._databaseManager.isConnected() == False,
                                "templateSpools": allTemplateSpoolsAsDict,
                                "catalogs": catalogs,
                                "totalItemCount": totalItemCount,
                                "allSpools": allSpoolsAsDict,
                                "selectedSpools": selectedSpoolsAsDicts,
                                "schemeUpgradeNeeded": schemeUpgradeNeeded
                            })

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
            "PPSU"
        ]
        for currentMaterial in allMeterials:
            if ( (currentMaterial.upper() in databaseMaterials) == False and (currentMaterial.lower() in databaseMaterials) == False):
                databaseMaterials.append(currentMaterial)
        return databaseMaterials


    ##################################################################################################   NEXT SPOOL ID
    @octoprint.plugin.BlueprintPlugin.route("/nextSpoolId", methods=["GET"])
    def loadNextSpoolId(self):
        # prospective databaseId of the next created spool, used for the {id} display name variable preview
        maxDatabaseId = self._databaseManager.getMaxSpoolDatabaseId()
        nextSpoolId = 1 if maxDatabaseId is None else maxDatabaseId + 1
        return flask.jsonify({
                                "nextSpoolId": nextSpoolId
                            })

    def _resolveDisplayNameVariables(self, spoolModel):
        # replaces variables like {material}-{color}-{id} in the display name with the spool's field values,
        # see https://github.com/WildRikku/OctoPrint-SpoolManager/issues/49
        displayName = spoolModel.displayName
        if (StringUtils.isEmpty(displayName) or ("{" in displayName) == False):
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
        if (newDisplayName != displayName):
            spoolModel.displayName = newDisplayName
            return True
        return False

    #######################################################################################################   SAVE SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/saveSpool", methods=["PUT"])
    def saveSpool(self):
        self._logger.info("API Save spool")
        jsonData = request.json

        databaseId = self._getValueFromJSONOrNone("databaseId", jsonData)
        self._databaseManager.connectoToDatabase()
        validationErrors = []
        if (databaseId != None):
            self._logger.info("Load spool for update with database id '"+str(databaseId)+"'")
            spoolModel = self._databaseManager.loadSpool(databaseId, withReusedConnection=True)
            if (spoolModel == None):
                self._logger.warning("Save spool failed. Inital loading not possible, maybe already deleted.")
            else:
                validationErrors = self._updateSpoolModelFromJSONData(spoolModel, jsonData)
        else:
            self._logger.info("Create new spool")
            spoolModel = SpoolModel()
            validationErrors = self._updateSpoolModelFromJSONData(spoolModel, jsonData)

        # reject invalid input instead of silently dropping it (e.g. letters in the Cost field)
        if (validationErrors):
            self._databaseManager.closeDatabase()
            self._logger.warning("Save spool rejected, validation errors: " + str(validationErrors))
            return make_response(jsonify({"validationErrors": validationErrors}), 400)

        newDatabaseId = self._databaseManager.saveSpool(spoolModel, withReusedConnection=True)

        # resolve display name variables ({id} is only known after the initial save), but never inside templates
        if (databaseId == None and spoolModel.isTemplate != True):
            if (self._resolveDisplayNameVariables(spoolModel)):
                self._databaseManager.saveSpool(spoolModel, withReusedConnection=True)

        self._databaseManager.closeDatabase()

        if (databaseId == None):
            # New spool was created
            eventPayload = {
                "databaseId": spoolModel.databaseId,
                "spoolName": spoolModel.displayName,
                "material": spoolModel.material,
                "colorName": spoolModel.colorName,
                "remainingWeight": spoolModel.remainingWeight
            }
            self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_ADDED, eventPayload)

        # data for the sidebar
        self.checkRemainingFilament()

        return flask.jsonify()


    #####################################################################################################   DELETE SPOOL
    @octoprint.plugin.BlueprintPlugin.route("/deleteSpool/<int:databaseId>", methods=["DELETE"])
    def deleteSpool(self, databaseId):
        self._logger.info("API Delete spool with database id '" + str(databaseId) + "'")
        databaseId = self._databaseManager.deleteSpool(databaseId)
        if (databaseId != None):
            eventPayload = {
                "databaseId": databaseId
            }
            self._sendPayload2EventBus(EventBusKeys.EVENT_BUS_SPOOL_DELETED, eventPayload)

        return flask.jsonify()


