/**
 * Add Spool Wizard: guided, step-by-step alternative to the edit dialog for creating a spool.
 *
 * Reached through the dropdown next to "+ Add Spool"; the plain button still opens the classic
 * dialog. The wizard asks for the same fields, only one topic at a time, and offers the two
 * OctoScale steps (weigh the spool, write its NFC tag) when a device is configured.
 *
 * It deliberately owns its own SpoolItem instead of sharing the edit dialog's: both dialogs can
 * be opened in the same session, and a shared item would leak half-entered wizard data into the
 * edit dialog (and vice versa).
 *
 * That item is built with isEditable:false on purpose. The editable variant wires vendor,
 * material, labels and the color pickers to fixed DOM ids inside #spool-form (see SpoolItem):
 * a second editable item would hijack the edit dialog's widgets. The wizard therefore uses plain
 * inputs and composes the color value itself (see composeColor below).
 *
 * Saving goes through POST /spool (callCreateSpool), not /saveSpool, because only that route
 * answers with the new database id - which the NFC step needs to put on the tag.
 */
function SpoolManagerAddSpoolWizard() {

    var FORMAT_DATE = SPOOLMANAGER_CONSTANTS.DATES.DISPLAY_FORMATS.DATE;
    var COMBINED = SPOOLMANAGER_CONSTANTS.FILAMENT_STATS_CALC_MODES.COMBINED;

    var self = this;

    self.apiClient = null;
    self.pluginSettings = null;
    self.catalogs = null;
    self.closeDialogHandler = null;
    self.wizardDialog = null;

    self.spoolItemForCreation = null;
    self.octoScaleWeighing = null;
    self.octoScaleTagWriter = null;
    // the three color picker widgets, created in initBinding (see _createColorPickers)
    self.colorPickers = [];

    // catalogs for the vendor/material/color inputs, same source as the edit dialog
    self.allMaterials = ko.observableArray([]);
    self.allVendors = ko.observableArray([]);
    self.allColors = ko.observableArray([]);
    self.templateSpools = ko.observableArray([]);

    self.useFullFieldSet = ko.observable(false);
    self.nextSpoolId = ko.observable(null);

    // Color inputs. These stay plain observables rather than being the pickers' own: the
    // subscriptions below are wired in the constructor, while the picker widgets can only be
    // created in initBinding (their containers do not exist before that). The pickers are
    // attached to these observables there, in both directions.
    self.colorHex = ko.observable(SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT);
    self.colorHex2 = ko.observable("#0000ff");
    self.colorHex3 = ko.observable("#ffff00");
    self.colorCount = ko.observable(1);
    self.isRainbow = ko.observable(false);
    self.isTransparent = ko.observable(false);
    // transparent with no base tint at all -> stored as plain "transparent"
    self.isColorless = ko.observable(false);

    // A colorless spool has no hex value to derive a name from, so replace a name that was
    // suggested for the previous base color ("Red" would be wrong for clear filament).
    self.isColorless.subscribe(function(newValue){
        if (newValue === true && self.spoolItemForCreation != null){
            var currentName = (self.spoolItemForCreation.colorName() || "").trim();
            if (currentName.length === 0 || tinycolor(currentName).isValid()){
                self.spoolItemForCreation.colorName("Transparent");
            }
        }
    });

    self.addColor = function(){
        if (self.colorCount() < 3){
            self.colorCount(self.colorCount() + 1);
        }
    };

    self.removeColor = function(){
        if (self.colorCount() > 1){
            self.colorCount(self.colorCount() - 1);
        }
    };

    self.finishOptions = SPOOLMANAGER_CONSTANTS.FINISH_OPTIONS;

    // Vendor/material are offered as a dropdown of known values plus a free-text field. Picking
    // from the dropdown writes through to the spool item; typing clears the dropdown so the two
    // never contradict each other.
    // (wired to the spool item in initBinding, once it exists)
    self.selectedVendor = ko.observable(null);
    self.selectedMaterial = ko.observable(null);

    // Density autosuggest. SpoolItem has the same logic, but it only fires while the *edit*
    // dialog is visible (it guards on DOM_SELECTORS.SPOOL_DIALOG), so the wizard needs its own.
    //
    // Changing the material always re-applies its density, except when the user typed a density
    // that belongs to no material. Only skipping "any non-empty density" would strand the value
    // of a previously picked material: selecting ABS+ and then PETG kept 1.06 g/cm³.
    self._lastSuggestedDensity = null;

    self._suggestDensityForMaterial = function(materialName){
        if (!materialName){
            return;
        }
        var density = SPOOLMANAGER_CONSTANTS.MATERIALS_DENSITY_MAPPING[
            SPOOLMANAGER_UTILS.normalizeMaterialKey(materialName)
        ];
        if (!density){
            return;
        }

        var currentDensity = parseFloat(self.spoolItemForCreation.density());
        var isUnset = isNaN(currentDensity) || currentDensity <= 0;
        var isOurOwnSuggestion = self._lastSuggestedDensity != null && currentDensity === self._lastSuggestedDensity;
        if (isUnset || isOurOwnSuggestion){
            self.spoolItemForCreation.density(density);
            self._lastSuggestedDensity = density;
        }
    };

    // set once the spool has been created, drives the NFC step and blocks a second save
    self.createdDatabaseId = ko.observable(null);
    self.isSaving = ko.observable(false);
    self.saveErrorMessages = ko.observableArray([]);

    self.currentStepIndex = ko.observable(0);

    ///////////////////////////////////////////////////////////////////////////////////////// STEPS

    // Step ids in fixed order; visibility decides which of them the user actually walks through.
    // Keeping the full list static (rather than rebuilding an array) means the Back/Next logic
    // never has to care about a step appearing or disappearing mid-flow.
    var ALL_STEPS = [
        { id: "mode",         title: "How much detail?",     isVisible: function(){ return true; } },
        { id: "identity",     title: "Name and material",    isVisible: function(){ return true; } },
        { id: "color",        title: "Color and finish",     isVisible: function(){ return true; } },
        { id: "filament",     title: "Filament properties",  isVisible: function(){ return true; } },
        { id: "weight",       title: "Weight",               isVisible: function(){ return true; } },
        { id: "temperatures", title: "Temperatures",         isVisible: function(){ return self.useFullFieldSet(); } },
        { id: "purchase",     title: "Purchase and cost",    isVisible: function(){ return self.useFullFieldSet(); } },
        { id: "misc",         title: "Additional details",   isVisible: function(){ return self.useFullFieldSet(); } },
        { id: "review",       title: "Review and save",      isVisible: function(){ return true; } },
        { id: "nfc",          title: "Write NFC tag",        isVisible: function(){ return self.isOctoScaleEnabled() && self.createdDatabaseId() != null; } }
    ];

    self.isOctoScaleEnabled = ko.pureComputed(function(){
        if (self.pluginSettings == null || self.pluginSettings.octoScaleEnabled == null){
            return false;
        }
        return self.pluginSettings.octoScaleEnabled() == true;
    });

    // Display unit for every weight shown in the wizard. pluginSettings is only assigned in
    // initBinding, so this has to tolerate null - hence the read through the helper rather than
    // a captured reference.
    self.selectedWeightUnit = function(){
        return SPOOLMANAGER_UTILS.selectedWeightUnit(self.pluginSettings);
    };

    self.weightUnitText = ko.pureComputed(self.selectedWeightUnit);

    self.visibleSteps = ko.pureComputed(function(){
        return ko.utils.arrayFilter(ALL_STEPS, function(step){
            return step.isVisible();
        });
    });

    self.currentStep = ko.pureComputed(function(){
        var steps = self.visibleSteps();
        var index = Math.min(self.currentStepIndex(), steps.length - 1);
        return steps[Math.max(index, 0)];
    });

    self.currentStepId = ko.pureComputed(function(){
        var step = self.currentStep();
        return step != null ? step.id : null;
    });

    self.currentStepTitle = ko.pureComputed(function(){
        var step = self.currentStep();
        return step != null ? step.title : "";
    });

    self.stepCounterText = ko.pureComputed(function(){
        return "Step " + (self.currentStepIndex() + 1) + " of " + self.visibleSteps().length;
    });

    self.progressPercentage = ko.pureComputed(function(){
        var total = self.visibleSteps().length;
        if (total <= 1){
            return 100;
        }
        return Math.round(((self.currentStepIndex() + 1) / total) * 100);
    });

    ///////////////////////////////////////////////////////////////////////////////////// VALIDATION

    // The same three fields the edit dialog treats as mandatory (see _isEveryMandatoryFieldValid
    // there): a spool without them cannot be tracked. Checked per step so the user is stopped at
    // the step that is missing something, not at the very end.
    self.isDisplayNamePresent = ko.pureComputed(function(){
        if (self.spoolItemForCreation == null) return false;
        return SPOOLMANAGER_UTILS.isDisplayNamePresent(self.spoolItemForCreation);
    });

    self.isColorNamePresent = ko.pureComputed(function(){
        if (self.spoolItemForCreation == null) return false;
        return SPOOLMANAGER_UTILS.isColorNamePresent(self.spoolItemForCreation);
    });

    self.isTotalCombinedWeightPresent = ko.pureComputed(function(){
        if (self.spoolItemForCreation == null) return false;
        return SPOOLMANAGER_UTILS.isTotalCombinedWeightPresent(self.spoolItemForCreation);
    });

    self.areMandatoryFieldsValid = ko.pureComputed(function(){
        return self.isDisplayNamePresent() && self.isColorNamePresent() && self.isTotalCombinedWeightPresent();
    });

    // Reason the Next button is blocked on the current step, empty when it is fine to continue.
    self.currentStepBlockReason = ko.pureComputed(function(){
        var stepId = self.currentStepId();
        if (stepId === "identity" && !self.isDisplayNamePresent()){
            return "Please enter a display name.";
        }
        if (stepId === "color" && !self.isColorNamePresent()){
            return "Please enter a color name.";
        }
        if (stepId === "weight" && !self.isTotalCombinedWeightPresent()){
            return "Please enter the total weight (spool including filament).";
        }
        return "";
    });

    self.canGoNext = ko.pureComputed(function(){
        return self.currentStepBlockReason().length === 0;
    });

    self.canGoBack = ko.pureComputed(function(){
        // once the spool exists, going back would suggest the entered data can still be changed here
        return self.currentStepIndex() > 0 && self.createdDatabaseId() == null;
    });

    self.isLastInputStep = ko.pureComputed(function(){
        return self.currentStepId() === "review";
    });

    ///////////////////////////////////////////////////////////////////////////////////// NAVIGATION

    self.goNext = function(){
        if (!self.canGoNext()){
            return;
        }
        self._leaveCurrentStep();
        if (self.currentStepIndex() < self.visibleSteps().length - 1){
            self.currentStepIndex(self.currentStepIndex() + 1);
        }
    };

    self.goBack = function(){
        if (!self.canGoBack()){
            return;
        }
        self._leaveCurrentStep();
        self.currentStepIndex(self.currentStepIndex() - 1);
    };

    // Stop any device polling that belongs to the step being left, so a wizard left open on
    // another step does not keep hitting the scale.
    self._leaveCurrentStep = function(){
        if (self.currentStepId() === "weight" && self.octoScaleWeighing != null){
            self.octoScaleWeighing.stop();
        }
        if (self.currentStepId() === "nfc" && self.octoScaleTagWriter != null){
            self.octoScaleTagWriter.stop();
        }
    };

    ///////////////////////////////////////////////////////////////////////////////////// WEIGHING

    // A brand new spool on the scale reads as spool core + filament, which is exactly what
    // totalCombinedWeight means. (The equivalent backend conversion for spools already in use
    // lives in _applyMeasuredGrossWeight; the wizard only ever creates fresh spools.)
    self.applyMeasuredWeight = function(){
        if (self.octoScaleWeighing == null){
            return;
        }
        var grams = self.octoScaleWeighing.currentWeight();
        if (grams == null){
            return;
        }
        self.spoolItemForCreation.totalCombinedWeight(Math.round(grams * 10) / 10);
        self._syncFilamentWeight();
    };

    self.toggleWeighing = function(){
        if (self.octoScaleWeighing != null){
            self.octoScaleWeighing.toggle();
        }
    };

    ////////////////////////////////////////////////////////////////////////////////// TEMPLATE COPY

    self.selectedTemplateSpool = ko.observable(null);

    self.hasTemplateSpools = ko.pureComputed(function(){
        return self.templateSpools().length > 0;
    });

    // Prefill from a template spool. Everything that describes a *specific physical spool*
    // (ids, usage, dates) is dropped - only the product description is copied.
    self.applyTemplateSpool = function(templateSpoolItem){
        if (templateSpoolItem == null){
            return;
        }
        var templateData = ko.mapping.toJS(templateSpoolItem);
        var copiedFields = [
            "displayName", "vendor", "material", "density", "diameter", "diameterTolerance",
            "colorName", "finish",
            "totalWeight", "spoolWeight", "flowRateCompensation",
            "temperature", "bedTemperature", "enclosureTemperature",
            "offsetTemperature", "offsetBedTemperature", "offsetEnclosureTemperature",
            "purchasedFrom", "cost", "costUnit", "labels"
        ];
        copiedFields.forEach(function(fieldName){
            var observable = self.spoolItemForCreation[fieldName];
            if (typeof observable === "function" && templateData[fieldName] !== undefined){
                observable(templateData[fieldName]);
            }
        });

        // combined weight is derived, not stored on the template in a directly usable form
        var totalWeight = parseFloat(templateData.totalWeight);
        var spoolWeight = parseFloat(templateData.spoolWeight);
        if (!isNaN(totalWeight)){
            self.spoolItemForCreation.totalCombinedWeight(totalWeight + (isNaN(spoolWeight) ? 0 : spoolWeight));
        }

        // the template's composed color has to be split back into the wizard's own inputs
        self._applyColorValue(templateData.color);

        self.selectedTemplateSpool(templateSpoolItem);
    };

    self.clearTemplateSelection = function(){
        self.selectedTemplateSpool(null);
    };

    /////////////////////////////////////////////////////////////////////////////////////// COLOR

    self._composeColor = function(){
        if (self.spoolItemForCreation == null){
            return;
        }
        var pickers = [self.colorHex(), self.colorHex2(), self.colorHex3()];
        self.spoolItemForCreation.color(SPOOLMANAGER_UTILS.composeSpoolColor({
            isRainbow: self.isRainbow(),
            isTransparent: self.isTransparent(),
            isUntinted: self.isTransparent() && self.isColorless(),
            colors: pickers.slice(0, self.colorCount())
        }));
    };

    // rainbow and transparent describe the same slot on the spool, so they exclude each other
    self.isRainbow.subscribe(function(newValue){
        if (newValue === true && self.isTransparent() === true){
            self.isTransparent(false);
        }
        self._composeColor();
    });
    self.isTransparent.subscribe(function(newValue){
        if (newValue === true && self.isRainbow() === true){
            self.isRainbow(false);
        }
        if (newValue !== true){
            // "colorless" only makes sense for a transparent spool
            self.isColorless(false);
        }
        self._composeColor();
    });
    [self.colorHex, self.colorHex2, self.colorHex3, self.colorCount, self.isColorless].forEach(function(observable){
        observable.subscribe(function(){
            self._composeColor();
        });
    });

    // Split a stored color value back into the wizard's inputs.
    self._applyColorValue = function(colorValue){
        if (!colorValue){
            return;
        }
        var parts = SPOOLMANAGER_UTILS.parseSpoolColor(colorValue);
        self.isRainbow(parts.isRainbow);
        self.isTransparent(parts.isTransparent);
        self.isColorless(parts.isUntinted);
        var pickers = [self.colorHex, self.colorHex2, self.colorHex3];
        for (var i = 0; i < parts.colors.length && i < pickers.length; i++){
            pickers[i](parts.colors[i]);
        }
        self.colorCount(Math.min(Math.max(parts.colors.length, 1), 3));
        self._composeColor();
    };

    // Creates the three picker widgets and ties them to colorHex/colorHex2/colorHex3. Called from
    // initBinding, because the containers only exist once the wizard template is in the DOM.
    //
    // The link is two-way: the picker pushes what the user chose into the observable, and
    // _applyColorValue (template spools, reset) pushes the other way. A guard per picker keeps the
    // two from echoing each other.
    self._createColorPickers = function(){
        var pickerSpecs = [
            { elementId: "wizard-color-picker",  observable: self.colorHex,  suggestsName: true },
            { elementId: "wizard-color-picker2", observable: self.colorHex2, suggestsName: false },
            { elementId: "wizard-color-picker3", observable: self.colorHex3, suggestsName: false }
        ];

        self.colorPickers = pickerSpecs.map(function(spec){
            var picker = SPOOLMANAGER_COLOR_PICKER.create("#" + spec.elementId, {
                initialColor: spec.observable()
            });
            var syncing = false;

            picker.selectedColor.subscribe(function(newColor){
                if (syncing == true){
                    return;
                }
                syncing = true;
                try {
                    spec.observable(newColor);
                    if (spec.suggestsName == true){
                        // used to hang off the native input's change event
                        self.suggestColorName();
                    }
                } finally {
                    syncing = false;
                }
            });

            spec.observable.subscribe(function(newColor){
                if (syncing == true){
                    return;
                }
                syncing = true;
                try {
                    picker.selectedColor(newColor);
                } finally {
                    syncing = false;
                }
            });

            return picker;
        });
    };

    // Suggest a color name from the picked value, but never overwrite something the user typed.
    self.suggestColorName = function(){
        if ((self.spoolItemForCreation.colorName() || "").trim().length > 0){
            return;
        }
        if (self.isRainbow()){
            self.spoolItemForCreation.colorName("Rainbow");
            return;
        }
        if (self.isTransparent() && self.isColorless()){
            self.spoolItemForCreation.colorName("Transparent");
            return;
        }
        if (self.colorCount() > 1){
            // multi-color: no sensible single name to derive
            return;
        }
        var suggestion = tinycolor(self.colorHex()).toName();
        if (suggestion !== false){
            self.spoolItemForCreation.colorName(self.isTransparent() ? "Transparent " + suggestion : suggestion);
        }
    };

    ////////////////////////////////////////////////////////////////////////////////// DERIVED WEIGHT

    // The backend stores totalWeight (filament only) and spoolWeight (empty core) separately,
    // while the wizard asks for the combined weight that a scale actually shows. Keep the
    // filament share in sync so the created spool has sensible values in every field.
    self.totalCombinedWeightChanged = function(){
        self._syncFilamentWeight();
        return true;
    };

    self._syncFilamentWeight = function(){
        var combined = parseFloat(self.spoolItemForCreation.totalCombinedWeight());
        if (isNaN(combined)){
            return;
        }
        var spoolWeight = parseFloat(self.spoolItemForCreation.spoolWeight());
        if (isNaN(spoolWeight)){
            spoolWeight = 0;
        }
        var filamentWeight = combined - spoolWeight;
        self.spoolItemForCreation.totalWeight(filamentWeight > 0 ? Math.round(filamentWeight * 10) / 10 : 0);
    };

    self.filamentWeightPreview = ko.pureComputed(function(){
        if (self.spoolItemForCreation == null){
            return "";
        }
        var combined = parseFloat(self.spoolItemForCreation.totalCombinedWeight());
        if (isNaN(combined)){
            return "";
        }
        var spoolWeight = parseFloat(self.spoolItemForCreation.spoolWeight());
        if (isNaN(spoolWeight)){
            spoolWeight = 0;
        }
        var filamentWeight = combined - spoolWeight;
        if (filamentWeight < 0){
            return "The empty spool weight is higher than the total weight.";
        }
        // round in grams first, then convert - otherwise kg display would round away the tenths
        return "Filament: " + SPOOLMANAGER_UTILS.formatWeightForDisplay(
            Math.round(filamentWeight * 10) / 10, self.pluginSettings);
    });

    ////////////////////////////////////////////////////////////////////////////////////// REVIEW

    var reviewEntry = function(label, value){
        if (value === null || value === undefined || ("" + value).trim().length === 0){
            return null;
        }
        return { label: label, value: "" + value };
    };

    self.reviewEntries = ko.pureComputed(function(){
        if (self.spoolItemForCreation == null){
            return [];
        }
        var item = self.spoolItemForCreation;
        var entries = [
            reviewEntry("Display name", item.displayName()),
            reviewEntry("Vendor", item.vendor()),
            reviewEntry("Material", item.material()),
            reviewEntry("Color", item.colorName()),
            reviewEntry("Finish", item.finish()),
            reviewEntry("Density", item.density() ? item.density() + " g/cm³" : null),
            reviewEntry("Diameter", item.diameter() ? item.diameter() + " mm" : null),
            reviewEntry("Total weight", item.totalCombinedWeight()
                ? SPOOLMANAGER_UTILS.formatWeightForDisplay(item.totalCombinedWeight(), self.pluginSettings) : null),
            reviewEntry("Empty spool weight", item.spoolWeight()
                ? SPOOLMANAGER_UTILS.formatWeightForDisplay(item.spoolWeight(), self.pluginSettings) : null)
        ];
        if (self.useFullFieldSet()){
            entries = entries.concat([
                reviewEntry("Tool temperature", item.temperature()),
                reviewEntry("Bed temperature", item.bedTemperature()),
                reviewEntry("Enclosure temperature", item.enclosureTemperature()),
                reviewEntry("Flow rate compensation", item.flowRateCompensation()),
                reviewEntry("Serial number", item.code()),
                reviewEntry("Batch number", item.batchNumber()),
                reviewEntry("Purchased from", item.purchasedFrom()),
                reviewEntry("Purchased on", item.purchasedOnKO()),
                reviewEntry("Cost", item.cost())
            ]);
        }
        return ko.utils.arrayFilter(entries, function(entry){
            return entry != null;
        });
    });

    ///////////////////////////////////////////////////////////////////////////////////////// SAVE

    self.saveSpool = function(){
        if (self.isSaving() || self.createdDatabaseId() != null){
            return;
        }
        if (!self.areMandatoryFieldsValid()){
            return;
        }

        self.saveErrorMessages([]);
        self.isSaving(true);

        // combined weight is a view-model field; the backend wants the filament share
        self._syncFilamentWeight();

        // A spool created here has not been printed from yet, so its usage is zero - not unknown.
        // Leaving these null stores NULL in the database (saveSpool() then derives remainingWeight
        // from an assumed 0), and the edit dialog shows empty "Used"/"Remaining" fields afterwards
        // because its calculation chain has no usedWeight to work from. Spools created through the
        // classic dialog carry 0 here, so match that.
        self.spoolItemForCreation.usedWeight(0);
        self.spoolItemForCreation.usedLength(0);
        // same workaround as the edit dialog: the currency symbol is a setting, not a field
        self.spoolItemForCreation.costUnit(self.pluginSettings.currencySymbol());
        self.spoolItemForCreation.isActive(true);
        self.spoolItemForCreation.isInActive(false);

        self.apiClient.callCreateSpool(self.spoolItemForCreation, function(success, responseData, validationErrors){
            self.isSaving(false);
            if (success !== true){
                if (validationErrors && validationErrors.length > 0){
                    self.saveErrorMessages(validationErrors);
                } else {
                    self.saveErrorMessages(["The spool could not be created."]);
                }
                return;
            }

            var databaseId = responseData != null ? responseData.databaseId : null;
            self.createdDatabaseId(databaseId);

            // the table/sidebar refresh is pushed by the backend (POST /spool sends
            // "reloadTable and sidebarSpools"), so nothing to do here for other open UIs
            if (self.isOctoScaleEnabled() && databaseId != null){
                // move on to the NFC step; visibleSteps() has just grown by one entry
                self.currentStepIndex(self.visibleSteps().length - 1);
                self.octoScaleTagWriter.start(databaseId);
            } else {
                self.closeDialog(true);
            }
        });
    };

    self.skipTagWriting = function(){
        self.closeDialog(true);
    };

    self.finishAfterTagWriting = function(){
        self.closeDialog(true);
    };

    ///////////////////////////////////////////////////////////////////////////////// DIALOG HANDLING

    self.initBinding = function(apiClient, pluginSettings){
        self.apiClient = apiClient;
        self.pluginSettings = pluginSettings;

        self.wizardDialog = $("#dialog_addSpoolWizard");
        // isEditable:false is essential - see the note at the top of this file. The editable
        // variant wires vendor/material/labels and the color pickers to the edit dialog's
        // fixed ids; creating a second editable item would steal them from that dialog.
        self.spoolItemForCreation = new SpoolItem(null, { isEditable: false, catalogs: self.catalogs });

        // The weight inputs bind to these, not to the SpoolItem observables: the item keeps holding
        // grams for validation and for the save payload, while the field shows the configured unit.
        // Created here because spoolItemForCreation does not exist before this point - safe, since
        // initBinding runs from onBeforeBinding, i.e. before ko.applyBindings.
        self.totalCombinedWeightDisplay = SPOOLMANAGER_UTILS.makeWeightDisplayKo(
            self.spoolItemForCreation.totalCombinedWeight, self.selectedWeightUnit);
        self.spoolWeightDisplay = SPOOLMANAGER_UTILS.makeWeightDisplayKo(
            self.spoolItemForCreation.spoolWeight, self.selectedWeightUnit);

        self._createColorPickers();
        self.spoolItemForCreation.isInActive.subscribe(function(newValue){
            self.spoolItemForCreation.isActive(!newValue);
        });

        // dropdown selection -> spool item
        self.selectedVendor.subscribe(function(newValue){
            if (newValue){
                self.spoolItemForCreation.vendor(newValue);
            }
        });
        self.selectedMaterial.subscribe(function(newValue){
            if (newValue){
                self.spoolItemForCreation.material(newValue);
            }
        });
        // material (from the dropdown or typed) -> density suggestion
        self.spoolItemForCreation.material.subscribe(function(newValue){
            self._suggestDensityForMaterial(newValue);
        });

        self.octoScaleWeighing = new SpoolManagerOctoScaleWeighing(apiClient, pluginSettings);
        self.octoScaleTagWriter = new SpoolManagerOctoScaleTagWriter(apiClient);

        // a dialog closed with Esc or the backdrop must not leave pollers running
        self.wizardDialog.on("hidden", function(){
            self.octoScaleWeighing.stop();
            self.octoScaleTagWriter.stop();
        });
    };

    self.afterBinding = function(){
    };

    self.updateCatalogs = function(allCatalogs){
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
    };

    self.updateTemplateSpools = function(templateSpoolItems){
        self.templateSpools(templateSpoolItems || []);
    };

    self.showDialog = function(closeDialogHandler){
        self.closeDialogHandler = closeDialogHandler;

        // reset everything from a previous run
        self.spoolItemForCreation.update({}, { catalogs: self.catalogs });
        self.spoolItemForCreation.isInActive(false);
        self.spoolItemForCreation.drivenScope(COMBINED);
        self.spoolItemForCreation.isSpoolVisible(true);
        // same new-spool defaults the edit dialog applies
        self.spoolItemForCreation.purchasedOnKO(moment().format(FORMAT_DATE));
        self.spoolItemForCreation.diameter(1.75);

        self.colorHex(SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT);
        self.colorHex2("#0000ff");
        self.colorHex3("#ffff00");
        self.colorCount(1);
        self.isRainbow(false);
        self.isTransparent(false);
        self.isColorless(false);
        self._composeColor();

        // clear the dropdowns too, otherwise the previous run's selection is still shown (and its
        // density already applied) before the user picks anything
        self.selectedVendor(null);
        self.selectedMaterial(null);
        self._lastSuggestedDensity = null;

        self.createdDatabaseId(null);
        self.saveErrorMessages([]);
        self.selectedTemplateSpool(null);
        self.currentStepIndex(0);
        self.useFullFieldSet(self.pluginSettings.defaultViewModeSimple() != true);

        // prospective id for the {id} display name preview (issue #49)
        self.apiClient.callLoadNextSpoolId(function(responseData){
            if (responseData != null && responseData.nextSpoolId != null){
                self.nextSpoolId(responseData.nextSpoolId);
            }
        });

        self.wizardDialog.modal({
            keyboard: false,
            clickClose: false,
            showClose: false,
            backdrop: "static"
        });
    };

    self.closeDialog = function(wasSaved){
        self.octoScaleWeighing.stop();
        self.octoScaleTagWriter.stop();
        self.spoolItemForCreation.isSpoolVisible(false);
        self.wizardDialog.modal("hide");
        if (self.closeDialogHandler != null){
            self.closeDialogHandler(wasSaved === true);
        }
    };

    self.cancelDialog = function(){
        self.closeDialog(false);
    };
}
