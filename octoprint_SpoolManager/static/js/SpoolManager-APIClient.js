

function SpoolManagerAPIClient(pluginId, baseUrl) {

    this.pluginId = pluginId;
    this.baseUrl = baseUrl;

    var self = this;

    // see https://gomakethings.com/how-to-build-a-query-string-from-an-object-with-vanilla-js/
    var _buildRequestQuery = function (data) {
        // If the data is already a string, return it as-is
        if (typeof (data) === 'string') return data;

        // Create a query array to hold the key/value pairs
        var query = [];

        // Loop through the data object
        for (var key in data) {
            if (data.hasOwnProperty(key)) {

                // Encode each key and value, concatenate them into a string, and push them to the array
                query.push(encodeURIComponent(key) + '=' + encodeURIComponent(data[key]));
            }
        }
        // Join each item in the array with a `&` and return the resulting string
        return query.join('&');

    };

    var _addApiKeyIfNecessary = function(urlContext){
        if (UI_API_KEY){
            urlContext = urlContext + "?apikey=" + UI_API_KEY;
        }
        return urlContext;
    }

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
            body: options.body,          // undefined for GET/bodyless requests
            credentials: "same-origin"   // match jQuery: send the session cookie
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

    this.getExportUrl = function(exportType, databaseInUse){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/exportSpools/" + exportType + "?instance=" + databaseInUse);
    }

    this.getInventoryReportUrl = function(tableQuery, databaseInUse, reportFormat){
        var params = ["instance=" + encodeURIComponent(databaseInUse)];
        params.push("format=" + encodeURIComponent(reportFormat || "pdf"));
        if (tableQuery){
            var passThrough = ["sortColumn", "sortOrder", "filterName", "textFilter"];
            passThrough.forEach(function(key){
                if (tableQuery[key] != null){
                    params.push(key + "=" + encodeURIComponent(tableQuery[key]));
                }
            });
            // Array filters (materialFilter/vendorFilter/colorFilter) are joined by comma,
            // matching how the backend reads them via flask.request.values.
            // Always send all three: the backend accesses them together and would
            // KeyError if only some are present (see _applyTableQueryFilters).
            ["materialFilter", "vendorFilter", "colorFilter"].forEach(function(key){
                var value = tableQuery[key];
                if (value == null){
                    value = "all";
                } else if (Array.isArray(value)){
                    value = value.join(",");
                }
                params.push(key + "=" + encodeURIComponent(value));
            });
        }
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/exportInventoryReport?" + params.join("&"));
    }

    this.getSampleCSVUrl = function(){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/sampleCSV");
    }

    this.getDatabaseDumpExportUrl = function(){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/exportDatabaseDump");
    }

    this.getDatabaseBackupDownloadUrl = function(backupFileName){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/downloadDatabaseBackup?fileName=" + encodeURIComponent(backupFileName));
    }

    this.callCreateDatabaseBackup = function (responseHandler){
        _callApi(_buildPluginUrl("createDatabaseBackup"), { method: "PUT" },
            function( data ){
                responseHandler(data);
            });
    }

    // Creates a safety backup of the active database before an import (.db/.sql + best-effort .csv),
    // stored in the plugin data folder. Response: { mandatoryBackupFile, optionalBackupFiles: [...] }.
    // Download each via getDatabaseBackupDownloadUrl(name).
    this.callCreateImportBackup = function (responseHandler){
        _callApi(_buildPluginUrl("createImportBackup"), { method: "PUT" },
            function( data ){
                responseHandler(true, data);
            },
            function( body, rawText ){
                // consumers read .responseText (formerly jqXHR) for the error message
                responseHandler(false, body || { responseText: rawText });
            });
    }

    //////////////////////////////////////////////////////////////////////////////// IMPORT MYSQL DATABASE DUMP
    this.callImportDatabaseDump = function (file, importMode, responseHandler){
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        _callApi(_buildPluginUrl("importDatabaseDump"), { method: "POST", body: formData },
            function( data ){
                responseHandler(data);
            });
    }

    //////////////////////////////////////////////////////////////////////////////// RESTORE LOCAL .db FILE
    this.callImportDatabaseFile = function (file, importMode, responseHandler){
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        _callApi(_buildPluginUrl("importDatabaseFile"), { method: "POST", body: formData },
            function( data ){
                responseHandler(true, data);
            },
            function( body, rawText ){
                // consumers read .errorMessage (JSON) or .responseText (formerly jqXHR)
                responseHandler(false, body || { responseText: rawText });
            });
    }

    //////////////////////////////////////////////////////////////////////////////// LOAD AdditionalSettingsValues
    this.callAdditionalSettings = function (responseHandler){
        var urlToCall = this.baseUrl + "api/plugin/"+this.pluginId+"?action=additionalSettingsValues";
        _callApi(urlToCall, { method: "GET" },
            function( data ){
                responseHandler(data)
            });
    }
    //////////////////////////////////////////////////////////////////////////////// LOAD DatabaseMetaData
    this.loadDatabaseMetaData = function (responseHandler){
        _callApi(_buildPluginUrl("loadDatabaseMetaData"), { method: "GET" },
            function( data ){
                responseHandler(data)
            });
    }
    //////////////////////////////////////////////////////////////////////////////// TEST DatabaseConnection
    this.testDatabaseConnection = function (databaseSettings, responseHandler){
        var jsonPayload = ko.toJSON(databaseSettings);

        _callApi(_buildPluginUrl("testDatabaseConnection"), { method: "PUT", body: jsonPayload },
            function( data ){
                responseHandler(data);
            });
    }

    //////////////////////////////////////////////////////////////////////////////////// UPGRADE Database Scheme
    this.callUpgradeDatabaseScheme = function (payload, responseHandler){
        _callApi(_buildPluginUrl("upgradeDatabaseScheme"), { method: "PUT", body: JSON.stringify(payload || {}) },
            function( data ){
                responseHandler(data);
            });
    }

    //////////////////////////////////////////////////////////////////////////////// CONFIRM DatabaseConnectionPoblem
    this.confirmDatabaseProblemMessage = function (responseHandler){
        _callApi(_buildPluginUrl("confirmDatabaseProblemMessage"), { method: "PUT" },
            function( data ){
                responseHandler(data);
            });
    }


    //////////////////////////////////////////////////////////////////////////////// LOAD FILTERED/SORTED PrintJob-Items
    this.callLoadSpoolsByQuery = function (tableQuery, responseHandler){
        var query = _buildRequestQuery(tableQuery);
        _callApi(_buildPluginUrl("loadSpoolsByQuery?" + query), { method: "GET" },
            function( data ){
                responseHandler(data)
            });
    }


    ///////////////////////////////////////////////////////////////////////////////////////////// LOAD SELECTED Spools
    this.callLoadSelectedSpools = function (responseHandler){
        _callApi(_buildPluginUrl("loadSelectedSpools"), { method: "GET" },
            function( data ){
                responseHandler(data);
            });
    }

    ///////////////////////////////////////////////////////////////////////////////////////////////// LOAD Spool by Id
    this.callLoadSpoolById = function (databaseId, responseHandler){
        _callApi(_buildPluginUrl("spool/" + databaseId), { method: "GET" },
            function( data ){
                responseHandler(data);
            });
    }

    ///////////////////////////////////////////////////////////////////////////////////////////////// LOAD NEXT Spool-Id
    this.callLoadNextSpoolId = function (responseHandler){
        _callApi(_buildPluginUrl("nextSpoolId"), { method: "GET" },
            function( data ){
                responseHandler(data);
            });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// SAVE Spool-Item
    this.callSaveSpool = function (spoolItem, responseHandler){
        var jsonPayload = ko.toJSON(spoolItem);

        _callApi(_buildPluginUrl("saveSpool"), { method: "PUT", body: jsonPayload },
            function( data ){
                responseHandler(true);
            },
            function( body, rawText, response ){
                // server rejected the save (e.g. HTTP 400 with validation errors) - surface it instead of swallowing it
                var validationErrors = null;
                if (body && body.validationErrors){
                    validationErrors = body.validationErrors;
                }
                // HTTP 409 = someone else changed (or deleted) the spool while it was open in
                // the dialog. Passed on separately so the caller can offer a real choice
                // instead of just reporting a failure.
                var conflict = null;
                if (response && response.status === 409 && body){
                    conflict = {
                        type: body.conflict,          // "version" or "deleted"
                        message: body.error,
                        currentSpool: body.spool      // undefined for "deleted"
                    };
                }
                responseHandler(false, validationErrors, conflict);
            });
    }

    ////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Spool-Item
    this.callDeleteSpool = function (databaseId, responseHandler){
        _callApi(_buildPluginUrl("deleteSpool/" + databaseId), { method: "DELETE" },
            function( data ){
                responseHandler();
            });
    }

    ////////////////////////////////////////////////////////////////////////////////////////////////// SELECT Spool-Item
    this.callSelectSpool = function (toolIndex, databaseId, commitCurrentSpoolValues, responseHandler){
        if (databaseId == null){
            databaseId = -1;
        }
        var payload = {
            databaseId: databaseId,
            toolIndex: toolIndex,
        }
        if (commitCurrentSpoolValues !== undefined) {
            payload.commitCurrentSpoolValues = commitCurrentSpoolValues;
        }
        _callApi(_buildPluginUrl("selectSpool"), { method: "PUT", body: JSON.stringify(payload) },
            function( data ){
                responseHandler( data );
            });
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// ALLOWED TO PRINT
    this.allowedToPrint = function (responseHandler){
        _callApi(_buildPluginUrl("allowedToPrint"), { method: "GET" },
            function( data ){
                responseHandler(data);
            });
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// START PRINT CONFIRMED
    this.startPrintConfirmed = function (responseHandler){
        _callApi(_buildPluginUrl("startPrintConfirmed"), { method: "GET" },
            function( data ){
                responseHandler(data);
            });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Database
    this.callDeleteDatabase = function(databaseType, databaseSettings, responseHandler){
        var jsonPayload = ko.toJSON(databaseSettings);
        _callApi(_buildPluginUrl("deleteDatabase/" + databaseType), { method: "POST", body: jsonPayload },
            function( data ){
                responseHandler(data)
            });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// Copy Database
    this.callCopyDatabase = function(databaseSettings, responseHandler) {
        var jsonPayload = ko.toJSON(databaseSettings);
        _callApi(_buildPluginUrl("copyDatabase"), { method: "POST", body: jsonPayload },
            function( data ){
                responseHandler(data)
            });
    }

    ////////////////////////////////////////////////////////////////////////////////////////////////// DOWNLOAD Database
    this.getDownloadDatabaseUrl = function(exportType){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/downloadDatabase");
    }
}
