// Custom binding for <input type="number"> fields.
// Problem: a native number input reports an empty string via its `.value` when the
// user types garbage (e.g. "12abc") or pastes non-numeric text, so the plain knockout
// `value:` binding cannot tell "empty" apart from "invalid" - the bad value silently
// vanishes (Fall A). This binding reads the DOM `validity` object directly (badInput /
// rangeUnderflow / rangeOverflow / stepMismatch) AND intercepts paste of non-numeric
// text (which some browsers drop to an empty value without setting badInput), marks the
// field invalid (red border + message) and registers it in a shared set so the Save
// button can be blocked while anything is invalid.
ko.bindingHandlers.numberField = {
    init: function(element, valueAccessor, allBindings, viewModel, bindingContext){
        var options = valueAccessor();
        var observable = options.value;              // optional: only written in standalone mode
        var invalidFields = options.invalidFields;   // ko.observableArray of field keys
        var fieldKey = options.key;
        // trackOnly mode: another binding (e.g. the unit-conversion `value:` binding) owns the value,
        // so we only observe validity for the red border + Save block and never touch the observable.
        var trackOnly = options.trackOnly === true;

        // sticky flag set when a paste of non-numeric text is detected; cleared on the next
        // real edit. Guards against browsers that silently drop a bad paste to "" without badInput.
        var pasteRejected = false;
        // accepts optional sign, digits, one decimal separator (. or ,) and scientific notation
        var numericPattern = /^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?$/;

        var setInvalidFlag = function(isInvalid){
            $(element).toggleClass("spm-number-invalid", isInvalid);
            var current = invalidFields();
            var idx = current.indexOf(fieldKey);
            if (isInvalid && idx === -1){
                invalidFields.push(fieldKey);
            } else if (!isInvalid && idx !== -1){
                invalidFields.splice(idx, 1);
            }
        };

        var updateValidity = function(){
            // element.validity.valid is false for badInput, rangeUnderflow, stepMismatch, ...
            var isInvalid = pasteRejected || (element.validity && element.validity.valid === false);
            setInvalidFlag(isInvalid);

            // standalone mode: only push a real (parseable) value, never a half-typed garbage state
            if (!trackOnly && !isInvalid && observable){
                var raw = element.value;
                observable(raw === "" ? null : raw);
            }
        };

        var subscription = null;
        if (!trackOnly && observable){
            // keep the input's displayed text in sync when the observable changes programmatically
            subscription = observable.subscribe(function(newValue){
                if (!pasteRejected && element.validity && element.validity.valid !== false){
                    var display = (newValue === null || newValue === undefined) ? "" : ("" + newValue);
                    if (element.value !== display){
                        element.value = display;
                    }
                }
            });
            var initial = ko.unwrap(observable);
            element.value = (initial === null || initial === undefined) ? "" : ("" + initial);
        }

        // intercept non-numeric paste before the browser can silently discard it
        $(element).on("paste.numberField", function(e){
            var clip = (e.originalEvent || e).clipboardData || window.clipboardData;
            if (!clip){ return; }
            var text = clip.getData("text");
            if (text != null && text.trim().length > 0 && numericPattern.test(text.trim()) === false){
                e.preventDefault();
                pasteRejected = true;
                setInvalidFlag(true);
            }
        });
        // any real edit (typing, arrows, deleting) clears a previous paste rejection
        $(element).on("keydown.numberField", function(){
            if (pasteRejected){
                pasteRejected = false;
            }
        });

        $(element).on("input.numberField change.numberField blur.numberField", updateValidity);
        // run once so a value that arrives invalid (e.g. loaded then edited) is caught immediately
        updateValidity();

        ko.utils.domNodeDisposal.addDisposeCallback(element, function(){
            $(element).off(".numberField");
            if (subscription){
                subscription.dispose();
            }
            // drop this field from the invalid set when the node goes away (dialog close/reopen)
            var current = invalidFields();
            var idx = current.indexOf(fieldKey);
            if (idx !== -1){
                invalidFields.splice(idx, 1);
            }
        });
    }
};

// Dialog functionality
function SpoolManagerEditSpoolDialog(){

    var self = this;

    // keys of number inputs currently holding an invalid value (see ko.bindingHandlers.numberField)
    self.invalidNumberFields = ko.observableArray([]);
    // human readable labels for the Save-blocked hint, keyed by the field key used in the template
    self.numberFieldLabels = {
        density: "Density", diameter: "Diameter", diameterTolerance: "Diameter tolerance",
        flowRateCompensation: "Flow rate compensation", temperature: "Tool temperature",
        bedTemperature: "Bed temperature", enclosureTemperature: "Enclosure temperature",
        offsetTemperature: "Offset tool temperature", offsetBedTemperature: "Offset bed temperature",
        offsetEnclosureTemperature: "Offset enclosure temperature", cost: "Cost",
        totalWeight: "Filament amount (initial)", spoolWeight: "Empty spool weight",
        usedWeight: "Filament amount (used)", totalLength: "Filament length (initial)",
        usedLength: "Filament length (used)",
        totalCombinedWeight: "Combined weight (initial)", remainingCombinedWeight: "Combined weight (remaining)"
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////////////// CONSTANTS
    var DEFAULT_COLOR = "#ff0000";
    // Density values (g/cm3) derived from SpoolmanDB-Community
    // https://github.com/Icezaza2543/SpoolmanDB-Community (maintained fork of
    // https://github.com/Donkie/SpoolmanDB) - Copyright (c) 2024 Donkie, MIT License
    // Keys are the normalized material names, see normalizeMaterialKey().
    var densityMap = {
        PLA:	1.24,
        PLA_PLUS:	1.24,   // matches "PLA+" and legacy "PLA_plus"
        PLA_CF:	1.24,
        ABS:	1.04,
        ABS_PLUS:	1.06,
        ABS_T:	1.08,
        ABS_CF:	1.065,
        ASA:	1.05,
        ASA_CF:	1.12,
        PETG:	1.27,
        PETG_CF:	1.27,
        PCTG:	1.21,
        NYLON:	1.14,
        PA6:	1.13,
        PA11:	1.03,
        PA12:	1.01,
        PA_CF:	1.20,
        PA6_CF:	1.24,
        PA12_CF:	1.15,
        TPU:	1.21,
        TPU_85A:	1.12,
        TPU_90A:	1.185,
        TPU_95A:	1.21,
        TPE:	1.15,
        FLEXIBLE_TPE_32D:	1.10,
        FLEXIBLE_TPE_88A:	0.89,
        FPE:	2.16,
        PC:	    1.20,
        PC_ABS:	1.19,
        PC_PBT:	1.20,
        PC_CF:	1.24,
        WOOD:	1.28,
        CARBON_FIBER:	1.30,
        HIPS:	1.03,
        PVA:	1.23,
        PVB:	1.10,
        BVOH:	1.25,
        PP:	    0.90,
        PP_CF:	1.145,
        PP_GF:	1.03,
        POM:	1.40,
        PMMA:	1.18,
        PET:	1.38,
        PET_CF:	1.29,
        PBT:	1.31,
        PPS:	1.35,
        PPS_CF:	1.35,
        PVDF:	1.78,
        PEI_ULTEM:	1.27,
        PEKK:	1.28,
        PEEK:	1.32,
        PEEK_CF:	1.35,
        PPSU:	1.37,
        // aliases for alternative spellings (e.g. imported from Spoolman / typed manually)
        FLEXIBLE_TPU:	1.21,
        SEMI_FLEXIBLE_FPE:	2.16,
        POLYCARBONATE_PC:	1.20,
        POLYPROPYLENE_PP:	0.90,
        ACETAL_POM:	1.40,
        PEI:	1.27,
        PA:	    1.14
    };

    // Normalizes a material display name to a densityMap key:
    // "Flexible (TPU)" -> "FLEXIBLE_TPU", "PC/ABS" -> "PC_ABS", "PLA+" -> "PLA_PLUS"
    var normalizeMaterialKey = function(materialName){
        if (!materialName){
            return "";
        }
        return materialName
            .trim()
            .toUpperCase()
            .replace(/\+/g, "_PLUS")
            .replace(/[^A-Z0-9]+/g, "_")
            .replace(/^_+|_+$/g, "");
    };

    self.unitValues = {
        WEIGHT: "weight",
        LENGTH: "length"
    };
    self.stateValues = {
        INITIAL: "initial",
        USED: "used",
        REMAINING: "remaining"
    };
    self.scopeValues = {
        FILAMENT: "filament",
        SPOOL: "spool",
        COMBINED: "spool+filament"
    };

    var FILAMENT = self.scopeValues.FILAMENT;
    var COMBINED = self.scopeValues.COMBINED;
    var SPOOL = self.scopeValues.SPOOL;

    ///////////////////////////////////////////////////////////////////////////////////////////////////////// ITEM MODEL
    var SpoolItem = function(spoolData, editable) {
        // Init Item

        // if we use the Item for Editing we need to initialise the widget-model as well , e.g. Option-Values, Suggestion-List
        // if we just use this Item in readonly-mode we need simple ko.observer

        // FormatHelperFunction
        formatOnlyDate = function (data, dateBindingName) {
            var dateValue = data[dateBindingName];
            if (dateValue != null && dateValue() != null && dateValue() != ""){
                dateValue = dateValue();
                var result = dateValue.split(" ")[0];
                return result
            }
            return "";
        };

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
//        this.vendor = ko.observable();
//        this.material = ko.observable();
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
//        this.labels = ko.observable();
//            this.allLabels = ko.observable();
        this.noteText = ko.observable()
        this.noteDeltaFormat = ko.observable()
        this.noteHtml = ko.observable()

        this.firstUse = ko.observable();
        this.lastUse = ko.observable();
        this.firstUseKO = ko.observable();
        this.lastUseKO = ko.observable();
        this.purchasedOn = ko.observable();
        this.purchasedOnKO = ko.observable();


        this.purchasedFrom = ko.observable();
        this.cost = ko.observable();
        this.costUnit = ko.observable();

        // Assign default values for editing
        // overwrite and/or add attributes
        var vendorViewModel = self.componentFactory.createSelectWithFilter("spool-vendor-select", $('#spool-form'));
        this.vendor = vendorViewModel.selectedOption;
        this.allVendors = vendorViewModel.allOptions;

        var materialViewModel = self.componentFactory.createSelectWithFilter("spool-material-select", $('#spool-form'));
        this.material = materialViewModel.selectedOption;
        // this.allMaterials = materialViewModel.allOptions;

        // Autosuggest for "density"
        this.material.subscribe(function(newMaterial){
            if ($("#dialog_spool_edit").is(":visible")){
                if (self.spoolItemForEditing.isSpoolVisible() == true){
                    var mat = self.spoolItemForEditing.material();
                    if (mat){
                        var density = densityMap[normalizeMaterialKey(mat)]
                        if (density){
                           self.spoolItemForEditing.density(density);
                        }
                    }
                }
            }
        });

        if (editable == true){
            // Multi-color support (issue #19): "color" holds the composed value
            // ("#hex", "#hex;#hex[;#hex]" or "rainbow"), the pickers hold the parts.
            var spoolItemInstance = this;
            this.colorCount = ko.observable(1);
            this.isRainbow = ko.observable(false);
            this.isTransparent = ko.observable(false);
            var colorViewModel = self.componentFactory.createColorPicker("filament-color-picker", true);
            var colorViewModel2 = self.componentFactory.createColorPicker("filament-color-picker2", true);
            var colorViewModel3 = self.componentFactory.createColorPicker("filament-color-picker3", true);
            // picking the "translucent" swatch activates the transparent flag
            var activateTransparent = function(){
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
            pickerColors[0].subscribe(composeColor);
            pickerColors[1].subscribe(composeColor);
            pickerColors[2].subscribe(composeColor);
            this.colorCount.subscribe(composeColor);
            this.isRainbow.subscribe(composeColor);
            this.isTransparent.subscribe(composeColor);
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
                        spoolItemInstance.colorCount(1);
                        pickerColors[0](DEFAULT_COLOR);
                    } else {
                        var plainColorValue = "" + colorValue;
                        var transparent = plainColorValue.toLowerCase().indexOf("transparent") === 0;
                        if (transparent){
                            plainColorValue = plainColorValue.substr("transparent".length);
                            if (plainColorValue.indexOf(":") === 0){
                                plainColorValue = plainColorValue.substr(1);
                            }
                            if (plainColorValue.length === 0){
                                plainColorValue = DEFAULT_COLOR;
                            }
                        }
                        var colors = plainColorValue.split(";");
                        spoolItemInstance.isRainbow(false);
                        spoolItemInstance.isTransparent(transparent);
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

            var firstUseViewModel = self.componentFactory.createDateTimePicker("firstUse-date-picker");
            var lastUseViewModel = self.componentFactory.createDateTimePicker("lastUse-date-picker");
            var purchasedOnViewModel = self.componentFactory.createDateTimePicker("purchasedOn-date-picker", false);
            this.firstUse = firstUseViewModel.currentDateTime;
            this.lastUse = lastUseViewModel.currentDateTime;
            this.purchasedOn = purchasedOnViewModel.currentDateTime;
        }
        self.labelsViewModel = self.componentFactory.createLabels("spool-labels-select", $('#spool-form'));
        this.labels   = self.labelsViewModel.selectedOptions;
        this.allLabels = self.labelsViewModel.allOptions;

        // Non-persistent fields (these exist only in this view model for weight-calculation)
        this.totalCombinedWeight = ko.observable();
        this.remainingCombinedWeight = ko.observable();
        this.drivenScope = ko.observable();
        this.drivenScopeOptions = ko.observableArray([
            {
                text: "Filament Amount",
                value: FILAMENT,
            },
            {
                text: "Spool Weight",
                value: SPOOL,
            },
            {
                text: "Combined Weight",
                value: COMBINED,
            },
        ]);

        // Fill Item with data
        this.update(spoolData);
    }

    SpoolItem.prototype.update = function (data) {
        var updateData = data || {}

        // TODO weight: renaming
        self.autoUpdateEnabled = false;

        // update latest all catalog
        if (self.catalogs != null){
            // labels
            this.allLabels.removeAll();
            ko.utils.arrayPushAll(this.allLabels, self.catalogs.labels);
            // materials
            // this.allMaterials(self.catalogs.materials);

            //vendors
            this.allVendors(self.catalogs.vendors);
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
            var convertedDateTime = moment(data.firstUse, "DD.MM.YYYY HH:mm").format("YYYY-MM-DDTHH:mm")
            this.firstUseKO(convertedDateTime);
        }
        else{
            this.firstUseKO(null);
        }
        if (updateData.lastUse){
            var convertedDateTime = moment(data.lastUse, "DD.MM.YYYY HH:mm").format("YYYY-MM-DDTHH:mm")
            this.lastUseKO(convertedDateTime);
        }
        else{
            this.lastUseKO(null);
        }
        if (updateData.purchasedOn){
            var convertedDateTime = moment(data.purchasedOn, "DD.MM.YYYY").format("YYYY-MM-DD")
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
        // fill editor
        if (self.noteEditor != null){
            if (updateData.noteDeltaFormat == null || updateData.noteDeltaFormat.length == 0) {
                // Fallback is text (if present), not Html
                if (updateData.noteText != null){
                    self.noteEditor.setText(updateData.noteText, 'api');
                } else {
                    self.noteEditor.setText("", 'api');
                }
            }else {
                    deltaFormat = JSON.parse(updateData.noteDeltaFormat);
                    self.noteEditor.setContents(deltaFormat, 'api');
            }
        }

        // Calculate derived fields (these exists only in this view model)
        this.totalCombinedWeight(_getValueOrZero(updateData.totalWeight) + _getValueOrZero(updateData.spoolWeight));
        this.remainingCombinedWeight(_getValueOrZero(updateData.remainingWeight) + _getValueOrZero(updateData.spoolWeight));

        self.autoUpdateEnabled = true;
    };


    ///////////////////////////////////////////////////////////////////////////////////////////////// Instance Variables
    self.componentFactory = new ComponentFactory();
    self.spoolDialog = null;
    self.templateSpoolDialog = null;
    self.closeDialogHandler = null;
    self.spoolItemForEditing = null;
    self.templateSpools = ko.observableArray([]);

    // static options for the "Finish" dropdown
    self.finishOptions = [
        { text: "Silk", value: "silk" },
        { text: "Matt", value: "matt" },
        { text: "Marble", value: "marble" },
        { text: "Metal", value: "metal" },
        { text: "Glow", value: "glow" },
        { text: "Custom…", value: "custom" }
    ];

    // Template-combobox on the displayname field (issue #48)
    self.templateComboVisible = ko.observable(false);
    self.templateComboFilter = ko.observable("");
    self._suppressTemplateCombo = false;
    self.filteredTemplateSpools = ko.pureComputed(function(){
        var filterText = ("" + (self.templateComboFilter() || "")).trim().toLowerCase();
        var allTemplates = self.templateSpools();
        if (filterText.length == 0){
            return allTemplates;
        }
        return ko.utils.arrayFilter(allTemplates, function(spoolItem){
            var haystack = (spoolItem.displayName() || "") + " " +
                           (spoolItem.material() || "") + " " +
                           (spoolItem.vendor() || "");
            return haystack.toLowerCase().indexOf(filterText) !== -1;
        });
    });
    self.isTemplateComboAvailable = ko.pureComputed(function(){
        return self.isExistingSpool() == false && self.templateSpools().length > 0;
    });

    // Display name variables (issue #49): prospective databaseId of the next created spool for the {id} preview
    self.nextSpoolId = ko.observable(null);

    self._refreshNextSpoolId = function(){
        if (self.apiClient == null){
            return;
        }
        self.apiClient.callLoadNextSpoolId(function(responseData){
            if (responseData != null && responseData.nextSpoolId != null){
                self.nextSpoolId(responseData.nextSpoolId);
            }
        });
    };

    // replaces all variables except {id} (only known server-side after saving) with the current field values
    self._substituteDisplayNameVariables = function(displayName){
        var spoolItem = self.spoolItemForEditing;
        var asText = function(value){
            if (value === null || value === undefined || (typeof value === "number" && isNaN(value))){
                return "";
            }
            return "" + value;
        };
        var totalWeight = parseFloat(spoolItem.totalWeight());
        var replacements = {
            "{material}": asText(spoolItem.material()),
            "{color}": asText(spoolItem.colorName()),
            "{vendor}": asText(spoolItem.vendor()),
            "{diameter}": asText(spoolItem.diameter()),
            "{weight}": isNaN(totalWeight) ? "" : "" + Math.round(totalWeight),
            "{code}": asText(spoolItem.code()),
            "{batch}": asText(spoolItem.batchNumber()),
        };
        var result = displayName;
        for (var token in replacements){
            result = result.split(token).join(replacements[token]);
        }
        return result;
    };

    self.noteEditor = null;

    // Do I need these viewModels?
    self.firstUseDatePickerModel = null;
    self.lastUseDatePickerModel = null;
    self.purchasedOndatePickerModel = null;
    self.labelsViewModel = null;
    self.filamentColorViewModel = null;
    self.materialViewModel = null;

    self.catalogs = null;
    self.allMaterials = ko.observableArray([]);
    self.allVendors = ko.observableArray([]);
    self.allColors = ko.observableArray([]);

    self.allToolIndices = ko.observableArray([]);

    // Knockout stuff
    this.isExistingSpool = ko.observable(false);
    this.spoolSelectedByQRCode = ko.observable(false);


    ///////////////////////////////////////////////////////////////////////////////////////////////////////////// HELPER

    self.isFormValidForSubmit = ko.pureComputed(function () {
        if (self._checkMandatoryFields() == false){
            return false;
        }
        if (self._checkDateTimeFormats() == false){
            return false;
        }
        // block submit while any number field holds an invalid value (Fall A)
        if (self.invalidNumberFields().length > 0){
            return false;
        }

        return true;
    });

    // comma-separated list of invalid number field labels, for the hint next to the Save button
    self.invalidNumberFieldsLabel = ko.pureComputed(function () {
        return self.invalidNumberFields().map(function(key){
            return self.numberFieldLabels[key] || key;
        }).join(", ");
    });

    self._checkMandatoryFields = function(){
        // "Displayname", "total weight", "color name/code"
        let namePresent = self.isDisplayNamePresent();
        if (namePresent == false){
            return false;
        }
        let colorNametPresent = self.isColorNamePresent();
        if (colorNametPresent == false){
            return false;
        }
        let weightPresent = self.isTotalCombinedWeightPresent();
        if (weightPresent == false){
            return false;
        }
        return true;
    }

    self._checkDateTimeFormats = function(){

        // "First/LastUse", "purchasedOn"
        let firstUse = self.spoolItemForEditing.firstUseKO()
        if (firstUse && firstUse.trim().length != 0){
            if (moment(firstUse, "YYYY-MM-DDTHH:mm").isValid() == false){
                return false;
            }
        }
        let lastUse = self.spoolItemForEditing.lastUseKO()
        if (lastUse && lastUse.trim().length != 0){
            if (moment(lastUse, "YYYY-MM-DDTHH:mm").isValid() == false){
                return false;
            }
        }
        let purchasedOn = self.spoolItemForEditing.purchasedOnKO()
        if (purchasedOn && purchasedOn.trim().length != 0){
            if (moment(purchasedOn, "YYYY-MM-DD").isValid() == false){
                return false;
            }
        }
        return true;
    }

    self.isDisplayNamePresent = function(){
        var displayName = self.spoolItemForEditing.displayName();
        return (!displayName || displayName.trim().length === 0) == false;
    }

    self.addColorClicked = function(){
        var count = self.spoolItemForEditing.colorCount();
        if (count < 3){
            self.spoolItemForEditing.colorCount(count + 1);
        }
    }

    self.removeColorClicked = function(){
        var count = self.spoolItemForEditing.colorCount();
        if (count > 1){
            self.spoolItemForEditing.colorCount(count - 1);
        }
    }

    self.isColorNamePresent = function(){
        var colorName = self.spoolItemForEditing.colorName();
        return (!colorName || colorName.trim().length === 0) == false;
    }

    self.isTotalCombinedWeightPresent = function(){
        var totalCombinedWeight = self.spoolItemForEditing.totalCombinedWeight();
        return (!totalCombinedWeight || (""+totalCombinedWeight).trim().length === 0) == false;
    }

    // self.transform2Date = function(dateValue){
    //     if (dateValue == null){
    //         return null;
    //     }
    //     if (dateValue instanceof Date){
    //         return dateValue;
    //     }
    //     return new Date(dateValue);
    // }

//    self.getValueOrDefault = function(data, attribute, defaultValue){
//        if (data == null){
//            return defaultValue;
//        }
//        var value = data[attribute];
//        if (value == null || value == undefine){
//            return defaultValue;
//        }
//        return value;
//    }

    function _roundTo(x, precision) {
        var increments = Math.pow(10, precision);
        return Math.round((x + Number.EPSILON) * increments) / increments;
    }

    this._reColorFilamentIcon = function(newColor){
        var colorValue = "" + newColor;
        var rectColors;
        var strokeColor;
        if (colorValue.toLowerCase() === "rainbow"){
            rectColors = ["#ff2d2d", "#ff9a00", "#ffe600", "#16c172", "#2f7bff", "#a044ff"];
            strokeColor = rectColors[0];
        } else {
            if (colorValue.toLowerCase().indexOf("transparent") === 0){
                colorValue = colorValue.substr("transparent".length).replace(/^:/, "");
                if (colorValue.length === 0){
                    colorValue = "#e8e8e8";
                }
            }
            var colors = colorValue.split(";");
            if (colors.length === 1){
                // single color: alternate with a slightly darkened shade
                rectColors = [colors[0], tinycolor(colors[0]).darken(12).toString()];
            } else {
                rectColors = colors;
            }
            strokeColor = colors[0];
        }
        var svgIcon = $("#svg-filament")
        svgIcon.children("rect").each(function(loopIndex){
            $(this).attr("fill", rectColors[loopIndex % rectColors.length]);
        });
        svgIcon.children("path").each(function(loopIndex){
            $(this).attr("stroke", strokeColor);
        });
    };

    function _getValueOrZero(x) {
        if (!x){
            x = 0
        }
        return parseFloat(x);
    }

    ///////////////////////////////////////////////////////////////////////////////////////////////////////////// PUBLIC
    this.initBinding = function(apiClient, pluginSettings, printerProfilesViewModel, printerStateViewModel){

        self.autoUpdateEnabled = false;
        self.apiClient = apiClient;
        self.pluginSettings = pluginSettings;
        self.printerProfilesViewModel = printerProfilesViewModel;
        self.printerStateViewModel = printerStateViewModel;

        self.spoolDialog = $("#dialog_spool_edit");
        self.templateSpoolDialog = $("#dialog_template_spool_selection");

        self.noteEditor = new Quill('#spool-note-editor', {
            modules: {
                toolbar: [
                    ['bold', 'italic', 'underline'],
                    [{ 'color': [] }, { 'background': [] }],
                    [{ 'list': 'ordered' }, { 'list': 'bullet' }],
                    ['link']
                ]
            },
            theme: 'snow'
        });

        Quill.prototype.getHtml = function() {
            return this.container.querySelector('.ql-editor').innerHTML;
        };

        // initial coloring
        self._createSpoolItemForEditing();

        // typing into the displayname field filters the template-combobox (issue #48)
        self.spoolItemForEditing.displayName.subscribe(function(newValue){
            if (self._suppressTemplateCombo == true){
                return;
            }
            if (self.isTemplateComboAvailable() == false){
                return;
            }
            if (self.spoolDialog == null || self.spoolDialog.is(":visible") == false){
                return;
            }
            self.templateComboFilter(newValue || "");
            self.templateComboVisible(true);
        });

        // live preview of the final display name when it contains variables like {material}-{color}-{id} (issue #49);
        // only shown for new spools (variables are resolved on save) and templates (resolved for spools created from them)
        self.displayNamePreview = ko.pureComputed(function(){
            var displayName = self.spoolItemForEditing.displayName();
            if (!displayName || displayName.indexOf("{") === -1){
                return "";
            }
            if (self.isExistingSpool() == true && self.spoolItemForEditing.isTemplate() != true){
                return "";
            }
            var resolved = self._substituteDisplayNameVariables(displayName);
            var nextId = self.nextSpoolId();
            return resolved.split("{id}").join(nextId != null ? "" + nextId : "…");
        });

        self._reColorFilamentIcon(self.spoolItemForEditing.color());
        self.spoolItemForEditing.color.subscribe(function(newColor){
            self._reColorFilamentIcon(newColor);
            if (("" + newColor).toLowerCase() === "rainbow"){
                self.spoolItemForEditing.colorName("Rainbow");
                return;
            }
            var plainColor = "" + newColor;
            var transparentPrefix = "";
            if (plainColor.toLowerCase().indexOf("transparent") === 0){
                transparentPrefix = "Transparent";
                plainColor = plainColor.substr("transparent".length).replace(/^:/, "");
                if (plainColor.length === 0){
                    self.spoolItemForEditing.colorName(transparentPrefix);
                    return;
                }
            }
            if (plainColor.indexOf(";") !== -1){
                // multi-color: keep the name the user typed
                return;
            }
            var colorName = tinycolor(plainColor).toName();
            if (colorName != false){
                self.spoolItemForEditing.colorName(transparentPrefix ? transparentPrefix + " " + colorName : colorName);
            } else if (transparentPrefix){
                self.spoolItemForEditing.colorName(transparentPrefix);
            }
        });
        // ----------------- start: weight stuff
        var remainingWeightKo = self.spoolItemForEditing.remainingWeight;
        var totalWeightKo = self.spoolItemForEditing.totalWeight;
        var usedWeightKo = self.spoolItemForEditing.usedWeight;
        var remainingCombinedWeightKo = self.spoolItemForEditing.remainingCombinedWeight;
        var spoolWeightKo = self.spoolItemForEditing.spoolWeight;
        var totalCombinedWeightKo = self.spoolItemForEditing.totalCombinedWeight;
        var totalLengthKo = self.spoolItemForEditing.totalLength;
        var usedLengthKo = self.spoolItemForEditing.usedLength;
        var remainingLengthKo = self.spoolItemForEditing.remainingLength;
        var densityKo = self.spoolItemForEditing.density;
        var diameterKo = self.spoolItemForEditing.diameter;
        var usedPercentageKo = self.spoolItemForEditing.usedPercentage;
        var remainingPercentageKo = self.spoolItemForEditing.remainingPercentage;
        var usedLengthPercentageKo = self.spoolItemForEditing.usedLengthPercentage;
        var remainingLengthPercentageKo = self.spoolItemForEditing.remainingLengthPercentage;
        var drivenScopeKo = self.spoolItemForEditing.drivenScope;

        // ----------------- start: display units
        // the base observables always hold mm/g, these computeds only convert for display/input
        var LENGTH_UNIT_FACTORS = { "mm": 1, "cm": 10, "m": 1000 };
        var WEIGHT_UNIT_FACTORS = { "g": 1, "kg": 1000 };
        var UNIT_DISPLAY_DECIMALS = { "mm": 1, "cm": 2, "m": 3, "g": 1, "kg": 3 };

        var selectedLengthUnit = function () {
            var unit = self.pluginSettings.lengthUnit ? self.pluginSettings.lengthUnit() : "mm";
            return LENGTH_UNIT_FACTORS[unit] ? unit : "mm";
        };
        var selectedWeightUnit = function () {
            var unit = self.pluginSettings.weightUnit ? self.pluginSettings.weightUnit() : "g";
            return WEIGHT_UNIT_FACTORS[unit] ? unit : "g";
        };
        self.lengthUnitText = ko.pureComputed(selectedLengthUnit);
        self.weightUnitText = ko.pureComputed(selectedWeightUnit);

        var _makeUnitDisplayKo = function (baseKo, unitFunction, unitFactors) {
            return ko.pureComputed({
                read: function () {
                    var unit = unitFunction();
                    var value = parseFloat(baseKo());
                    if (isNaN(value)) {
                        return baseKo();
                    }
                    return parseFloat((value / unitFactors[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit]));
                },
                write: function (newValue) {
                    var unit = unitFunction();
                    var value = parseFloat(newValue);
                    if (isNaN(value)) {
                        baseKo(newValue);
                        return;
                    }
                    baseKo(parseFloat((value * unitFactors[unit]).toFixed(1)));
                }
            });
        };

        self.totalWeightDisplay = _makeUnitDisplayKo(totalWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        self.usedWeightDisplay = _makeUnitDisplayKo(usedWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        self.remainingWeightDisplay = _makeUnitDisplayKo(remainingWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        self.totalLengthDisplay = _makeUnitDisplayKo(totalLengthKo, selectedLengthUnit, LENGTH_UNIT_FACTORS);
        self.usedLengthDisplay = _makeUnitDisplayKo(usedLengthKo, selectedLengthUnit, LENGTH_UNIT_FACTORS);
        self.remainingLengthDisplay = _makeUnitDisplayKo(remainingLengthKo, selectedLengthUnit, LENGTH_UNIT_FACTORS);
        self.spoolWeightDisplay = _makeUnitDisplayKo(spoolWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        self.totalCombinedWeightDisplay = _makeUnitDisplayKo(totalCombinedWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        self.remainingCombinedWeightDisplay = _makeUnitDisplayKo(remainingCombinedWeightKo, selectedWeightUnit, WEIGHT_UNIT_FACTORS);
        // ----------------- end: display units

        function addition(a, b) {
            return a + b;
        }

        function subtraction(a, b) {
            return a - b;
        }

        // Subscriptions for auto updates

        totalWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(totalWeightKo);
            if (drivenScopeKo() === SPOOL) {
                self.updateSpoolWithScopes();
            } else {
                self.updateCombinedInitialWithScopes();
            }
            self.updateFilamentRemainingWithStates();
            self.doUnitConversion(totalWeightKo, totalLengthKo, self.convertToLength);
            self.updatePercentages(usedPercentageKo, remainingPercentageKo, totalWeightKo, usedWeightKo);
            self.resetLocksIf(iAmRootChange);
        });

        totalLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(totalLengthKo);
            self.doUnitConversion(totalLengthKo, totalWeightKo, self.convertToWeight);
            self.updatePercentages(usedLengthPercentageKo, remainingLengthPercentageKo, totalLengthKo, usedLengthKo);
            self.resetLocksIf(iAmRootChange);
        });

        usedWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(usedWeightKo);
            self.doUnitConversion(usedWeightKo, usedLengthKo, self.convertToLength);
            self.updateFilamentRemainingWithStates();
            self.updatePercentages(usedPercentageKo, remainingPercentageKo, totalWeightKo, usedWeightKo);
            self.resetLocksIf(iAmRootChange);
        });

        usedLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(usedLengthKo);
            self.doUnitConversion(usedLengthKo, usedWeightKo, self.convertToWeight);
            self.updatePercentages(usedLengthPercentageKo, remainingLengthPercentageKo, totalLengthKo, usedLengthKo);
            self.resetLocksIf(iAmRootChange);
        });

        remainingWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(remainingWeightKo);
            if (drivenScopeKo() === COMBINED) {
                self.updateCombinedRemainingWithScopes();
            }
            self.updateFilamentUsedWithStates();
            self.doUnitConversion(remainingWeightKo, remainingLengthKo, self.convertToLength);
            self.updatePercentages(usedPercentageKo, remainingPercentageKo, totalWeightKo, usedWeightKo);
            self.resetLocksIf(iAmRootChange);
        });

        remainingLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(remainingLengthKo);
            self.doUnitConversion(remainingLengthKo, remainingWeightKo, self.convertToWeight);
            self.updatePercentages(usedLengthPercentageKo, remainingLengthPercentageKo, totalLengthKo, usedLengthKo);
            self.resetLocksIf(iAmRootChange);
        });

        densityKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(densityKo);
            self.convertAllUnits();
            self.resetLocksIf(iAmRootChange);
        })

        diameterKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(diameterKo);
            self.convertAllUnits();
            self.resetLocksIf(iAmRootChange);
        })

        spoolWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(spoolWeightKo);
            if (drivenScopeKo() === FILAMENT) {
                self.updateFilamentInitialWithScopes();
            } else if (drivenScopeKo() === COMBINED) {
                self.updateCombinedInitialWithScopes();
                self.updateCombinedRemainingWithScopes();
            }
            self.resetLocksIf(iAmRootChange);
        });

        totalCombinedWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(totalCombinedWeightKo);
            if (drivenScopeKo() === FILAMENT) {
                self.updateFilamentInitialWithScopes();
            } else if (drivenScopeKo() === SPOOL) {
                self.updateSpoolWithScopes();
            }
            self.resetLocksIf(iAmRootChange);
        });

        remainingCombinedWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(remainingCombinedWeightKo);
            if (drivenScopeKo() === FILAMENT) {
                self.updateFilamentRemainingWithScopes();
            }
            self.resetLocksIf(iAmRootChange);
        });

        // Update functions

        self.updateFilamentRemainingWithStates = function () {
            self.safeUpdate(remainingWeightKo, subtraction, [totalWeightKo, usedWeightKo]);
        };

        self.updateFilamentRemainingWithScopes = function () {
            self.safeUpdate(remainingWeightKo, subtraction, [remainingCombinedWeightKo, spoolWeightKo]);
        };

        self.updateFilamentUsedWithStates = function () {
            self.safeUpdate(usedWeightKo, subtraction, [totalWeightKo, remainingWeightKo]);
        };

        self.updateFilamentInitialWithScopes = function () {
            self.safeUpdate(totalWeightKo, subtraction, [totalCombinedWeightKo, spoolWeightKo]);
        };

        self.updateSpoolWithScopes = function () {
            self.safeUpdate(spoolWeightKo, subtraction, [totalCombinedWeightKo, totalWeightKo]);
        };

        self.updateCombinedInitialWithScopes = function () {
            self.safeUpdate(totalCombinedWeightKo, addition, [totalWeightKo, spoolWeightKo]);
        };

        self.updateCombinedRemainingWithScopes = function () {
            self.safeUpdate(remainingCombinedWeightKo, addition, [remainingWeightKo, spoolWeightKo]);
        };

        self.convertAllUnits = function () {
            self.doUnitConversion(totalWeightKo, totalLengthKo, self.convertToLength);
            self.doUnitConversion(totalLengthKo, totalWeightKo, self.convertToWeight);
            self.doUnitConversion(usedWeightKo, usedLengthKo, self.convertToLength);
            self.doUnitConversion(usedLengthKo, usedWeightKo, self.convertToWeight);
            self.doUnitConversion(remainingWeightKo, remainingLengthKo, self.convertToLength);
            self.doUnitConversion(remainingLengthKo, remainingWeightKo, self.convertToWeight);
        };

        self.doUnitConversion = function (sourceKo, targetKo, converter) {
            var source = parseFloat(sourceKo());
            if (isNaN(source) || !self.areDensityAndDiameterValid() || !self.getLock(targetKo)) {
                return;
            }
            self.getLock(sourceKo);
            targetKo(converter(source, parseFloat(densityKo()), parseFloat(diameterKo())));
        };

        self.updatePercentages = function (usedPercentageKo, remainPercentageKo, totalKo, usedKo) {
            var total = parseFloat(totalKo());
            var used = parseFloat(usedKo());
            if (isNaN(total) || total <= 0
                || isNaN(used) || used < 0 || used > total) {
                usedPercentageKo(NaN);
                remainPercentageKo(NaN);
                return;
            }
            var usedPercentage = _roundTo(
                100 * used / total,
                0
            );
            usedPercentageKo(usedPercentage);
            remainPercentageKo(100 - usedPercentage);
        };

        self.safeUpdate = function (targetKo, calcFn, calcFnArguments) {
            if (!self.getLock(targetKo)) {
                return;
            }

            function getValueOrZero(x) {
                return parseFloat(x()) || 0;
            }

            targetKo(_roundTo(
                calcFn.apply(null, calcFnArguments.map(getValueOrZero)),
                1
            ));
        };

        // helper functions

        self.areDensityAndDiameterValid = function () {
            var diameter = parseFloat(diameterKo());
            var density = parseFloat(densityKo());
            return (!isNaN(diameter) && diameter > 0
                && !isNaN(density) && density > 0);
        };

        self.convertToLength = function (weight, density, diameter) {
            var volume = weight / (density *  Math.pow(10, -3)); // [mm^3] = [g] / ( [g/cm^3] * 10^-3 )
            var area = (Math.PI / 4) * Math.pow(diameter, 2); // [mm^2] = pi/4 * [mm]^2
            return _roundTo(volume / area, 0); // [mm] = [mm^3] / [mm^2}
        };

        self.convertToWeight = function (length, density, diameter) {
            var area = (Math.PI / 4) * Math.pow(diameter, 2); // [mm^2] = pi/4 * [mm]^2
            var volume = area * length; // [mm^3] = [mm^2] * [mm]
            return _roundTo(volume * density * Math.pow(10, -3), 1); // [g] = [mm^3] * [g/cm^3] * 10^3
        };

        // lock mechanism to prevent infinite update loops

        self.locksOfInProgressUpdate = [];
        self.getLock = function (updatableEntity) {
            if (!self.autoUpdateEnabled || self.locksOfInProgressUpdate.includes(updatableEntity)) {
                return false;
            }
            self.locksOfInProgressUpdate.push(updatableEntity);
            return true;
        };
        self.resetLocksIf = function (condition) {
            if (condition) {
                self.locksOfInProgressUpdate = [];
            }
        };
        self.amIRootChange = function (source) {
            return self.locksOfInProgressUpdate.length === 0 && self.getLock(source);
        };

        // ----------------- end: weight stuff
    }

    this.afterBinding = function(){
    }

    this._createSpoolItemForEditing = function(){
        self.spoolItemForEditing = new SpoolItem(null, true);

        self.spoolItemForEditing.isInActive.subscribe(function(newValue){
            self.spoolItemForEditing.isActive(!newValue);
        });

        return self.spoolItemForEditing;
    }

    this.createSpoolItemForTable = function(spoolData){
        var newSpoolItem = new SpoolItem(spoolData, false);
        return newSpoolItem;
    }

    this.updateCatalogs = function(allCatalogs){
        self.catalogs = allCatalogs;
        if (self.catalogs != null){
            self.allMaterials(self.catalogs["materials"]);
            self.allVendors(self.catalogs["vendors"]);
            self.allColors(self.catalogs["colors"]);
        } else {
            self.allMaterials([]);
            self.allVendors([]);
            self.allColors([]);
        }

    }

    this.updateTemplateSpools = function(templateSpoolsData){

        var spoolItemsArray = [];
        if (templateSpoolsData != null && templateSpoolsData.length !=0){
            spoolItemsArray = ko.utils.arrayMap(templateSpoolsData, function (spoolData) {
                var result = self.createSpoolItemForTable(spoolData);
                return result;
            });
        }
        self.templateSpools(spoolItemsArray);
    }

    this.showDialog = function(spoolItem, closeDialogHandler){
        self.autoUpdateEnabled = false;
        self.closeDialogHandler = closeDialogHandler;
        // get the current tool caunt
        self.allToolIndices([]);
        var toolCount = self.printerProfilesViewModel.currentProfileData().extruder.count();
        for (var toolIndex=0; toolIndex<toolCount; toolIndex++){
            self.allToolIndices.push(toolIndex);
        }

        // initial coloring
        self._reColorFilamentIcon(self.spoolItemForEditing.color());

        // prospective id for the {id} display name variable preview (issue #49)
        self._refreshNextSpoolId();

        if (spoolItem == null){
            // New Spool
            self.isExistingSpool(false);
            // reset values for a new spool
            self.spoolItemForEditing.update({});
            // self.spoolItemForEditing.isActive(true);
            self.spoolItemForEditing.isInActive(false);
            // self.spoolItemForEditing.isTemplate(false);
            // self.spoolItemForEditing.isActive(true);
            // self.spoolItemForEditing.databaseId(null);
            // self.spoolItemForEditing.costUnit(self.pluginSettings.currencySymbol());
            // self.spoolItemForEditing.displayName(null);
            // self.spoolItemForEditing.totalWeight(0.0);
            // self.spoolItemForEditing.usedWeight(0.0);
            // self.spoolItemForEditing.totalLength(0);
            // self.spoolItemForEditing.usedLength(0);
            // self.spoolItemForEditing.firstUse(null);
            // self.spoolItemForEditing.firstUseKO(null);
            // self.spoolItemForEditing.lastUse(null);
            // self.spoolItemForEditing.lastUseKO(null);
            // self.spoolItemForEditing.purchasedOn(null);
            // self.spoolItemForEditing.remainingCombinedWeight(0);
            // self.spoolItemForEditing.totalCombinedWeight(0);

            // Force the current day on new spools
            self.spoolItemForEditing.purchasedOnKO(moment().format("YYYY-MM-DD"))

            // Prefill diameter with the de-facto consumer standard of 1.75mm
            self.spoolItemForEditing.diameter(1.75);
        } else {
            self.isExistingSpool(true);
            // Make a copy of provided spoolItem
            spoolItemCopy = ko.mapping.toJS(spoolItem);
            self.spoolItemForEditing.update(spoolItemCopy);
        }
        self.spoolItemForEditing.drivenScope(COMBINED); // default calculation mode
        self.spoolItemForEditing.isSpoolVisible(true);

        self.spoolDialog.modal({
            minHeight: function() { return Math.max($.fn.modal.defaults.maxHeight() - 180, 250); },
            keyboard: false,
            clickClose: true,
            showClose: false,
            backdrop: "static"
        })
        .css({
            width: 'auto',
            'margin-left': function() { return -($(this).width() /2); }
        });


        self.autoUpdateEnabled = true;
    };

    self.copySpoolItem = function(){
        self._copySpoolItemForEditing(self.spoolItemForEditing);
    }

    self.copySpoolItemFromTemplate = function (spoolItem) {
        // don't treat the programmatic displayName change as combobox typing
        self._suppressTemplateCombo = true;
        // Copy everything
        self._copySpoolItemForEditing(spoolItem);
        // Reset values that shouldn't be copied

        var defaultExcludedNumericFields = [
            "usedLength",
            "usedLengthPercentage",
            "usedWeight",
            "usedPercentage",
        ];

        var defaultExcludedFields = [
            "selectedForTool",
            "version",
            "firstUseKO",
            "lastUseKO",
            "remainingWeight",
            "remainingPercentage",
            "remainingLength",
            "remainingLengthPercentage",
            "totalCombinedWeight",
            "remainingCombinedWeight",
        ].concat(defaultExcludedNumericFields);

        var allFieldNames = Object.keys(spoolItem);
        var excludedFieldsFromSettings =
            self.pluginSettings.excludedFromTemplateCopy();
        for (const fieldName of allFieldNames) {
            if (
                excludedFieldsFromSettings.includes(fieldName) ||
                defaultExcludedFields.includes(fieldName)
            ) {
                if (defaultExcludedNumericFields.includes(fieldName)) {
                    self.spoolItemForEditing[fieldName]("0");
                } else if (fieldName == "selectedForTool") {
                    // "" would wrongly pass the "selectedForTool() != undefined" check on save
                    // and trigger a selectSpool API call with an empty toolIndex (issue #48 follow-up)
                    self.spoolItemForEditing[fieldName](undefined);
                } else {
                    self.spoolItemForEditing[fieldName]("");
                }
            }
        }
        if (excludedFieldsFromSettings.includes("allNotes")) {
            if (self.noteEditor != null) {
                self.noteEditor.setText("", "api");
            }
            // self.spoolItemForEditing["noteText"]("");
            // self.spoolItemForEditing["noteDeltaFormat"]("");
            // self.spoolItemForEditing["noteHtml"]("");
        }
        // Trigger the auto-calculation
        var copiedWeight = self.spoolItemForEditing["spoolWeight"]();
        self.spoolItemForEditing.spoolWeight(0);
        self.spoolItemForEditing.spoolWeight(copiedWeight);

        // resolve display name variables from the copied field values; {id} stays and is resolved on save (issue #49)
        var copiedDisplayName = self.spoolItemForEditing.displayName();
        if (copiedDisplayName && copiedDisplayName.indexOf("{") !== -1){
            self.spoolItemForEditing.displayName(self._substituteDisplayNameVariables(copiedDisplayName));
        }

        self._suppressTemplateCombo = false;

        // close dialog
        self.templateSpoolDialog.modal("hide");
    };

    self._copySpoolItemForEditing = function (spoolItem) {
        self.isExistingSpool(false);
        self._refreshNextSpoolId();
        let spoolItemCopy = ko.mapping.toJS(spoolItem);
        self.spoolItemForEditing.update(spoolItemCopy);
        self.spoolItemForEditing.isTemplate(false);
        // This sets isActive as well
        self.spoolItemForEditing.isInActive(false);
        self.spoolItemForEditing.databaseId(null);
        self.spoolItemForEditing.isSpoolVisible(true);
    };

    self.saveSpoolItem = function(){

        // Input validation
        var displayName = self.spoolItemForEditing.displayName();
        if (!displayName || displayName.trim().length === 0){
            alert("Display name not entered!");
            return;
        }
        // workaround
        self.spoolItemForEditing.costUnit(self.pluginSettings.currencySymbol())

        var noteText = self.noteEditor.getText();
        var noteDeltaFormat = self.noteEditor.getContents();
        var noteHtml = self.noteEditor.getHtml();

        self.spoolItemForEditing.noteText(noteText);
        self.spoolItemForEditing.noteDeltaFormat(noteDeltaFormat);
        self.spoolItemForEditing.noteHtml(noteHtml);

        // read current note values and push to item, because there is no 2-way binding

//        self.printJobItemForEdit.noteText(noteText);
//        self.printJobItemForEdit.noteDeltaFormat(noteDeltaFormat);
//        self.printJobItemForEdit.noteHtml(noteHtml);
//
        self.apiClient.callSaveSpool(self.spoolItemForEditing, function(success, validationErrors){
            if (success === false){
                // server rejected the save - keep the dialog open and tell the user why
                var message = "Spool could not be saved.";
                if (validationErrors && validationErrors.length > 0){
                    message += "\n\n- " + validationErrors.join("\n- ");
                }
                alert(message);
                return;
            }
            self.spoolItemForEditing.isSpoolVisible(false);
            self.spoolDialog.modal('hide');
            if (self.spoolItemForEditing.selectedForTool() != undefined && self.printerStateViewModel.isPrinting()) {
                // spool that is currently printed from was updated - warn
                console.log(self.spoolItemForEditing.selectedForTool());
                alert("Your changes will not be applied automatically because a print is running. You can apply the changes by manually re-selecting the spool.");
                self.closeDialogHandler(true);
            }
            else if(self.spoolItemForEditing.selectedForTool() != undefined) {
                // spool that is currently selected for printing was updated - refresh
                self.closeDialogHandler(true, "selectSpoolForPrinting", self.spoolItemForEditing);
            }
            else {
                // some other spool was updated - not relevant
                self.closeDialogHandler(true);
            }
        });
    }

    self.deleteSpoolItem = function(){
        var result = confirm("Do you really want to delete this spool?");
        if (result == true){
            self.apiClient.callDeleteSpool(self.spoolItemForEditing.databaseId(), function(responseData) {
                self.spoolItemForEditing.isSpoolVisible(false);
                self.spoolDialog.modal('hide');
                self.closeDialogHandler(true);
            });
        }
    }

    self.selectSpoolItemForPrinting = function(){
        self.spoolItemForEditing.isSpoolVisible(false);
        self.spoolDialog.modal('hide');
        self.closeDialogHandler(false, "selectSpoolForPrinting", self.spoolItemForEditing);
    }

    // Template-combobox handlers (issue #48)
    self.onDisplayNameFocus = function(){
        if (self.isTemplateComboAvailable()){
            self.templateComboFilter("");
            self.templateComboVisible(true);
        }
        return true;
    }

    self.onDisplayNameBlur = function(){
        self.templateComboVisible(false);
        return true;
    }

    self.toggleTemplateCombo = function(data, event){
        if (self.isTemplateComboAvailable()){
            self.templateComboFilter("");
            self.templateComboVisible(!self.templateComboVisible());
        }
        // prevent the input from losing focus
        return false;
    }

    self.selectTemplateFromCombo = function(spoolItem){
        self.templateComboVisible(false);
        self.copySpoolItemFromTemplate(spoolItem);
    }

    self.selectAndCopyTemplateSpool = function(){

        /* needed for Filter-Search dropdown-menu */
        $('.dropdown-menu.keep-open').click(function(e) {
            e.stopPropagation();
        });

        self.templateSpoolDialog.modal({
                minHeight: function () {
                    return Math.max($.fn.modal.defaults.maxHeight() - 80, 250);
                },
                show: true
            });
    }
}
