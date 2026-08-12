/*
 * View model for OctoPrint-SpoolManager
 *
 * Author: OllisGit
 * License: AGPLv3
 */

// Builds a CSS background value for a spool color field.
// Supported values: single hex ("#ff0000"), multi-color ("#ff0000;#0000ff",
// up to three colors, rendered as a diagonally split box), the keyword
// "rainbow" (rendered as a rainbow gradient, see upstream issue #19) and
// "transparent"/"transparent:#hex" (rendered as a checkerboard, optionally
// tinted with the base color).
// Attached to window because OctoPrint wraps packed plugin JS in a closure,
// but the knockout bindings in the templates need global access.
window.spmSpoolColorCss = function (color) {
    var colorValue = ko.utils.unwrapObservable(color);
    if (!colorValue) {
        return "";
    }
    // value format is parsed in one place only, see SPOOLMANAGER_UTILS.parseSpoolColor
    var colorParts = SPOOLMANAGER_UTILS.parseSpoolColor(colorValue);
    if (colorParts.isRainbow) {
        return "linear-gradient(135deg, #ff2d2d 0%, #ff9a00 20%, #ffe600 40%, #16c172 60%, #2f7bff 80%, #a044ff 100%)";
    }
    var checkerboard =
        "repeating-conic-gradient(#c8c8c8 0% 25%, #ffffff 0% 50%) 50% / 8px 8px";
    if (colorParts.isTransparent && colorParts.isUntinted) {
        return checkerboard;
    }
    var transparentTint = colorParts.isTransparent ? colorParts.colors[0] : null;
    var colors = colorParts.colors;
    var singleOrGradient;
    if (colors.length === 1) {
        singleOrGradient = colors[0];
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

$(function () {
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
        self.addSpoolWizard = new SpoolManagerAddSpoolWizard();

        //////////////////////////////////////////////////////////////////////////////////////////////// HELPER FUNCTION

        var loadSettingsFromBrowserStore = function () {
            // TODO maybe in a separate js-file
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }
            // Table visibility
            self.initTableVisibilities();

            var storageKey = "spoolmanager.table.selectedPageSize";
            if (localStorage[storageKey] == null) {
                localStorage[storageKey] = "25"; // default page size
            } else {
                // localStorage only stores strings; the page-size options are numbers
                // (except "all"), so convert back or the select shows the default (PR #8 fix)
                var storedPageSize = localStorage[storageKey];
                self.spoolItemTableHelper.selectedPageSize(
                    storedPageSize == "all" ? "all" : Number(storedPageSize)
                );
            }
            self.spoolItemTableHelper.selectedPageSize.subscribe(function (newValue) {
                localStorage[storageKey] = newValue;
            });
        };

        // Toast while the plugin downloads/analyzes a printer-storage file
        self.printerFileAnalysisNotify = null;
        self.showPrinterFileAnalysisStarted = function (path) {
            if (self.printerFileAnalysisNotify == null) {
                self.printerFileAnalysisNotify = new PNotify({
                    title: "SPM: Analyzing print file",
                    text:
                        "Fetching '" +
                        path +
                        "' from printer storage to calculate the needed filament...",
                    type: "info",
                    icon: "fa fa-spinner fa-spin",
                    hide: false
                });
            }
        };
        self.hidePrinterFileAnalysisToast = function () {
            if (self.printerFileAnalysisNotify != null) {
                self.printerFileAnalysisNotify.remove();
                self.printerFileAnalysisNotify = null;
            }
        };

        // Typs: error
        // Thin delegate: the actual toast (including the dedupe logic) lives in common/dialogs.js
        // so that other files can raise the same kind of notification without a viewmodel reference.
        self.showPopUp = function (popupType, popupTitle, message, autoclose) {
            SPOOLMANAGER_DIALOGS.notify({
                title: popupTitle,
                message: message,
                type: popupType,
                autoclose: autoclose
            });
        };

        // found here: https://stackoverflow.com/questions/19491336/how-to-get-url-parameter-using-jquery-or-plain-javascript?rq=1
        var getUrlParameter = function getUrlParameter(sParam) {
            var sPageURL = window.location.search.substring(1),
                sURLVariables = sPageURL.split("&"),
                sParameterName,
                i;

            for (i = 0; i < sURLVariables.length; i++) {
                sParameterName = sURLVariables[i].split("=");

                if (sParameterName[0] === sParam) {
                    return sParameterName[1] === undefined
                        ? true
                        : decodeURIComponent(sParameterName[1]);
                }
            }
        };

        self.reloadQRCodePreviewImage = function () {
            var imageDom = $("#settings-qrimage-preview");
            var currentSrc = imageDom.attr("src");
            currentSrc = currentSrc + "&" + new Date().getTime();
            imageDom.attr("src", currentSrc);
        };

        // Generate HTML-Image Attributes for the QR-Code
        self.generateQRCodeImageSourceAttribute = function (
            databaseId,
            spoolDisplayName,
            showHtmlView,
            withColors
        ) {
            var requestParameters = "";
            if (withColors) {
                requestParameters =
                    "?" +
                    "fillColor=" +
                    encodeURIComponent(self.pluginSettings.qrCodeFillColor()) +
                    "&" +
                    "backgroundColor=" +
                    encodeURIComponent(self.pluginSettings.qrCodeBackgroundColor());

                if (self.pluginSettings.qrCodeUseURLPrefix() == true) {
                    requestParameters =
                        requestParameters +
                        "&" +
                        "useURLPrefix=true" +
                        "&" +
                        "urlPrefix=" +
                        encodeURIComponent(self.pluginSettings.qrCodeURLPrefix());
                }
            }

            var source = "";
            if (showHtmlView == "htmlView") {
                source =
                    PLUGIN_BASEURL +
                    "SpoolManager/generateQRCodeView/" +
                    databaseId +
                    "" +
                    requestParameters;
            } else {
                source =
                    PLUGIN_BASEURL +
                    "SpoolManager/generateQRCode/" +
                    databaseId +
                    "" +
                    requestParameters;
            }
            var title = "QR-Code for " + spoolDisplayName;
            return {
                src: source,
                href: source,
                title: title
            };
        };

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
            schemeVersionFromPlugin: ko.observable()
        };
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

        self.resetDatabaseMessages = function () {
            self.showInternalSuccessMessage(false);
            self.showInternalDatabaseErrorMessage(false);
            self.showExternalSuccessMessage(false);
            self.showExternalDatabaseErrorMessage(false);
            self.showUpdateSchemeMessage(false);
            self.externalDatabaseErrorMessage("");
            self.internalDatabaseErrorMessage("");
            self.schemeUpgradeResultText("");
        };

        self.handleDatabaseMetaDataResponse = function (metaDataResponse) {
            var metadata = metaDataResponse["metadata"];
            console.log(metadata);

            if (metadata != null) {
                var errorMessage = metadata["errorMessage"];
                if (errorMessage != null && errorMessage.length != 0) {
                    if (self.pluginSettings.useExternal()) {
                        self.showExternalDatabaseErrorMessage(true);
                        self.externalDatabaseErrorMessage(errorMessage);
                    } else {
                        self.showInternalDatabaseErrorMessage(true);
                        self.internalDatabaseErrorMessage(errorMessage);
                    }
                }
                var success = metadata["success"];
                if (success != null && success == true) {
                    self.showExternalSuccessMessage(
                        true && self.pluginSettings.useExternal()
                    );
                    self.showInternalSuccessMessage(
                        true && !self.pluginSettings.useExternal()
                    );
                } else {
                    self.showExternalSuccessMessage(
                        false && self.pluginSettings.useExternal()
                    );
                    self.showInternalSuccessMessage(
                        false && !self.pluginSettings.useExternal()
                    );
                }

                self.databaseMetaData.localSchemeVersionFromDatabaseModel(
                    metadata["localSchemeVersionFromDatabaseModel"]
                );
                self.databaseMetaData.localSchemeVersionFromDatabaseModel(
                    metadata["localSchemeVersionFromDatabaseModel"]
                );
                self.databaseMetaData.localSpoolItemCount(
                    metadata["localSpoolItemCount"]
                );
                self.databaseMetaData.externalSchemeVersionFromDatabaseModel(
                    metadata["externalSchemeVersionFromDatabaseModel"]
                );
                self.databaseMetaData.externalSpoolItemCount(
                    metadata["externalSpoolItemCount"]
                );
                self.databaseMetaData.schemeVersionFromPlugin(
                    metadata["schemeVersionFromPlugin"]
                );

                if (
                    self.databaseMetaData.schemeVersionFromPlugin() !=
                    self.databaseMetaData.externalSchemeVersionFromDatabaseModel()
                ) {
                    self.showUpdateSchemeMessage(true);
                }
            }
        };

        self.buildDatabaseSettings = function () {
            var databaseSettings = {
                useExternal: self.pluginSettings.useExternal(),
                databaseType: self.pluginSettings.databaseType(),
                databaseHost: self.pluginSettings.databaseHost(),
                databasePort: self.pluginSettings.databasePort(),
                databaseName: self.pluginSettings.databaseName(),
                databaseUser: self.pluginSettings.databaseUser(),
                databasePassword: self.pluginSettings.databasePassword()
            };
            return databaseSettings;
        };

        self.testDatabaseConnection = function () {
            self.resetDatabaseMessages();
            self.showLocalBusyIndicator(self.pluginSettings.useExternal() == false);
            self.showExternalBusyIndicator(
                self.pluginSettings.databasePassword() == true
            );

            var databaseSettings = self.buildDatabaseSettings();

            self.apiClient.testDatabaseConnection(
                databaseSettings,
                function (responseData) {
                    self.handleDatabaseMetaDataResponse(responseData);
                    self.showLocalBusyIndicator(false);
                    self.showExternalBusyIndicator(false);
                }
            );
        };

        // - OctoScale connection test (settings dialog)
        self.octoScaleTestBusy = ko.observable(false);
        self.octoScaleTestSuccess = ko.observable(false);
        self.octoScaleTestFailed = ko.observable(false);
        self.octoScaleTestResultMessage = ko.observable("");

        self.testOctoScaleConnection = function () {
            self.octoScaleTestSuccess(false);
            self.octoScaleTestFailed(false);
            self.octoScaleTestResultMessage("");
            self.octoScaleTestBusy(true);

            // the URL comes from the input field, so an address can be tested before it is saved
            self.apiClient.testOctoScaleConnection(
                self.pluginSettings.octoScaleUrl(),
                function (responseData) {
                    self.octoScaleTestBusy(false);
                    if (responseData && responseData.success === true) {
                        self.octoScaleTestSuccess(true);
                        self.octoScaleTestResultMessage(
                            responseData.version ? "(" + responseData.version + ")" : ""
                        );
                    } else {
                        self.octoScaleTestFailed(true);
                        self.octoScaleTestResultMessage(
                            responseData && responseData.error
                                ? responseData.error
                                : "Connection failed."
                        );
                    }
                }
            );
        };

        self.spoolmanDbRefreshBusy = ko.observable(false);
        self.spoolmanDbRefreshResult = ko.observable(null);
        self.refreshSpoolmanDbStatus = function () {
            if (
                !self.pluginSettings ||
                !self.pluginSettings.spoolmanDbEnabled ||
                self.pluginSettings.spoolmanDbEnabled() !== true
            ) {
                self.spoolmanDbRefreshResult(null);
                return;
            }
            self.apiClient.getSpoolmanDbVendors(function (response) {
                self.spoolmanDbRefreshResult(
                    response && response.cache ? response.cache : response
                );
            });
        };
        self.refreshSpoolmanDb = function () {
            self.spoolmanDbRefreshBusy(true);
            self.spoolmanDbRefreshResult(null);
            self.apiClient.refreshSpoolmanDb(function (response) {
                self.spoolmanDbRefreshBusy(false);
                self.spoolmanDbRefreshResult(
                    response && response.cache ? response.cache : response
                );
            });
        };

        self.deleteDatabaseAction = function (databaseType) {
            SPOOLMANAGER_DIALOGS.confirmDanger({
                title: "Delete all spool data",
                message:
                    "All SpoolManager data in the " +
                    SPOOLMANAGER_DIALOGS.escapeHtml(databaseType) +
                    " database will be deleted. This cannot be undone.",
                question: "Do you really want to delete all data?",
                cancel: "Keep data",
                proceed: "Delete everything"
            }).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                var databaseSettings = self.buildDatabaseSettings();
                databaseSettings.useExternal = databaseType == "external";

                self.apiClient.callDeleteDatabase(
                    databaseType,
                    databaseSettings,
                    function (responseData) {
                        self.handleDatabaseMetaDataResponse(responseData);
                        self.spoolItemTableHelper.reloadItems();
                    }
                );
            });
        };

        self.copySpools = function () {
            SPOOLMANAGER_DIALOGS.confirmDanger({
                title: "Copy data from internal database",
                message:
                    "All SpoolManager data is copied from the internal database. " +
                    "This replaces all existing data in the current database.",
                question: "Do you really want to copy and replace?",
                cancel: "Cancel",
                proceed: "Copy and replace"
            }).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                var databaseSettings = self.buildDatabaseSettings();
                self.apiClient.callCopyDatabase(
                    databaseSettings,
                    function (responseData) {
                        self.spoolItemTableHelper.reloadItems();
                        self.apiClient.loadDatabaseMetaData(function (responseData) {
                            self.handleDatabaseMetaDataResponse(responseData);
                        });
                    }
                );
            });
        };

        // - external database scheme upgrade (issue #30/#49 follow-up: scheme V8, auto-upgrade only runs for local SQLite)
        self.isExternalSchemeUpgradeAvailable = ko.pureComputed(function () {
            if (self.pluginSettings.useExternal() != true) {
                return false;
            }
            var externalVersion = Number(
                self.databaseMetaData.externalSchemeVersionFromDatabaseModel()
            );
            var pluginVersion = Number(self.databaseMetaData.schemeVersionFromPlugin());
            return (
                isNaN(externalVersion) == false &&
                isNaN(pluginVersion) == false &&
                externalVersion < pluginVersion
            );
        });
        // - local SQLite scheme upgrade: the auto-upgrade normally runs at startup, but if the local
        //   database file was restored/replaced at an old scheme version the user needs a way to re-trigger it
        self.isLocalSchemeUpgradeAvailable = ko.pureComputed(function () {
            if (self.pluginSettings.useExternal() == true) {
                return false;
            }
            var localVersion = Number(
                self.databaseMetaData.localSchemeVersionFromDatabaseModel()
            );
            var pluginVersion = Number(self.databaseMetaData.schemeVersionFromPlugin());
            return (
                isNaN(localVersion) == false &&
                isNaN(pluginVersion) == false &&
                localVersion < pluginVersion
            );
        });
        self.isSchemeUpgradeAvailable = ko.pureComputed(function () {
            return (
                self.isExternalSchemeUpgradeAvailable() == true ||
                self.isLocalSchemeUpgradeAvailable() == true
            );
        });
        self.schemeUpgradeInProgress = ko.observable(false);
        self.schemeUpgradeResultText = ko.observable("");

        // Downloads a URL and resolves only when the browser received a non-empty file.
        // Rejects (so callers can abort) on HTTP error or an empty response body.
        // Shared by the scheme-upgrade flow and the pre-import backup flow.
        self.downloadBackupFile = function (url, downloadFileName) {
            return fetch(url)
                .then(function (response) {
                    if (response.ok == false) {
                        return response.text().then(function (text) {
                            throw new Error(
                                text ||
                                    "Backup download failed (HTTP " +
                                        response.status +
                                        ")"
                            );
                        });
                    }
                    return response.blob();
                })
                .then(function (blob) {
                    if (blob == null || blob.size == 0) {
                        throw new Error(
                            "Backup download failed (empty file), operation aborted."
                        );
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
        self.runPreImportBackupThenImport = function (onReady, onError) {
            self.apiClient.callCreateImportBackup(function (success, data) {
                if (success != true) {
                    var msg = "Backup before import failed.";
                    if (
                        data != null &&
                        data.responseText != null &&
                        data.responseText != ""
                    ) {
                        msg = data.responseText;
                    }
                    onError(msg);
                    return;
                }
                var mandatoryFile = data != null ? data["mandatoryBackupFile"] : null;
                var optionalFiles =
                    data != null && data["optionalBackupFiles"]
                        ? data["optionalBackupFiles"]
                        : [];
                if (mandatoryFile == null || mandatoryFile == "") {
                    onError("No backup was created, import aborted.");
                    return;
                }

                // 1) download the mandatory backup - failure aborts the import
                self.downloadBackupFile(
                    self.apiClient.getDatabaseBackupDownloadUrl(mandatoryFile),
                    mandatoryFile
                )
                    .then(function () {
                        // 2) best-effort: download optional backups, ignoring their failures.
                        // Chained sequentially with a small gap so browsers don't drop the second download.
                        var chain = Promise.resolve();
                        optionalFiles.forEach(function (fileName) {
                            chain = chain
                                .then(function () {
                                    return new Promise(function (resolve) {
                                        setTimeout(resolve, 300);
                                    });
                                })
                                .then(function () {
                                    return self.downloadBackupFile(
                                        self.apiClient.getDatabaseBackupDownloadUrl(
                                            fileName
                                        ),
                                        fileName
                                    );
                                })
                                .catch(function (optError) {
                                    if (window.console) {
                                        console.warn(
                                            "Optional backup download failed (import continues):",
                                            fileName,
                                            optError
                                        );
                                    }
                                });
                        });
                        return chain;
                    })
                    .then(function () {
                        onReady();
                    })
                    .catch(function (error) {
                        // only the mandatory download reaches here (optional errors are swallowed above)
                        onError(
                            "" + (error != null && error.message ? error.message : error)
                        );
                    });
            });
        };

        self.upgradeDatabaseSchemeAction = function () {
            var isExternal = self.pluginSettings.useExternal() == true;
            var confirmMessage = isExternal
                ? "A SQL dump backup is downloaded first. " +
                  "If the backup download fails, the upgrade is aborted."
                : "A copy of the database file is downloaded first. " +
                  "If the backup download fails, the upgrade is aborted.";

            SPOOLMANAGER_DIALOGS.confirm({
                title: isExternal
                    ? "Upgrade external database scheme"
                    : "Upgrade local database scheme",
                message: confirmMessage,
                question: "Upgrade the database scheme now?",
                cancel: "Cancel",
                proceed: "Upgrade"
            }).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                self._runDatabaseSchemeUpgrade(isExternal);
            });
        };

        // Actual upgrade routine, split off so the confirmation above can stay asynchronous.
        self._runDatabaseSchemeUpgrade = function (isExternal) {
            self.resetDatabaseMessages();
            self.schemeUpgradeResultText("");
            self.schemeUpgradeInProgress(true);

            var handleUpgradeResponse = function (responseData) {
                self.schemeUpgradeInProgress(false);
                var result = responseData != null ? responseData["result"] : null;
                if (result != null && result["success"] == true) {
                    self.schemeUpgradeResultText(
                        "Database scheme upgraded to version " +
                            result["toVersion"] +
                            ". The backup was downloaded."
                    );
                    if (responseData["metadata"] != null) {
                        self.handleDatabaseMetaDataResponse(responseData);
                    }
                    // a full reload makes sure all cached viewmodels (sidebar, dialogs) pick up the repaired database
                    SPOOLMANAGER_DIALOGS.confirm({
                        title: "Database scheme upgraded",
                        message:
                            "The database scheme was upgraded to version " +
                            SPOOLMANAGER_DIALOGS.escapeHtml(result["toVersion"]) +
                            ".",
                        question: "A page reload is recommended. Reload now?",
                        cancel: "Later",
                        proceed: "Reload now"
                    }).then(function (reloadConfirmed) {
                        if (reloadConfirmed == true) {
                            location.reload();
                        }
                    });
                    return;
                } else {
                    var errorMessage =
                        result != null && result["errorMessage"] != null
                            ? result["errorMessage"]
                            : "Scheme upgrade failed. See octoprint.log for details.";
                    self.showExternalDatabaseErrorMessage(true);
                    self.externalDatabaseErrorMessage(errorMessage);
                }
                if (responseData != null && responseData["metadata"] != null) {
                    self.handleDatabaseMetaDataResponse(responseData);
                }
            };

            // shared helper (self.downloadBackupFile): downloads a URL, rejects on empty/failed download
            var downloadBackupFile = self.downloadBackupFile;

            var handleDownloadError = function (error) {
                self.schemeUpgradeInProgress(false);
                self.showExternalDatabaseErrorMessage(true);
                self.externalDatabaseErrorMessage(
                    "" + (error != null && error.message ? error.message : error)
                );
            };

            // local SQLite: create the .db backup on the server, download it, and only then migrate.
            // If the backup creation or download fails, the migration is aborted (same guarantee as external).
            if (isExternal == false) {
                self.apiClient.callCreateDatabaseBackup(function (backupResponse) {
                    var backupFileName =
                        backupResponse != null ? backupResponse["backupFileName"] : null;
                    if (backupFileName == null) {
                        handleDownloadError(
                            new Error(
                                "Could not create the database backup, upgrade aborted."
                            )
                        );
                        return;
                    }
                    downloadBackupFile(
                        self.apiClient.getDatabaseBackupDownloadUrl(backupFileName),
                        backupFileName
                    )
                        .then(function () {
                            // backup is on disk and downloaded, run the migration (no second backup)
                            self.apiClient.callUpgradeDatabaseScheme(
                                {backupDownloaded: true},
                                handleUpgradeResponse
                            );
                        })
                        .catch(handleDownloadError);
                });
                return;
            }

            // external: download the backup dump; the migration only starts after a successful download
            var now = new Date();
            var pad = function (value) {
                return (value < 10 ? "0" : "") + value;
            };
            var dumpFileName =
                "SpoolManager-mysql-backup-" +
                now.getFullYear() +
                pad(now.getMonth() + 1) +
                pad(now.getDate()) +
                "-" +
                pad(now.getHours()) +
                pad(now.getMinutes()) +
                ".sql";
            downloadBackupFile(self.apiClient.getDatabaseDumpExportUrl(), dumpFileName)
                .then(function () {
                    // backup is on disk, run the migration
                    self.apiClient.callUpgradeDatabaseScheme(
                        {backupDownloaded: true},
                        handleUpgradeResponse
                    );
                })
                .catch(handleDownloadError);
        };

        // - MySQL dump export/import (Storage tab, external database only)
        self.isExternalMySQL = ko.pureComputed(function () {
            return (
                self.pluginSettings.useExternal() == true &&
                self.pluginSettings.databaseType() == "mysql"
            );
        });
        self.sqlImportMode = ko.observable("append");
        self.sqlDumpFileName = ko.observable();
        self.sqlImportInProgress = ko.observable(false);
        self.sqlImportResultText = ko.observable();
        self.sqlDumpFile = undefined;

        self.getDatabaseDumpExportUrl = function () {
            return self.apiClient.getDatabaseDumpExportUrl();
        };

        self.sqlDumpFileChanged = function (data, event) {
            var files = event.target.files;
            if (files == null || files.length == 0) {
                self.sqlDumpFileName(undefined);
                self.sqlDumpFile = undefined;
                return;
            }
            self.sqlDumpFileName(files[0].name);
            self.sqlDumpFile = files[0];
        };

        // - local SQLite .db restore (internal database only)
        self.dbRestoreMode = ko.observable("append");
        self.dbRestoreFileName = ko.observable();
        self.dbRestoreInProgress = ko.observable(false);
        self.dbRestoreResultText = ko.observable();
        self.dbRestoreFile = undefined;

        self.dbRestoreFileChanged = function (data, event) {
            var files = event.target.files;
            if (files == null || files.length == 0) {
                self.dbRestoreFileName(undefined);
                self.dbRestoreFile = undefined;
                return;
            }
            self.dbRestoreFileName(files[0].name);
            self.dbRestoreFile = files[0];
        };

        self.performDBRestoreFromUpload = function () {
            if (self.dbRestoreFile === undefined) return;

            self.confirmReplaceIfNeeded(self.dbRestoreMode()).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                self._runDBRestoreFromUpload();
            });
        };

        self._runDBRestoreFromUpload = function () {
            self.resetDatabaseMessages();
            self.dbRestoreResultText(undefined);
            self.dbRestoreInProgress(true);
            self.showLocalBusyIndicator(true);

            var runDbRestore = function () {
                self.apiClient.callImportDatabaseFile(
                    self.dbRestoreFile,
                    self.dbRestoreMode(),
                    function (success, data) {
                        self.dbRestoreInProgress(false);
                        self.showLocalBusyIndicator(false);

                        if (success == true && data != null && data.success == true) {
                            self.handleDatabaseMetaDataResponse(data);
                            self.showInternalSuccessMessage(false);
                            self.dbRestoreResultText(
                                "Database file restore successful: " +
                                    data.importedSpoolCount +
                                    " spool(s) (" +
                                    self.dbRestoreMode() +
                                    ")."
                            );
                            self.spoolItemTableHelper.reloadItems();
                            // a replace swaps the whole db file - a reload makes cached viewmodels pick it up
                            if (self.dbRestoreMode() == "replace") {
                                SPOOLMANAGER_DIALOGS.confirm({
                                    title: "Database file restored",
                                    message:
                                        "The database file was restored successfully.",
                                    question: "A page reload is recommended. Reload now?",
                                    cancel: "Later",
                                    proceed: "Reload now"
                                }).then(function (reloadConfirmed) {
                                    if (reloadConfirmed == true) {
                                        location.reload();
                                    }
                                });
                            }
                        } else {
                            var errorMessage = "Database file restore failed.";
                            if (data != null && data.errorMessage != null) {
                                errorMessage = data.errorMessage;
                            } else if (
                                data != null &&
                                data.responseText != null &&
                                data.responseText != ""
                            ) {
                                errorMessage = data.responseText;
                            }
                            self.showInternalDatabaseErrorMessage(true);
                            self.internalDatabaseErrorMessage(errorMessage);
                        }
                    }
                );
            };

            // create + download the pre-import backup first; only restore after it succeeded
            self.runPreImportBackupThenImport(runDbRestore, function (errorMessage) {
                self.dbRestoreInProgress(false);
                self.showLocalBusyIndicator(false);
                self.showInternalDatabaseErrorMessage(true);
                self.internalDatabaseErrorMessage("Restore aborted - " + errorMessage);
            });
        };

        self.performSQLImportFromUpload = function () {
            if (self.sqlDumpFile === undefined) return;

            self.confirmReplaceIfNeeded(self.sqlImportMode()).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                self._runSQLImportFromUpload();
            });
        };

        self._runSQLImportFromUpload = function () {
            self.resetDatabaseMessages();
            self.sqlImportResultText(undefined);
            self.sqlImportInProgress(true);
            self.showExternalBusyIndicator(true);

            var runSqlImport = function () {
                self.apiClient.callImportDatabaseDump(
                    self.sqlDumpFile,
                    self.sqlImportMode(),
                    function (responseData) {
                        self.sqlImportInProgress(false);
                        self.showExternalBusyIndicator(false);

                        if (responseData != null && responseData.success == true) {
                            self.handleDatabaseMetaDataResponse(responseData);
                            // show a dedicated import result instead of the generic connection message
                            self.showExternalSuccessMessage(false);
                            self.sqlImportResultText(
                                "Database dump import successful: " +
                                    responseData.importedSpoolCount +
                                    " spools imported (" +
                                    self.sqlImportMode() +
                                    ")."
                            );
                            self.spoolItemTableHelper.reloadItems();
                        } else {
                            var errorMessage = "Database dump import failed.";
                            if (
                                responseData != null &&
                                responseData.errorMessage != null
                            ) {
                                errorMessage = responseData.errorMessage;
                            } else if (
                                responseData != null &&
                                responseData.responseText != null
                            ) {
                                errorMessage = responseData.responseText;
                            }
                            self.showExternalDatabaseErrorMessage(true);
                            self.externalDatabaseErrorMessage(errorMessage);
                        }
                    }
                );
            };

            // create + download the pre-import backup first; only import after it succeeded
            self.runPreImportBackupThenImport(runSqlImport, function (errorMessage) {
                self.sqlImportInProgress(false);
                self.showExternalBusyIndicator(false);
                self.showExternalDatabaseErrorMessage(true);
                self.externalDatabaseErrorMessage("Import aborted - " + errorMessage);
            });
        };

        $("#spoolmanger-settings-tab")
            .find('a[data-toggle="tab"]')
            .on("shown", function (e) {
                var activatedTab = e.target.hash; // activated tab

                if (self.pluginSettings.useExternal() == true) {
                    self.databaseInUse("External");
                } else {
                    self.databaseInUse("Internal");
                }

                if ("#tab-spool-Storage" == activatedTab) {
                    self.resetDatabaseMessages();

                    self.showLocalBusyIndicator(
                        self.pluginSettings.useExternal() == false
                    );
                    self.showExternalBusyIndicator(
                        self.pluginSettings.databasePassword() == true
                    );

                    self.apiClient.loadDatabaseMetaData(function (responseData) {
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

        // - U1 RFID (settings tab): detection chain status + "test connection" result
        self.u1RfidStatus = ko.observable({
            supported: false,
            connected: false,
            chain: {},
            chainMessage: "",
            printerInfo: {},
            host: null,
            port: null
        });
        self.u1RfidTesting = ko.observable(false);
        self.u1RfidTestMessage = ko.observable("");
        self.u1RfidTestOk = ko.observable(false);
        self.u1RfidChannels = ko.observableArray([]);

        // True when a spool's weight came from an RFID tag instead of a scale reading.
        // Drives the hint in the spool table; the marker lives in `labels` so no schema
        // change was needed.
        self.isWeightEstimated = function (spoolItem) {
            if (spoolItem == null || typeof spoolItem.labels !== "function") {
                return false;
            }
            var labels = spoolItem.labels();
            if (!Array.isArray(labels)) {
                return false;
            }
            return labels.indexOf(SPOOLMANAGER_CONSTANTS.LABEL_WEIGHT_ESTIMATED) >= 0;
        };

        self.loadU1RfidStatus = function () {
            if (self.apiClient == null || self.apiClient.getU1RfidStatus == null) {
                return;
            }
            self.apiClient.getU1RfidStatus(function (response) {
                if (response != null) {
                    self.u1RfidStatus(response);
                }
            });
        };

        self.testU1RfidConnection = function () {
            if (self.apiClient == null || self.apiClient.testU1RfidConnection == null) {
                return;
            }
            self.u1RfidTesting(true);
            self.u1RfidTestMessage("");
            self.apiClient.testU1RfidConnection(function (response) {
                self.u1RfidTesting(false);
                if (response == null) {
                    self.u1RfidTestOk(false);
                    self.u1RfidTestMessage("No response from the server.");
                    return;
                }
                self.u1RfidTestOk(response.ok === true);
                self.u1RfidTestMessage(response.message || "");
                if (response.status != null) {
                    self.u1RfidStatus(response.status);
                }
                var channels = (response.channels || []).map(function (channel) {
                    return {
                        channel: ko.observable(channel.channel),
                        uid: ko.observable(channel.uid),
                        cardType: ko.observable(channel.cardType),
                        spoolName: ko.observable(channel.spoolName)
                    };
                });
                self.u1RfidChannels(channels);
            });
        };

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
            add: function (e, data) {
                if (data.files.length === 0) {
                    // no files? ignore
                    return false;
                }
                self.csvFileUploadName(data.files[0].name);
                self.csvImportUploadData = data;
            },
            done: function (e, data) {
                self.csvImportInProgress(false);
                self.csvFileUploadName(undefined);
                self.csvImportUploadData = undefined;
            },
            error: function (response, data, errorMessage) {
                self.csvImportInProgress(false);
                // e.g. 400 / BAD REQUEST / Invalid request
                console.error(
                    "CSV import upload failed:",
                    response.status,
                    response.statusText,
                    response.responseText
                );
            }
        });
        // Spool count of the CURRENTLY ACTIVE database (for the replace-confirm dialog).
        self.activeDatabaseSpoolCount = function () {
            var count =
                self.pluginSettings.useExternal() == true
                    ? self.databaseMetaData.externalSpoolItemCount()
                    : self.databaseMetaData.localSpoolItemCount();
            return count == null || count == "" ? "?" : count;
        };

        // Confirms a replace import (shows how many spools will be deleted).
        // Resolves with true when the import may proceed - non-replace modes resolve immediately.
        self.confirmReplaceIfNeeded = function (importMode) {
            if (importMode != "replace") {
                return Promise.resolve(true);
            }
            var dbName = self.databaseInUse().toLowerCase();
            return SPOOLMANAGER_DIALOGS.confirmDanger({
                title: "REPLACE import",
                message:
                    "All " +
                    self.activeDatabaseSpoolCount() +
                    " spool(s) in the " +
                    SPOOLMANAGER_DIALOGS.escapeHtml(dbName) +
                    " database will be DELETED and replaced " +
                    "with the contents of the uploaded file. A backup is created and downloaded first.",
                question: "Do you really want to replace all spools?",
                cancel: "Cancel import",
                proceed: "Replace all spools"
            });
        };

        self.performCSVImportFromUpload = function () {
            if (self.csvImportUploadData === undefined) return;

            var importMode = self.pluginSettings.importCSVMode();
            self.confirmReplaceIfNeeded(importMode).then(function (confirmed) {
                if (confirmed != true) {
                    return;
                }
                self._runCSVImportFromUpload();
            });
        };

        self._runCSVImportFromUpload = function () {
            self.csvImportInProgress(true);

            // create + download the pre-import backup first; only import after it succeeded
            self.runPreImportBackupThenImport(
                function () {
                    self.csvImportDialog.showDialog(function (shouldTableReload) {
                        if (shouldTableReload == true) {
                            self.spoolItemTableHelper.reloadItems();
                        }
                    });
                    self.csvImportUploadData.submit();
                },
                function (errorMessage) {
                    self.csvImportInProgress(false);
                    self.showExternalDatabaseErrorMessage(true);
                    self.externalDatabaseErrorMessage("Import aborted - " + errorMessage);
                    self.showInternalDatabaseErrorMessage(
                        self.pluginSettings.useExternal() != true
                    );
                    self.internalDatabaseErrorMessage("Import aborted - " + errorMessage);
                }
            );
        };

        // template stuff
        self.checkExcludedFromTemplateCopy = function (fieldName) {
            return ko.pureComputed({
                read: function () {
                    var result =
                        self.pluginSettings
                            .excludedFromTemplateCopy()
                            .includes(fieldName) == false;
                    return result;
                },
                write: function (value) {
                    if (value == false) {
                        self.pluginSettings.excludedFromTemplateCopy.push(fieldName);
                    } else {
                        self.pluginSettings.excludedFromTemplateCopy.remove(fieldName);
                    }
                },
                owner: this
            });
        };

        // overwrite save-button
        const origSaveSettingsFunction = self.settingsViewModel.saveData;
        const newSaveSettingsFunction = function confirmSpoolSelectionBeforeStartPrint(
            data,
            successCallback,
            setAsSending
        ) {
            if (
                self.pluginSettings.useExternal() == true &&
                (self.showExternalDatabaseErrorMessage() == true ||
                    self.showInternalDatabaseErrorMessage() == true ||
                    self.showUpdateSchemeMessage() == true)
            ) {
                return origSaveSettingsFunction(data, successCallback, setAsSending);
            }
            return origSaveSettingsFunction(data, successCallback, setAsSending);
        };
        self.settingsViewModel.saveData = newSaveSettingsFunction;

        // QR-Code stuff
        self.generateQRCodeTestLink = function () {
            var source =
                self.pluginSettings.qrCodeURLPrefix() +
                "/plugin/SpoolManager/selectSpoolByQRCode/qrPreviewId";
            var title = "This link is used for the QR-Code";
            return {
                href: source,
                title: title
            };
        };

        ///////////////////////////////////////////////////// END: SETTINGS

        ////////////////////////////////////////////////////////////////////////////////////////// SIDEBAR - REPLACEMENT
        self.printerStateViewModel.spoolsWithWeight = ko.observableArray([]);
        self.printerStateViewModel.extrusionValues = ko.observableArray([]);

        self.updateExtrusionValues = function (extrusionValuesArray) {
            // update patched PrinterStateViewModel
            self.printerStateViewModel.extrusionValues(extrusionValuesArray);
        };

        self.updateRequiredFilament = function (requiredFilament) {
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
            for (let filamentItem of filamentList) {
                if (filamentItem.requiredLength > 0) {
                    filteredFilamentList.push(filamentItem);
                }
            }
            self.printerStateViewModel.spoolsWithWeight(filteredFilamentList);
        };

        self.printerStateViewModel.formatSpoolsWithWeight =
            function formatSpoolsWithWeightInSidebar(filament) {
                if (!filament) return "-";

                // length in the configured display unit
                var result = self.formatLengthForDisplay(filament.requiredLength);
                // try to get the weight
                if (filament.requiredWeight) {
                    result +=
                        " / " + self.formatWeightForDisplay(filament.requiredWeight);
                }
                if (filament.spoolSelected && filament.spoolSelected == true) {
                    if (filament.notEnough) {
                        if (filament.notEnough == true) {
                            result +=
                                ' (<span style="color:red">' +
                                self.formatWeightForDisplay(filament.remainingWeight) +
                                "</span>)";
                        }
                    }
                } else {
                    if (filament.requiredLength > 0) {
                        result += " (no spool selected)";
                    }
                }

                return result;
            };

        self.replaceFilamentView = function replaceFilamentViewInSidebar() {
            $("#state")
                .find(".accordion-inner")
                .contents()
                .each(function (index, item) {
                    if (item.nodeType === Node.COMMENT_NODE) {
                        if (
                            item.nodeValue === " ko foreach: filament " ||
                            item.nodeValue === " ko foreach: [] "
                        ) {
                            item.nodeValue = " ko foreach: [] ";
                            var element =
                                "<!-- ko if: spoolsWithWeight().length < 1 -->  <span><strong>Required Filament unknown</strong></span><br/> <!-- /ko -->";
                            element +=
                                "<!-- ko foreach: spoolsWithWeight --> <span data-bind=\"text: 'Tool ' + toolIndex + ': ', attr: {title: 'Filament usage for Spool ' + spoolName}\"></span><strong data-bind=\"html: $root.formatSpoolsWithWeight($data)\"></strong><br> <!-- /ko -->";

                            element +=
                                '<div data-bind="visible: settings.settings.plugins.SpoolManager.extrusionDebuggingEnabled">';
                            element += "<!-- ko foreach: extrusionValues -->";
                            element +=
                                '<div>Extruded Tool <span data-bind="text: $index"></span>: <strong data-bind="text: $data.toFixed(2)"></strong></div>';
                            element += "<!-- /ko -->";

                            element += "</div>";
                            $(element).insertBefore(item);

                            return false; // exit loop
                        }
                    }
                    return true;
                });
        };

        /////////////////////////////////////////////////////////////////////////////////////////// SIDEBAR - SELECT
        self.allSpoolsForSidebar = ko.observableArray([]);
        // Grand total of every spool in the database (unfiltered) - shown as "(N Spools in Database)".
        self.sidebarDatabaseItemCount = ko.observable(0);
        self.selectedSpoolsForSidebar = ko.observableArray([]);

        self.sidebarSelectSpoolModalToolIndex = ko.observable(null); // index of the current tool we want to select for
        self.sidebarSelectSpoolModalSpoolItem = ko.observable(null); // current spoolitem

        self.deselectSpoolForSidebar = function (toolIndex, item) {
            self.selectSpoolForSidebar(toolIndex, null);
        };

        // Lazy loading of the spool selector's data (Attribution @mdziekon, PR #8):
        // when enabled, the (up to 3333 items) selector dataset is fetched on first
        // dialog open instead of on OctoPrint page load.
        self.hasInitializedSpoolsSelector = false;

        // Drives the spinner inside <spm-select-spool-table> while the selector query is
        // in flight (Attribution @mdziekon, PR #42). Always reset in the response callback,
        // including on API failure, so the spinner can never hang.
        self.isLoadingSpoolsSelectorData = ko.observable(false);

        self._isLazySelectorEnabled = function () {
            return (
                self.pluginSettings != null &&
                self.pluginSettings.performanceLazyLoadSpoolSelectorData &&
                self.pluginSettings.performanceLazyLoadSpoolSelectorData() == true
            );
        };

        // resize the sidebar spool slots to match the printer profile's extruder count
        self.updateAvailableSpoolSlots = function () {
            var currentProfileData =
                    self.settingsViewModel.printerProfiles.currentProfileData(),
                numExtruders = currentProfileData
                    ? currentProfileData.extruder.count()
                    : 0,
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
        };

        // fill the sidebar spool slots with the given selected-spools data
        self._applySelectedSpoolsData = function (spoolsData) {
            var slot, spoolData, spoolItem;
            for (var i = 0; i < self.selectedSpoolsForSidebar().length; i++) {
                slot = self.selectedSpoolsForSidebar()[i];
                spoolData = i < spoolsData.length ? spoolsData[i] : null;
                spoolItem = spoolData
                    ? self.spoolDialog.createSpoolItemForTable(spoolData)
                    : null;
                slot(spoolItem);
            }
        };

        // fetch the current spool selection via the dedicated endpoint.
        // `doneHandler` is optional and fires once the slots have been filled, for callers
        // that need to read selectedSpoolsForSidebar() afterwards (e.g. the QR-Code path).
        self.loadCurrentSelectedSpoolsData = function (doneHandler) {
            self.apiClient.callLoadSelectedSpools(function (responseData) {
                var spoolsData =
                    responseData != null ? responseData["selectedSpools"] : null;
                if (spoolsData != null) {
                    self._applySelectedSpoolsData(spoolsData);
                }
                if (doneHandler) {
                    doneHandler();
                }
            });
        };

        // fetch the full spool list for the sidebar select-spool dialog
        self.loadSpoolSelectorData = function () {
            self.hasInitializedSpoolsSelector = true;
            self.isLoadingSpoolsSelectorData(true);

            var currentFilterName = "all";

            var tableQuery = {
                filterName: currentFilterName,
                from: 0,
                to: 3333,
                sortColumn: "lastUse",
                sortOrder: "desc"
            };

            // api-call
            self.apiClient.callLoadSpoolsByQuery(tableQuery, function (responseData) {
                // Clear the busy flag first: callLoadSpoolsByQuery passes no onFail, so
                // _callApi invokes this handler with an undefined body on network errors.
                // Clearing up-front guarantees the spinner stops even if the body throws.
                self.isLoadingSpoolsSelectorData(false);

                if (responseData == null) {
                    // Request failed; keep the previously loaded list rather than blanking
                    // it, otherwise a transient error would wipe a good list and the empty
                    // state would wrongly claim there are no spools at all.
                    return;
                }

                self.sidebarDatabaseItemCount(responseData["databaseItemCount"]);

                var allSpoolData = responseData["allSpools"]; // rawdtata
                if (allSpoolData != null) {
                    var allSpoolItems = ko.utils.arrayMap(
                        allSpoolData,
                        function (spoolData) {
                            var result =
                                self.spoolDialog.createSpoolItemForTable(spoolData);
                            return result;
                        }
                    ); // transform to SpoolItems with KO.obseravables
                    self.allSpoolsForSidebar(allSpoolItems);
                }
            });
        };

        // load everything the sidebar widgets need; while the lazy selector setting is
        // active and the selector was never opened, the big selector query is skipped
        self.loadSidebarSpoolWidgetsData = function () {
            self.updateAvailableSpoolSlots();
            self.loadCurrentSelectedSpoolsData();
            if (
                self._isLazySelectorEnabled() == false ||
                self.hasInitializedSpoolsSelector == true
            ) {
                self.loadSpoolSelectorData();
            }
        };

        // ----------------- start: display units (table / sidebar / tooltips)
        // base values are always stored in mm/g, these helpers only convert for display
        var LENGTH_UNIT_FACTORS = {mm: 1, cm: 10, m: 1000};
        var WEIGHT_UNIT_FACTORS = {g: 1, kg: 1000};
        var UNIT_DISPLAY_DECIMALS = {mm: 1, cm: 2, m: 3, g: 1, kg: 3};

        self.selectedLengthUnit = function () {
            var unit =
                self.pluginSettings && self.pluginSettings.lengthUnit
                    ? self.pluginSettings.lengthUnit()
                    : "mm";
            return LENGTH_UNIT_FACTORS[unit] ? unit : "mm";
        };
        self.selectedWeightUnit = function () {
            var unit =
                self.pluginSettings && self.pluginSettings.weightUnit
                    ? self.pluginSettings.weightUnit()
                    : "g";
            return WEIGHT_UNIT_FACTORS[unit] ? unit : "g";
        };

        // convert a raw value (mm resp. g) to the configured display unit; returns a number
        self.convertLengthForDisplay = function (rawMillimeter) {
            var unit = self.selectedLengthUnit();
            var value = parseFloat(rawMillimeter);
            if (isNaN(value)) return rawMillimeter;
            return parseFloat(
                (value / LENGTH_UNIT_FACTORS[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit])
            );
        };
        self.convertWeightForDisplay = function (rawGram) {
            var unit = self.selectedWeightUnit();
            var value = parseFloat(rawGram);
            if (isNaN(value)) return rawGram;
            return parseFloat(
                (value / WEIGHT_UNIT_FACTORS[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit])
            );
        };

        // full "<value><unit>" strings for direct display
        self.formatLengthForDisplay = function (rawMillimeter) {
            if (rawMillimeter == null || rawMillimeter === "") return "";
            var value = self.convertLengthForDisplay(rawMillimeter);
            if (value === rawMillimeter && isNaN(parseFloat(rawMillimeter)))
                return "" + rawMillimeter;
            return value + self.selectedLengthUnit();
        };
        self.formatWeightForDisplay = function (rawGram) {
            if (rawGram == null || rawGram === "") return "";
            var value = self.convertWeightForDisplay(rawGram);
            if (value === rawGram && isNaN(parseFloat(rawGram))) return "" + rawGram;
            return value + self.selectedWeightUnit();
        };
        // ----------------- end: display units

        var _buildRemainingText = function (spoolItem) {
            var remainingInfo = "";
            // if (  spoolItem.remainingWeight() != null && spoolItem.remainingWeight().length != 0
            //     && spoolItem.remainingPercentage() != null && spoolItem.remainingPercentage().length != 0){
            //     remainingInfo = "("+spoolItem.remainingWeight()+"g / "+spoolItem.remainingPercentage()+"%)";
            // }
            if (
                spoolItem.remainingWeight() != null &&
                spoolItem.remainingWeight().length != 0
            ) {
                // remainingInfo = "(R: "+spoolItem.remainingWeight()+"g)";
                remainingInfo = self.formatWeightForDisplay(spoolItem.remainingWeight());
            }
            return remainingInfo;
        };

        self.remainingText = function (spoolItem) {
            var remainingWeight = _buildRemainingText(spoolItem);
            var remainingLength = self.formatLengthForDisplay(
                spoolItem.remainingLength()
            );
            var remainingInfo = "(" + remainingWeight + ", " + remainingLength + ")";
            return remainingInfo;
        };

        // attribute-name -> value/unit conversion for tooltips (values are stored in mm/g)
        var _isLengthAttribute = function (attribute) {
            return (
                typeof attribute === "string" &&
                attribute.toLowerCase().indexOf("length") !== -1
            );
        };
        var _isWeightAttribute = function (attribute) {
            return (
                typeof attribute === "string" &&
                attribute.toLowerCase().indexOf("weight") !== -1
            );
        };
        // format a single attribute value honoring the configured display units;
        // falls back to the raw value plus the passed-in unit for non-length/weight attributes
        var _formatTooltipAttribute = function (rawValue, attribute, fallbackUnit) {
            if (_isLengthAttribute(attribute)) {
                return self.formatLengthForDisplay(rawValue);
            }
            if (_isWeightAttribute(attribute)) {
                return self.formatWeightForDisplay(rawValue);
            }
            return rawValue + (fallbackUnit || "");
        };

        self.buildTooltipForSpoolItem = function (
            spoolItem,
            textPrefix,
            attribute,
            unit,
            textPrefix2,
            attribute2,
            unit2
        ) {
            var tooltip = "";

            // first attribute
            if (spoolItem[attribute]() != null) {
                tooltip =
                    textPrefix +
                    _formatTooltipAttribute(spoolItem[attribute](), attribute, unit);
            }

            // optional second attribute
            if (attribute2 && spoolItem[attribute2]() != null) {
                tooltip +=
                    (tooltip ? ", " : "") +
                    textPrefix2 +
                    _formatTooltipAttribute(spoolItem[attribute2](), attribute2, unit2);
            }

            return tooltip;
        };

        // Which spool currently sits in a tool slot (null if the slot is empty)?
        self._currentSpoolInTool = function (toolIndex) {
            var slots = self.selectedSpoolsForSidebar();
            if (toolIndex == null || toolIndex < 0 || toolIndex >= slots.length) {
                return null;
            }
            return slots[toolIndex]();
        };

        // Which tool currently holds this spool (-1 if none)? A spool can only ever be
        // assigned to a single tool, so selecting it for another one moves it.
        self._findToolHoldingSpool = function (databaseId) {
            if (databaseId == null) {
                return -1;
            }
            var slots = self.selectedSpoolsForSidebar();
            for (var i = 0; i < slots.length; i++) {
                var slotItem = slots[i]();
                if (slotItem !== null && slotItem.databaseId() === databaseId) {
                    return i;
                }
            }
            return -1;
        };

        self.selectSpoolForSidebar = function (toolIndex, spoolItem) {
            var currentSpoolItem = self._currentSpoolInTool(toolIndex);
            var newDatabaseId = spoolItem != null ? spoolItem.databaseId() : null;
            // The spool may already sit in another tool - assigning it here just moves it.
            var toolAlreadyHoldingSpool = self._findToolHoldingSpool(newDatabaseId);
            var isSameSpool = toolAlreadyHoldingSpool !== -1;

            if (self.printerStateViewModel.isPrinting() == false) {
                self._doSelectSpoolForSidebar(toolIndex, spoolItem, undefined);
                return;
            }

            // From here on a print is running. The backend then insists on an explicit
            // commitCurrentSpoolValues (409 otherwise), so every branch passes one.
            if (isSameSpool == false) {
                // A different spool goes into the tool -> the usage so far has to be booked
                // to either the previous or the new spool. A native confirm() only offers
                // OK/Cancel, which forced "Cancel" to mean "book to the new spool"; the
                // multi-button showConfirmationDialog() makes both choices explicit and lets
                // Cancel do what it says: nothing.
                var changeDescription;
                if (spoolItem == null) {
                    var removedName =
                        currentSpoolItem != null
                            ? "'" + currentSpoolItem.displayName() + "'"
                            : "The spool";
                    changeDescription =
                        removedName + " will be removed from tool " + toolIndex;
                } else if (currentSpoolItem == null) {
                    changeDescription =
                        "'" +
                        spoolItem.displayName() +
                        "' will be loaded into tool " +
                        toolIndex;
                } else {
                    changeDescription =
                        "Tool " +
                        toolIndex +
                        " will be changed from '" +
                        currentSpoolItem.displayName() +
                        "' to '" +
                        spoolItem.displayName() +
                        "'";
                }
                showConfirmationDialog({
                    title: "Change spool while printing?",
                    message:
                        changeDescription +
                        " while a print is running. The filament used so far still has to be booked to a spool.",
                    question:
                        "The spool will be changed either way - where should the usage of the print so far be booked?",
                    cancel: "Don't change spool",
                    proceed: ["Book to previous", "Book to new"],
                    proceedClass: "primary",
                    onproceed: function (buttonIndex) {
                        // index 0 = previous spool (the old confirm()'s "OK"), 1 = new spool
                        self._doSelectSpoolForSidebar(
                            toolIndex,
                            spoolItem,
                            buttonIndex === 0
                        );
                    },
                    nofade: true
                });
                return;
            }

            if (toolAlreadyHoldingSpool !== toolIndex) {
                // Same spool, but currently in a different tool -> selecting it here moves it
                // between tools mid-print, which is easy to trigger accidentally (e.g. via a
                // QR scan). Ask for a plain confirmation. No usage question: it is one and the
                // same spool, so the print so far is booked to it regardless.
                showConfirmationDialog({
                    title: "Move spool while printing?",
                    message:
                        "'" +
                        spoolItem.displayName() +
                        "' is currently loaded in tool " +
                        toolAlreadyHoldingSpool +
                        ". A print is running.",
                    question:
                        "Move it from tool " +
                        toolAlreadyHoldingSpool +
                        " to tool " +
                        toolIndex +
                        "?",
                    cancel: "Don't move spool",
                    proceed: "Move to tool " + toolIndex,
                    proceedClass: "primary",
                    onproceed: function () {
                        self._doSelectSpoolForSidebar(toolIndex, spoolItem, true);
                    },
                    nofade: true
                });
                return;
            }

            // Same spool, already in this very tool -> nothing actually changes, just re-assign.
            self._doSelectSpoolForSidebar(toolIndex, spoolItem, true);
        };

        self._doSelectSpoolForSidebar = function (
            toolIndex,
            spoolItem,
            commitCurrentSpoolValues
        ) {
            // api-call
            var databaseId = -1;
            if (spoolItem != null) {
                databaseId = spoolItem.databaseId();
            }
            self.apiClient.callSelectSpool(
                toolIndex,
                databaseId,
                commitCurrentSpoolValues,
                function (responseData) {
                    var spoolItem = null;
                    if (responseData == null) {
                        // request failed (e.g. stale CSRF token after a server restart, network error).
                        // Without this guard the sidebar was cleared as if the spool had been
                        // deselected, showing a selection state the server never agreed to.
                        self.showPopUp(
                            "error",
                            "Spool selection failed",
                            "The spool selection could not be saved. Please reload the page and try again.",
                            false
                        );
                        self.loadCurrentSelectedSpoolsData();
                        return;
                    }
                    var spoolData = responseData["selectedSpool"];
                    if (spoolData != null) {
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
                        if (
                            tmpSpoolItem !== null &&
                            tmpSpoolItem.databaseId() === currentDatabaseId
                        ) {
                            self.selectedSpoolsForSidebar()[i](null);
                            break;
                        }
                    }
                    // assign to new (or same) toolIndex
                    if (toolIndex != -1) {
                        self.selectedSpoolsForSidebar()[toolIndex](spoolItem);
                    }
                }
            );
        };

        self.editSpoolFromSidebar = function (toolIndex, spoolItem) {
            if (spoolItem == null) {
                SPOOLMANAGER_DIALOGS.notify({
                    title: "No spool selected",
                    message:
                        "There is no spool selected for this tool, so there is nothing to edit.",
                    type: "error"
                });
            }
            self.showSpoolDialogAction(spoolItem);
        };

        self.sidebarSelectSpoolFromDialog = function (spoolItem) {
            self.selectionSpoolDialog.modal("hide");
            self.selectSpoolForSidebar(
                self.sidebarSelectSpoolModalToolIndex(),
                spoolItem
            );
        };

        self.sidebarOpenSelectSpoolDialog = function (toolIndex, spoolItem) {
            /* needed for Filter-Search dropdown-menu */
            $(".dropdown-menu.keep-open").click(function (e) {
                e.stopPropagation();
            });

            // lazy selector: fetch the spool list on first dialog open
            if (
                self._isLazySelectorEnabled() &&
                self.hasInitializedSpoolsSelector == false
            ) {
                self.loadSpoolSelectorData();
            }

            self.sidebarSelectSpoolModalSpoolItem(spoolItem);
            self.sidebarSelectSpoolModalToolIndex(toolIndex);

            self.selectionSpoolDialog.modal({
                minHeight: 300,
                show: true
            });
        };

        //////////////////////////////////////////////////////////////////////////////////////////////////// TABLE / TAB

        // Shared hint for the two entry points that are blocked by an outdated database scheme.
        self._notifySchemeUpgradeNeeded = function () {
            SPOOLMANAGER_DIALOGS.notify({
                title: "Database scheme is outdated",
                message:
                    "Saving would fail. Open Plugin Settings &rarr; SpoolManager &rarr; Storage " +
                    "and press 'Upgrade database scheme' first.",
                type: "error"
            });
        };

        self.addNewSpool = function () {
            if (self.schemeUpgradeNeeded() == true) {
                // saving would fail anyway, guide the user to the upgrade instead
                self._notifySchemeUpgradeNeeded();
                return;
            }
            self.spoolDialog.showDialog(null, closeDialogHandler);
        };

        // Guided alternative to the dialog above, reached through the dropdown next to "+ Add Spool".
        self.addNewSpoolViaWizard = function () {
            if (self.schemeUpgradeNeeded() == true) {
                self._notifySchemeUpgradeNeeded();
                return;
            }
            self.addSpoolWizard.showDialog(closeDialogHandler);
        };

        ///////////////////////////////////////////////////////////////////////////// U1 RFID

        // An unknown tag was detected in one of the U1's channels. NOTHING opens on its
        // own here: the backend pushes this to every connected client, so an
        // auto-opening dialog would appear in all of them - including where someone is
        // in the middle of editing a spool. Same reasoning as the QR notfound popup.
        self._showU1RfidUnknownTagPopUp = function (data) {
            if (data == null || !data.uid) {
                return;
            }
            var tagDescription = SPOOLMANAGER_U1RFID.describeTag(data.metadata);
            var message =
                "Channel " +
                data.channel +
                " reported tag <strong>" +
                data.uid +
                "</strong>" +
                (tagDescription ? " (" + tagDescription + ")" : "") +
                ", which is not assigned to any spool yet.";

            var rfidContext = {
                uid: data.uid,
                rfidTagKey: data.rfidTagKey,
                channel: data.channel,
                metadata: data.metadata || {}
            };

            SPOOLMANAGER_DIALOGS.notifyWithActions({
                type: "notice",
                title: "Unknown RFID tag",
                message: message,
                // one popup per UID: several channels reporting in quick succession must
                // not stack notifications
                identity: "u1rfid-unknown-" + data.uid,
                buttons: [
                    {
                        text: "Open Spool Wizard",
                        addClass: "btn-small btn-primary",
                        onClick: function () {
                            if (self.schemeUpgradeNeeded() == true) {
                                self._notifySchemeUpgradeNeeded();
                                return;
                            }
                            self.addSpoolWizard.showDialog(
                                closeDialogHandler,
                                rfidContext
                            );
                        }
                    },
                    {
                        text: "Open Spool Edit",
                        addClass: "btn-small",
                        onClick: function () {
                            if (self.schemeUpgradeNeeded() == true) {
                                self._notifySchemeUpgradeNeeded();
                                return;
                            }
                            self.spoolDialog.showDialog(
                                null,
                                closeDialogHandler,
                                false,
                                rfidContext
                            );
                        }
                    },
                    {
                        text: "Cancel",
                        addClass: "btn-small",
                        onClick: function () {
                            // nothing to do - the UID stays available in the settings
                            // status and in the edit dialog's "adopt UID" button
                        }
                    }
                ]
            });
        };

        // A known tag resolved to a spool - report the outcome of the selection.
        self._showU1RfidSelectionPopUp = function (data) {
            if (data == null) {
                return;
            }
            var spoolName = data.spoolName || "Spool";
            if (data.status === "selected") {
                self.showPopUp(
                    "success",
                    "Spool selected",
                    "'" +
                        spoolName +
                        "' was loaded into tool " +
                        data.toolIndex +
                        " (U1 channel " +
                        data.channel +
                        ").",
                    true
                );
                return;
            }
            if (data.status === "printing") {
                self.showPopUp(
                    "warning",
                    "Spool not selected",
                    "A print is currently running, so '" +
                        spoolName +
                        "' was not selected. The running job's usage tracking stays untouched.",
                    false
                );
                return;
            }
            if (data.status === "notfound") {
                self.showPopUp(
                    "error",
                    "Spool not found",
                    "The spool assigned to tag " +
                        data.uid +
                        " no longer exists in the database.",
                    false
                );
            }
        };

        // Downloads an inventory report (pdf/csv/xlsx) honoring the current tab filter/sort state.
        self.generateInventoryReport = function (reportFormat) {
            var tableQuery = self.spoolItemTableHelper.buildTableQuery();
            var instance = self.databaseInUse().toLowerCase();
            var reportUrl = self.apiClient.getInventoryReportUrl(
                tableQuery,
                instance,
                reportFormat || "pdf"
            );
            window.open(reportUrl, "_newTab");
        };

        var TableAttributeVisibility = function () {
            this.databaseId = ko.observable(false);
            this.displayName = ko.observable(true);
            this.material = ko.observable(true);
            this.lastFirstUse = ko.observable(true);
            this.weight = ko.observable(true);
            this.used = ko.observable(true);
            this.note = ko.observable(true);
        };
        self.tableAttributeVisibility = new TableAttributeVisibility();

        self.initTableVisibilities = function () {
            // load all settings from browser storage
            if (!Modernizr.localstorage) {
                // damn!!!
                return false;
            }

            var assignVisibility = function (attributeName) {
                var storageKey = "spoolmanager.table.visible." + attributeName;
                if (localStorage[storageKey] == null) {
                    // localStorage[storageKey] = true; // default value
                    localStorage[storageKey] =
                        self.tableAttributeVisibility[attributeName](); // default value
                } else {
                    self.tableAttributeVisibility[attributeName](
                        "true" == localStorage[storageKey]
                    );
                }
                self.tableAttributeVisibility[attributeName].subscribe(
                    function (newValue) {
                        localStorage[storageKey] = newValue;
                    }
                );
            };

            assignVisibility("databaseId");
            assignVisibility("displayName");
            assignVisibility("material");
            assignVisibility("lastFirstUse");
            assignVisibility("weight");
            assignVisibility("used");
            assignVisibility("note");
        };

        ///////////////////////////////////////////////////////////////////////////////////////////////// TABLE BEHAVIOR
        /* needed for Filter-Search dropdown-menu */
        $(".dropdown-menu.keep-open").click(function (e) {
            e.stopPropagation();
        });

        self.spoolItemTableHelper = new TableItemHelper(
            function (
                tableQuery,
                observableTableModel,
                observableTotalItemCount,
                observableDatabaseItemCount
            ) {
                // api-call
                self.apiClient.callLoadSpoolsByQuery(tableQuery, function (responseData) {
                    if (
                        responseData["databaseConnectionProblem"] != null &&
                        responseData["databaseConnectionProblem"] == true
                    ) {
                        self.pluginNotWorking(true);
                    } else {
                        self.pluginNotWorking(false);
                    }
                    var wasSchemeUpgradeNeeded = self.schemeUpgradeNeeded();
                    self.schemeUpgradeNeeded(responseData["schemeUpgradeNeeded"] == true);
                    if (
                        wasSchemeUpgradeNeeded == true &&
                        self.schemeUpgradeNeeded() == false
                    ) {
                        // the database is usable again (scheme upgraded, possibly by another instance) -
                        // the sidebar spools/selection were loaded while it was broken, so refresh them too
                        self.loadSidebarSpoolWidgetsData();
                    }

                    var totalItemCount = responseData["totalItemCount"];
                    var allSpoolItems = responseData["allSpools"];
                    var allCatalogs = responseData["catalogs"];

                    // assign catalogs to tablehelper
                    self.spoolItemTableHelper.updateCatalogs(allCatalogs);
                    // assign all catalogs to editview
                    self.spoolDialog.updateCatalogs(allCatalogs);
                    self.addSpoolWizard.updateCatalogs(allCatalogs);

                    var templateSpoolsData = responseData["templateSpools"];
                    self.spoolDialog.updateTemplateSpools(templateSpoolsData);
                    // the wizard reuses the SpoolItems the dialog just built from the same data
                    self.addSpoolWizard.updateTemplateSpools(
                        self.spoolDialog.templateSpools()
                    );

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

        // The whole table row opens the edit dialog, so a click on a link inside a note would
        // never reach the browser's default navigation. Stop it from bubbling up to the row
        // binding for anchors only - every other spot in the note cell keeps opening the dialog.
        // Returning true matters: Knockout suppresses the default action when a click handler
        // returns false, which would swallow the navigation we are trying to allow here.
        self.handleNoteClick = function (event) {
            // closest(), not target.tagName: the click usually lands on nested markup
            // (<a><strong>text</strong></a>), where the target is the inner element
            if (event.target.closest("a[href]") == null) {
                return true;
            }
            event.stopPropagation();
            return true;
        };

        self.showSpoolDialogAction = function (selectedSpoolItem) {
            // identify for which toolindex is the current selectedSpoolItem is selected
            var currentDatabaseId = selectedSpoolItem.databaseId();
            var isLoadedInTool = false;
            if (currentDatabaseId) {
                for (var i = 0; i < self.selectedSpoolsForSidebar().length; i++) {
                    var spoolItem = self.selectedSpoolsForSidebar()[i]();
                    if (
                        spoolItem !== null &&
                        spoolItem.databaseId() === currentDatabaseId
                    ) {
                        selectedSpoolItem.selectedForTool(i);
                        isLoadedInTool = true;
                        break;
                    }
                }
            }
            self.spoolDialog.showDialog(
                selectedSpoolItem,
                closeDialogHandler,
                isLoadedInTool
            );
        };

        // `toolIndexOverride` added for the dropdown "Select for printing" button, adapted from
        // mdziekon/OctoPrint-SpoolManager PR #29 (GH-24). Optional/backwards-compatible: existing
        // positional callers (save/delete paths) omit it and keep the selectedForTool() behaviour.
        var closeDialogHandler = function (
            shouldTableReload,
            specialAction,
            currentSpoolItem,
            toolIndexOverride
        ) {
            if (specialAction === "selectSpoolForPrinting") {
                // the dropdown button passes the chosen tool explicitly; the save-path falls back to the spool's tool
                var toolIndex = toolIndexOverride;
                if (toolIndex === undefined || toolIndex === null || toolIndex === "") {
                    toolIndex = currentSpoolItem.selectedForTool();
                }
                if (toolIndex === undefined || toolIndex === null || toolIndex === "") {
                    // clear current selection
                    toolIndex = -1;
                }
                self.selectSpoolForSidebar(toolIndex, currentSpoolItem);
            }

            if (shouldTableReload == true) {
                self.spoolItemTableHelper.reloadItems();
                // TODO auto reload of sidebar spools without loosing selection
                self.loadSidebarSpoolWidgetsData();
            }
        };

        ///////////////////////////////////////////////////////////////////////////////////////// OCTOPRINT PRINT-BUTTON
        const origStartPrintFunction = self.printerStateViewModel.print;
        const newStartPrintFunction = function confirmSpoolSelectionBeforeStartPrint() {
            // api-call
            self.apiClient.allowedToPrint(function (responseData) {
                var result = responseData.result;

                // The three checks below used to be blocking confirm() calls in sequence.
                // They are now promise steps: every step resolves with true when the print may
                // continue, and the chain only reaches startPrintConfirmed() when all of them did.
                // Cancelling ANY step aborts the print start - same semantics as the old `return`.
                var warning = "";
                var warning2 = "";
                if (responseData.metaDataMissing) {
                    warning =
                        "<strong>ATTENTION:</strong> Needed filament could not be calculated " +
                        "(missing metadata - wait for the uploaded file to be processed).<br><br>";
                    warning2 = " (maybe)";
                }
                if (responseData.attributesMissing) {
                    warning =
                        "<strong>ATTENTION:</strong> Needed filament could not be calculated " +
                        "(missing spool-fields - edit your spool).<br><br>";
                }

                var buildSpoolLabel = function (item) {
                    var label =
                        item.toolIndex + ": '" + item.material + " - " + item.spoolName;

                    if (
                        item.remainingWeight != null &&
                        typeof item.remainingWeight === "number"
                    ) {
                        label =
                            label +
                            " (" +
                            self.formatWeightForDisplay(item.remainingWeight) +
                            ")";
                    }
                    label = label + "'";
                    return SPOOLMANAGER_DIALOGS.escapeHtml(label);
                };

                var askNoSpoolSelected = function () {
                    if (!result.noSpoolSelected.length) {
                        return Promise.resolve(true);
                    }
                    var itemList = [];
                    for (let item of result.noSpoolSelected) {
                        itemList.push(
                            SPOOLMANAGER_DIALOGS.escapeHtml("Tool " + item.toolIndex)
                        );
                    }
                    var message;
                    var question;
                    if (itemList.length === 1) {
                        message =
                            warning +
                            "There is no spool selected for " +
                            itemList[0] +
                            " despite it being used" +
                            warning2 +
                            " by this print.";
                        question =
                            "Do you want to start the print without a selected spool?";
                    } else {
                        message =
                            warning +
                            "There are no spools selected for the following tools despite them being used" +
                            warning2 +
                            " by this print:" +
                            SPOOLMANAGER_DIALOGS.buildHtmlList(itemList);
                        question =
                            "Do you want to start the print without selected spools?";
                    }
                    return SPOOLMANAGER_DIALOGS.confirm({
                        title: "No spool selected",
                        message: message,
                        question: question,
                        cancel: "Don't start print",
                        proceed: "Start anyway"
                    });
                };

                var askFilamentNotEnough = function () {
                    if (!result.filamentNotEnough.length) {
                        return Promise.resolve(true);
                    }
                    var itemList = [];
                    for (let item of result.filamentNotEnough) {
                        itemList.push(buildSpoolLabel(item));
                    }
                    var message;
                    if (itemList.length === 1) {
                        message =
                            warning +
                            "The selected spool for tool " +
                            itemList[0] +
                            " does not have enough remaining filament" +
                            warning2 +
                            ".";
                    } else {
                        message =
                            warning +
                            "The following selected spools do not have enough remaining filament" +
                            warning2 +
                            ":" +
                            SPOOLMANAGER_DIALOGS.buildHtmlList(itemList);
                    }
                    return SPOOLMANAGER_DIALOGS.confirm({
                        title: "Not enough filament",
                        message: message,
                        question: "Do you want to start the print anyway?",
                        cancel: "Don't start print",
                        proceed: "Start anyway"
                    });
                };

                var askReminderSpoolSelection = function () {
                    if (!result.reminderSpoolSelection.length) {
                        return Promise.resolve(true);
                    }
                    var itemList = [];
                    // build message for each tool
                    for (let item of result.reminderSpoolSelection) {
                        var offsets = [];
                        if (responseData.toolOffsetEnabled && item.toolOffset != null)
                            offsets.push("Tool Offset: " + item.toolOffset + "\u00B0");
                        if (responseData.bedOffsetEnabled && item.bedOffset != null)
                            offsets.push("Bed Offset: " + item.bedOffset + "\u00B0");
                        if (
                            responseData.enclosureOffsetEnabled &&
                            item.enclosureOffset != null
                        )
                            offsets.push(
                                "Enclosure Offset: " + item.enclosureOffset + "\u00B0"
                            );

                        var toolMessage = buildSpoolLabel(item);
                        if (offsets.length > 0) {
                            toolMessage += SPOOLMANAGER_DIALOGS.buildHtmlList(
                                offsets.map(function (offset) {
                                    return SPOOLMANAGER_DIALOGS.escapeHtml(offset);
                                })
                            );
                        }
                        itemList.push(toolMessage);
                    }
                    return SPOOLMANAGER_DIALOGS.confirm({
                        title: "Confirm spool selection",
                        message:
                            "The print will use the following spools:" +
                            SPOOLMANAGER_DIALOGS.buildHtmlList(itemList),
                        question: "Do you want to start the print with these spools?",
                        cancel: "Don't start print",
                        proceed: "Start print"
                    });
                };

                askNoSpoolSelected()
                    .then(function (mayContinue) {
                        return mayContinue == true ? askFilamentNotEnough() : false;
                    })
                    .then(function (mayContinue) {
                        return mayContinue == true ? askReminderSpoolSelection() : false;
                    })
                    .then(function (mayContinue) {
                        if (mayContinue != true) {
                            // user cancelled one of the steps - do NOT start the print
                            return;
                        }
                        // we are ready to go. Inform the backend and after that START PRINT
                        self.apiClient.startPrintConfirmed(function (responseData) {
                            origStartPrintFunction();
                        });
                    });
            });
        };
        // overwrite loadFile
        self.filesViewModel.loadFile = function confirmSpoolSelectionOnLoadAndPrint(
            data,
            printAfterLoad
        ) {
            // orig. SourceCode
            if (
                !self.filesViewModel.loginState.hasPermission(
                    self.filesViewModel.access.permissions.FILES_SELECT
                )
            )
                return;

            if (!data) {
                return;
            }

            if (
                printAfterLoad &&
                self.filesViewModel.listHelper.isSelected(data) &&
                self.filesViewModel.enablePrint(data)
            ) {
                // file was already selected, just start the print job with the newStartPrint function
                // SPOOLMANAGER-CHANGE changed OctoPrint.job.start();
                newStartPrintFunction();
            } else {
                // select file, start print job (if requested and within dimensions)
                var withinPrintDimensions = self.filesViewModel.evaluatePrintDimensions(
                    data,
                    true
                );
                var print = printAfterLoad && withinPrintDimensions;

                if (
                    print &&
                    self.filesViewModel.settingsViewModel.feature_printStartConfirmation()
                ) {
                    showConfirmationDialog({
                        message: gettext(
                            "This will start a new print job. Please check that the print bed is clear."
                        ),
                        question: gettext("Do you want to start the print job now?"),
                        cancel: gettext("No"),
                        proceed: gettext("Yes"),
                        onproceed: function () {
                            OctoPrint.files
                                .select(data.origin, data.path, false)
                                .done(function () {
                                    if (print) {
                                        newStartPrintFunction();
                                    }
                                });
                        },
                        nofade: true
                    });
                } else {
                    OctoPrint.files
                        .select(data.origin, data.path, false)
                        .done(function () {
                            if (print) {
                                newStartPrintFunction();
                            }
                        });
                }
            }
        };

        self.printerStateViewModel.print = newStartPrintFunction;

        //////////////////////////////////////////////////////////////////////////////////////// PUBLIC VIEWMODEL - APIs
        // e.g. for CostEstaminator-Plugin
        self.api_getSelectedSpoolInformations = function () {
            var result = [];
            var spoolItem;
            for (var i = 0; i < self.selectedSpoolsForSidebar().length; i++) {
                var spoolData = null;
                spoolItem = self.selectedSpoolsForSidebar()[i]();
                if (spoolItem !== null) {
                    spoolData = {
                        toolIndex: i,
                        databaseId: spoolItem.databaseId(),
                        spoolName: spoolItem.displayName(),
                        vendor: spoolItem.vendor(),
                        material: spoolItem.material(),
                        diameter: spoolItem.diameter(),
                        density: spoolItem.density(),
                        colorName: spoolItem.colorName(),
                        color: spoolItem.color(),
                        cost: spoolItem.cost(),
                        weight: spoolItem.totalWeight()
                    };
                }
                result.push(spoolData);
            }
            return result;
        };

        //////////////////////////////////////////////////////////////////////////////////////////////// OCTOPRINT HOOKS
        self.onStartup = function onStartupCallback() {
            // Replace Filementview in sidebar to show weight instead of volumne
            self.replaceFilamentView();
        };

        self.onBeforeBinding = function () {
            // Register Knockout Components
            new SpoolSelectionTableComp().registerSpoolSelectionTableComp();

            // assign current pluginSettings
            self.pluginSettings = self.settingsViewModel.settings.plugins[PLUGIN_ID];

            // Lazy table loading (issue mdziekon#5): defer the first table fetch until the
            // SpoolManager tab is shown. Must be set before loadSettingsFromBrowserStore(),
            // because restoring the page size already triggers a table load.
            if (
                self.pluginSettings.performanceLazyLoadSpoolTable &&
                self.pluginSettings.performanceLazyLoadSpoolTable() == true
            ) {
                self.spoolItemTableHelper.isLoadingEnabled = false;
            }

            // load browser stored settings (includs TabelVisibility and pageSize, ...)
            loadSettingsFromBrowserStore();

            // resetSettings-Stuff
            new ResetSettingsUtilV3(self.pluginSettings).assignResetSettingsFeature(
                PLUGIN_ID,
                function (data) {
                    // the reset writes straight into pluginSettings, so push the new values back into
                    // the pickers (they render themselves from their observable)
                    self.qrCodeFillColor(self.pluginSettings.qrCodeFillColor());
                    self.qrCodeBackgroundColor(
                        self.pluginSettings.qrCodeBackgroundColor()
                    );
                }
            );

            // Load sidebar data (selected spools always; full selector list only when not lazy)
            self.loadSidebarSpoolWidgetsData();
            // Edit Spool Dialog Binding
            self.spoolDialog.initBinding(
                self.apiClient,
                self.pluginSettings,
                self.printerProfilesViewModel,
                self.printerStateViewModel
            );
            self.addSpoolWizard.initBinding(self.apiClient, self.pluginSettings);
            // Import Dialog
            self.csvImportDialog.init(self.apiClient);
            // Database connection problem dialog
            self.databaseConnectionProblemDialog.init(self.apiClient);
            // Select Spool Dialog (no special binding)
            self.selectionSpoolDialog = $("#dialog_spool_selection");

            // Settings - Color-Picker
            // (on self, not this: the reset-settings callback above reaches for them by that name)
            self.componentFactory = new ComponentFactory();
            var fillColorViewModel = self.componentFactory.createColorPicker(
                "qrcode-fill-color-picker",
                self.pluginSettings.qrCodeFillColor()
            );
            self.qrCodeFillColor = fillColorViewModel.selectedColor;
            self.qrCodeFillColor.subscribe(function (newColorValue) {
                self.pluginSettings.qrCodeFillColor(newColorValue);
            });

            var backgroundColorViewModel = self.componentFactory.createColorPicker(
                "qrcode-background-color-picker",
                self.pluginSettings.qrCodeBackgroundColor()
            );
            self.qrCodeBackgroundColor = backgroundColorViewModel.selectedColor;
            self.qrCodeBackgroundColor.subscribe(function (newColorValue) {
                self.pluginSettings.qrCodeBackgroundColor(newColorValue);
            });

            // needed after the tool-count is changed
            self.settingsViewModel.printerProfiles.currentProfileData.subscribe(
                function () {
                    self.updateAvailableSpoolSlots();
                }
            );
        };

        self.onAfterBinding = function () {
            self.spoolDialog.afterBinding();
            self.addSpoolWizard.afterBinding();
            self.downloadDatabaseUrl(self.apiClient.getDownloadDatabaseUrl());

            // testing            self.spoolDialog.showDialog(null, closeDialogHandler);
        };

        self.onSettingsShown = function () {
            if (
                self.isFilamentManagerPluginAvailable() == false ||
                self.isMqttPluginAvailable() == false
            ) {
                self.apiClient.callAdditionalSettings(function (responseData) {
                    self.isFilamentManagerPluginAvailable(
                        responseData.isFilamentManagerPluginAvailable
                    );
                    self.isMqttPluginAvailable(responseData.isMqttPluginAvailable);
                });
            }
            self.refreshSpoolmanDbStatus();
            // re-evaluates the detection chain server-side, so the tab always shows the
            // current state (e.g. after the printer connection changed)
            self.loadU1RfidStatus();
        };

        // receive data from server
        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin != PLUGIN_ID) {
                return;
            }

            // NOTE: the backend sends "initialData"; the old "initalData" comparison (typo)
            // made this handler dead code. With lazy table loading this push is the startup
            // source for pluginNotWorking, so the typo had to be fixed.
            if ("initialData" == data.action) {
                self.pluginNotWorking(data.pluginNotWorking);
                self.isFilamentManagerPluginAvailable(
                    data.isFilamentManagerPluginAvailable
                );
                if (data.selectedSpools != null) {
                    self._applySelectedSpoolsData(data.selectedSpools);
                }

                return;
            }
            if ("showPopUp" == data.action) {
                self.showPopUp(data.type, data.title, data.message, data.autoclose);
                return;
            }
            if ("u1RfidUnknownTag" == data.action) {
                self._showU1RfidUnknownTagPopUp(data);
                return;
            }
            if ("u1RfidSpoolSelected" == data.action) {
                self._showU1RfidSelectionPopUp(data);
                return;
            }
            if ("printerFileAnalysisStarted" == data.action) {
                self.showPrinterFileAnalysisStarted(data.path);
                return;
            }
            if ("printerFileAnalysisFinished" == data.action) {
                self.hidePrinterFileAnalysisToast();
                return;
            }
            if ("reloadTable" == data.action) {
                self.spoolItemTableHelper.reloadItems();
                return;
            }
            if ("reloadTable and sidebarSpools" == data.action) {
                self.spoolItemTableHelper.reloadItems();
                self.loadSidebarSpoolWidgetsData();
                return;
            }
            if ("csvImportStatus" == data.action) {
                self.csvImportDialog.updateText(data);
                return;
            }
            if ("errorPopUp" == data.action) {
                self.showPopUp(
                    "error",
                    "ERROR:" + data.title,
                    data.message,
                    data.autoclose
                );
                return;
            }
            if ("requiredFilamentChanged" == data.action) {
                self.updateRequiredFilament(data);
                return;
            }
            if ("extrusionValuesChanged" == data.action) {
                self.updateExtrusionValues(data.extrusionValues);
                return;
            }

            if ("showConnectionProblem" == data.action) {
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
        };

        self.onTabChange = function (next, current) {
            if ("#tab_plugin_SpoolManager" == next) {
                // with lazy table loading this is the first (deferred) load,
                // afterwards it behaves like the previous reloadItems() call
                self.spoolItemTableHelper.enableLoadingAndReload();
            }
            //alert("Next:"+next +" Current:"+current);
            if ("#tab_plugin_PrintJobHistory" == next) {
                //self.reloadTableData();
            }
        };

        // Feedback for the QR-code selection outcome, reported by /selectSpoolByQRCode
        // via the "spmQrStatus" fragment parameter.
        // See https://github.com/mdziekon/OctoPrint-SpoolManager/issues/41 (@mdziekon)
        self._showQRCodeSelectionPopUp = function (status, spoolData) {
            var spoolName =
                spoolData != null && spoolData["displayName"]
                    ? spoolData["displayName"]
                    : "Spool";
            if (status == "printing") {
                // autoclose off: the user needs to understand nothing was selected
                self.showPopUp(
                    "warning",
                    "Spool not selected",
                    "A print is currently running, so '" +
                        spoolName +
                        "' was not selected. Stop the print and scan again.",
                    false
                );
            } else if (status == "notfound") {
                self.showPopUp(
                    "error",
                    "Spool not found",
                    "The scanned spool no longer exists in the database. It may have been deleted.",
                    false
                );
            } else {
                self.showPopUp(
                    "success",
                    "Spool selected",
                    "'" + spoolName + "' is now selected for printing.",
                    true
                );
            }
        };

        // Guards against re-entering the QR handling below: clearing the hash and calling
        // .tab('show') both trigger onAfterTabChange again, which would otherwise start a
        // second run (and open a second dialog) while the first one is still in flight.
        self._qrCodeSelectionInProgress = false;

        self.onAfterTabChange = function (current, previous) {
            var tabHashCode = window.location.hash;
            // QR-Code-Call: We can only contain -spoolId on the very first page.
            // The hash carries only the spool id (#tab_plugin_SpoolManager-spoolId<id>);
            // the optional outcome rides in a real query string (?spmQrStatus=<status>),
            // NOT inside the fragment - a "?" in the hash breaks OctoPrint's startup and
            // leaves the UI stuck on "Loading OctoPrint's UI".
            var qrCodeMatch = /^#tab_plugin_SpoolManager-spoolId(\d+)/.exec(tabHashCode);
            if (qrCodeMatch != null && self._qrCodeSelectionInProgress == false) {
                self._qrCodeSelectionInProgress = true;
                var selectedSpoolId = parseInt(qrCodeMatch[1]);
                // older QR codes/bookmarks carry no status -> assume the selection worked
                var qrStatus = getUrlParameter("spmQrStatus") || "selected";
                console.info(
                    "Loading spool: " + selectedSpoolId + " (status: " + qrStatus + ")"
                );

                // The spool has already been selected server-side by /selectSpoolByQRCode,
                // which redirected us here. So only fetch it for display instead of
                // selecting it a second time.
                self.apiClient.callLoadSpoolById(
                    selectedSpoolId,
                    function (responseData) {
                        //Select the SpoolManager tab
                        $('a[href="#tab_plugin_SpoolManager"]').tab("show");
                        // Drop both the status query param and the spoolId hash only now that the
                        // tab is settled, otherwise a reload re-runs the whole thing and pops the
                        // same message up again.
                        if (window.history && window.history.replaceState) {
                            window.history.replaceState(
                                null,
                                "",
                                window.location.pathname + "#tab_plugin_SpoolManager"
                            );
                        }
                        self._qrCodeSelectionInProgress = false;
                        var spoolData =
                            responseData != null ? responseData["spool"] : null;
                        self._showQRCodeSelectionPopUp(qrStatus, spoolData);
                        if (spoolData == null) {
                            // spool is gone (deleted in the meantime) -> nothing to show
                            return;
                        }
                        var spoolItem =
                            self.spoolDialog.createSpoolItemForTable(spoolData);
                        // Only true when the server actually performed the selection - the dialog
                        // uses this to disable "Select for printing" as redundant.
                        spoolItem.selectedFromQRCode(qrStatus == "selected");
                        // Reflect the server-side selection in the sidebar instead of assuming
                        // it ended up in tool 0. The dialog is only opened afterwards, because
                        // showSpoolDialogAction() reads selectedSpoolsForSidebar() to determine
                        // isLoadedInTool - opening it earlier would race against this request.
                        // The dialog opens unconditionally now, even mid-print: the popup above
                        // explains the refusal, and the footer buttons guard themselves.
                        self.loadCurrentSelectedSpoolsData(function () {
                            self.showSpoolDialogAction(spoolItem);
                        });
                    }
                );
            }
        };

        self.calculateRemainingPercentage = function (spoolItem) {
            if (!spoolItem.remainingLength() || !spoolItem.totalLength()) {
                return {
                    width: 0,
                    isLow: true
                };
            }
            var percentage =
                (Number(spoolItem.remainingLength()) / Number(spoolItem.totalLength())) *
                100;
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
