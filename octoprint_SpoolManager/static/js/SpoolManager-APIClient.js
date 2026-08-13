function SpoolManagerAPIClient(pluginId, baseUrl) {
    this.pluginId = pluginId;
    this.baseUrl = baseUrl;

    var self = this;

    // see https://gomakethings.com/how-to-build-a-query-string-from-an-object-with-vanilla-js/
    var _buildRequestQuery = function (data) {
        // If the data is already a string, return it as-is
        if (typeof data === "string") return data;

        // Create a query array to hold the key/value pairs
        var query = [];

        // Loop through the data object
        for (var key in data) {
            if (data.hasOwnProperty(key)) {
                // Encode each key and value, concatenate them into a string, and push them to the array
                query.push(encodeURIComponent(key) + "=" + encodeURIComponent(data[key]));
            }
        }
        // Join each item in the array with a `&` and return the resulting string
        return query.join("&");
    };

    var _addApiKeyIfNecessary = function (urlContext) {
        if (UI_API_KEY) {
            urlContext = urlContext + "?apikey=" + UI_API_KEY;
        }
        return urlContext;
    };

    // fetch()-based API layer, approach adopted from mdziekon/OctoPrint-SpoolManager
    // PR #2 (GH-1) and PR #7 (GH-4): a shared callApi() helper replaces the
    // per-method $.ajax calls.
    // The implementation deliberately differs: the callback-style contracts of the
    // existing consumers are preserved instead of porting his Result/safeAsync
    // pattern, so no call site had to change.
    var _buildPluginUrl = function (path) {
        return self.baseUrl + "plugin/" + self.pluginId + "/" + path;
    };

    // OctoPrint injects its auth/CSRF headers (X-CSRF-Token) globally into jQuery's
    // $.ajax; raw fetch() bypasses that, so without these headers all POST/PUT/DELETE
    // requests would fail with HTTP 400 on OctoPrint >= 1.8.3. Content-Type is only
    // set for string bodies; FormData must set its own multipart boundary, so it
    // gets no manual Content-Type.
    var _buildFetchOptions = function (options) {
        var method = options.method || "GET";
        // the method MUST be passed: getRequestHeaders(method, ...) only adds the
        // X-CSRF-Token header for non-GET/HEAD/OPTIONS requests and defaults to "GET"
        var headers = OctoPrint.getRequestHeaders(method);
        if (typeof options.body === "string") {
            headers["Content-Type"] = "application/json; charset=UTF-8";
        }
        return {
            method: method,
            headers: headers,
            body: options.body, // undefined for GET/bodyless requests
            credentials: "same-origin" // match jQuery: send the session cookie
        };
    };

    // fetch()-based replacement for $.ajax that preserves the old callback contracts.
    // onDone(parsedBody, response) fires like jQuery's .always() for any completed
    // request; when onFail(parsedBody, rawText, response) is provided, onDone only
    // fires for response.ok and onFail handles error statuses. Network-level failures
    // still invoke the callback (with undefined body), so busy indicators and error
    // handling in the consumers never silently stall.
    var _callApi = function (url, options, onDone, onFail) {
        fetch(url, _buildFetchOptions(options))
            .then(function (response) {
                return response.text().then(function (rawText) {
                    var body;
                    try {
                        body = rawText ? JSON.parse(rawText) : undefined;
                    } catch (error) {
                        body = undefined;
                    }
                    if (!response.ok && onFail) {
                        onFail(body, rawText, response);
                    } else {
                        onDone(body, response);
                    }
                });
            })
            .catch(function (error) {
                console.error("SpoolManager API call failed: " + url, error);
                if (onFail) {
                    onFail(undefined, "", undefined);
                } else {
                    onDone(undefined, undefined);
                }
            });
    };

    var _spoolmanDbRequestCache = {};
    var _callSpoolmanDb = function (path, callback) {
        if (_spoolmanDbRequestCache[path]) {
            _spoolmanDbRequestCache[path].push(callback);
            return;
        }
        _spoolmanDbRequestCache[path] = [callback];
        _callApi(
            _buildPluginUrl(path),
            {method: "GET"},
            function (data) {
                var callbacks = _spoolmanDbRequestCache[path] || [];
                delete _spoolmanDbRequestCache[path];
                callbacks.forEach(function (handler) {
                    handler(data || {enabled: false, status: "error"});
                });
            },
            function (body) {
                var callbacks = _spoolmanDbRequestCache[path] || [];
                delete _spoolmanDbRequestCache[path];
                callbacks.forEach(function (handler) {
                    handler(body || {enabled: false, status: "error"});
                });
            }
        );
    };

    this.getExportUrl = function (exportType, databaseInUse) {
        return _addApiKeyIfNecessary(
            "./plugin/" +
                this.pluginId +
                "/exportSpools/" +
                exportType +
                "?instance=" +
                databaseInUse
        );
    };

    this.getInventoryReportUrl = function (tableQuery, databaseInUse, reportFormat) {
        var params = ["instance=" + encodeURIComponent(databaseInUse)];
        params.push("format=" + encodeURIComponent(reportFormat || "pdf"));
        if (tableQuery) {
            var passThrough = ["sortColumn", "sortOrder", "filterName", "textFilter"];
            passThrough.forEach(function (key) {
                if (tableQuery[key] != null) {
                    params.push(key + "=" + encodeURIComponent(tableQuery[key]));
                }
            });
            // Array filters (materialFilter/vendorFilter/colorFilter) are joined by comma,
            // matching how the backend reads them via flask.request.values.
            // Always send all three: the backend accesses them together and would
            // KeyError if only some are present (see _applyTableQueryFilters).
            ["materialFilter", "vendorFilter", "colorFilter"].forEach(function (key) {
                var value = tableQuery[key];
                if (value == null) {
                    value = "all";
                } else if (Array.isArray(value)) {
                    value = value.join(",");
                }
                params.push(key + "=" + encodeURIComponent(value));
            });
        }
        return _addApiKeyIfNecessary(
            "./plugin/" + this.pluginId + "/exportInventoryReport?" + params.join("&")
        );
    };

    this.getSampleCSVUrl = function () {
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/sampleCSV");
    };

    this.getDatabaseDumpExportUrl = function () {
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/exportDatabaseDump");
    };

    this.getDatabaseBackupDownloadUrl = function (backupFileName) {
        return _addApiKeyIfNecessary(
            "./plugin/" +
                this.pluginId +
                "/downloadDatabaseBackup?fileName=" +
                encodeURIComponent(backupFileName)
        );
    };

    this.callCreateDatabaseBackup = function (responseHandler) {
        _callApi(
            _buildPluginUrl("createDatabaseBackup"),
            {method: "PUT"},
            function (data) {
                responseHandler(data);
            }
        );
    };

    // Creates a safety backup of the active database before an import (.db/.sql + best-effort .csv),
    // stored in the plugin data folder. Response: { mandatoryBackupFile, optionalBackupFiles: [...] }.
    // Download each via getDatabaseBackupDownloadUrl(name).
    this.callCreateImportBackup = function (responseHandler) {
        _callApi(
            _buildPluginUrl("createImportBackup"),
            {method: "PUT"},
            function (data) {
                responseHandler(true, data);
            },
            function (body, rawText) {
                // consumers read .responseText (formerly jqXHR) for the error message
                responseHandler(false, body || {responseText: rawText});
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////// IMPORT MYSQL DATABASE DUMP
    this.callImportDatabaseDump = function (file, importMode, responseHandler) {
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        _callApi(
            _buildPluginUrl("importDatabaseDump"),
            {method: "POST", body: formData},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////// RESTORE LOCAL .db FILE
    this.callImportDatabaseFile = function (file, importMode, responseHandler) {
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        _callApi(
            _buildPluginUrl("importDatabaseFile"),
            {method: "POST", body: formData},
            function (data) {
                responseHandler(true, data);
            },
            function (body, rawText) {
                // consumers read .errorMessage (JSON) or .responseText (formerly jqXHR)
                responseHandler(false, body || {responseText: rawText});
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////// LOAD AdditionalSettingsValues
    this.callAdditionalSettings = function (responseHandler) {
        var urlToCall =
            this.baseUrl +
            "api/plugin/" +
            this.pluginId +
            "?action=additionalSettingsValues";
        _callApi(urlToCall, {method: "GET"}, function (data) {
            responseHandler(data);
        });
    };
    //////////////////////////////////////////////////////////////////////////////// LOAD DatabaseMetaData
    this.loadDatabaseMetaData = function (responseHandler) {
        _callApi(
            _buildPluginUrl("loadDatabaseMetaData"),
            {method: "GET"},
            function (data) {
                responseHandler(data);
            }
        );
    };
    //////////////////////////////////////////////////////////////////////////////// TEST DatabaseConnection
    this.testDatabaseConnection = function (databaseSettings, responseHandler) {
        var jsonPayload = ko.toJSON(databaseSettings);

        _callApi(
            _buildPluginUrl("testDatabaseConnection"),
            {method: "PUT", body: jsonPayload},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////////// UPGRADE Database Scheme
    this.callUpgradeDatabaseScheme = function (payload, responseHandler) {
        _callApi(
            _buildPluginUrl("upgradeDatabaseScheme"),
            {method: "PUT", body: JSON.stringify(payload || {})},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////// CONFIRM DatabaseConnectionPoblem
    this.confirmDatabaseProblemMessage = function (responseHandler) {
        _callApi(
            _buildPluginUrl("confirmDatabaseProblemMessage"),
            {method: "PUT"},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////// LOAD FILTERED/SORTED PrintJob-Items
    this.callLoadSpoolsByQuery = function (tableQuery, responseHandler) {
        var query = _buildRequestQuery(tableQuery);
        _callApi(
            _buildPluginUrl("loadSpoolsByQuery?" + query),
            {method: "GET"},
            function (data) {
                responseHandler(data);
            }
        );
    };

    ///////////////////////////////////////////////////////////////////////////////////////////// LOAD SELECTED Spools
    this.callLoadSelectedSpools = function (responseHandler) {
        _callApi(_buildPluginUrl("loadSelectedSpools"), {method: "GET"}, function (data) {
            responseHandler(data);
        });
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////// LOAD Spool by Id
    this.callLoadSpoolById = function (databaseId, responseHandler) {
        _callApi(
            _buildPluginUrl("spool/" + databaseId),
            {method: "GET"},
            function (data) {
                responseHandler(data);
            }
        );
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////// LOAD NEXT Spool-Id
    this.callLoadNextSpoolId = function (responseHandler) {
        _callApi(_buildPluginUrl("nextSpoolId"), {method: "GET"}, function (data) {
            responseHandler(data);
        });
    };

    //////////////////////////////////////////////////////////////////////////////////////////////////// SAVE Spool-Item
    this.callSaveSpool = function (spoolItem, responseHandler) {
        var jsonPayload = ko.toJSON(spoolItem);

        _callApi(
            _buildPluginUrl("saveSpool"),
            {method: "PUT", body: jsonPayload},
            function (data) {
                responseHandler(true);
            },
            function (body, rawText, response) {
                // server rejected the save (e.g. HTTP 400 with validation errors) - surface it instead of swallowing it
                var validationErrors = null;
                if (body && body.validationErrors) {
                    validationErrors = body.validationErrors;
                }
                // HTTP 409 = someone else changed (or deleted) the spool while it was open in
                // the dialog. Passed on separately so the caller can offer a real choice
                // instead of just reporting a failure.
                var conflict = null;
                if (response && response.status === 409 && body) {
                    conflict = {
                        type: body.conflict, // "version" or "deleted"
                        message: body.error,
                        currentSpool: body.spool // undefined for "deleted"
                    };
                }
                responseHandler(false, validationErrors, conflict);
            }
        );
    };

    ////////////////////////////////////////////////////////////////////////////////////////////////// CREATE Spool-Item
    // Unlike callSaveSpool this answers with the new database id, which the wizard needs to
    // write an NFC tag right after creating the spool.
    this.callCreateSpool = function (spoolItem, responseHandler) {
        var jsonPayload = ko.toJSON(spoolItem);

        _callApi(
            _buildPluginUrl("spool"),
            {method: "POST", body: jsonPayload},
            function (data) {
                responseHandler(true, data);
            },
            function (body, rawText, response) {
                var validationErrors = null;
                if (body && body.validationErrors) {
                    validationErrors = body.validationErrors;
                } else {
                    // No JSON body: usually OctoPrint itself rejecting the request before the
                    // plugin sees it. The most common case is an expired session, whose CSRF
                    // token check fails with a plain HTTP 400 - without naming it here the UI
                    // could only report "could not be created" and the user has no way to guess
                    // that reloading the page fixes it.
                    var status = response ? response.status : 0;
                    if (status === 400 || status === 403) {
                        validationErrors = [
                            "The request was rejected by OctoPrint (HTTP " +
                                status +
                                "). This usually means the browser session expired - please reload the page and try again."
                        ];
                    } else if (status !== 0) {
                        validationErrors = [
                            "The server answered with HTTP " +
                                status +
                                (rawText ? ": " + ("" + rawText).substring(0, 200) : "")
                        ];
                    } else {
                        validationErrors = ["No connection to OctoPrint."];
                    }
                }
                responseHandler(false, body, validationErrors);
            }
        );
    };

    /////////////////////////////////////////////////////////////////////////////////////////////////////////// OCTOSCALE
    // All OctoScale calls are proxied through the plugin backend: the device speaks plain HTTP
    // without CORS headers, so the browser cannot reach it directly from an HTTPS OctoPrint.
    // Every handler gets (data) where data.success tells whether the device answered.

    this.testOctoScaleConnection = function (octoScaleUrl, responseHandler) {
        var jsonPayload = JSON.stringify({octoScaleUrl: octoScaleUrl});

        _callApi(
            _buildPluginUrl("octoscale/testConnection"),
            {method: "PUT", body: jsonPayload},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {
                        success: false,
                        error: rawText || "The connection test failed."
                    }
                );
            }
        );
    };

    this.getOctoScaleWeight = function (responseHandler) {
        _callApi(
            _buildPluginUrl("octoscale/weight"),
            {method: "GET"},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {
                        success: false,
                        error: rawText || "Could not read the weight."
                    }
                );
            }
        );
    };

    this.tareOctoScale = function (responseHandler) {
        _callApi(
            _buildPluginUrl("octoscale/tare"),
            {method: "POST"},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {
                        success: false,
                        error: rawText || "Could not tare the scale."
                    }
                );
            }
        );
    };

    this.getOctoScaleNfcStatus = function (responseHandler) {
        _callApi(
            _buildPluginUrl("octoscale/nfc"),
            {method: "GET"},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {
                        success: false,
                        error: rawText || "Could not read the NFC status."
                    }
                );
            }
        );
    };

    // No tagFormat parameter anymore: the firmware picks the format from the tag actually
    // on the reader and reports which one it used via getOctoScaleWriteStatus below. This
    // call only starts the write (device answers 202) - it does not wait for the result.
    this.writeOctoScaleTag = function (databaseId, responseHandler) {
        var payload = {databaseId: databaseId};

        _callApi(
            _buildPluginUrl("octoscale/writeTag"),
            {method: "POST", body: JSON.stringify(payload)},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {success: false, error: rawText || "Could not write the tag."}
                );
            }
        );
    };

    // Polls the result of a write started via writeOctoScaleTag. Returns
    // {success, pending, done, ok, error, format, bytesWritten, droppedFields, warning}. Stop
    // polling once done=true - the device self-clears its status after being read once.
    this.getOctoScaleWriteStatus = function (responseHandler) {
        _callApi(
            _buildPluginUrl("octoscale/writeStatus"),
            {method: "GET"},
            function (data) {
                responseHandler(
                    data || {success: false, error: "No answer from the plugin backend."}
                );
            },
            function (body, rawText) {
                responseHandler(
                    body || {
                        success: false,
                        error: rawText || "Could not read the write status."
                    }
                );
            }
        );
    };

    ////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Spool-Item
    this.callDeleteSpool = function (databaseId, responseHandler) {
        _callApi(
            _buildPluginUrl("deleteSpool/" + databaseId),
            {method: "DELETE"},
            function (data) {
                responseHandler();
            }
        );
    };

    ////////////////////////////////////////////////////////////////////////////////////////////////// SELECT Spool-Item
    this.callSelectSpool = function (
        toolIndex,
        databaseId,
        commitCurrentSpoolValues,
        responseHandler
    ) {
        if (databaseId == null) {
            databaseId = -1;
        }
        var payload = {
            databaseId: databaseId,
            toolIndex: toolIndex
        };
        if (commitCurrentSpoolValues !== undefined) {
            payload.commitCurrentSpoolValues = commitCurrentSpoolValues;
        }
        _callApi(
            _buildPluginUrl("selectSpool"),
            {method: "PUT", body: JSON.stringify(payload)},
            function (data) {
                responseHandler(data);
            }
        );
    };

    /////////////////////////////////////////////////////////////////////////////////////////////////// ALLOWED TO PRINT
    this.allowedToPrint = function (responseHandler) {
        _callApi(_buildPluginUrl("allowedToPrint"), {method: "GET"}, function (data) {
            responseHandler(data);
        });
    };

    /////////////////////////////////////////////////////////////////////////////////////////////////// START PRINT CONFIRMED
    this.startPrintConfirmed = function (responseHandler) {
        _callApi(
            _buildPluginUrl("startPrintConfirmed"),
            {method: "GET"},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Database
    this.callDeleteDatabase = function (databaseType, databaseSettings, responseHandler) {
        var jsonPayload = ko.toJSON(databaseSettings);
        _callApi(
            _buildPluginUrl("deleteDatabase/" + databaseType),
            {method: "POST", body: jsonPayload},
            function (data) {
                responseHandler(data);
            }
        );
    };

    //////////////////////////////////////////////////////////////////////////////////////////////////// Copy Database
    this.callCopyDatabase = function (databaseSettings, responseHandler) {
        var jsonPayload = ko.toJSON(databaseSettings);
        _callApi(
            _buildPluginUrl("copyDatabase"),
            {method: "POST", body: jsonPayload},
            function (data) {
                responseHandler(data);
            }
        );
    };

    ////////////////////////////////////////////////////////////////////////////////////////////////// DOWNLOAD Database
    this.getDownloadDatabaseUrl = function (exportType) {
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/downloadDatabase");
    };

    this.getSpoolmanDbVendors = function (responseHandler) {
        _callSpoolmanDb("spoolmanDbVendors", responseHandler);
    };

    ////////////////////////////////////////////////////////////////////////////////// U1 RFID

    // Detection-chain status of the Snapmaker U1 RFID reader (settings display).
    this.getU1RfidStatus = function (responseHandler) {
        _callApi(
            _buildPluginUrl("u1Rfid/status"),
            {method: "GET"},
            function (data) {
                responseHandler(data || {supported: false});
            },
            function (body) {
                responseHandler(body || {supported: false});
            }
        );
    };

    // "Test connection" button - re-runs the chain and lists all channels.
    this.testU1RfidConnection = function (responseHandler) {
        _callApi(
            _buildPluginUrl("u1Rfid/test"),
            {method: "POST"},
            function (data) {
                responseHandler(data || {ok: false, message: "No response"});
            },
            function (body) {
                responseHandler(body || {ok: false, message: "Request failed"});
            }
        );
    };

    // Last unknown tag UIDs per channel, for the "adopt UID" button in the edit dialog.
    this.getU1RfidUnknownTags = function (responseHandler) {
        _callApi(
            _buildPluginUrl("u1Rfid/unknownTags"),
            {method: "GET"},
            function (data) {
                responseHandler(data || {});
            },
            function () {
                responseHandler({});
            }
        );
    };

    this.getSpoolmanDbMaterials = function (vendor, responseHandler) {
        _callSpoolmanDb(
            "spoolmanDbMaterials?" + _buildRequestQuery({vendor: vendor}),
            responseHandler
        );
    };

    this.getSpoolmanDbProducts = function (vendor, material, responseHandler) {
        _callSpoolmanDb(
            "spoolmanDbProducts?" +
                _buildRequestQuery({vendor: vendor, material: material}),
            responseHandler
        );
    };

    this.refreshSpoolmanDb = function (responseHandler) {
        _callApi(
            _buildPluginUrl("spoolmanDbRefresh"),
            {method: "POST"},
            function (data) {
                _spoolmanDbRequestCache = {};
                responseHandler(data || {enabled: false, status: "error"});
            },
            function (body) {
                responseHandler(body || {enabled: false, status: "error"});
            }
        );
    };
}
