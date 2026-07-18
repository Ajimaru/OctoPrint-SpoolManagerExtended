// SpoolItem extraction adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10):
// the item view model no longer lives inside the edit dialog and no longer mutates
// dialog state (note editor / autoUpdateEnabled are handled by the dialog itself,
// catalogs are passed in explicitly). The model body carries our local features
// (multi-color, finish, split date/time inputs, QR & tool selection, density
// autosuggest via SpoolmanDB-derived mapping).
// TODO: Figure out better way to export things from closure
let SpoolItem;

(function() {
    /**
     * Note: depends on sources concatenation order as defined in __init__.py & performed by Octoprint.
     * If it ever fails because of architectural change in Octoprint,
     * consider moving these "imports" to a closure ensured to run after everything loads.
     */
    var MATERIALS_DENSITY_MAP = SPOOLMANAGER_CONSTANTS.MATERIALS_DENSITY_MAPPING;
    var FORMAT_DATETIME_LOCAL = SPOOLMANAGER_CONSTANTS.DATES.DISPLAY_FORMATS.DATETIME_LOCAL;
    var FORMAT_DATE = SPOOLMANAGER_CONSTANTS.DATES.DISPLAY_FORMATS.DATE;
    var PARSE_FORMAT_DATETIME = SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME;
    var PARSE_FORMAT_DATE = SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATE;
    var FILAMENT_STATS_CALC_MODES = SPOOLMANAGER_CONSTANTS.FILAMENT_STATS_CALC_MODES;
    var SPOOL_DIALOG_SELECTOR = SPOOLMANAGER_CONSTANTS.DOM_SELECTORS.SPOOL_DIALOG;
    var normalizeMaterialKey = SPOOLMANAGER_UTILS.normalizeMaterialKey;

    var DEFAULT_COLOR = "#ff0000";

    function _getValueOrZero(x) {
        if (!x){
            x = 0
        }
        return parseFloat(x);
    }

    SpoolItem = function(spoolData, params) {
        var isEditable = (params != null) && params.isEditable == true;
        var catalogs = (params != null) ? params.catalogs : null;

        // Init Item

        // if we use the Item for Editing we need to initialise the widget-model as well , e.g. Option-Values, Suggestion-List
        // if we just use this Item in readonly-mode we need simple ko.observer

        this.selectedFromQRCode = ko.observable(false);
        this.selectedForTool = ko.observable(0);    // Default Tool 0
        this.isFilteredForSelection = ko.observable(false);
        // - list all attributes
        this.version = ko.observable();
        this.isSpoolVisible = ko.observable(false);
        this.hasNoData = ko.observable();
        this.databaseId = ko.observable();
        this.isTemplate = ko.observable();
        this.isActive = ko.observable();
        this.isInActive = ko.observable();
        this.displayName = ko.observable();
        this.density = ko.observable();
        this.diameter = ko.observable();
        this.diameterTolerance = ko.observable();
        this.flowRateCompensation = ko.observable();
        this.temperature = ko.observable();
        this.bedTemperature = ko.observable();
        this.enclosureTemperature = ko.observable();
        this.offsetTemperature = ko.observable();
        this.offsetBedTemperature = ko.observable();
        this.offsetEnclosureTemperature = ko.observable();
        this.colorName = ko.observable();
        this.color = ko.observable();
        // "finish" is the persisted value; the dropdown works on finishSelection,
        // free text (selection "custom") on finishCustomText
        this.finishSelection = ko.observable();
        this.finishCustomText = ko.observable();
        this.finish = ko.computed({
            read: function(){
                if (this.finishSelection() === "custom"){
                    return this.finishCustomText();
                }
                return this.finishSelection();
            },
            write: function(value){
                var predefinedFinishes = ["silk", "matt", "marble", "metal", "glow"];
                if (!value){
                    this.finishSelection(undefined);
                    this.finishCustomText(undefined);
                } else if (predefinedFinishes.indexOf(value) !== -1){
                    this.finishSelection(value);
                    this.finishCustomText(undefined);
                } else {
                    this.finishSelection("custom");
                    this.finishCustomText(value);
                }
            },
            owner: this
        });
        this.totalWeight = ko.observable();
        this.spoolWeight = ko.observable();
        this.remainingWeight = ko.observable();
        this.remainingPercentage = ko.observable();
        this.totalLength = ko.observable();
        this.usedLength = ko.observable();
        this.usedLengthPercentage = ko.observable();
        this.remainingLength = ko.observable();
        this.remainingLengthPercentage = ko.observable();
        this.usedWeight = ko.observable();
        this.usedPercentage = ko.observable();
        this.code = ko.observable();
        this.batchNumber = ko.observable();
        this.noteText = ko.observable()
        this.noteDeltaFormat = ko.observable()
        this.noteHtml = ko.observable()

        this.firstUse = ko.observable();
        this.lastUse = ko.observable();
        this.firstUseKO = ko.observable();
        this.lastUseKO = ko.observable();
        this.purchasedOn = ko.observable();
        this.purchasedOnKO = ko.observable();

        // Split date/time bindings for the native inputs. A single datetime-local input
        // reports an empty value until BOTH date and time are filled in, so a date-only
        // entry was silently lost on save. Separate date + time inputs avoid that; a
        // missing time defaults to 00:00.
        var createDatePartKO = function(dateTimeKO){
            return ko.pureComputed({
                read: function(){
                    var value = dateTimeKO();
                    return (value) ? value.split("T")[0] : "";
                },
                write: function(newDate){
                    if (!newDate){
                        dateTimeKO(null);
                        return;
                    }
                    var value = dateTimeKO();
                    var timePart = (value && value.indexOf("T") != -1) ? value.split("T")[1] : "00:00";
                    dateTimeKO(newDate + "T" + timePart);
                }
            });
        };
        var createTimePartKO = function(dateTimeKO){
            return ko.pureComputed({
                read: function(){
                    var value = dateTimeKO();
                    return (value && value.indexOf("T") != -1) ? value.split("T")[1] : "";
                },
                write: function(newTime){
                    var value = dateTimeKO();
                    if (!value){
                        // time without a date is meaningless - wait for a date
                        return;
                    }
                    var datePart = value.split("T")[0];
                    dateTimeKO(datePart + "T" + (newTime ? newTime : "00:00"));
                }
            });
        };
        this.firstUseDateKO = createDatePartKO(this.firstUseKO);
        this.firstUseTimeKO = createTimePartKO(this.firstUseKO);
        this.lastUseDateKO = createDatePartKO(this.lastUseKO);
        this.lastUseTimeKO = createTimePartKO(this.lastUseKO);


        this.purchasedFrom = ko.observable();
        this.cost = ko.observable();
        this.costUnit = ko.observable();

        if (isEditable == true){
            // Editing mode: bind to the dialog's select2/picker widgets.
            // Widget creation only happens here (never for read-only table items) - adopted from
            // mdziekon/OctoPrint-SpoolManager PR #11 (GH-10). Previously every table item re-ran
            // the select2 initialisation on the dialog's DOM nodes, which could reset the
            // editor's vendor/material selection while spools were still loading (race condition).
            var vendorViewModel = ComponentFactory.createSelectWithFilter("spool-vendor-select", $('#spool-form'));
            this.vendor = vendorViewModel.selectedOption;
            this.allVendors = vendorViewModel.allOptions;

            var materialViewModel = ComponentFactory.createSelectWithFilter("spool-material-select", $('#spool-form'));
            this.material = materialViewModel.selectedOption;

            var labelsViewModel = ComponentFactory.createLabels("spool-labels-select", $('#spool-form'));
            this.labels = labelsViewModel.selectedOptions;
            this.allLabels = labelsViewModel.allOptions;

            // Multi-color support (issue #19): "color" holds the composed value
            // ("#hex", "#hex;#hex[;#hex]" or "rainbow"), the pickers hold the parts.
            var spoolItemInstance = this;
            this.colorCount = ko.observable(1);
            this.isRainbow = ko.observable(false);
            this.isTransparent = ko.observable(false);
            // true = transparent without any base tint ("transparent"),
            // false = transparent with a chosen base color ("transparent:#hex")
            this.transparentUntinted = ko.observable(false);
            var colorViewModel = ComponentFactory.createColorPicker("filament-color-picker", true);
            var colorViewModel2 = ComponentFactory.createColorPicker("filament-color-picker2", true);
            var colorViewModel3 = ComponentFactory.createColorPicker("filament-color-picker3", true);
            // picking the "translucent" swatch enables transparent without a base tint
            var activateTransparent = function(){
                spoolItemInstance.transparentUntinted(true);
                spoolItemInstance.isTransparent(true);
            };
            colorViewModel.onTranslucentSelected = activateTransparent;
            colorViewModel2.onTranslucentSelected = activateTransparent;
            colorViewModel3.onTranslucentSelected = activateTransparent;
            var pickerColors = [colorViewModel.selectedColor, colorViewModel2.selectedColor, colorViewModel3.selectedColor];
            var applyingColor = false;
            var composeColor = function(){
                if (applyingColor == true){
                    return;
                }
                if (spoolItemInstance.isRainbow() == true){
                    spoolItemInstance.color("rainbow");
                    return;
                }
                // transparent without a base tint: no hex value at all
                if (spoolItemInstance.isTransparent() == true && spoolItemInstance.transparentUntinted() == true){
                    spoolItemInstance.color("transparent");
                    return;
                }
                var colors = [];
                for (var i = 0; i < spoolItemInstance.colorCount(); i++){
                    colors.push(pickerColors[i]() || DEFAULT_COLOR);
                }
                var composedColor = colors.join(";");
                if (spoolItemInstance.isTransparent() == true){
                    composedColor = "transparent:" + composedColor;
                }
                spoolItemInstance.color(composedColor);
            };
            // picking a real base color after "translucent" turns it into a tinted transparent
            var onBaseColorPicked = function(newValue){
                if (applyingColor == true){
                    // color is being loaded programmatically, keep the untinted flag as set
                    return;
                }
                if (newValue && spoolItemInstance.transparentUntinted() == true){
                    spoolItemInstance.transparentUntinted(false);
                }
                composeColor();
            };
            pickerColors[0].subscribe(onBaseColorPicked);
            pickerColors[1].subscribe(onBaseColorPicked);
            pickerColors[2].subscribe(onBaseColorPicked);
            this.colorCount.subscribe(composeColor);
            this.isRainbow.subscribe(composeColor);
            this.isTransparent.subscribe(composeColor);
            this.transparentUntinted.subscribe(composeColor);
            // unchecking the transparent checkbox also clears the untinted flag
            this.isTransparent.subscribe(function(newValue){
                if (newValue == false){
                    spoolItemInstance.transparentUntinted(false);
                }
            });
            // rainbow and transparent are mutually exclusive
            this.isRainbow.subscribe(function(newValue){
                if (newValue == true && spoolItemInstance.isTransparent() == true){
                    spoolItemInstance.isTransparent(false);
                }
            });
            this.isTransparent.subscribe(function(newValue){
                if (newValue == true && spoolItemInstance.isRainbow() == true){
                    spoolItemInstance.isRainbow(false);
                }
            });
            // pushes a stored color value into the picker widgets/flags
            this.applyColorToEditor = function(colorValue){
                applyingColor = true;
                try {
                    if (("" + colorValue).toLowerCase() === "rainbow"){
                        spoolItemInstance.isRainbow(true);
                        spoolItemInstance.isTransparent(false);
                        spoolItemInstance.transparentUntinted(false);
                        spoolItemInstance.colorCount(1);
                        pickerColors[0](DEFAULT_COLOR);
                    } else {
                        var plainColorValue = "" + colorValue;
                        var transparent = plainColorValue.toLowerCase().indexOf("transparent") === 0;
                        // "transparent" without a ":#hex" suffix = untinted
                        var untinted = false;
                        if (transparent){
                            plainColorValue = plainColorValue.substr("transparent".length);
                            if (plainColorValue.indexOf(":") === 0){
                                plainColorValue = plainColorValue.substr(1);
                            }
                            if (plainColorValue.length === 0){
                                untinted = true;
                                plainColorValue = DEFAULT_COLOR;
                            }
                        }
                        var colors = plainColorValue.split(";");
                        spoolItemInstance.isRainbow(false);
                        spoolItemInstance.isTransparent(transparent);
                        spoolItemInstance.transparentUntinted(untinted);
                        spoolItemInstance.colorCount(Math.min(colors.length, 3));
                        for (var i = 0; i < 3; i++){
                            if (i < colors.length && colors[i]){
                                pickerColors[i](colors[i]);
                            }
                        }
                    }
                } finally {
                    applyingColor = false;
                }
            };
            this.color(DEFAULT_COLOR);  // needed
            pickerColors[0](DEFAULT_COLOR);
            pickerColors[1]("#0000ff");
            pickerColors[2]("#ffff00");

            var firstUseViewModel = ComponentFactory.createDateTimePicker("firstUse-date-picker");
            var lastUseViewModel = ComponentFactory.createDateTimePicker("lastUse-date-picker");
            var purchasedOnViewModel = ComponentFactory.createDateTimePicker("purchasedOn-date-picker", false);
            this.firstUse = firstUseViewModel.currentDateTime;
            this.lastUse = lastUseViewModel.currentDateTime;
            this.purchasedOn = purchasedOnViewModel.currentDateTime;
        } else {
            // Read-only mode (table/list items): plain observables, no widget coupling
            // (adopted from mdziekon/OctoPrint-SpoolManager PR #11, GH-10)
            this.vendor = ko.observable();
            this.allVendors = ko.observableArray();
            this.material = ko.observable();
            this.labels = ko.observableArray();
            this.allLabels = ko.observableArray();
        }

        // Autosuggest for "density" (data source: SpoolmanDB-derived mapping, see common/constants.js).
        // Guarded so it only fires for the item currently open in the edit dialog.
        var densitySuggestItem = this;
        this.material.subscribe(function(newMaterial){
            if ($(SPOOL_DIALOG_SELECTOR).is(":visible") == false){
                return;
            }
            if (densitySuggestItem.isSpoolVisible() != true){
                return;
            }
            if (!newMaterial){
                return;
            }
            var density = MATERIALS_DENSITY_MAP[normalizeMaterialKey(newMaterial)];
            if (density){
                densitySuggestItem.density(density);
            }
        });

        // Non-persistent fields (these exist only in this view model for weight-calculation)
        this.totalCombinedWeight = ko.observable();
        this.remainingCombinedWeight = ko.observable();
        this.drivenScope = ko.observable();
        this.drivenScopeOptions = ko.observableArray([
            {
                text: "Filament Amount",
                value: FILAMENT_STATS_CALC_MODES.FILAMENT,
            },
            {
                text: "Spool Weight",
                value: FILAMENT_STATS_CALC_MODES.SPOOL,
            },
            {
                text: "Combined Weight",
                value: FILAMENT_STATS_CALC_MODES.COMBINED,
            },
        ]);

        // Fill Item with data
        this.update(spoolData, { catalogs: catalogs });
    }

    SpoolItem.prototype.update = function (data, params) {
        var updateData = data || {}
        var catalogs = (params != null) ? params.catalogs : null;

        // update latest all catalog
        if (catalogs != null){
            // labels
            this.allLabels.removeAll();
            ko.utils.arrayPushAll(this.allLabels, catalogs.labels);
            //vendors
            this.allVendors(catalogs.vendors);
        }

        this.selectedFromQRCode(updateData.selectedFromQRCode);
        this.selectedForTool(updateData.selectedForTool);
        this.hasNoData(data == null);
        this.version(updateData.version);
        this.databaseId(updateData.databaseId);
        this.isTemplate(updateData.isTemplate);
        this.isActive(updateData.isActive);
        this.isInActive(!updateData.isActive);
        this.displayName(updateData.displayName);
        this.vendor(updateData.vendor);

        this.material(updateData.material);
        this.density(updateData.density);
        this.diameter(updateData.diameter);
        this.diameterTolerance(updateData.diameterTolerance);
        this.finish(updateData.finish);
        // first update color code, and then update the color name
        var rawColor = updateData.color == null ? DEFAULT_COLOR : updateData.color;
        if (this.applyColorToEditor != null){
            this.applyColorToEditor(rawColor);
        }
        this.color(rawColor);
        // if no custom color name present, use predefined name
        if (updateData.colorName == null || updateData.colorName.length == 0){
            var preDefinedColorName = false;
            if (("" + rawColor).toLowerCase() === "rainbow"){
                preDefinedColorName = "Rainbow";
            } else if (("" + rawColor).toLowerCase().indexOf("transparent") === 0){
                var baseColor = ("" + rawColor).substr("transparent".length).replace(/^:/, "").split(";")[0];
                var baseName = baseColor ? tinycolor(baseColor).toName() : false;
                preDefinedColorName = baseName != false ? "Transparent " + baseName : "Transparent";
            } else {
                preDefinedColorName = tinycolor(("" + rawColor).split(";")[0]).toName();
            }
            if (preDefinedColorName != false){
                this.colorName(preDefinedColorName);
            }
        } else {
            this.colorName(updateData.colorName);
        }

        this.flowRateCompensation(updateData.flowRateCompensation);
        this.temperature(updateData.temperature);
        this.bedTemperature(updateData.bedTemperature);
        this.enclosureTemperature(updateData.enclosureTemperature);
        this.offsetTemperature(updateData.offsetTemperature);
        this.offsetBedTemperature(updateData.offsetBedTemperature);
        this.offsetEnclosureTemperature(updateData.offsetEnclosureTemperature);
        this.totalWeight(parseFloat(updateData.totalWeight));
        this.spoolWeight(parseFloat(updateData.spoolWeight));
        this.remainingWeight(parseFloat(updateData.remainingWeight));
        this.remainingPercentage(updateData.remainingPercentage);
        this.code(updateData.code);
        this.batchNumber(updateData.batchNumber);
        this.usedPercentage(updateData.usedPercentage);

        this.totalLength(updateData.totalLength);
        this.usedLength(updateData.usedLength);
        this.usedLengthPercentage(updateData.usedLengthPercentage);
        this.remainingLength(updateData.remainingLength);
        this.remainingLengthPercentage(updateData.remainingLengthPercentage);
        this.usedWeight(parseFloat(updateData.usedWeight));

        this.firstUse(updateData.firstUse);
        this.lastUse(updateData.lastUse);
        this.purchasedOn(updateData.purchasedOn);
        if (updateData.firstUse){
            var convertedDateTime = moment(data.firstUse, PARSE_FORMAT_DATETIME).format(FORMAT_DATETIME_LOCAL)
            this.firstUseKO(convertedDateTime);
        }
        else{
            this.firstUseKO(null);
        }
        if (updateData.lastUse){
            var convertedDateTime = moment(data.lastUse, PARSE_FORMAT_DATETIME).format(FORMAT_DATETIME_LOCAL)
            this.lastUseKO(convertedDateTime);
        }
        else{
            this.lastUseKO(null);
        }
        if (updateData.purchasedOn){
            var convertedDateTime = moment(data.purchasedOn, PARSE_FORMAT_DATE).format(FORMAT_DATE)
            this.purchasedOnKO(convertedDateTime);
        }
        else {
            this.purchasedOnKO(null);
        }

        this.purchasedFrom(updateData.purchasedFrom);

        this.cost(updateData.cost);
        this.costUnit(updateData.costUnit);

        // update label selections
        if (updateData.labels != null){
            this.labels.removeAll();
            selectedLabels = updateData.labels
            if (Array.isArray(updateData.labels) == false){
                selectedLabels = JSON.parse(updateData.labels)
            }
            ko.utils.arrayPushAll(this.labels, selectedLabels);
        }

        // assign content to the Note-Section
        // fill Obseravbles
        this.noteText(updateData.noteText);
        this.noteDeltaFormat(updateData.noteDeltaFormat);
        if (updateData.noteHtml != null){
            this.noteHtml(updateData.noteHtml);
        } else {
            // Fallback text
            this.noteHtml(updateData.noteText);
        }

        // Calculate derived fields (these exists only in this view model)
        this.totalCombinedWeight(_getValueOrZero(updateData.totalWeight) + _getValueOrZero(updateData.spoolWeight));
        this.remainingCombinedWeight(_getValueOrZero(updateData.remainingWeight) + _getValueOrZero(updateData.spoolWeight));
    };
})();
