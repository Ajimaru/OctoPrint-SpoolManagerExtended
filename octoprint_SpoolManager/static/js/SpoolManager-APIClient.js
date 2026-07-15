

function SpoolManagerAPIClient(pluginId, baseUrl) {

    this.pluginId = pluginId;
    this.baseUrl = baseUrl;

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
        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/createDatabaseBackup",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            type: "PUT"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    // Creates a safety backup of the active database before an import (.db/.sql + best-effort .csv),
    // stored in the plugin data folder. Response: { mandatoryBackupFile, optionalBackupFiles: [...] }.
    // Download each via getDatabaseBackupDownloadUrl(name).
    this.callCreateImportBackup = function (responseHandler){
        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/createImportBackup",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            type: "PUT"
        }).done(function(data){
            responseHandler(true, data);
        }).fail(function(jqXHR){
            responseHandler(false, jqXHR);
        });
    }

    //////////////////////////////////////////////////////////////////////////////// IMPORT MYSQL DATABASE DUMP
    this.callImportDatabaseDump = function (file, importMode, responseHandler){
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/importDatabaseDump",
            type: "POST",
            data: formData,
            processData: false,
            contentType: false,
            headers: OctoPrint.getRequestHeaders()
        }).always(function( data ){
            responseHandler(data);
        });
    }

    //////////////////////////////////////////////////////////////////////////////// RESTORE LOCAL .db FILE
    this.callImportDatabaseFile = function (file, importMode, responseHandler){
        var formData = new FormData();
        formData.append("file", file);
        formData.append("importMode", importMode);

        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/importDatabaseFile",
            type: "POST",
            data: formData,
            processData: false,
            contentType: false,
            headers: OctoPrint.getRequestHeaders()
        }).done(function(data){
            responseHandler(true, data);
        }).fail(function(jqXHR){
            responseHandler(false, jqXHR);
        });
    }

    //////////////////////////////////////////////////////////////////////////////// LOAD AdditionalSettingsValues
    this.callAdditionalSettings = function (responseHandler){
        var urlToCall = this.baseUrl + "api/plugin/"+this.pluginId+"?action=additionalSettingsValues";
        $.ajax({
            url: urlToCall,
            type: "GET"
        }).always(function( data ){
            responseHandler(data)
        });
    }
    //////////////////////////////////////////////////////////////////////////////// LOAD DatabaseMetaData
    this.loadDatabaseMetaData = function (responseHandler){
        var urlToCall = this.baseUrl + "plugin/"+this.pluginId+"/loadDatabaseMetaData";
        $.ajax({
            url: urlToCall,
            type: "GET"
        }).always(function( data ){
            responseHandler(data)
        });
    }
    //////////////////////////////////////////////////////////////////////////////// TEST DatabaseConnection
    this.testDatabaseConnection = function (databaseSettings, responseHandler){
        jsonPayload = ko.toJSON(databaseSettings)

        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/testDatabaseConnection",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: jsonPayload,
            type: "PUT"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    //////////////////////////////////////////////////////////////////////////////////// UPGRADE Database Scheme
    this.callUpgradeDatabaseScheme = function (payload, responseHandler){
        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/upgradeDatabaseScheme",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: JSON.stringify(payload || {}),
            type: "PUT"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    //////////////////////////////////////////////////////////////////////////////// CONFIRM DatabaseConnectionPoblem
    this.confirmDatabaseProblemMessage = function (responseHandler){
        $.ajax({
            //url: API_BASEURL + "plugin/"+PLUGIN_ID+"/loadPrintJobHistory",
            url: this.baseUrl + "plugin/" + this.pluginId + "/confirmDatabaseProblemMessage",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            type: "PUT"
        }).always(function( data ){
            responseHandler(data);
        });
    }


    //////////////////////////////////////////////////////////////////////////////// LOAD FILTERED/SORTED PrintJob-Items
    this.callLoadSpoolsByQuery = function (tableQuery, responseHandler){
        query = _buildRequestQuery(tableQuery);
        urlToCall = this.baseUrl + "plugin/"+this.pluginId+"/loadSpoolsByQuery?"+query;
        $.ajax({
            //url: API_BASEURL + "plugin/"+PLUGIN_ID+"/loadPrintJobHistory",
            url: urlToCall,
            type: "GET"
        }).always(function( data ){
            responseHandler(data)
            //shoud be done by the server to make sure the server is informed countdownDialog.modal('hide');
            //countdownDialog.modal('hide');
            //countdownCircle = null;
        });
    }


    ///////////////////////////////////////////////////////////////////////////////////////////////// LOAD NEXT Spool-Id
    this.callLoadNextSpoolId = function (responseHandler){
        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/nextSpoolId",
            type: "GET"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// SAVE Spool-Item
    this.callSaveSpool = function (spoolItem, responseHandler){
        jsonPayload = ko.toJSON(spoolItem)

        $.ajax({
            //url: API_BASEURL + "plugin/"+PLUGIN_ID+"/loadPrintJobHistory",
            url: this.baseUrl + "plugin/" + this.pluginId + "/saveSpool",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: jsonPayload,
            type: "PUT"
        }).done(function( data ){
            responseHandler(true);
        }).fail(function( jqXHR ){
            // server rejected the save (e.g. HTTP 400 with validation errors) - surface it instead of swallowing it
            var validationErrors = null;
            if (jqXHR && jqXHR.responseJSON && jqXHR.responseJSON.validationErrors){
                validationErrors = jqXHR.responseJSON.validationErrors;
            }
            responseHandler(false, validationErrors);
        });
    }

    ////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Spool-Item
    this.callDeleteSpool = function (databaseId, responseHandler){
        $.ajax({
            //url: API_BASEURL + "plugin/"+PLUGIN_ID+"/loadPrintJobHistory",
            url: this.baseUrl + "plugin/" + this.pluginId + "/deleteSpool/" + databaseId,
            type: "DELETE"
        }).always(function( data ){
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
        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/selectSpool",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: JSON.stringify(payload),
            type: "PUT"
        }).always(function( data ){
            responseHandler( data );
        });
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// ALLOWED TO PRINT
    this.allowedToPrint = function (responseHandler){

        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/allowedToPrint",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            type: "GET"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    /////////////////////////////////////////////////////////////////////////////////////////////////// START PRINT CONFIRMED
    this.startPrintConfirmed = function (responseHandler){

        $.ajax({
            url: this.baseUrl + "plugin/" + this.pluginId + "/startPrintConfirmed",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            type: "GET"
        }).always(function( data ){
            responseHandler(data);
        });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// DELETE Database
    this.callDeleteDatabase = function(databaseType, databaseSettings, responseHandler){
        jsonPayload = ko.toJSON(databaseSettings)
        $.ajax({
            //url: API_BASEURL + "plugin/"+PLUGIN_ID+"/loadPrintJobHistory",
            url: this.baseUrl + "plugin/"+this.pluginId+"/deleteDatabase/"+databaseType,
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: jsonPayload,
            type: "POST"
        }).always(function( data ){
            responseHandler(data)
        });
    }

    //////////////////////////////////////////////////////////////////////////////////////////////////// Copy Database
    this.callCopyDatabase = function(databaseSettings, responseHandler) {
        jsonPayload = ko.toJSON(databaseSettings)
        $.ajax({
            url: this.baseUrl + "plugin/"+this.pluginId+"/copyDatabase",
            dataType: "json",
            contentType: "application/json; charset=UTF-8",
            data: jsonPayload,
            type: "POST"
        }).always(function( data ){
            responseHandler(data)
        });
    }

    ////////////////////////////////////////////////////////////////////////////////////////////////// DOWNLOAD Database
    this.getDownloadDatabaseUrl = function(exportType){
        return _addApiKeyIfNecessary("./plugin/" + this.pluginId + "/downloadDatabase");
    }

//    // deactivate the Plugin/Check
//    this.callDeactivatePluginCheck =  function (){
//        $.ajax({
//            url: this.baseUrl + "plugin/"+ this.pluginId +"/deactivatePluginCheck",
//            type: "PUT"
//        }).done(function( data ){
//            //responseHandler(data)
//        });
//    }





}
