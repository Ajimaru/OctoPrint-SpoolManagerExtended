/*
 * View model for OctoPrint-SpoolManager
 *
 * Author: OllisGit
 * License: AGPLv3
 */
 // START: TESTZONE

 var expanded = false;

function showCheckboxes() {

  var checkboxes = document.getElementById("checkboxes");
  if (!expanded) {
    checkboxes.style.display = "block";
    expanded = true;
  } else {
    checkboxes.style.display = "none";
    expanded = false;
  }
}

var data = [{
   id: 0,
   text: 'enhancement',
	html: '<div class="pick-a-color-markup"><span class="color-preview" style="background-color: rgb(255, 255, 0);"></span>enhancement</div>'
}, {
   id: 1,
   text: 'bug',
	html: '<div style="color:red">bug</div><div><small>This is some small text on a new line</small></div>'
}];

function template(data) {
	return data.html;
}

$("#colorFilter").select2({
   data: data,
   templateResult: template,
   escapeMarkup: function(m) {
      return m;
   }
});

 // END: TESTZONE

// Builds a CSS background value for a spool color field.
// Supported values: single hex ("#ff0000"), multi-color ("#ff0000;#0000ff",
// up to three colors, rendered as a diagonally split box), the keyword
// "rainbow" (rendered as a rainbow gradient, see upstream issue #19) and
// "transparent"/"transparent:#hex" (rendered as a checkerboard, optionally
// tinted with the base color).
// Attached to window because OctoPrint wraps packed plugin JS in a closure,
// but the knockout bindings in the templates need global access.
window.spmSpoolColorCss = function(color) {
    var colorValue = ko.utils.unwrapObservable(color);
    if (!colorValue) {
        return "";
    }
    colorValue = "" + colorValue;
    if (colorValue.toLowerCase() === "rainbow") {
        return "linear-gradient(135deg, #ff2d2d 0%, #ff9a00 20%, #ffe600 40%, #16c172 60%, #2f7bff 80%, #a044ff 100%)";
    }
    var checkerboard = "repeating-conic-gradient(#c8c8c8 0% 25%, #ffffff 0% 50%) 50% / 8px 8px";
    if (colorValue.toLowerCase() === "transparent") {
        return checkerboard;
    }
    var transparentTint = null;
    if (colorValue.toLowerCase().indexOf("transparent:") === 0) {
        transparentTint = colorValue.substr("transparent:".length);
        colorValue = transparentTint;
    }
    var colors = colorValue.split(";");
    var singleOrGradient;
    if (colors.length === 1) {
        singleOrGradient = colorValue;
    } else {
        var stops = [];
        var step = 100 / colors.length;
        for (var i = 0; i < colors.length; i++) {
            stops.push(colors[i] + " " + (i * step).toFixed(1) + "%");
            stops.push(colors[i] + " " + ((i + 1) * step).toFixed(1) + "%");
        }
        singleOrGradient = "linear-gradient(135deg, " + stops.join(", ") + ")";
    }
    if (transparentTint != null) {
        // semi-opaque tint layered over the checkerboard
        var tinted = [];
        var tintStep = 100 / colors.length;
        for (var j = 0; j < colors.length; j++) {
            var rgb = tinycolor(colors[j]).setAlpha(0.55).toRgbString();
            tinted.push(rgb + " " + (j * tintStep).toFixed(1) + "%");
            tinted.push(rgb + " " + ((j + 1) * tintStep).toFixed(1) + "%");
        }
        return "linear-gradient(135deg, " + tinted.join(", ") + "), " + checkerboard;
    }
    return singleOrGradient;
};

$(function() {

    var PLUGIN_ID = "SpoolManager"; // from setup.py plugin_identifier


    ///////////////////////////////////////////////////////////////////////////////////////////////////////// VIEW MODEL
    function SpoolManagerViewModel(parameters) {

        var PLUGIN_ID = "SpoolManager"; // from setup.py plugin_identifier

        var self = this;

        // assign the injected parameters, e.g.:
        self.loginStateViewModel = parameters[0];
        self.loginState = parameters[0];
        self.settingsViewModel = parameters[1];
        self.printerStateViewModel = parameters[2];
        self.filesViewModel = parameters[3];
        self.printerProfilesViewModel = parameters[4];

        self.pluginSettings = null;

        self.apiClient = new SpoolManagerAPIClient(PLUGIN_ID, BASEURL);
        self.spoolDialog = new SpoolManagerEditSpoolDialog();


        //////////////////////////////////////////////////////////////////////////////////////////////// HELPER FUNCTION

        loadSettingsFromBrowserStore = function(){
            // TODO maybe in a separate js-file
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }
            // Table visibility
            self.initTableVisibilities();

            var storageKey = "spoolmanager.table.selectedPageSize";
            if (localStorage[storageKey] == null){
                localStorage[storageKey] = "25"; // default page size
            } else {
                // localStorage only stores strings; the page-size options are numbers
                // (except "all"), so convert back or the select shows the default (PR #8 fix)
                var storedPageSize = localStorage[storageKey];
                self.spoolItemTableHelper.selectedPageSize(storedPageSize == "all" ? "all" : Number(storedPageSize));
            }
            self.spoolItemTableHelper.selectedPageSize.subscribe(function(newValue){
                localStorage[storageKey] = newValue;
            });
        }

        // Toast while the plugin downloads/analyzes a printer-storage file
        self.printerFileAnalysisNotify = null;
        self.showPrinterFileAnalysisStarted = function(path){
            if (self.printerFileAnalysisNotify == null){
                self.printerFileAnalysisNotify = new PNotify({
                    title: "SPM: Analyzing print file",
                    text: "Fetching '" + path + "' from printer storage to calculate the needed filament...",
                    type: "info",
                    icon: "fa fa-spinner fa-spin",
                    hide: false
                });
            }
        };
        self.hidePrinterFileAnalysisToast = function(){
            if (self.printerFileAnalysisNotify != null){
                self.printerFileAnalysisNotify.remove();
                self.printerFileAnalysisNotify = null;
            }
        };

        // Typs: error
        self.showPopUp = function(popupType, popupTitle, message, autoclose){
            var title = popupType.toUpperCase() + ": " + popupTitle;
            var popupId = (title+message).replace(/([^a-z0-9]+)/gi, '-');
            if($("."+popupId).length <1) {
                new PNotify({
                    title: "SPM:" + title,
                    text: message,
                    type: popupType,
                    hide: autoclose,
                    addclass: popupId
                });
            }
        };


        // found here: https://stackoverflow.com/questions/19491336/how-to-get-url-parameter-using-jquery-or-plain-javascript?rq=1
        var getUrlParameter = function getUrlParameter(sParam) {
            var sPageURL = window.location.search.substring(1),
                sURLVariables = sPageURL.split('&'),
                sParameterName,
                i;

            for (i = 0; i < sURLVariables.length; i++) {
                sParameterName = sURLVariables[i].split('=');

                if (sParameterName[0] === sParam) {
                    return sParameterName[1] === undefined ? true : decodeURIComponent(sParameterName[1]);
                }
            }
        };

        self.reloadQRCodePreviewImage = function(){
            var imageDom = $("#settings-qrimage-preview");
            var currentSrc = imageDom.attr("src");
            currentSrc = currentSrc + "&"+new Date().getTime();
            imageDom.attr("src", currentSrc);
        }

        // Generate HTML-Image Attributes for the QR-Code
        self.generateQRCodeImageSourceAttribute = function(databaseId, spoolDisplayName, showHtmlView, withColors){
            var requestParameters = "";
            if (withColors){
                requestParameters = "?" +
                                    "fillColor=" + encodeURIComponent(self.pluginSettings.qrCodeFillColor()) + "&" +
                                    "backgroundColor=" + encodeURIComponent(self.pluginSettings.qrCodeBackgroundColor());

                if (self.pluginSettings.qrCodeUseURLPrefix() == true){
                    requestParameters = requestParameters + "&" +
                        "useURLPrefix=true" + "&" +
                        "urlPrefix=" + encodeURIComponent(self.pluginSettings.qrCodeURLPrefix())
                }
            }

            var source = "";
            if (showHtmlView == "htmlView"){
                source = PLUGIN_BASEURL + "SpoolManager/generateQRCodeView/" + databaseId + "" + requestParameters;
            } else {
                source = PLUGIN_BASEURL + "SpoolManager/generateQRCode/" + databaseId+ "" + requestParameters;
            }
            var title = "QR-Code for " + spoolDisplayName;
            return {
                src: source,
                href: source,
                title: title
            }
        }

        ///////////////////////////////////////////////////// START: SETTINGS
        self.pluginNotWorking = ko.observable(undefined);
        // external database still on an old scheme, needs the upgrade button in the settings (Storage tab)
        self.schemeUpgradeNeeded = ko.observable(false);

        self.downloadDatabaseUrl = ko.observable();
        self.databaseConnectionProblemDialog = new DatabaseConnectionProblemDialog();

        self.databaseMetaData = {
            localSchemeVersionFromDatabaseModel: ko.observable(),
            localSpoolItemCount: ko.observable(),
            externalSchemeVersionFromDatabaseModel: ko.observable(),
            externalSpoolItemCount: ko.observable(),
            schemeVersionFromPlugin: ko.observable(),
        }
        self.showInternalSuccessMessage = ko.observable(false);
        self.showInternalDatabaseErrorMessage = ko.observable(false);
        self.showExternalSuccessMessage = ko.observable(false);
        self.showExternalDatabaseErrorMessage = ko.observable(false);
        self.showUpdateSchemeMessage = ko.observable(false);
        self.externalDatabaseErrorMessage = ko.observable("");
        self.internalDatabaseErrorMessage = ko.observable("");
        self.showLocalBusyIndicator = ko.observable(false);
        self.showExternalBusyIndicator = ko.observable(false);
        self.databaseInUse = ko.observable("Internal");

        self.resetDatabaseMessages = function(){
            self.showInternalSuccessMessage(false);
            self.showInternalDatabaseErrorMessage(false);
            self.showExternalSuccessMessage(false);
            self.showExternalDatabaseErrorMessage(false);
            self.showUpdateSchemeMessage(false);
            self.externalDatabaseErrorMessage("");
            self.internalDatabaseErrorMessage("");
            self.schemeUpgradeResultText("");
        }

        self.handleDatabaseMetaDataResponse = function(metaDataResponse){
            var metadata = metaDataResponse["metadata"];
            console.log(metadata);

            if (metadata != null){
                var errorMessage = metadata["errorMessage"];
                if (errorMessage != null && errorMessage.length != 0){
                    if (self.pluginSettings.useExternal()) {
                        self.showExternalDatabaseErrorMessage(true);
                        self.externalDatabaseErrorMessage(errorMessage);
                    }
                    else {
                        self.showInternalDatabaseErrorMessage(true);
                        self.internalDatabaseErrorMessage(errorMessage);
                    }
                }
                var success = metadata["success"];
                if (success != null && success == true){
                    self.showExternalSuccessMessage(true && self.pluginSettings.useExternal());
                    self.showInternalSuccessMessage(true && !self.pluginSettings.useExternal());
                } else {
                    self.showExternalSuccessMessage(false && self.pluginSettings.useExternal());
                    self.showInternalSuccessMessage(false && !self.pluginSettings.useExternal());
                }

                self.databaseMetaData.localSchemeVersionFromDatabaseModel(metadata["localSchemeVersionFromDatabaseModel"]);
                self.databaseMetaData.localSchemeVersionFromDatabaseModel(metadata["localSchemeVersionFromDatabaseModel"]);
                self.databaseMetaData.localSpoolItemCount(metadata["localSpoolItemCount"]);
                self.databaseMetaData.externalSchemeVersionFromDatabaseModel(metadata["externalSchemeVersionFromDatabaseModel"]);
                self.databaseMetaData.externalSpoolItemCount(metadata["externalSpoolItemCount"]);
                self.databaseMetaData.schemeVersionFromPlugin(metadata["schemeVersionFromPlugin"]);

                if (self.databaseMetaData.schemeVersionFromPlugin() != self.databaseMetaData.externalSchemeVersionFromDatabaseModel()){
                    self.showUpdateSchemeMessage(true);
                }
            }
        }

        self.buildDatabaseSettings = function(){

            var databaseSettings = {
                useExternal: self.pluginSettings.useExternal(),
                databaseType: self.pluginSettings.databaseType(),
                databaseHost: self.pluginSettings.databaseHost(),
                databasePort: self.pluginSettings.databasePort(),
                databaseName: self.pluginSettings.databaseName(),
                databaseUser: self.pluginSettings.databaseUser(),
                databasePassword: self.pluginSettings.databasePassword(),
            }
            return databaseSettings
        }

        self.testDatabaseConnection = function(){
            self.resetDatabaseMessages()
            self.showLocalBusyIndicator(self.pluginSettings.useExternal() == false);
            self.showExternalBusyIndicator(self.pluginSettings.databasePassword() == true);

            var databaseSettings = self.buildDatabaseSettings();

            self.apiClient.testDatabaseConnection(databaseSettings, function(responseData) {
                  self.handleDatabaseMetaDataResponse(responseData);
                  self.showLocalBusyIndicator(false);
                  self.showExternalBusyIndicator(false);
             });
        }

        self.deleteDatabaseAction = function(databaseType) {
            var result = confirm("Do you really want to delete all SpoolManager data in the " + databaseType + " database?");
            if (result == true){
                var databaseSettings = self.buildDatabaseSettings();
                databaseSettings.useExternal = databaseType == "external"
                
                self.apiClient.callDeleteDatabase(databaseType, databaseSettings, function(responseData) {
                    self.handleDatabaseMetaDataResponse(responseData);
                    self.spoolItemTableHelper.reloadItems();
                });
            }
        };

        self.copySpools = function() {
            var result = confirm("Do you really want to copy all SpoolManager data from the internal database? This will replace all existing data.");
            if (result == true) {
                var databaseSettings = self.buildDatabaseSettings();
                self.apiClient.callCopyDatabase(databaseSettings, function(responseData) {
                    self.spoolItemTableHelper.reloadItems();
                    self.apiClient.loadDatabaseMetaData(function(responseData) {
                        self.handleDatabaseMetaDataResponse(responseData);
                    });
                });

            }
        }

        // - external database scheme upgrade (issue #30/#49 follow-up: scheme V8, auto-upgrade only runs for local SQLite)
        self.isExternalSchemeUpgradeAvailable = ko.pureComputed(function(){
            if (self.pluginSettings.useExternal() != true){
                return false;
            }
            var externalVersion = Number(self.databaseMetaData.externalSchemeVersionFromDatabaseModel());
            var pluginVersion = Number(self.databaseMetaData.schemeVersionFromPlugin());
            return isNaN(externalVersion) == false && isNaN(pluginVersion) == false && externalVersion < pluginVersion;
        });
        // - local SQLite scheme upgrade: the auto-upgrade normally runs at startup, but if the local
        //   database file was restored/replaced at an old scheme version the user needs a way to re-trigger it
        self.isLocalSchemeUpgradeAvailable = ko.pureComputed(function(){
            if (self.pluginSettings.useExternal() == true){
                return false;
            }
            var localVersion = Number(self.databaseMetaData.localSchemeVersionFromDatabaseModel());
            var pluginVersion = Number(self.databaseMetaData.schemeVersionFromPlugin());
            return isNaN(localVersion) == false && isNaN(pluginVersion) == false && localVersion < pluginVersion;
        });
        self.isSchemeUpgradeAvailable = ko.pureComputed(function(){
            return self.isExternalSchemeUpgradeAvailable() == true || self.isLocalSchemeUpgradeAvailable() == true;
        });
        self.schemeUpgradeInProgress = ko.observable(false);
        self.schemeUpgradeResultText = ko.observable("");

        // Downloads a URL and resolves only when the browser received a non-empty file.
        // Rejects (so callers can abort) on HTTP error or an empty response body.
        // Shared by the scheme-upgrade flow and the pre-import backup flow.
        self.downloadBackupFile = function(url, downloadFileName){
            return fetch(url)
                .then(function(response){
                    if (response.ok == false){
                        return response.text().then(function(text){
                            throw new Error(text || ("Backup download failed (HTTP " + response.status + ")"));
                        });
                    }
                    return response.blob();
                })
                .then(function(blob){
                    if (blob == null || blob.size == 0){
                        throw new Error("Backup download failed (empty file), operation aborted.");
                    }
                    var link = document.createElement("a");
                    link.href = URL.createObjectURL(blob);
                    link.download = downloadFileName;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(link.href);
                });
        };

        // Creates the pre-import safety backup of the ACTIVE database and downloads it, then calls
        // onReady(). The MANDATORY backup (.db/.sql) must download successfully or the import is
        // aborted via onError(message). OPTIONAL backups (.csv) are best-effort: a failed optional
        // download is logged but does NOT block the import (browsers can throttle multiple
        // auto-downloads). See the createImportBackup route.
        self.runPreImportBackupThenImport = function(onReady, onError){
            self.apiClient.callCreateImportBackup(function(success, data){
                if (success != true){
                    var msg = "Backup before import failed.";
                    if (data != null && data.responseText != null && data.responseText != ""){
                        msg = data.responseText;
                    }
                    onError(msg);
                    return;
                }
                var mandatoryFile = data != null ? data["mandatoryBackupFile"] : null;
                var optionalFiles = (data != null && data["optionalBackupFiles"]) ? data["optionalBackupFiles"] : [];
                if (mandatoryFile == null || mandatoryFile == ""){
                    onError("No backup was created, import aborted.");
                    return;
                }

                // 1) download the mandatory backup - failure aborts the import
                self.downloadBackupFile(self.apiClient.getDatabaseBackupDownloadUrl(mandatoryFile), mandatoryFile)
                    .then(function(){
                        // 2) best-effort: download optional backups, ignoring their failures.
                        // Chained sequentially with a small gap so browsers don't drop the second download.
                        var chain = Promise.resolve();
                        optionalFiles.forEach(function(fileName){
                            chain = chain.then(function(){
                                return new Promise(function(resolve){ setTimeout(resolve, 300); });
                            }).then(function(){
                                return self.downloadBackupFile(self.apiClient.getDatabaseBackupDownloadUrl(fileName), fileName);
                            }).catch(function(optError){
                                if (window.console){ console.warn("Optional backup download failed (import continues):", fileName, optError); }
                            });
                        });
                        return chain;
                    })
                    .then(function(){
                        onReady();
                    })
                    .catch(function(error){
                        // only the mandatory download reaches here (optional errors are swallowed above)
                        onError("" + ((error != null && error.message) ? error.message : error));
                    });
            });
        };

        self.upgradeDatabaseSchemeAction = function(){
            var isExternal = self.pluginSettings.useExternal() == true;
            var confirmMessage = isExternal
                ? "Upgrade the external database scheme now?\n\n" +
                  "A SQL dump backup is downloaded first. " +
                  "If the backup download fails, the upgrade is aborted."
                : "Upgrade the local database scheme now?\n\n" +
                  "A copy of the database file is downloaded first. " +
                  "If the backup download fails, the upgrade is aborted.";
            var confirmed = confirm(confirmMessage);
            if (confirmed != true){
                return;
            }
            self.resetDatabaseMessages();
            self.schemeUpgradeResultText("");
            self.schemeUpgradeInProgress(true);

            var handleUpgradeResponse = function(responseData){
                self.schemeUpgradeInProgress(false);
                var result = responseData != null ? responseData["result"] : null;
                if (result != null && result["success"] == true){
                    self.schemeUpgradeResultText("Database scheme upgraded to version " + result["toVersion"] +
                                                 ". The backup was downloaded.");
                    if (responseData["metadata"] != null){
                        self.handleDatabaseMetaDataResponse(responseData);
                    }
                    // a full reload makes sure all cached viewmodels (sidebar, dialogs) pick up the repaired database
                    if (confirm("Database scheme upgraded to version " + result["toVersion"] + ".\n\n" +
                                "A page reload is recommended. Reload now?")){
                        location.reload();
                    }
                    return;
                } else {
                    var errorMessage = (result != null && result["errorMessage"] != null)
                                        ? result["errorMessage"]
                                        : "Scheme upgrade failed. See octoprint.log for details.";
                    self.showExternalDatabaseErrorMessage(true);
                    self.externalDatabaseErrorMessage(errorMessage);
                }
                if (responseData != null && responseData["metadata"] != null){
                    self.handleDatabaseMetaDataResponse(responseData);
                }
            };

            // shared helper (self.downloadBackupFile): downloads a URL, rejects on empty/failed download
            var downloadBackupFile = self.downloadBackupFile;

            var handleDownloadError = function(error){
                self.schemeUpgradeInProgress(false);
                self.showExternalDatabaseErrorMessage(true);
                self.externalDatabaseErrorMessage("" + ((error != null && error.message) ? error.message : error));
            };

            // local SQLite: create the .db backup on the server, download it, and only then migrate.
            // If the backup creation or download fails, the migration is aborted (same guarantee as external).
            if (isExternal == false){
                self.apiClient.callCreateDatabaseBackup(function(backupResponse){
                    var backupFileName = backupResponse != null ? backupResponse["backupFileName"] : null;
                    if (backupFileName == null){
                        handleDownloadError(new Error("Could not create the database backup, upgrade aborted."));
                        return;
                    }
                    downloadBackupFile(self.apiClient.getDatabaseBackupDownloadUrl(backupFileName), backupFileName)
                        .then(function(){
                            // backup is on disk and downloaded, run the migration (no second backup)
                            self.apiClient.callUpgradeDatabaseScheme({backupDownloaded: true}, handleUpgradeResponse);
                        })
                        .catch(handleDownloadError);
                });
                return;
            }

            // external: download the backup dump; the migration only starts after a successful download
            var now = new Date();
            var pad = function(value){ return (value < 10 ? "0" : "") + value; };
            var dumpFileName = "SpoolManager-mysql-backup-" + now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) +
                               "-" + pad(now.getHours()) + pad(now.getMinutes()) + ".sql";
            downloadBackupFile(self.apiClient.getDatabaseDumpExportUrl(), dumpFileName)
                .then(function(){
                    // backup is on disk, run the migration
                    self.apiClient.callUpgradeDatabaseScheme({backupDownloaded: true}, handleUpgradeResponse);
                })
                .catch(handleDownloadError);
        }

        // - MySQL dump export/import (Storage tab, external database only)
        self.isExternalMySQL = ko.pureComputed(function(){
            return self.pluginSettings.useExternal() == true && self.pluginSettings.databaseType() == "mysql";
        });
        self.sqlImportMode = ko.observable("append");
        self.sqlDumpFileName = ko.observable();
        self.sqlImportInProgress = ko.observable(false);
        self.sqlImportResultText = ko.observable();
        self.sqlDumpFile = undefined;

        self.getDatabaseDumpExportUrl = function(){
            return self.apiClient.getDatabaseDumpExportUrl();
        }

        self.sqlDumpFileChanged = function(data, event){
            var files = event.target.files;
            if (files == null || files.length == 0){
                self.sqlDumpFileName(undefined);
                self.sqlDumpFile = undefined;
                return;
            }
            self.sqlDumpFileName(files[0].name);
            self.sqlDumpFile = files[0];
        }

        // - local SQLite .db restore (internal database only)
        self.dbRestoreMode = ko.observable("append");
        self.dbRestoreFileName = ko.observable();
        self.dbRestoreInProgress = ko.observable(false);
        self.dbRestoreResultText = ko.observable();
        self.dbRestoreFile = undefined;

        self.dbRestoreFileChanged = function(data, event){
            var files = event.target.files;
            if (files == null || files.length == 0){
                self.dbRestoreFileName(undefined);
                self.dbRestoreFile = undefined;
                return;
            }
            self.dbRestoreFileName(files[0].name);
            self.dbRestoreFile = files[0];
        }

        self.performDBRestoreFromUpload = function(){
            if (self.dbRestoreFile === undefined) return;

            if (self.confirmReplaceIfNeeded(self.dbRestoreMode()) != true){
                return;
            }

            self.resetDatabaseMessages();
            self.dbRestoreResultText(undefined);
            self.dbRestoreInProgress(true);
            self.showLocalBusyIndicator(true);

            var runDbRestore = function(){
                self.apiClient.callImportDatabaseFile(self.dbRestoreFile, self.dbRestoreMode(), function(success, data){
                    self.dbRestoreInProgress(false);
                    self.showLocalBusyIndicator(false);

                    if (success == true && data != null && data.success == true){
                        self.handleDatabaseMetaDataResponse(data);
                        self.showInternalSuccessMessage(false);
                        self.dbRestoreResultText("Database file restore successful: " + data.importedSpoolCount + " spool(s) (" + self.dbRestoreMode() + ").");
                        self.spoolItemTableHelper.reloadItems();
                        // a replace swaps the whole db file - a reload makes cached viewmodels pick it up
                        if (self.dbRestoreMode() == "replace" && confirm("Database file restored. A page reload is recommended. Reload now?")){
                            location.reload();
                        }
                    } else {
                        var errorMessage = "Database file restore failed.";
                        if (data != null && data.errorMessage != null){
                            errorMessage = data.errorMessage;
                        } else if (data != null && data.responseText != null && data.responseText != ""){
                            errorMessage = data.responseText;
                        }
                        self.showInternalDatabaseErrorMessage(true);
                        self.internalDatabaseErrorMessage(errorMessage);
                    }
                });
            };

            // create + download the pre-import backup first; only restore after it succeeded
            self.runPreImportBackupThenImport(runDbRestore, function(errorMessage){
                self.dbRestoreInProgress(false);
                self.showLocalBusyIndicator(false);
                self.showInternalDatabaseErrorMessage(true);
                self.internalDatabaseErrorMessage("Restore aborted - " + errorMessage);
            });
        }

        self.performSQLImportFromUpload = function(){
            if (self.sqlDumpFile === undefined) return;

            if (self.confirmReplaceIfNeeded(self.sqlImportMode()) != true){
                return;
            }

            self.resetDatabaseMessages();
            self.sqlImportResultText(undefined);
            self.sqlImportInProgress(true);
            self.showExternalBusyIndicator(true);

            var runSqlImport = function(){
                self.apiClient.callImportDatabaseDump(self.sqlDumpFile, self.sqlImportMode(), function(responseData){
                    self.sqlImportInProgress(false);
                    self.showExternalBusyIndicator(false);

                    if (responseData != null && responseData.success == true){
                        self.handleDatabaseMetaDataResponse(responseData);
                        // show a dedicated import result instead of the generic connection message
                        self.showExternalSuccessMessage(false);
                        self.sqlImportResultText("Database dump import successful: " + responseData.importedSpoolCount + " spools imported (" + self.sqlImportMode() + ").");
                        self.spoolItemTableHelper.reloadItems();
                    } else {
                        var errorMessage = "Database dump import failed.";
                        if (responseData != null && responseData.errorMessage != null){
                            errorMessage = responseData.errorMessage;
                        } else if (responseData != null && responseData.responseText != null){
                            errorMessage = responseData.responseText;
                        }
                        self.showExternalDatabaseErrorMessage(true);
                        self.externalDatabaseErrorMessage(errorMessage);
                    }
                });
            };

            // create + download the pre-import backup first; only import after it succeeded
            self.runPreImportBackupThenImport(runSqlImport, function(errorMessage){
                self.sqlImportInProgress(false);
                self.showExternalBusyIndicator(false);
                self.showExternalDatabaseErrorMessage(true);
                self.externalDatabaseErrorMessage("Import aborted - " + errorMessage);
            });
        }

        $("#spoolmanger-settings-tab").find('a[data-toggle="tab"]').on('shown', function (e) {

              var activatedTab = e.target.hash; // activated tab
              var prevTab = e.relatedTarget.hash; // previous tab

              if (self.pluginSettings.useExternal() == true) {
                self.databaseInUse("External")
              } else {
                self.databaseInUse("Internal")
              }

              if ("#tab-spool-Storage" == activatedTab){
                  self.resetDatabaseMessages()
                  
                  self.showLocalBusyIndicator(self.pluginSettings.useExternal() == false);
                  self.showExternalBusyIndicator(self.pluginSettings.databasePassword() == true);      
                  
                  var databaseSettings = self.buildDatabaseSettings();
                  self.apiClient.loadDatabaseMetaData(function(responseData) {
                        self.handleDatabaseMetaDataResponse(responseData);
                        self.showExternalSuccessMessage(false);
                        self.showInternalSuccessMessage(false);
                        self.showLocalBusyIndicator(false);
                        self.showExternalBusyIndicator(false);
                   });
              }
        });

        self.isFilamentManagerPluginAvailable = ko.observable(false);
        self.isMqttPluginAvailable = ko.observable(false);

        // - Import CSV
        self.csvFileUploadName = ko.observable();
        self.csvImportInProgress = ko.observable(false);

        self.csvImportDialog = new SpoolManagerImportDialog();
        self.csvImportUploadButton = $("#settings-spool-importcsv-upload");
        self.csvImportUploadData = undefined;
        self.csvImportUploadButton.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                self.csvFileUploadName(data.files[0].name);
                self.csvImportUploadData = data;
            },
            done: function(e, data) {
                self.csvImportInProgress(false);
                self.csvFileUploadName(undefined);
                self.csvImportUploadData = undefined;
            },
            error: function(response, data, errorMessage){
                self.csvImportInProgress(false);
                statusCode = response.status;       // e.g. 400
                statusText = response.statusText;   // e.g. BAD REQUEST
                responseText = response.responseText; // e.g. Invalid request
            }
        });
        // Spool count of the CURRENTLY ACTIVE database (for the replace-confirm dialog).
        self.activeDatabaseSpoolCount = function(){
            var count = self.pluginSettings.useExternal() == true
                ? self.databaseMetaData.externalSpoolItemCount()
                : self.databaseMetaData.localSpoolItemCount();
            return (count == null || count == "") ? "?" : count;
        };

        // Confirms a replace import (shows how many spools will be deleted). Returns true to proceed.
        self.confirmReplaceIfNeeded = function(importMode){
            if (importMode != "replace"){
                return true;
            }
            var dbName = self.databaseInUse().toLowerCase();
            return confirm("REPLACE import: this will DELETE all " + self.activeDatabaseSpoolCount() +
                           " spool(s) in the " + dbName + " database and replace them with the uploaded file.\n\n" +
                           "A backup is created and downloaded first. Continue?");
        };

        self.performCSVImportFromUpload = function() {
            if (self.csvImportUploadData === undefined) return;

            var importMode = self.pluginSettings.importCSVMode();
            if (self.confirmReplaceIfNeeded(importMode) != true){
                return;
            }

            self.csvImportInProgress(true);

            // create + download the pre-import backup first; only import after it succeeded
            self.runPreImportBackupThenImport(function(){
                self.csvImportDialog.showDialog(function(shouldTableReload){
                        if (shouldTableReload == true){
                            self.spoolItemTableHelper.reloadItems();
                        }
                    }
                );
                self.csvImportUploadData.submit();
            }, function(errorMessage){
                self.csvImportInProgress(false);
                self.showExternalDatabaseErrorMessage(true);
                self.externalDatabaseErrorMessage("Import aborted - " + errorMessage);
                self.showInternalDatabaseErrorMessage(self.pluginSettings.useExternal() != true);
                self.internalDatabaseErrorMessage("Import aborted - " + errorMessage);
            });
        };

        // template stuff
        self.checkExcludedFromTemplateCopy = function(fieldName) {
            return ko.pureComputed({
                read: function () {
                    var result = self.pluginSettings.excludedFromTemplateCopy().includes(fieldName) == false;
                    return result
                },
                write: function (value) {
                    if (value == false){
                        self.pluginSettings.excludedFromTemplateCopy.push(fieldName);
                    } else {
                       self.pluginSettings.excludedFromTemplateCopy.remove(fieldName);
                    }
                },
                owner: this
            });
        }

        // overwrite save-button
        const origSaveSettingsFunction = self.settingsViewModel.saveData;
        const newSaveSettingsFunction = function confirmSpoolSelectionBeforeStartPrint(data, successCallback, setAsSending) {
            if (self.pluginSettings.useExternal() == true &&
                (self.showExternalDatabaseErrorMessage() == true || self.showInternalDatabaseErrorMessage() == true || 
                    self.showUpdateSchemeMessage() == true)
                ){
                return origSaveSettingsFunction(data, successCallback, setAsSending);
            }
            return origSaveSettingsFunction(data, successCallback, setAsSending);
        }
        self.settingsViewModel.saveData = newSaveSettingsFunction;

        // QR-Code stuff
        self.generateQRCodeTestLink = function(){
            var source = self.pluginSettings.qrCodeURLPrefix() + "/plugin/SpoolManager/selectSpoolByQRCode/qrPreviewId";
            var title = "This link is used for the QR-Code";
            return {
                href: source,
                title: title
            }
        }

        ///////////////////////////////////////////////////// END: SETTINGS


        ////////////////////////////////////////////////////////////////////////////////////////// SIDEBAR - REPLACEMENT
        self.printerStateViewModel.spoolsWithWeight = ko.observableArray([]);
        self.printerStateViewModel.extrusionValues = ko.observableArray([]);

        self.updateExtrusionValues = function(extrusionValuesArray){
            // update patched PrinterStateViewModel
            self.printerStateViewModel.extrusionValues(extrusionValuesArray);
        }

        self.updateRequiredFilament = function(requiredFilament){
/*
                    # "metaDataPresent": metaDataPresent,
					# "warnUser": fromPluginSettings,
					# "attributesMissing": someAttributesMissing,
					# "notEnough": notEnough,
					# "detailedSpoolResult": [
					# 				"toolIndex": toolIndex,
					# 				"requiredWeight": requiredWeight,
					# 				"requiredLength": filamentLength,
					# 				"diameter": diameter,
					# 				"density": density,
					# 				"notEnough": notEnough,
					# 				"spoolSelected": True
					# ]
 */
            var filamentList = requiredFilament["detailedSpoolResult"];
            var filteredFilamentList = [];
            // filter not required tools
            for (filamentItem of filamentList){
                if (filamentItem.requiredLength > 0){
                    filteredFilamentList.push(filamentItem)
                }
            }
            self.printerStateViewModel.spoolsWithWeight(filteredFilamentList)
        }

        self.printerStateViewModel.formatSpoolsWithWeight = function formatSpoolsWithWeightInSidebar(filament) {
            if (!filament) return '-';

            // length in the configured display unit
            var result = self.formatLengthForDisplay(filament.requiredLength);
            // try to get the weight
            if (filament.requiredWeight) {
                result += ' / ' + self.formatWeightForDisplay(filament.requiredWeight);
            }
            if (filament.spoolSelected && filament.spoolSelected == true){
                if (filament.notEnough) {
                    if (filament.notEnough == true){
                        result += ' (<span style="color:red">'+ self.formatWeightForDisplay(filament.remainingWeight) +'</span>)';
                    }
                }
            } else {
                if (filament.requiredLength > 0){
                    result += ' (no spool selected)';
                }
            }

            return result;
        };

        self.replaceFilamentView = function replaceFilamentViewInSidebar() {
            $('#state').find('.accordion-inner').contents().each(function (index, item) {
                if (item.nodeType === Node.COMMENT_NODE) {
                    if (item.nodeValue === ' ko foreach: filament ' || item.nodeValue === ' ko foreach: [] ') {
                        item.nodeValue = ' ko foreach: [] '; // eslint-disable-line no-param-reassign
                        var element = '<!-- ko if: spoolsWithWeight().length < 1 -->  <span><strong>Required Filament unknown</strong></span><br/> <!-- /ko -->';
                        element += '<!-- ko foreach: spoolsWithWeight --> <span data-bind="text: \'Tool \' + toolIndex + \': \', attr: {title: \'Filament usage for Spool \' + spoolName}"></span><strong data-bind="html: $root.formatSpoolsWithWeight($data)"></strong><br> <!-- /ko -->';

                        element += '<div data-bind="visible: settings.settings.plugins.SpoolManager.extrusionDebuggingEnabled">';
                        element += '<!-- ko foreach: extrusionValues -->';
                        element += '<div>Extruded Tool <span data-bind="text: $index"></span>: <strong data-bind="text: $data.toFixed(2)"></strong></div>';
                        element += '<!-- /ko -->';

                        element += '</div>'
                        $(element).insertBefore(item);

                        return false; // exit loop
                    }
                }
                return true;
            });
        };

        /////////////////////////////////////////////////////////////////////////////////////////// SIDEBAR - SELECT
        self.allSpoolsForSidebar = ko.observableArray([]);
        self.selectedSpoolsForSidebar = ko.observableArray([]);
        // see FILTER/SORTING https://embed.plnkr.co/plunk/Kj5JMv
        self.filterSelectionQuery = ko.observable();

        // self.sidebarFilterSorter = new SpoolsFilterSorter("sidebarSpoolSelection", self.allSpoolsForSidebar);

        self.sidebarSelectSpoolModalToolIndex = ko.observable(null);  // index of the current tool we want to select for
        self.sidebarSelectSpoolModalSpoolItem = ko.observable(null); // current spoolitem

        self.deselectSpoolForSidebar = function(toolIndex, item){
            self.selectSpoolForSidebar(toolIndex, null);
        }

        // Lazy loading of the spool selector's data (Attribution @mdziekon, PR #8):
        // when enabled, the (up to 3333 items) selector dataset is fetched on first
        // dialog open instead of on OctoPrint page load.
        self.hasInitializedSpoolsSelector = false;

        self._isLazySelectorEnabled = function(){
            return self.pluginSettings != null
                && self.pluginSettings.performanceLazyLoadSpoolSelectorData
                && self.pluginSettings.performanceLazyLoadSpoolSelectorData() == true;
        }

        // resize the sidebar spool slots to match the printer profile's extruder count
        self.updateAvailableSpoolSlots = function() {
            var currentProfileData = self.settingsViewModel.printerProfiles.currentProfileData(),
                numExtruders = (currentProfileData ? currentProfileData.extruder.count() : 0),
                currentSelectedSpools = self.selectedSpoolsForSidebar().length,
                diff = numExtruders - currentSelectedSpools,
                i;
            if (diff !== 0) {
                if (diff > 0) {
                    for (i = 0; i < diff; i++) {
                        self.selectedSpoolsForSidebar().push(ko.observable(null));
                    }
                } else if (diff < 0) {
                    for (i = 0; i > diff; i--) {
                        self.selectedSpoolsForSidebar().pop();
                    }
                }
                self.selectedSpoolsForSidebar.valueHasMutated();
            }
        }

        // fill the sidebar spool slots with the given selected-spools data
        self._applySelectedSpoolsData = function(spoolsData) {
            var slot, spoolData, spoolItem;
            for(var i=0; i<self.selectedSpoolsForSidebar().length; i++) {
                slot = self.selectedSpoolsForSidebar()[i];
                spoolData = (i < spoolsData.length) ? spoolsData[i] : null;
                spoolItem = spoolData ? self.spoolDialog.createSpoolItemForTable(spoolData) : null;
                slot(spoolItem);
            }
        }

        // fetch the current spool selection via the dedicated endpoint
        self.loadCurrentSelectedSpoolsData = function() {
            self.apiClient.callLoadSelectedSpools(function(responseData){
                var spoolsData = responseData["selectedSpools"];
                if (spoolsData != null){
                    self._applySelectedSpoolsData(spoolsData);
                }
            });
        }

        // fetch the full spool list for the sidebar select-spool dialog
        self.loadSpoolSelectorData = function() {
            self.hasInitializedSpoolsSelector = true;

            var currentFilterName = "all";
            // if (self.pluginSettings!= null){
            //      if(self.pluginSettings.hideEmptySpoolsInSidebar() == true) {
            //          currentFilterName = "hideEmptySpools";
            //      }
            //      if(self.pluginSettings.hideInactiveSpoolsInSidebar() == true) {
            //          currentFilterName = "hideInactiveSpools";
            //      }
            //      if(self.pluginSettings.hideEmptySpoolsInSidebar() == true && self.pluginSettings.hideInactiveSpoolsInSidebar() == true) {
            //          currentFilterName = "hideEmptySpools,hideInactiveSpools";
            //      }
            // }

            var tableQuery = {
                filterName: currentFilterName,
                from: 0,
                to: 3333,
                sortColumn: "lastUse",
                sortOrder: "desc"
            }

            // api-call
            self.apiClient.callLoadSpoolsByQuery(tableQuery, function(responseData){

                var allSpoolData = responseData["allSpools"]; // rawdtata
                if (allSpoolData != null){
                    var allSpoolItems = ko.utils.arrayMap(allSpoolData, function (spoolData) {
                        var result = self.spoolDialog.createSpoolItemForTable(spoolData);
                        return result;
                    }); // transform to SpoolItems with KO.obseravables
                    self.allSpoolsForSidebar(allSpoolItems);
                    // Pre sorting in Selection-Dialog
                    // self.sidebarFilterSorter.sortSpoolArray("displayName", "ascending");
                }
            });
        }

        // load everything the sidebar widgets need; while the lazy selector setting is
        // active and the selector was never opened, the big selector query is skipped
        self.loadSidebarSpoolWidgetsData = function() {
            self.updateAvailableSpoolSlots();
            self.loadCurrentSelectedSpoolsData();
            if (self._isLazySelectorEnabled() == false || self.hasInitializedSpoolsSelector == true){
                self.loadSpoolSelectorData();
            }
        }

        // ----------------- start: display units (table / sidebar / tooltips)
        // base values are always stored in mm/g, these helpers only convert for display
        var LENGTH_UNIT_FACTORS = { "mm": 1, "cm": 10, "m": 1000 };
        var WEIGHT_UNIT_FACTORS = { "g": 1, "kg": 1000 };
        var UNIT_DISPLAY_DECIMALS = { "mm": 1, "cm": 2, "m": 3, "g": 1, "kg": 3 };

        self.selectedLengthUnit = function(){
            var unit = (self.pluginSettings && self.pluginSettings.lengthUnit) ? self.pluginSettings.lengthUnit() : "mm";
            return LENGTH_UNIT_FACTORS[unit] ? unit : "mm";
        };
        self.selectedWeightUnit = function(){
            var unit = (self.pluginSettings && self.pluginSettings.weightUnit) ? self.pluginSettings.weightUnit() : "g";
            return WEIGHT_UNIT_FACTORS[unit] ? unit : "g";
        };

        // convert a raw value (mm resp. g) to the configured display unit; returns a number
        self.convertLengthForDisplay = function(rawMillimeter){
            var unit = self.selectedLengthUnit();
            var value = parseFloat(rawMillimeter);
            if (isNaN(value)) return rawMillimeter;
            return parseFloat((value / LENGTH_UNIT_FACTORS[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit]));
        };
        self.convertWeightForDisplay = function(rawGram){
            var unit = self.selectedWeightUnit();
            var value = parseFloat(rawGram);
            if (isNaN(value)) return rawGram;
            return parseFloat((value / WEIGHT_UNIT_FACTORS[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit]));
        };

        // full "<value><unit>" strings for direct display
        self.formatLengthForDisplay = function(rawMillimeter){
            if (rawMillimeter == null || rawMillimeter === "") return "";
            var value = self.convertLengthForDisplay(rawMillimeter);
            if (value === rawMillimeter && isNaN(parseFloat(rawMillimeter))) return "" + rawMillimeter;
            return value + self.selectedLengthUnit();
        };
        self.formatWeightForDisplay = function(rawGram){
            if (rawGram == null || rawGram === "") return "";
            var value = self.convertWeightForDisplay(rawGram);
            if (value === rawGram && isNaN(parseFloat(rawGram))) return "" + rawGram;
            return value + self.selectedWeightUnit();
        };
        // ----------------- end: display units

        _buildRemainingText = function(spoolItem){
            var remainingInfo = "";
            // if (  spoolItem.remainingWeight() != null && spoolItem.remainingWeight().length != 0
            //     && spoolItem.remainingPercentage() != null && spoolItem.remainingPercentage().length != 0){
            //     remainingInfo = "("+spoolItem.remainingWeight()+"g / "+spoolItem.remainingPercentage()+"%)";
            // }
            if (  spoolItem.remainingWeight() != null && spoolItem.remainingWeight().length != 0){
                // remainingInfo = "(R: "+spoolItem.remainingWeight()+"g)";
                remainingInfo = self.formatWeightForDisplay(spoolItem.remainingWeight());
            }
            return remainingInfo
        }

        self.remainingText = function(spoolItem){
            var remainingWeight = _buildRemainingText(spoolItem);
            var remainingLength = self.formatLengthForDisplay(spoolItem.remainingLength());
            var remainingInfo = "(" + remainingWeight + ", " + remainingLength + ")";
            return remainingInfo;
        }

        // attribute-name -> value/unit conversion for tooltips (values are stored in mm/g)
        var _isLengthAttribute = function(attribute){
            return typeof attribute === "string" && attribute.toLowerCase().indexOf("length") !== -1;
        };
        var _isWeightAttribute = function(attribute){
            return typeof attribute === "string" && attribute.toLowerCase().indexOf("weight") !== -1;
        };
        // format a single attribute value honoring the configured display units;
        // falls back to the raw value plus the passed-in unit for non-length/weight attributes
        var _formatTooltipAttribute = function(rawValue, attribute, fallbackUnit){
            if (_isLengthAttribute(attribute)){
                return self.formatLengthForDisplay(rawValue);
            }
            if (_isWeightAttribute(attribute)){
                return self.formatWeightForDisplay(rawValue);
            }
            return rawValue + (fallbackUnit || "");
        };

        self.buildTooltipForSpoolItem = function(spoolItem, textPrefix, attribute, unit, textPrefix2, attribute2, unit2){
            var tooltip = "";

            // first attribute
            if (spoolItem[attribute]() != null){
                tooltip = textPrefix + _formatTooltipAttribute(spoolItem[attribute](), attribute, unit);
            }

            // optional second attribute
            if (attribute2 && spoolItem[attribute2]() != null){
                tooltip += (tooltip ? ", " : "") + textPrefix2 + _formatTooltipAttribute(spoolItem[attribute2](), attribute2, unit2);
            }

            return tooltip;
        }

        self.getSpoolItemSelectedTool = function(databaseId) {
            var spoolItem;
            for (var i=0; i<self.selectedSpoolsForSidebar().length; i++) {
                spoolItem = self.selectedSpoolsForSidebar()[i]();
                if (spoolItem !== null && self.selectedSpoolsForSidebar()[i]().databaseId() === databaseId) {
                    return i;
                }
            }
            return null;
        }

        self.selectSpoolForSidebar = function(toolIndex, spoolItem){
            var commitCurrentSpoolValues;
            if (self.printerStateViewModel.isPrinting()) {
                commitCurrentSpoolValues = confirm(
                    'You are changing a spool while printing. SpoolManager will commit the usage so far to the previous spool, unless you wish otherwise.\n\n' +
                    'Commit the usage of the print so far…\n' +
                    '"OK": …to the previously selected spool\n' +
                    '"Cancel": …to the new spool'
                )
            }
            // api-call
            var databaseId = -1
            if (spoolItem != null){
                databaseId = spoolItem.databaseId();
                // Why do we need this information
                // if (toolIndex != -1){
                //     var alreadyInTool = self.getSpoolItemSelectedTool(databaseId);
                //     if (alreadyInTool !== null) {
                //         alert('This spool is already selected for tool ' + alreadyInTool + '!');
                //         return;
                //     }
                // }
            }
            self.apiClient.callSelectSpool(toolIndex, databaseId, commitCurrentSpoolValues, function(responseData){
                var spoolItem = null;
                var spoolData = responseData["selectedSpool"];
                if (spoolData != null){
                    spoolItem = self.spoolDialog.createSpoolItemForTable(spoolData);
                } else {
                    // remove spool from toolIndex
                    self.selectedSpoolsForSidebar()[toolIndex](null);
                    return;
                }

                // remove the spool from the current toolIndex
                var currentDatabaseId = spoolItem.databaseId();
                for (var i = 0; i < self.selectedSpoolsForSidebar().length; i++) {
                    var tmpSpoolItem = self.selectedSpoolsForSidebar()[i]();
                    if (tmpSpoolItem !== null && tmpSpoolItem.databaseId() === currentDatabaseId) {
                        self.selectedSpoolsForSidebar()[i](null);
                        break;
                    }
                }
                // assign to new (or same) toolIndex
                if (toolIndex != -1) {
                    self.selectedSpoolsForSidebar()[toolIndex](spoolItem)
                }

            });
        }

        self.editSpoolFromSidebar = function(toolIndex, spoolItem){
            if (spoolItem == null){
                alert("Something is wrong. No Spool is selected to edit from sidebar!")
            }
            self.showSpoolDialogAction(spoolItem);
        }

        self.sidebarSelectSpoolFromDialog = function (spoolItem) {
            self.selectionSpoolDialog.modal("hide");
            self.selectSpoolForSidebar(self.sidebarSelectSpoolModalToolIndex(), spoolItem);
        }

        self.sidebarOpenSelectSpoolDialog = function(toolIndex, spoolItem){

            /* needed for Filter-Search dropdown-menu */
            $('.dropdown-menu.keep-open').click(function(e) {
                e.stopPropagation();
            });

            // lazy selector: fetch the spool list on first dialog open
            if (self._isLazySelectorEnabled() && self.hasInitializedSpoolsSelector == false){
                self.loadSpoolSelectorData();
            }

            self.sidebarSelectSpoolModalSpoolItem(spoolItem);
            self.sidebarSelectSpoolModalToolIndex(toolIndex);

            // self.sidebarFilterSorter.initFilterSorter();

            self.selectionSpoolDialog.modal({
                minHeight: 300,
                show: true
            });
            $("#filterSelectionQueryTextfield").focus();
        }

        //////////////////////////////////////////////////////////////////////////////////////////////////// TABLE / TAB

        self.addNewSpool = function(){
            if (self.schemeUpgradeNeeded() == true){
                // saving would fail anyway, guide the user to the upgrade instead
                alert("The database scheme is outdated. Open Plugin Settings -> SpoolManager -> Storage and press 'Upgrade database scheme' first!");
                return;
            }
            self.spoolDialog.showDialog(null, closeDialogHandler);
        }

        // Downloads an inventory report (pdf/csv/xlsx) honoring the current tab filter/sort state.
        self.generateInventoryReport = function(reportFormat){
            var tableQuery = self.spoolItemTableHelper.buildTableQuery();
            var instance = self.databaseInUse().toLowerCase();
            var reportUrl = self.apiClient.getInventoryReportUrl(tableQuery, instance, reportFormat || "pdf");
            window.open(reportUrl, "_newTab");
        }

        var TableAttributeVisibility = function (){
            this.databaseId = ko.observable(false);
            this.displayName = ko.observable(true);
            this.material = ko.observable(true);
            this.lastFirstUse = ko.observable(true);
            this.weight = ko.observable(true);
            this.used = ko.observable(true);
            this.note = ko.observable(true);
        }
        self.tableAttributeVisibility = new TableAttributeVisibility();

        self.initTableVisibilities = function(){
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }

            assignVisibility = function(attributeName){
                var storageKey = "spoolmanager.table.visible." + attributeName;
                if (localStorage[storageKey] == null){
                    // localStorage[storageKey] = true; // default value
                    localStorage[storageKey] = self.tableAttributeVisibility[attributeName](); // default value
                } else {
                    self.tableAttributeVisibility[attributeName]( "true" == localStorage[storageKey]);
                }
                self.tableAttributeVisibility[attributeName].subscribe(function(newValue){
                    localStorage[storageKey] = newValue;
                });
            }

            assignVisibility("databaseId");
            assignVisibility("displayName");
            assignVisibility("material");
            assignVisibility("lastFirstUse");
            assignVisibility("weight");
            assignVisibility("used");
            assignVisibility("note");
        }

        ///////////////////////////////////////////////////////////////////////////////////////////////// TABLE BEHAVIOR
        /* needed for Filter-Search dropdown-menu */
        $('.dropdown-menu.keep-open').click(function(e) {
            e.stopPropagation();
        });

        self.spoolItemTableHelper = new TableItemHelper(function(tableQuery, observableTableModel, observableTotalItemCount, observableDatabaseItemCount){

            // api-call
            self.apiClient.callLoadSpoolsByQuery(tableQuery, function(responseData){

                if (responseData["databaseConnectionProblem"] != null && responseData["databaseConnectionProblem"] == true){
                    self.pluginNotWorking(true);
                } else {
                    self.pluginNotWorking(false);
                }
                var wasSchemeUpgradeNeeded = self.schemeUpgradeNeeded();
                self.schemeUpgradeNeeded(responseData["schemeUpgradeNeeded"] == true);
                if (wasSchemeUpgradeNeeded == true && self.schemeUpgradeNeeded() == false){
                    // the database is usable again (scheme upgraded, possibly by another instance) -
                    // the sidebar spools/selection were loaded while it was broken, so refresh them too
                    self.loadSidebarSpoolWidgetsData();
                }

                totalItemCount = responseData["totalItemCount"];
                allSpoolItems = responseData["allSpools"];
                var allCatalogs = responseData["catalogs"];

                // assign catalogs to sidebarFilterSorter
                // self.sidebarFilterSorter.updateCatalogs(allCatalogs);
                // assign catalogs to tablehelper
                self.spoolItemTableHelper.updateCatalogs(allCatalogs);
                // assign all catalogs to editview
                self.spoolDialog.updateCatalogs(allCatalogs);

                templateSpoolsData = responseData["templateSpools"];
                self.spoolDialog.updateTemplateSpools(templateSpoolsData);

                var dataRows = ko.utils.arrayMap(allSpoolItems, function (spoolData) {
                    var result = self.spoolDialog.createSpoolItemForTable(spoolData);
                    return result;
                });

                observableTotalItemCount(totalItemCount);
                observableDatabaseItemCount(responseData["databaseItemCount"]);
                observableTableModel(dataRows);
            });
            },
            10,
            "displayName",
            "all",
            "spoolmanager.spooltable."
        );

        self.showSpoolDialogAction = function(selectedSpoolItem) {

            // identify for which toolindex is the current selectedSpoolItem is selected
            var currentDatabaseId = selectedSpoolItem.databaseId();
            var isLoadedInTool = false;
            if (currentDatabaseId) {
                for (var i = 0; i < self.selectedSpoolsForSidebar().length; i++) {
                    spoolItem = self.selectedSpoolsForSidebar()[i]();
                    if (spoolItem !== null && spoolItem.databaseId() === currentDatabaseId) {
                        selectedSpoolItem.selectedForTool(i);
                        isLoadedInTool = true;
                        break;
                    }
                }
            }
            self.spoolDialog.showDialog(selectedSpoolItem, closeDialogHandler, isLoadedInTool);
        };

        // `toolIndexOverride` added for the dropdown "Select for printing" button, adapted from
        // mdziekon/OctoPrint-SpoolManager PR #29 (GH-24). Optional/backwards-compatible: existing
        // positional callers (save/delete paths) omit it and keep the selectedForTool() behaviour.
        closeDialogHandler = function(shouldTableReload, specialAction, currentSpoolItem, toolIndexOverride){

            if (specialAction === "selectSpoolForPrinting"){
                // the dropdown button passes the chosen tool explicitly; the save-path falls back to the spool's tool
                var toolIndex = toolIndexOverride;
                if (toolIndex === undefined || toolIndex === null || toolIndex === ""){
                    toolIndex = currentSpoolItem.selectedForTool();
                }
                if (toolIndex === undefined || toolIndex === null || toolIndex === ""){
                    // clear current selection
                    toolIndex = -1;
                }
                self.selectSpoolForSidebar(toolIndex, currentSpoolItem);
            }

            if (shouldTableReload == true){
                self.spoolItemTableHelper.reloadItems();
                // TODO auto reload of sidebar spools without loosing selection
                self.loadSidebarSpoolWidgetsData();
            }
        }

        ///////////////////////////////////////////////////////////////////////////////////////// OCTOPRINT PRINT-BUTTON
        const origStartPrintFunction = self.printerStateViewModel.print;
        const newStartPrintFunction = function confirmSpoolSelectionBeforeStartPrint() {
                // api-call
                self.apiClient.allowedToPrint(function(responseData){
                    var result = responseData.result,
                        check, itemList;

                    var warning = "";
                    var warning2 = "";
                    if (responseData.metaDataMissing) {
                        warning = "ATTENTION: Needed filament could not be calculated (missing metadata - wait for the uploaded file to be processed)\n\n";
                        warning2 = " (maybe)"
                    }
                    if (responseData.attributesMissing) {
                        warning = "ATTENTION: Needed filament could not be calculated (missing spool-fields - edit your spool)\n\n";
                    }

                    if (result.noSpoolSelected.length) {
                        itemList = [];
                        for (item of result.noSpoolSelected) {
                            itemList.push('Tool '+item.toolIndex)
                        }
                        if (itemList.length === 1) {
                            check = confirm(
                                warning +
                                'There is no spool selected for ' + itemList[0] + ' despite it being used' + warning2 + ' by this print.\n\n' +
                                'Do you want to start the print without a selected spool?'
                            );
                        } else {
                            check = confirm(
                                warning +
                                'There are no spools selected for the following tools despite them being used' + warning2 + ' by this print:\n' +
                                '- '+ itemList.join('\n- ') + '\n\n' +
                                'Do you want to start the print without selected spools?'
                            );
                        }
                        if (!check) {
                            return;
                        }
                    }

                    buildSpoolLabel = function(item){
                        var label =  item.toolIndex+": '" + item.material + " - " + item.spoolName;

                        if (item.remainingWeight != null && typeof item.remainingWeight === 'number'){
                            label = label + " ("+ self.formatWeightForDisplay(item.remainingWeight) +")";
                        }
                        label = label + "'";
                        return label;
                    }

                    if (result.filamentNotEnough.length) {
                        itemList = [];
                        for (item of result.filamentNotEnough) {
                            var spoolLabel = buildSpoolLabel(item);
                            // itemList.push("'" + item.spoolName + "' (tool "+item.toolIndex+")");
                            itemList.push(spoolLabel);
                        }
                        if (itemList.length === 1) {
                            check = confirm(
                                warning +
                                'The selected spool for tool ' + itemList[0] + ' does not have enough remaining filament'+warning2+'.\n\n' +
                                'Do you want to start the print anyway?'
                            );
                        } else {
                            check = confirm(
                                warning +
                                'The following selected spools do not have enough remaining filament'+warning2+':\n' +
                                '- '+ itemList.join('\n- ') + '\n\n' +
                                'Do you want to start the print anyway?'
                            );
                        }
                        if (!check) {
                            return;
                        }
                    }

                    if (result.reminderSpoolSelection.length) {
                        itemList = [];
                        // for (item of result.reminderSpoolSelection) {
                        //     itemList.push(((result.reminderSpoolSelection.length>1)?("Tool "+item.toolIndex+": "):'')+"'" + item.spoolName + "'");
                        // }
                        // if (itemList.length === 1) {
                        //     check = confirm(
                        //         'Do you want to start the print with the selected spool?\n- ' + itemList[0] + '?'
                        //     );
                        // } else {
                        //     check = confirm(
                        //         "Do you want to start the print with following selected spools?\n" +
                        //         '- '+ itemList.join('\n- ')
                        //     );
                        // }
                        // build message for each tool
                        for (item of result.reminderSpoolSelection) {
                            var toolMessage = buildSpoolLabel(item);
                            if (responseData.toolOffsetEnabled && item.toolOffset != null) toolMessage += "\n--  Tool Offset:  "+item.toolOffset+'\u00B0';
                            if (responseData.bedOffsetEnabled && item.bedOffset != null) toolMessage += "\n--  Bed Offset:  "+item.bedOffset+'\u00B0';
                            if (responseData.enclosureOffsetEnabled && item.enclosureOffset != null) toolMessage += "\n--  Enclosure Offset:  "+item.enclosureOffset+'\u00B0';
                            itemList.push(toolMessage);
                        }
                        check = confirm(
                            "Do you want to start the print with following selected spools?\n" +
                            "- "+ itemList.join("\n- ")
                        );

                        if (!check) {
                            return;
                        }
                    }
                    // we are ready to go. Inform the backend and after that START PRINT
                    self.apiClient.startPrintConfirmed(function(responseData){
                        origStartPrintFunction();
                    });
                });
        };
        // overwrite loadFile
        self.filesViewModel.loadFile = function confirmSpoolSelectionOnLoadAndPrint(data, printAfterLoad) {
            // orig. SourceCode
            if (!self.filesViewModel.loginState.hasPermission(self.filesViewModel.access.permissions.FILES_SELECT)) return;

            if (!data) {
                return;
            }

            if (printAfterLoad && self.filesViewModel.listHelper.isSelected(data) && self.filesViewModel.enablePrint(data)) {
                // file was already selected, just start the print job with the newStartPrint function
                // SPOOLMANAGER-CHANGE changed OctoPrint.job.start();
                newStartPrintFunction();
            } else {
                // select file, start print job (if requested and within dimensions)
                var withinPrintDimensions = self.filesViewModel.evaluatePrintDimensions(data, true);
                var print = printAfterLoad && withinPrintDimensions;

                if (print && self.filesViewModel.settingsViewModel.feature_printStartConfirmation()) {
                    showConfirmationDialog({
                        message: gettext("This will start a new print job. Please check that the print bed is clear."),
                        question: gettext("Do you want to start the print job now?"),
                        cancel: gettext("No"),
                        proceed: gettext("Yes"),
                        onproceed: function() {
                            OctoPrint.files.select(data.origin, data.path, false).done(function () {
                                                                                    if (print){
                                                                                     newStartPrintFunction();
                                                                                    }
                                                                                });
                        },
                        nofade: true
                    });
                } else {
                    OctoPrint.files.select(data.origin, data.path, false).done(function () {
                                                                                    if (print){
                                                                                     newStartPrintFunction();
                                                                                    }
                                                                                });
                }
            }
        };

        self.printerStateViewModel.print = newStartPrintFunction;

        //////////////////////////////////////////////////////////////////////////////////////// PUBLIC VIEWMODEL - APIs
        // e.g. for CostEstaminator-Plugin
        self.api_getSelectedSpoolInformations = function(){
            var result = [];
            var spoolItem;
            for (var i=0; i<self.selectedSpoolsForSidebar().length; i++) {
                var spoolData = null;
                spoolItem = self.selectedSpoolsForSidebar()[i]();
                if (spoolItem !== null) {
                    spoolData = {
                        "toolIndex": i,
                        "databaseId": spoolItem.databaseId(),
                        "spoolName": spoolItem.displayName(),
                        "vendor": spoolItem.vendor(),
                        "material": spoolItem.material(),
                        "diameter": spoolItem.diameter(),
                        "density": spoolItem.density(),
                        "colorName": spoolItem.colorName(),
                        "color": spoolItem.color(),
                        "cost": spoolItem.cost(),
                        "weight": spoolItem.totalWeight()
                    }
                }
                result.push(spoolData);
            }
            return result;
        }

        //////////////////////////////////////////////////////////////////////////////////////////////// OCTOPRINT HOOKS
        self.onStartup = function onStartupCallback() {
            // Replace Filementview in sidebar to show weight instead of volumne
            self.replaceFilamentView();
        };

        self.onBeforeBinding = function() {
            // Register Knockout Components
            new SpoolSelectionTableComp().registerSpoolSelectionTableComp();

            // assign current pluginSettings
            self.pluginSettings = self.settingsViewModel.settings.plugins[PLUGIN_ID];

            // Lazy table loading (issue mdziekon#5): defer the first table fetch until the
            // SpoolManager tab is shown. Must be set before loadSettingsFromBrowserStore(),
            // because restoring the page size already triggers a table load.
            if (self.pluginSettings.performanceLazyLoadSpoolTable
                && self.pluginSettings.performanceLazyLoadSpoolTable() == true){
                self.spoolItemTableHelper.isLoadingEnabled = false;
            }

            // load browser stored settings (includs TabelVisibility and pageSize, ...)
            loadSettingsFromBrowserStore();

            // resetSettings-Stuff
             new ResetSettingsUtilV3(self.pluginSettings).assignResetSettingsFeature(PLUGIN_ID, function(data){
                 // fix colors after settings were reset. This is a hack because the color picker does not update itself
                 $("#qrcode-fill-color-picker + .btn-group button span.color-preview").css("background-color", self.pluginSettings.qrCodeFillColor());
                 $("#qrcode-background-color-picker + .btn-group button span.color-preview").css("background-color", self.pluginSettings.qrCodeBackgroundColor());
             });

            // Load sidebar data (selected spools always; full selector list only when not lazy)
            self.loadSidebarSpoolWidgetsData();
            // Edit Spool Dialog Binding
            self.spoolDialog.initBinding(self.apiClient, self.pluginSettings, self.printerProfilesViewModel, self.printerStateViewModel);
            // Import Dialog
            self.csvImportDialog.init(self.apiClient);
            // Database connection problem dialog
            self.databaseConnectionProblemDialog.init(self.apiClient);
            // Select Spool Dialog (no special binding)
            self.selectionSpoolDialog = $("#dialog_spool_selection");



            // Settings - Color-Picker
            self.componentFactory = new ComponentFactory();
            var fillColorViewModel = self.componentFactory.createColorPicker("qrcode-fill-color-picker");
            this.qrCodeFillColor = fillColorViewModel.selectedColor;
            // Init with current value
            this.qrCodeFillColor(self.pluginSettings.qrCodeFillColor());  // needed
            this.qrCodeFillColor.subscribe(function(newColorValue){
                self.pluginSettings.qrCodeFillColor(newColorValue);
            });

            var backgroundColorViewModel = self.componentFactory.createColorPicker("qrcode-background-color-picker");
            this.qrCodeBackgroundColor = backgroundColorViewModel.selectedColor;
            // Init with current value
            this.qrCodeBackgroundColor(self.pluginSettings.qrCodeBackgroundColor());  // needed
            this.qrCodeBackgroundColor.subscribe(function(newColorValue){
                self.pluginSettings.qrCodeBackgroundColor(newColorValue);
            });

            // self.pluginSettings.hideEmptySpoolsInSidebar.subscribe(function(newCheckedVaue){
            //     var payload = {
            //             "hideEmptySpoolsInSidebar": newCheckedVaue
            //         };
            //     OctoPrint.settings.savePluginSettings(PLUGIN_ID, payload);
            //     // self.loadSpoolsForSidebar();
            //     // self.filterSelectionSidebar();
            // });
            // self.pluginSettings.hideInactiveSpoolsInSidebar.subscribe(function(newCheckedVaue){
            //     var payload = {
            //             "hideInactiveSpoolsInSidebar": newCheckedVaue
            //         };
            //     OctoPrint.settings.savePluginSettings(PLUGIN_ID, payload);
            //     // self.loadSpoolsForSidebar();
            //     // self.filterSelectionSidebar();
            // });

            // needed after the tool-count is changed
            self.settingsViewModel.printerProfiles.currentProfileData.subscribe(function(){
                self.updateAvailableSpoolSlots();
            });
        }

        self.onAfterBinding = function() {
            self.spoolDialog.afterBinding();
            self.downloadDatabaseUrl(self.apiClient.getDownloadDatabaseUrl());

// testing            self.spoolDialog.showDialog(null, closeDialogHandler);
        }

        self.onSettingsShown = function(){
            if (self.isFilamentManagerPluginAvailable() == false || self.isMqttPluginAvailable() == false){
                self.apiClient.callAdditionalSettings(function(responseData) {
                    self.isFilamentManagerPluginAvailable(responseData.isFilamentManagerPluginAvailable);
                    self.isMqttPluginAvailable(responseData.isMqttPluginAvailable);
                });
            }
        }

        // receive data from server
        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin != PLUGIN_ID) {
                return;
            }

            // NOTE: the backend sends "initialData"; the old "initalData" comparison (typo)
            // made this handler dead code. With lazy table loading this push is the startup
            // source for pluginNotWorking, so the typo had to be fixed.
            if ("initialData" == data.action){

                self.pluginNotWorking(data.pluginNotWorking);
                self.isFilamentManagerPluginAvailable(data.isFilamentManagerPluginAvailable);
                if (data.selectedSpools != null){
                    self._applySelectedSpoolsData(data.selectedSpools);
                }

                return;
            }
            if ("showPopUp" == data.action){
                self.showPopUp(data.type, data.title, data.message, data.autoclose);
                return;
            }
            if ("printerFileAnalysisStarted" == data.action){
                self.showPrinterFileAnalysisStarted(data.path);
                return;
            }
            if ("printerFileAnalysisFinished" == data.action){
                self.hidePrinterFileAnalysisToast();
                return;
            }
            if ("reloadTable" == data.action){
                self.spoolItemTableHelper.reloadItems();
                return;
            }
            if ("reloadTable and sidebarSpools" == data.action){
                self.spoolItemTableHelper.reloadItems();
                self.loadSidebarSpoolWidgetsData();
                return;
            }
            if ("csvImportStatus" == data.action){
                self.csvImportDialog.updateText(data);
                return;
            }
            if ("errorPopUp" == data.action){
                self.showPopUp("error", 'ERROR:' + data.title, data.message, data.autoclose);
                return;
            }
            if ("requiredFilamentChanged" == data.action){
                self.updateRequiredFilament(data);
                return;
            }
            if ("extrusionValuesChanged" == data.action){
                self.updateExtrusionValues(data.extrusionValues);
                return;
            }


            if ("showConnectionProblem" == data.action){
// TODO enable problem dialog again
//                new PNotify({
//                    title: 'ERROR:' + data.title,
//                    text: data.message,
//                    type: "error",
//                    hide: false
//                    });

//                self.databaseConnectionProblemDialog.showDialog(data, function(){
//                    // nothing special here, everything is done in the dialog
//                });

                return;
            }

        }

        self.onTabChange = function(next, current){
            if ("#tab_plugin_SpoolManager" == next) {
                // with lazy table loading this is the first (deferred) load,
                // afterwards it behaves like the previous reloadItems() call
                self.spoolItemTableHelper.enableLoadingAndReload();
            }
            //alert("Next:"+next +" Current:"+current);
            if ("#tab_plugin_PrintJobHistory" == next){
                //self.reloadTableData();
            }
        }

        self.onAfterTabChange = function(current, previous){
            // alert("Next:"+next +" Current:"+previous);
            //if ("#tab_plugin_SpoolManager" == current){
            // var selectedSpoolId = getUrlParameter("selectedSpoolId");
            // if (selectedSpoolId) {
            //     console.error("Id"+selectedSpoolId);
            // }
            var tabHashCode = window.location.hash;
            // QR-Code-Call: We can only contain -spoolId on the very first page
            if (tabHashCode.includes("#tab_plugin_SpoolManager-spoolId")){
                var selectedSpoolId = tabHashCode.replace("-spoolId", "").replace("#tab_plugin_SpoolManager", "");
                selectedSpoolId = parseInt(selectedSpoolId);
                console.info('Loading spool: '+selectedSpoolId);
                var alreadyInTool = self.getSpoolItemSelectedTool(selectedSpoolId);
                if (alreadyInTool !== null) {
                    alert('This spool is already selected for tool ' + alreadyInTool + '!');
                    return;
                }
                if (self.printerStateViewModel.isPrinting()) {
                    // not doing this while printing
                    return;
                }
                // - Load SpoolItem from Backend
                // - Open SpoolItem
                // methode signature: toolIndex, databaseId, commitCurrentSpoolValues, responseHandler
                var commitCurrentSpoolValues = false;
                var toolIndex = 0
                self.apiClient.callSelectSpool(0, selectedSpoolId, commitCurrentSpoolValues, function(responseData){
                    //Select the SpoolManager tab
                    $('a[href="#tab_plugin_SpoolManager"]').tab('show')
                    var spoolItem = null;
                    var spoolData = responseData["selectedSpool"];
                    if (spoolData != null){
                        spoolItem = self.spoolDialog.createSpoolItemForTable(spoolData);
                        spoolItem.selectedFromQRCode(true);
                        self.selectedSpoolsForSidebar()[0](spoolItem);
                        self.showSpoolDialogAction(spoolItem);
                    }
                });
            }
            //}
        }

        self.calculateRemainingPercentage = function(spoolItem) {
            if (!spoolItem.remainingLength() || !spoolItem.totalLength()) {
                return {
                    width: 0,
                    isLow: true
                };
            }
            var percentage = (Number(spoolItem.remainingLength()) / Number(spoolItem.totalLength())) * 100;
            percentage = Math.min(Math.max(percentage, 0), 100);
            return {
                width: percentage,
                isLow: percentage < 20
            };
        };

    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: SpoolManagerViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: [
            "loginStateViewModel",
            "settingsViewModel",
            "printerStateViewModel",
            "filesViewModel",
            "printerProfilesViewModel"
        ],
        // Elements to bind to, e.g. #settings_plugin_SpoolManager, #tab_plugin_SpoolManager, ...
        elements: [
            document.getElementById("settings_spoolmanager"),
            document.getElementById("tab_spoolOverview"),
            document.getElementById("modal-dialogs-spoolManager"),
            document.getElementById("sidebar_spool_select")
        ]
    });
});
