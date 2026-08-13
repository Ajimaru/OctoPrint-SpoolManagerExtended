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
    init: function (element, valueAccessor, allBindings, viewModel, bindingContext) {
        var options = valueAccessor();
        var observable = options.value; // optional: only written in standalone mode
        var invalidFields = options.invalidFields; // ko.observableArray of field keys
        var fieldKey = options.key;
        // trackOnly mode: another binding (e.g. the unit-conversion `value:` binding) owns the value,
        // so we only observe validity for the red border + Save block and never touch the observable.
        var trackOnly = options.trackOnly === true;

        // sticky flag set when a paste of non-numeric text is detected; cleared on the next
        // real edit. Guards against browsers that silently drop a bad paste to "" without badInput.
        var pasteRejected = false;
        // accepts optional sign, digits, one decimal separator (. or ,) and scientific notation
        var numericPattern = /^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?$/;

        var setInvalidFlag = function (isInvalid) {
            $(element).toggleClass("spm-number-invalid", isInvalid);
            var current = invalidFields();
            var idx = current.indexOf(fieldKey);
            if (isInvalid && idx === -1) {
                invalidFields.push(fieldKey);
            } else if (!isInvalid && idx !== -1) {
                invalidFields.splice(idx, 1);
            }
        };

        var updateValidity = function () {
            // element.validity.valid is false for badInput, rangeUnderflow, stepMismatch, ...
            var isInvalid =
                pasteRejected || (element.validity && element.validity.valid === false);
            setInvalidFlag(isInvalid);

            // standalone mode: only push a real (parseable) value, never a half-typed garbage state
            if (!trackOnly && !isInvalid && observable) {
                var raw = element.value;
                observable(raw === "" ? null : raw);
            }
        };

        var subscription = null;
        if (!trackOnly && observable) {
            // keep the input's displayed text in sync when the observable changes programmatically
            subscription = observable.subscribe(function (newValue) {
                if (
                    !pasteRejected &&
                    element.validity &&
                    element.validity.valid !== false
                ) {
                    var display =
                        newValue === null || newValue === undefined ? "" : "" + newValue;
                    if (element.value !== display) {
                        element.value = display;
                    }
                }
            });
            var initial = ko.unwrap(observable);
            element.value = initial === null || initial === undefined ? "" : "" + initial;
        }

        // intercept non-numeric paste before the browser can silently discard it
        $(element).on("paste.numberField", function (e) {
            var clip = (e.originalEvent || e).clipboardData || window.clipboardData;
            if (!clip) {
                return;
            }
            var text = clip.getData("text");
            if (
                text != null &&
                text.trim().length > 0 &&
                numericPattern.test(text.trim()) === false
            ) {
                e.preventDefault();
                pasteRejected = true;
                setInvalidFlag(true);
            }
        });
        // any real edit (typing, arrows, deleting) clears a previous paste rejection
        $(element).on("keydown.numberField", function () {
            if (pasteRejected) {
                pasteRejected = false;
            }
        });

        $(element).on(
            "input.numberField change.numberField blur.numberField",
            updateValidity
        );
        // run once so a value that arrives invalid (e.g. loaded then edited) is caught immediately
        updateValidity();

        // Programmatic writes (density autosuggest, weight auto-calculation, dialog reload)
        // update the input without firing any DOM event, so a previously set invalid flag
        // would stick (red border + blocked Save) until the user touches the field again.
        // Re-validate whenever the value-owning observable changes; deferred so the value
        // binding has already synced element.value before we read element.validity.
        var trackedValue = trackOnly ? allBindings.get("value") : observable;
        var programmaticSubscription = null;
        if (trackedValue && typeof trackedValue.subscribe === "function") {
            programmaticSubscription = trackedValue.subscribe(function () {
                setTimeout(function () {
                    pasteRejected = false;
                    updateValidity();
                }, 0);
            });
        }

        ko.utils.domNodeDisposal.addDisposeCallback(element, function () {
            $(element).off(".numberField");
            if (subscription) {
                subscription.dispose();
            }
            if (programmaticSubscription) {
                programmaticSubscription.dispose();
            }
            // drop this field from the invalid set when the node goes away (dialog close/reopen)
            var current = invalidFields();
            var idx = current.indexOf(fieldKey);
            if (idx !== -1) {
                invalidFields.splice(idx, 1);
            }
        });
    }
};

// Dialog functionality
function SpoolManagerEditSpoolDialog() {
    var self = this;

    // keys of number inputs currently holding an invalid value (see ko.bindingHandlers.numberField)
    self.invalidNumberFields = ko.observableArray([]);
    // human readable labels for the Save-blocked hint, keyed by the field key used in the template
    self.numberFieldLabels = {
        density: "Density",
        diameter: "Diameter",
        diameterTolerance: "Diameter tolerance",
        flowRateCompensation: "Flow rate compensation",
        temperature: "Tool temperature",
        minTemperature: "Tool temperature (min)",
        maxTemperature: "Tool temperature (max)",
        bedTemperature: "Bed temperature",
        minBedTemperature: "Bed temperature (min)",
        maxBedTemperature: "Bed temperature (max)",
        enclosureTemperature: "Enclosure temperature",
        offsetTemperature: "Offset tool temperature",
        offsetBedTemperature: "Offset bed temperature",
        offsetEnclosureTemperature: "Offset enclosure temperature",
        cost: "Cost",
        totalWeight: "Filament amount (initial)",
        spoolWeight: "Empty spool weight",
        usedWeight: "Filament amount (used)",
        totalLength: "Filament length (initial)",
        usedLength: "Filament length (used)",
        totalCombinedWeight: "Combined weight (initial)",
        remainingCombinedWeight: "Combined weight (remaining)"
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////////////// CONSTANTS
    // Shared constants & helpers moved to common/constants.js / common/utils.js
    // (structure adopted from mdziekon/OctoPrint-SpoolManager PR #11, GH-10).
    // Aliases are function-scoped on purpose: OctoPrint concatenates all plugin JS into one
    // bundle, top-level declarations with generic names would collide with other files.
    var roundWithPrecision = SPOOLMANAGER_UTILS.roundWithPrecision;
    var FORMAT_DATETIME_LOCAL =
        SPOOLMANAGER_CONSTANTS.DATES.DISPLAY_FORMATS.DATETIME_LOCAL;
    var FORMAT_DATE = SPOOLMANAGER_CONSTANTS.DATES.DISPLAY_FORMATS.DATE;

    // also referenced by the jinja2 template as spoolDialog.scopeValues
    self.scopeValues = SPOOLMANAGER_CONSTANTS.FILAMENT_STATS_CALC_MODES;

    var FILAMENT = self.scopeValues.FILAMENT;
    var COMBINED = self.scopeValues.COMBINED;
    var SPOOL = self.scopeValues.SPOOL;

    ///////////////////////////////////////////////////////////////////////////////////////////////////////// ITEM MODEL
    // SpoolItem was extracted to SpoolManager-SpoolItem.js
    // (adopted from mdziekon/OctoPrint-SpoolManager PR #11, GH-10)

    ///////////////////////////////////////////////////////////////////////////////////////////////// Instance Variables
    self.spoolDialog = null;
    self.templateSpoolDialog = null;
    self.closeDialogHandler = null;
    self.spoolItemForEditing = null;
    self.templateSpools = ko.observableArray([]);

    // static options for the "Finish" dropdown (shared with the Add Spool Wizard)
    self.finishOptions = SPOOLMANAGER_CONSTANTS.FINISH_OPTIONS;

    // Template-combobox on the displayname field (issue #48)
    self.templateComboVisible = ko.observable(false);
    self.templateComboFilter = ko.observable("");
    self._suppressTemplateCombo = false;
    self.filteredTemplateSpools = ko.pureComputed(function () {
        var filterText = ("" + (self.templateComboFilter() || "")).trim().toLowerCase();
        var allTemplates = self.templateSpools();
        if (filterText.length == 0) {
            return allTemplates;
        }
        return ko.utils.arrayFilter(allTemplates, function (spoolItem) {
            var haystack =
                (spoolItem.displayName() || "") +
                " " +
                (spoolItem.material() || "") +
                " " +
                (spoolItem.vendor() || "");
            return haystack.toLowerCase().indexOf(filterText) !== -1;
        });
    });
    self.isTemplateComboAvailable = ko.pureComputed(function () {
        return self.isExistingSpool() == false && self.templateSpools().length > 0;
    });

    // Display name variables (issue #49): prospective databaseId of the next created spool for the {id} preview
    self.nextSpoolId = ko.observable(null);

    self._refreshNextSpoolId = function () {
        if (self.apiClient == null) {
            return;
        }
        self.apiClient.callLoadNextSpoolId(function (responseData) {
            if (responseData != null && responseData.nextSpoolId != null) {
                self.nextSpoolId(responseData.nextSpoolId);
            }
        });
    };

    // replaces all variables except {id} (only known server-side after saving) with the current field values
    self._substituteDisplayNameVariables = function (displayName) {
        var spoolItem = self.spoolItemForEditing;
        var asText = function (value) {
            if (
                value === null ||
                value === undefined ||
                (typeof value === "number" && isNaN(value))
            ) {
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
            "{batch}": asText(spoolItem.batchNumber())
        };
        var result = displayName;
        for (var token in replacements) {
            result = result.split(token).join(replacements[token]);
        }
        return result;
    };

    self.noteEditor = null;

    self.catalogs = null;
    self.allMaterials = ko.observableArray([]);
    self.allVendors = ko.observableArray([]);
    self.allColors = ko.observableArray([]);
    self._localMaterials = [];
    self._localVendors = [];
    self._spoolmanVendors = {};
    self.userVendors = ko.observableArray([]);
    self.spoolmanDbVendors = ko.observableArray([]);
    self.spoolmanProducts = ko.observableArray([]);
    self.selectedSpoolmanProduct = ko.observable(null);
    self.spoolmanLoading = ko.observable(false);
    self._spoolmanRequestToken = 0;
    self._spoolmanApplyingTemperatures = false;
    self._spoolmanTemperatureEdited = {tool: false, bed: false};
    self._spoolmanApplyingColor = false;
    self._spoolmanColorEdited = false;
    self._spoolmanApplyingFinish = false;
    self._spoolmanFinishEdited = false;

    self._spoolmanEnabled = function () {
        return self.pluginSettings && self.pluginSettings.spoolmanDbEnabled();
    };
    self._updateVendorGroups = function (spoolmanVendors) {
        var localVendors = self._localVendors.filter(function (vendor) {
            return vendor;
        });
        var localVendorKeys = {};
        localVendors.forEach(function (vendor) {
            localVendorKeys[String(vendor).toLocaleLowerCase()] = true;
        });
        self.userVendors(localVendors);
        self.spoolmanDbVendors(
            (spoolmanVendors || []).filter(function (vendor) {
                return !localVendorKeys[String(vendor).toLocaleLowerCase()];
            })
        );
        self.allVendors(
            localVendors.concat(self.spoolmanDbVendors()).sort(function (left, right) {
                return left.localeCompare(right);
            })
        );
    };
    self.selectVendor = function (vendor) {
        self.spoolItemForEditing.vendor(vendor);
        return false;
    };
    self._loadSpoolmanVendors = function () {
        if (!self._spoolmanEnabled()) {
            return;
        }
        self.apiClient.getSpoolmanDbVendors(function (response) {
            if (!response.enabled) {
                return;
            }
            self._spoolmanVendors = {};
            (response.vendors || []).forEach(function (vendor) {
                self._spoolmanVendors[String(vendor).toLocaleLowerCase()] = vendor;
            });
            self._updateVendorGroups(response.vendors);
            self._loadSpoolmanMaterials();
        });
    };
    self._loadSpoolmanProducts = function () {
        var vendor = self.spoolItemForEditing.vendor();
        var material = self.spoolItemForEditing.material();
        var requestToken = ++self._spoolmanRequestToken;
        self.selectedSpoolmanProduct(null);
        self.spoolmanProducts([]);
        if (!self._spoolmanEnabled() || !vendor || !material) {
            return;
        }
        self.spoolmanLoading(true);
        self.apiClient.getSpoolmanDbProducts(vendor, material, function (response) {
            if (requestToken !== self._spoolmanRequestToken) {
                return;
            }
            self.spoolmanLoading(false);
            self.spoolmanProducts(response.products || []);
        });
    };
    self._loadSpoolmanMaterials = function () {
        var vendor = self.spoolItemForEditing.vendor();
        var isSpoolmanVendor =
            vendor && self._spoolmanVendors[String(vendor).toLocaleLowerCase()];
        if (!self._spoolmanEnabled() || !isSpoolmanVendor) {
            self.allMaterials(self._localMaterials);
            return;
        }
        var spoolmanVendor = self._spoolmanVendors[String(vendor).toLocaleLowerCase()];
        self.apiClient.getSpoolmanDbMaterials(spoolmanVendor, function (response) {
            if (!response.enabled || self.spoolItemForEditing.vendor() !== vendor) {
                return;
            }
            self.allMaterials(response.materials || []);
        });
    };
    // Belt-and-braces guard: the dropdown itself is disabled while isU1RfidFlow() is
    // true (see the edit dialog template), but selectedSpoolmanProduct could in
    // principle still change programmatically - the tag's per-spool values must never
    // lose to generic catalog data in that case either. Same reasoning as the wizard's
    // equivalent guards.
    self._applySpoolmanTemperatures = function (product) {
        if (!product || product.ambiguous || self.isU1RfidFlow()) {
            return;
        }
        self._spoolmanApplyingTemperatures = true;
        if (!self._spoolmanTemperatureEdited.tool && product.extruder_temp != null) {
            self.spoolItemForEditing.temperature(product.extruder_temp);
        }
        if (!self._spoolmanTemperatureEdited.bed && product.bed_temp != null) {
            self.spoolItemForEditing.bedTemperature(product.bed_temp);
        }
        self._spoolmanApplyingTemperatures = false;
    };
    self._applySpoolmanColor = function (product) {
        if (!product || self._spoolmanColorEdited || self.isU1RfidFlow()) {
            return;
        }
        var isTransparentProduct = product.is_transparent === true;
        var isUntintedTransparentProduct = product.is_untinted_transparent === true;
        var colors =
            product.color_hexes || (product.color_hex ? [product.color_hex] : []);
        if (colors.length === 0 && !isTransparentProduct) {
            return;
        }
        self._spoolmanApplyingColor = true;
        var colorValue = isUntintedTransparentProduct ? "" : colors.join(";");
        if (isTransparentProduct && colorValue) {
            colorValue = "transparent:" + colorValue;
        }
        self.spoolItemForEditing.applyColorToEditor(colorValue || "transparent");
        self.spoolItemForEditing.color(colorValue || "transparent");
        var suggestedName =
            product.color_name ||
            (colors.length > 1
                ? "Multi-color"
                : SPOOLMANAGER_UTILS.colorNameForSpoolColor(
                      self.spoolItemForEditing.color()
                  ));
        if (suggestedName != null) {
            self.spoolItemForEditing.colorName(suggestedName);
        }
        self._spoolmanApplyingColor = false;
        if (product.color_name) {
            setTimeout(function () {
                if (self.selectedSpoolmanProduct() === product) {
                    self.spoolItemForEditing.colorName(product.color_name);
                }
            }, 0);
        }
    };
    self._applySpoolmanFinish = function (product) {
        if (!product || !product.finish || self._spoolmanFinishEdited || self.isU1RfidFlow()) {
            return;
        }
        self._spoolmanApplyingFinish = true;
        self.spoolItemForEditing.finish(product.finish);
        self._spoolmanApplyingFinish = false;
    };

    self.allToolIndices = ko.observableArray([]);

    // Knockout stuff
    this.isExistingSpool = ko.observable(false);
    // true when the spool currently being edited is loaded into a tool slot -> deletion is blocked
    this.isLoadedInTool = ko.observable(false);
    this.spoolSelectedByQRCode = ko.observable(false);

    // Simple view mode (issue #1): strips the dialog down to basic filament tracking.
    // Hides temperatures, flow-rate, QR/DB-id, serial/batch, dates, purchase & cost and the
    // spool/combined-weight blocks. The per-browser choice is persisted in localStorage; when no
    // choice has been stored yet the plugin setting "Default view mode" decides (default: simple).
    var SIMPLE_MODE_STORAGE_KEY = "spoolManager.editDialog.simpleMode";
    var storedSimpleModeRaw = null;
    try {
        storedSimpleModeRaw = localStorage.getItem(SIMPLE_MODE_STORAGE_KEY);
    } catch (e) {
        /* localStorage unavailable (private mode) */
    }
    // start from the stored choice if present, otherwise simple (the plugin-setting default is
    // applied in initBinding once pluginSettings is available).
    this.simpleMode = ko.observable(
        storedSimpleModeRaw === null ? true : storedSimpleModeRaw === "true"
    );
    // when true, changing simpleMode does not pin the choice to localStorage (used while applying
    // the configured default, which must not count as a user decision).
    this._suppressSimpleModePersist = false;
    this.simpleMode.subscribe(function (newValue) {
        if (self._suppressSimpleModePersist) {
            return;
        }
        try {
            localStorage.setItem(SIMPLE_MODE_STORAGE_KEY, newValue ? "true" : "false");
        } catch (e) {
            /* ignore persistence errors */
        }
    });
    // Applied once pluginSettings is available (see initBinding): honour the configured default
    // only when the user has not yet made a per-browser choice in this browser.
    this._applyDefaultViewMode = function () {
        if (storedSimpleModeRaw !== null) {
            return; // user already toggled in this browser -> keep their choice
        }
        if (self.pluginSettings && self.pluginSettings.defaultViewModeSimple) {
            self._suppressSimpleModePersist = true;
            self.simpleMode(self.pluginSettings.defaultViewModeSimple() == true);
            self._suppressSimpleModePersist = false;
        }
    };
    this.toggleSimpleMode = function () {
        self.simpleMode(!self.simpleMode());
    };

    // Fields that are hidden in simple view, mapped to a human-readable label. Used to warn the
    // user when a template carries data in fields the simple view would hide (issue #1).
    // (firstUse/lastUse/combined weights are intentionally omitted: the template-copy flow resets
    // them, so they can never carry copied data at the point the warning is shown.)
    this._simpleModeHiddenFields = [
        {field: "flowRateCompensation", label: "Flow rate compensation"},
        {field: "temperature", label: "Tool temperature"},
        {field: "bedTemperature", label: "Bed temperature"},
        {field: "enclosureTemperature", label: "Enclosure temperature"},
        {field: "offsetTemperature", label: "Tool temperature offset"},
        {field: "offsetBedTemperature", label: "Bed temperature offset"},
        {field: "offsetEnclosureTemperature", label: "Enclosure temperature offset"},
        {field: "code", label: "Serial number"},
        {field: "batchNumber", label: "Batch number"},
        {field: "purchasedOnKO", label: "Purchased on"},
        {field: "purchasedFrom", label: "Purchased from"},
        {field: "cost", label: "Cost"}
    ];

    // observableArray of labels for hidden fields that currently hold data (drives the warning list)
    this.simpleModeHiddenFieldsWithData = ko.observableArray([]);

    this._hasValue = function (rawValue) {
        if (rawValue === null || rawValue === undefined) {
            return false;
        }
        var asString = ("" + rawValue).trim();
        if (asString.length === 0) {
            return false;
        }
        // numeric fields default to "0" / 0 -> treat as "no meaningful data"
        var asNumber = parseFloat(asString);
        if (!isNaN(asNumber) && asNumber === 0) {
            return false;
        }
        return true;
    };

    // Returns the labels of hidden fields that hold data on the currently edited spool.
    this._collectHiddenFieldsWithData = function () {
        var result = [];
        if (self.spoolItemForEditing == null) {
            return result;
        }
        self._simpleModeHiddenFields.forEach(function (entry) {
            var observable = self.spoolItemForEditing[entry.field];
            if (typeof observable === "function" && self._hasValue(observable())) {
                result.push(entry.label);
            }
        });
        return result;
    };

    // Called after a template copy while in simple view: if the copied data lands in hidden
    // fields, populate the warning list and open the warning dialog.
    this._warnIfTemplateHasSimpleHiddenData = function () {
        if (!self.simpleMode()) {
            return;
        }
        var hiddenWithData = self._collectHiddenFieldsWithData();
        if (hiddenWithData.length === 0) {
            return;
        }
        self.simpleModeHiddenFieldsWithData(hiddenWithData);
        // defer so the (possibly still closing) template-selection modal has released its backdrop
        // before we stack the warning dialog on top (Bootstrap 2 modal stacking quirk)
        setTimeout(function () {
            $("#dialog_simplemode_warning").modal("show");
        }, 300);
    };

    // Warning-dialog actions
    this.switchToFullViewFromWarning = function () {
        // an explicit switch here IS a user choice -> persist it
        self.simpleMode(false);
        $("#dialog_simplemode_warning").modal("hide");
    };
    this.stayInSimpleViewFromWarning = function () {
        $("#dialog_simplemode_warning").modal("hide");
    };
    // In simple mode the detailed weight inputs (initial / used) are hidden once a spool is in
    // use, leaving only the remaining amount. Fresh spools still show the initial weight so they
    // can be set up.
    // This is a *snapshot* taken when the dialog opens (see _snapshotSpoolInUse), deliberately not
    // a computed over usedWeight: as a live computed the four weight blocks vanished mid-typing the
    // moment a used amount was entered, which reads as data loss. The state a spool was opened in
    // stays put until it is saved and reopened.
    this.isSpoolInUse = ko.observable(false);
    // simpleMode stays reactive here so the view toggle keeps taking effect immediately.
    this.hideDetailedWeights = ko.pureComputed(function () {
        return self.simpleMode() && self.isSpoolInUse();
    });
    self._snapshotSpoolInUse = function () {
        if (self.spoolItemForEditing == null) {
            self.isSpoolInUse(false);
            return;
        }
        var used = parseFloat(self.spoolItemForEditing.usedWeight());
        self.isSpoolInUse(!isNaN(used) && used > 0);
    };

    /////////////////////////////////////////////////////////////////////////////////////////////////////////// OCTOSCALE

    // Shared weighing/tag-writing helpers, created in initBinding once apiClient exists.
    self.octoScaleWeighing = null;
    self.octoScaleTagWriter = null;

    this.isOctoScaleEnabled = ko.pureComputed(function () {
        if (self.pluginSettings == null || self.pluginSettings.octoScaleEnabled == null) {
            return false;
        }
        return self.pluginSettings.octoScaleEnabled() == true;
    });

    // A reading off the scale carries no information about what the user meant by it, and the two
    // possible meanings write to different fields:
    //   "total"     - setting up a spool: this is what it weighs full  -> totalCombinedWeight
    //   "remaining" - weighing it again: this is what is left          -> usedWeight (see below)
    // This used to be guessed from isSpoolInUse(), which gets it wrong in exactly the case that
    // matters: on the *first* re-weigh usedWeight is still 0, so a spool being checked for the
    // first time was treated as one being set up - silently rewriting its initial weight.
    // Hence two explicit buttons instead of a guess.

    this.toggleOctoScaleWeighing = function () {
        if (self.octoScaleWeighing != null) {
            self.octoScaleWeighing.toggle();
        }
    };

    // Values are read from / written to the spool item directly, never through the *Display
    // observables: those convert to the configured display unit, while the scale and the item
    // both work in grams.
    self._measuredGrams = function () {
        if (self.octoScaleWeighing == null) {
            return null;
        }
        return self.octoScaleWeighing.currentWeight();
    };

    self._numberOrNull = function (observable) {
        var value = parseFloat(observable());
        return isNaN(value) ? null : value;
    };

    // Interpreting a gross reading as "what is left" needs both reference values: without the
    // empty spool weight the filament share is unknown, without the initial weight there is
    // nothing to subtract the remainder from. Same rule the backend enforces in
    // _applyMeasuredGrossWeight() (api/SpoolManagerAPI.py).
    this.canApplyAsRemaining = ko.pureComputed(function () {
        if (self.spoolItemForEditing == null) {
            return false;
        }
        var spoolWeight = self._numberOrNull(self.spoolItemForEditing.spoolWeight);
        var totalWeight = self._numberOrNull(self.spoolItemForEditing.totalWeight);
        return (
            spoolWeight != null &&
            spoolWeight > 0 &&
            totalWeight != null &&
            totalWeight > 0
        );
    });

    this.remainingBlockReason = ko.pureComputed(function () {
        if (self.spoolItemForEditing == null || self.canApplyAsRemaining()) {
            return "";
        }
        var spoolWeight = self._numberOrNull(self.spoolItemForEditing.spoolWeight);
        if (spoolWeight == null || spoolWeight <= 0) {
            return "Enter the empty spool weight to use a reading as the remaining amount.";
        }
        return "Enter the initial filament amount to use a reading as the remaining amount.";
    });

    // Shows the arithmetic before the user commits to it, in grams.
    this.measuredRemainingPreview = ko.pureComputed(function () {
        if (self.spoolItemForEditing == null || !self.canApplyAsRemaining()) {
            return "";
        }
        var grams = self._measuredGrams();
        if (grams == null) {
            return "";
        }
        var spoolWeight = self._numberOrNull(self.spoolItemForEditing.spoolWeight);
        var totalWeight = self._numberOrNull(self.spoolItemForEditing.totalWeight);
        var remaining = Math.max(0, Math.min(grams - spoolWeight, totalWeight));
        var used = totalWeight - remaining;
        return (
            roundWithPrecision(grams, 1) +
            " g - " +
            roundWithPrecision(spoolWeight, 1) +
            " g empty = " +
            roundWithPrecision(remaining, 1) +
            " g left (" +
            roundWithPrecision(used, 1) +
            " g used)"
        );
    });

    // A real scale reading supersedes a weight that came from an RFID tag's nominal
    // value, so the "estimated" marker goes away. Applies to both apply-modes below -
    // both are actual measurements.
    self._clearWeightEstimatedLabel = function () {
        if (typeof self.spoolItemForEditing.labels !== "function") {
            return;
        }
        var currentLabels = self.spoolItemForEditing.labels();
        if (!Array.isArray(currentLabels)) {
            return;
        }
        var remaining = currentLabels.filter(function (label) {
            return label !== SPOOLMANAGER_CONSTANTS.LABEL_WEIGHT_ESTIMATED;
        });
        if (remaining.length !== currentLabels.length) {
            self.spoolItemForEditing.labels(remaining);
        }
    };

    this.applyMeasuredAsTotalWeight = function () {
        var grams = self._measuredGrams();
        if (grams == null) {
            return;
        }
        self.spoolItemForEditing.totalCombinedWeight(roundWithPrecision(grams, 1));
        self._clearWeightEstimatedLabel();
    };

    // Mirrors _applyMeasuredGrossWeight() in api/SpoolManagerAPI.py - keep the two in step.
    // Two reasons this writes usedWeight rather than a "remaining" field:
    //  - remainingWeight is derived: DatabaseManager.saveSpool() recomputes it as
    //    totalWeight - usedWeight on every save, so a value assigned to it is discarded.
    //  - the dialog's own auto-calculation only derives usage from remainingCombinedWeight while
    //    drivenScope is FILAMENT. Computing usedWeight here works whatever scope the user picked,
    //    and leaves their scope setting alone.
    this.applyMeasuredAsRemainingWeight = function () {
        var grams = self._measuredGrams();
        if (grams == null || !self.canApplyAsRemaining()) {
            return;
        }

        var spoolWeight = self._numberOrNull(self.spoolItemForEditing.spoolWeight);
        var totalWeight = self._numberOrNull(self.spoolItemForEditing.totalWeight);

        var remaining = grams - spoolWeight;
        if (remaining < 0) {
            // scale not tared, or the stored empty spool weight is wrong - clamp rather than
            // pushing a negative filament amount into the fields
            console.warn(
                "SpoolManager: measured " +
                    grams +
                    " g is below the empty spool weight " +
                    spoolWeight +
                    " g - clamping remaining filament to 0."
            );
            remaining = 0;
        }
        if (remaining > totalWeight) {
            console.warn(
                "SpoolManager: measured remaining filament " +
                    remaining +
                    " g exceeds the initial amount " +
                    totalWeight +
                    " g - clamping to the initial amount."
            );
            remaining = totalWeight;
        }

        var used = roundWithPrecision(totalWeight - remaining, 1);
        self.spoolItemForEditing.usedWeight(used);

        // keep the length in step, otherwise the UI reports a spool as e.g. 90% used by weight
        // and 0% used by length at the same time
        if (self.areDensityAndDiameterValid()) {
            self.spoolItemForEditing.usedLength(
                self.convertToLength(
                    used,
                    parseFloat(self.spoolItemForEditing.density()),
                    parseFloat(self.spoolItemForEditing.diameter())
                )
            );
        }

        self._clearWeightEstimatedLabel();
    };

    ///////////////////////////////////////////////////////////////////////////////// U1 RFID

    // Set for the lifetime of the current dialog session when it was opened from a
    // detected U1 RFID tag (via showDialog's u1RfidContext parameter) - used to disable
    // the SpoolmanDB dropdown, same reasoning as the wizard's isU1RfidFlow: the tag
    // already describes the exact physical spool, a catalog product would only overwrite
    // that with generic values.
    self.isU1RfidFlow = ko.observable(false);

    // Last unknown tag UIDs reported by the U1, so an existing spool can adopt one
    // without retyping it (and without depending on the popup still being open).
    self.u1RfidUnknownTags = ko.observableArray([]);

    self.hasU1RfidUnknownTags = ko.pureComputed(function () {
        return self.u1RfidUnknownTags().length > 0;
    });

    self.refreshU1RfidUnknownTags = function () {
        if (self.apiClient == null || self.apiClient.getU1RfidUnknownTags == null) {
            return;
        }
        self.apiClient.getU1RfidUnknownTags(function (response) {
            var entries = [];
            if (response != null) {
                for (var channelKey in response) {
                    if (!Object.prototype.hasOwnProperty.call(response, channelKey)) {
                        continue;
                    }
                    var entry = response[channelKey];
                    if (entry != null && entry.uid) {
                        entries.push({
                            channel: entry.channel,
                            uid: entry.uid,
                            rfidTagKey: entry.rfidTagKey,
                            label: "Channel " + entry.channel + ": " + entry.uid
                        });
                    }
                }
            }
            entries.sort(function (left, right) {
                return left.channel - right.channel;
            });
            self.u1RfidUnknownTags(entries);
        });
    };

    // Writes the derived rfidTagKey - what U1RfidManager actually matches on, since the
    // full UID differs between a Snapmaker spool's two physical tags (see
    // deriveRfidTagKey()'s PRELIMINARY collision note). `code` is left untouched: it's a
    // free-text field a spool may already carry its own, unrelated serial number in.
    self.applyU1RfidUid = function (entry) {
        if (entry == null || !entry.rfidTagKey) {
            return;
        }
        self.spoolItemForEditing.rfidTagKey(entry.rfidTagKey);
    };

    this.startTagWriting = function () {
        if (self.octoScaleTagWriter == null || self.isExistingSpool() != true) {
            return;
        }
        self.octoScaleTagWriter.start(self.spoolItemForEditing.databaseId());
    };

    this.stopTagWriting = function () {
        if (self.octoScaleTagWriter != null) {
            self.octoScaleTagWriter.stop();
        }
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////////////////// HELPER

    // Validation shape adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10);
    // extended with our invalidNumberFields check (see ko.bindingHandlers.numberField).
    self.isFormValidForSubmit = ko.pureComputed(function () {
        return (
            self._isEveryMandatoryFieldValid() &&
            self._isEveryFilledDateFieldValid() &&
            // block submit while any number field holds an invalid value (Fall A)
            self.invalidNumberFields().length === 0 &&
            self.isTemperatureRangePairValid()
        );
    });

    // min/max temperature must both be set or both left empty, and min must not exceed max
    self._isTemperatureRangePairValid = function (minValue, maxValue) {
        var min = parseFloat(minValue);
        var max = parseFloat(maxValue);
        var minSet = !isNaN(min);
        var maxSet = !isNaN(max);
        if (minSet !== maxSet) {
            return false;
        }
        return !minSet || !maxSet || min <= max;
    };

    self.isTemperatureRangePairValid = ko.pureComputed(function () {
        return (
            self._isTemperatureRangePairValid(
                self.spoolItemForEditing.minTemperature(),
                self.spoolItemForEditing.maxTemperature()
            ) &&
            self._isTemperatureRangePairValid(
                self.spoolItemForEditing.minBedTemperature(),
                self.spoolItemForEditing.maxBedTemperature()
            )
        );
    });

    // comma-separated list of invalid number field labels, for the hint next to the Save button
    self.invalidNumberFieldsLabel = ko.pureComputed(function () {
        return self
            .invalidNumberFields()
            .map(function (key) {
                return self.numberFieldLabels[key] || key;
            })
            .join(", ");
    });

    self._isEveryMandatoryFieldValid = function () {
        // "Displayname", "color name", "total weight"
        return (
            self.isDisplayNamePresent() &&
            self.isColorNamePresent() &&
            self.isTotalCombinedWeightPresent()
        );
    };

    self._isEveryFilledDateFieldValid = function () {
        // "First/LastUse", "purchasedOn" - empty fields are fine, filled ones must parse
        var isEmptyOrValid = function (value, format) {
            if (!value || value.trim().length === 0) {
                return true;
            }
            return moment(value, format).isValid();
        };
        return (
            isEmptyOrValid(
                self.spoolItemForEditing.firstUseKO(),
                FORMAT_DATETIME_LOCAL
            ) &&
            isEmptyOrValid(self.spoolItemForEditing.lastUseKO(), FORMAT_DATETIME_LOCAL) &&
            isEmptyOrValid(self.spoolItemForEditing.purchasedOnKO(), FORMAT_DATE)
        );
    };

    // Mandatory-field rules live in SPOOLMANAGER_UTILS so the wizard applies exactly the same ones.
    self.isDisplayNamePresent = function () {
        return SPOOLMANAGER_UTILS.isDisplayNamePresent(self.spoolItemForEditing);
    };

    self.addColorClicked = function () {
        var count = self.spoolItemForEditing.colorCount();
        if (count < 3) {
            self.spoolItemForEditing.colorCount(count + 1);
        }
    };

    self.removeColorClicked = function () {
        var count = self.spoolItemForEditing.colorCount();
        if (count > 1) {
            self.spoolItemForEditing.colorCount(count - 1);
        }
    };

    self.isColorNamePresent = function () {
        return SPOOLMANAGER_UTILS.isColorNamePresent(self.spoolItemForEditing);
    };

    self.isTotalCombinedWeightPresent = function () {
        return SPOOLMANAGER_UTILS.isTotalCombinedWeightPresent(self.spoolItemForEditing);
    };

    // builds (or refreshes) an SVG checkerboard <pattern> in the filament svg's
    // <defs> and returns the url(#..) reference. tintColor (optional) is layered
    // half-transparent over the checkerboard to render "tinted translucent".
    this._ensureTranslucentPattern = function (tintColor) {
        var svgRoot = $("#svg-filament").closest("svg");
        var svgNS = "http://www.w3.org/2000/svg";
        var defs = svgRoot.children("defs");
        if (defs.length === 0) {
            defs = $(document.createElementNS(svgNS, "defs"));
            svgRoot.prepend(defs);
        }
        // rebuild the pattern each call so the tint stays in sync
        defs.find("#translucentIconPattern").remove();
        var cell = 24; // checker cell size in svg user units
        var pattern = document.createElementNS(svgNS, "pattern");
        pattern.setAttribute("id", "translucentIconPattern");
        pattern.setAttribute("patternUnits", "userSpaceOnUse");
        pattern.setAttribute("width", "" + cell * 2);
        pattern.setAttribute("height", "" + cell * 2);
        // light/dark checker squares
        var squares = [
            {x: 0, y: 0, c: "#ffffff"},
            {x: cell, y: cell, c: "#ffffff"},
            {x: cell, y: 0, c: "#c8c8c8"},
            {x: 0, y: cell, c: "#c8c8c8"}
        ];
        squares.forEach(function (sq) {
            var r = document.createElementNS(svgNS, "rect");
            r.setAttribute("x", "" + sq.x);
            r.setAttribute("y", "" + sq.y);
            r.setAttribute("width", "" + cell);
            r.setAttribute("height", "" + cell);
            r.setAttribute("fill", sq.c);
            pattern.appendChild(r);
        });
        if (tintColor) {
            // half-transparent tint over the whole tile
            var tint = document.createElementNS(svgNS, "rect");
            tint.setAttribute("x", "0");
            tint.setAttribute("y", "0");
            tint.setAttribute("width", "" + cell * 2);
            tint.setAttribute("height", "" + cell * 2);
            tint.setAttribute("fill", tinycolor(tintColor).setAlpha(0.55).toRgbString());
            pattern.appendChild(tint);
        }
        defs.append(pattern);
        return "url(#translucentIconPattern)";
    };

    this._reColorFilamentIcon = function (newColor) {
        var colorParts = SPOOLMANAGER_UTILS.parseSpoolColor(newColor);
        var rectColors;
        var strokeColor;
        if (colorParts.isRainbow) {
            rectColors = [
                "#ff2d2d",
                "#ff9a00",
                "#ffe600",
                "#16c172",
                "#2f7bff",
                "#a044ff"
            ];
            strokeColor = rectColors[0];
        } else if (colorParts.isTransparent) {
            // translucent: render the filament as a checkerboard, optionally tinted
            var tint = colorParts.isUntinted ? "" : colorParts.colors[0];
            var patternRef = self._ensureTranslucentPattern(tint || null);
            var svgIconT = $("#svg-filament");
            svgIconT.children("rect").each(function () {
                $(this).attr("fill", patternRef);
            });
            svgIconT.children("path").each(function () {
                $(this).attr("stroke", tint ? tint : "#c8c8c8");
            });
            return;
        } else {
            var colors = colorParts.colors;
            if (colors.length === 1) {
                // single color: alternate with a slightly darkened shade
                rectColors = [colors[0], tinycolor(colors[0]).darken(12).toString()];
            } else {
                rectColors = colors;
            }
            strokeColor = colors[0];
        }
        var svgIcon = $("#svg-filament");
        svgIcon.children("rect").each(function (loopIndex) {
            $(this).attr("fill", rectColors[loopIndex % rectColors.length]);
        });
        svgIcon.children("path").each(function (loopIndex) {
            $(this).attr("stroke", strokeColor);
        });
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////////////////// PUBLIC
    this.initBinding = function (
        apiClient,
        pluginSettings,
        printerProfilesViewModel,
        printerStateViewModel
    ) {
        self.autoUpdateEnabled = false;
        self.apiClient = apiClient;
        self.pluginSettings = pluginSettings;
        self.printerProfilesViewModel = printerProfilesViewModel;
        self.printerStateViewModel = printerStateViewModel;

        // apply configured "Default view mode" when no per-browser choice was stored yet (issue #1)
        self._applyDefaultViewMode();

        self.spoolDialog = $("#dialog_spool_edit");
        self.templateSpoolDialog = $("#dialog_template_spool_selection");

        // OctoScale: weighing and NFC tag writing straight from the dialog, so a spool can be
        // weighed or tagged without going through the wizard. Shared implementation, see
        // SpoolManager-OctoScale.js.
        self.octoScaleWeighing = new SpoolManagerOctoScaleWeighing(
            apiClient,
            pluginSettings
        );
        self.octoScaleTagWriter = new SpoolManagerOctoScaleTagWriter(apiClient);

        // closing the dialog (Save, Close, Esc) must not leave the device pollers running
        self.spoolDialog.on("hidden", function () {
            self.octoScaleWeighing.stop();
            self.octoScaleTagWriter.stop();
        });

        // Adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10): note editor is created
        // via the static factory instead of instantiating Quill inline
        self.noteEditor = ComponentFactory.createNoteEditor("spool-note-editor");

        // initial coloring
        self._createSpoolItemForEditing();

        self.spoolItemForEditing.vendor.subscribe(function () {
            self._loadSpoolmanMaterials();
            self._loadSpoolmanProducts();
        });
        self.spoolItemForEditing.material.subscribe(self._loadSpoolmanProducts);
        self.selectedSpoolmanProduct.subscribe(self._applySpoolmanTemperatures);
        self.selectedSpoolmanProduct.subscribe(self._applySpoolmanColor);
        self.selectedSpoolmanProduct.subscribe(self._applySpoolmanFinish);
        self.spoolItemForEditing.temperature.subscribe(function () {
            if (!self._spoolmanApplyingTemperatures) {
                self._spoolmanTemperatureEdited.tool = true;
            }
        });
        self.spoolItemForEditing.bedTemperature.subscribe(function () {
            if (!self._spoolmanApplyingTemperatures) {
                self._spoolmanTemperatureEdited.bed = true;
            }
        });
        self.spoolItemForEditing.color.subscribe(function () {
            if (!self._spoolmanApplyingColor) {
                self._spoolmanColorEdited = true;
            }
        });
        self.spoolItemForEditing.finish.subscribe(function () {
            if (!self._spoolmanApplyingFinish) {
                self._spoolmanFinishEdited = true;
            }
        });

        // typing into the displayname field filters the template-combobox (issue #48)
        self.spoolItemForEditing.displayName.subscribe(function (newValue) {
            if (self._suppressTemplateCombo == true) {
                return;
            }
            if (self.isTemplateComboAvailable() == false) {
                return;
            }
            if (self.spoolDialog == null || self.spoolDialog.is(":visible") == false) {
                return;
            }
            self.templateComboFilter(newValue || "");
            self.templateComboVisible(true);
        });

        // live preview of the final display name when it contains variables like {material}-{color}-{id} (issue #49);
        // only shown for new spools (variables are resolved on save) and templates (resolved for spools created from them)
        self.displayNamePreview = ko.pureComputed(function () {
            var displayName = self.spoolItemForEditing.displayName();
            if (!displayName || displayName.indexOf("{") === -1) {
                return "";
            }
            if (
                self.isExistingSpool() == true &&
                self.spoolItemForEditing.isTemplate() != true
            ) {
                return "";
            }
            var resolved = self._substituteDisplayNameVariables(displayName);
            var nextId = self.nextSpoolId();
            return resolved.split("{id}").join(nextId != null ? "" + nextId : "…");
        });

        self._reColorFilamentIcon(self.spoolItemForEditing.color());
        self.spoolItemForEditing.color.subscribe(function (newColor) {
            self._reColorFilamentIcon(newColor);
            if (self._spoolmanApplyingColor) {
                return;
            }
            var suggestedName = SPOOLMANAGER_UTILS.colorNameForSpoolColor(newColor);
            if (suggestedName != null) {
                self.spoolItemForEditing.colorName(suggestedName);
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
        var remainingLengthPercentageKo =
            self.spoolItemForEditing.remainingLengthPercentage;
        var drivenScopeKo = self.spoolItemForEditing.drivenScope;

        // ----------------- start: display units
        // the base observables always hold mm/g, these computeds only convert for display/input
        var LENGTH_UNIT_FACTORS = {mm: 1, cm: 10, m: 1000};
        var WEIGHT_UNIT_FACTORS = {g: 1, kg: 1000};
        var UNIT_DISPLAY_DECIMALS = {mm: 1, cm: 2, m: 3, g: 1, kg: 3};

        var selectedLengthUnit = function () {
            var unit = self.pluginSettings.lengthUnit
                ? self.pluginSettings.lengthUnit()
                : "mm";
            return LENGTH_UNIT_FACTORS[unit] ? unit : "mm";
        };
        var selectedWeightUnit = function () {
            var unit = self.pluginSettings.weightUnit
                ? self.pluginSettings.weightUnit()
                : "g";
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
                    return parseFloat(
                        (value / unitFactors[unit]).toFixed(UNIT_DISPLAY_DECIMALS[unit])
                    );
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

        self.totalWeightDisplay = _makeUnitDisplayKo(
            totalWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
        self.usedWeightDisplay = _makeUnitDisplayKo(
            usedWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
        self.remainingWeightDisplay = _makeUnitDisplayKo(
            remainingWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
        self.totalLengthDisplay = _makeUnitDisplayKo(
            totalLengthKo,
            selectedLengthUnit,
            LENGTH_UNIT_FACTORS
        );
        self.usedLengthDisplay = _makeUnitDisplayKo(
            usedLengthKo,
            selectedLengthUnit,
            LENGTH_UNIT_FACTORS
        );
        self.remainingLengthDisplay = _makeUnitDisplayKo(
            remainingLengthKo,
            selectedLengthUnit,
            LENGTH_UNIT_FACTORS
        );
        self.spoolWeightDisplay = _makeUnitDisplayKo(
            spoolWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
        self.totalCombinedWeightDisplay = _makeUnitDisplayKo(
            totalCombinedWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
        self.remainingCombinedWeightDisplay = _makeUnitDisplayKo(
            remainingCombinedWeightKo,
            selectedWeightUnit,
            WEIGHT_UNIT_FACTORS
        );
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
            self.updatePercentages(
                usedPercentageKo,
                remainingPercentageKo,
                totalWeightKo,
                usedWeightKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        totalLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(totalLengthKo);
            self.doUnitConversion(totalLengthKo, totalWeightKo, self.convertToWeight);
            self.updatePercentages(
                usedLengthPercentageKo,
                remainingLengthPercentageKo,
                totalLengthKo,
                usedLengthKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        usedWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(usedWeightKo);
            self.doUnitConversion(usedWeightKo, usedLengthKo, self.convertToLength);
            self.updateFilamentRemainingWithStates();
            self.updatePercentages(
                usedPercentageKo,
                remainingPercentageKo,
                totalWeightKo,
                usedWeightKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        usedLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(usedLengthKo);
            self.doUnitConversion(usedLengthKo, usedWeightKo, self.convertToWeight);
            self.updatePercentages(
                usedLengthPercentageKo,
                remainingLengthPercentageKo,
                totalLengthKo,
                usedLengthKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        remainingWeightKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(remainingWeightKo);
            if (drivenScopeKo() === COMBINED) {
                self.updateCombinedRemainingWithScopes();
            }
            self.updateFilamentUsedWithStates();
            self.doUnitConversion(
                remainingWeightKo,
                remainingLengthKo,
                self.convertToLength
            );
            self.updatePercentages(
                usedPercentageKo,
                remainingPercentageKo,
                totalWeightKo,
                usedWeightKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        remainingLengthKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(remainingLengthKo);
            self.doUnitConversion(
                remainingLengthKo,
                remainingWeightKo,
                self.convertToWeight
            );
            self.updatePercentages(
                usedLengthPercentageKo,
                remainingLengthPercentageKo,
                totalLengthKo,
                usedLengthKo
            );
            self.resetLocksIf(iAmRootChange);
        });

        densityKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(densityKo);
            self.convertAllUnits();
            self.resetLocksIf(iAmRootChange);
        });

        diameterKo.subscribe(function (newValue) {
            var iAmRootChange = self.amIRootChange(diameterKo);
            self.convertAllUnits();
            self.resetLocksIf(iAmRootChange);
        });

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
            self.safeUpdate(remainingWeightKo, subtraction, [
                totalWeightKo,
                usedWeightKo
            ]);
        };

        self.updateFilamentRemainingWithScopes = function () {
            self.safeUpdate(remainingWeightKo, subtraction, [
                remainingCombinedWeightKo,
                spoolWeightKo
            ]);
        };

        self.updateFilamentUsedWithStates = function () {
            self.safeUpdate(usedWeightKo, subtraction, [
                totalWeightKo,
                remainingWeightKo
            ]);
        };

        self.updateFilamentInitialWithScopes = function () {
            self.safeUpdate(totalWeightKo, subtraction, [
                totalCombinedWeightKo,
                spoolWeightKo
            ]);
        };

        self.updateSpoolWithScopes = function () {
            self.safeUpdate(spoolWeightKo, subtraction, [
                totalCombinedWeightKo,
                totalWeightKo
            ]);
        };

        self.updateCombinedInitialWithScopes = function () {
            self.safeUpdate(totalCombinedWeightKo, addition, [
                totalWeightKo,
                spoolWeightKo
            ]);
        };

        self.updateCombinedRemainingWithScopes = function () {
            self.safeUpdate(remainingCombinedWeightKo, addition, [
                remainingWeightKo,
                spoolWeightKo
            ]);
        };

        self.convertAllUnits = function () {
            self.doUnitConversion(totalWeightKo, totalLengthKo, self.convertToLength);
            self.doUnitConversion(totalLengthKo, totalWeightKo, self.convertToWeight);
            self.doUnitConversion(usedWeightKo, usedLengthKo, self.convertToLength);
            self.doUnitConversion(usedLengthKo, usedWeightKo, self.convertToWeight);
            self.doUnitConversion(
                remainingWeightKo,
                remainingLengthKo,
                self.convertToLength
            );
            self.doUnitConversion(
                remainingLengthKo,
                remainingWeightKo,
                self.convertToWeight
            );
        };

        self.doUnitConversion = function (sourceKo, targetKo, converter) {
            var source = parseFloat(sourceKo());
            if (
                isNaN(source) ||
                !self.areDensityAndDiameterValid() ||
                !self.getLock(targetKo)
            ) {
                return;
            }
            self.getLock(sourceKo);
            targetKo(
                converter(source, parseFloat(densityKo()), parseFloat(diameterKo()))
            );
        };

        self.updatePercentages = function (
            usedPercentageKo,
            remainPercentageKo,
            totalKo,
            usedKo
        ) {
            var total = parseFloat(totalKo());
            var used = parseFloat(usedKo());
            if (isNaN(total) || total <= 0 || isNaN(used) || used < 0 || used > total) {
                usedPercentageKo(NaN);
                remainPercentageKo(NaN);
                return;
            }
            var usedPercentage = roundWithPrecision((100 * used) / total, 0);
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

            targetKo(
                roundWithPrecision(
                    calcFn.apply(null, calcFnArguments.map(getValueOrZero)),
                    1
                )
            );
        };

        // helper functions

        self.areDensityAndDiameterValid = function () {
            var diameter = parseFloat(diameterKo());
            var density = parseFloat(densityKo());
            return !isNaN(diameter) && diameter > 0 && !isNaN(density) && density > 0;
        };

        self.convertToLength = function (weight, density, diameter) {
            var volume = weight / (density * Math.pow(10, -3)); // [mm^3] = [g] / ( [g/cm^3] * 10^-3 )
            var area = (Math.PI / 4) * Math.pow(diameter, 2); // [mm^2] = pi/4 * [mm]^2
            return roundWithPrecision(volume / area, 0); // [mm] = [mm^3] / [mm^2}
        };

        self.convertToWeight = function (length, density, diameter) {
            var area = (Math.PI / 4) * Math.pow(diameter, 2); // [mm^2] = pi/4 * [mm]^2
            var volume = area * length; // [mm^3] = [mm^2] * [mm]
            return roundWithPrecision(volume * density * Math.pow(10, -3), 1); // [g] = [mm^3] * [g/cm^3] * 10^3
        };

        // lock mechanism to prevent infinite update loops

        self.locksOfInProgressUpdate = [];
        self.getLock = function (updatableEntity) {
            if (
                !self.autoUpdateEnabled ||
                self.locksOfInProgressUpdate.includes(updatableEntity)
            ) {
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
    };

    this.afterBinding = function () {};

    // SpoolItem construction/update flow adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10):
    // the item gets its dependencies (isEditable, catalogs) passed in explicitly and no longer
    // mutates dialog state; note-editor sync + autoUpdate toggling happen in _updateActiveSpoolItem.
    this._createSpoolItemForEditing = function () {
        self.spoolItemForEditing = new SpoolItem(null, {
            isEditable: true,
            catalogs: self.catalogs
        });

        self.spoolItemForEditing.isInActive.subscribe(function (newValue) {
            self.spoolItemForEditing.isActive(!newValue);
        });

        return self.spoolItemForEditing;
    };

    this.createSpoolItemForTable = function (spoolData) {
        var newSpoolItem = new SpoolItem(spoolData, {
            isEditable: false,
            catalogs: self.catalogs
        });
        return newSpoolItem;
    };

    // Central update of the item bound to the edit dialog: disables the weight auto-calculation
    // while loading and syncs the note editor content
    self._updateActiveSpoolItem = function (spoolData) {
        self.autoUpdateEnabled = false;
        self.spoolItemForEditing.update(spoolData, {catalogs: self.catalogs});

        var updateData = spoolData || {};
        if (self.noteEditor != null) {
            if (
                updateData.noteDeltaFormat == null ||
                updateData.noteDeltaFormat.length == 0
            ) {
                // Fallback is text (if present), not Html
                self.noteEditor.setText(
                    updateData.noteText != null ? updateData.noteText : "",
                    "api"
                );
            } else {
                // Links stored before the normalisation landed still carry a scheme-less href;
                // setContents() bypasses the Link blot's sanitize(), so repair them here.
                var deltaFormat = SPOOLMANAGER_UTILS.repairNoteDeltaLinks(
                    JSON.parse(updateData.noteDeltaFormat)
                );
                self.noteEditor.setContents(deltaFormat, "api");
            }
        }

        self.autoUpdateEnabled = true;
    };

    this.updateCatalogs = function (allCatalogs) {
        self.catalogs = allCatalogs;
        if (self.catalogs != null) {
            self._localMaterials = self.catalogs["materials"] || [];
            self._localVendors = self.catalogs["vendors"] || [];
            self.allMaterials(self._localMaterials);
            self._updateVendorGroups(
                Object.keys(self._spoolmanVendors).map(function (key) {
                    return self._spoolmanVendors[key];
                })
            );
            self.allColors(self.catalogs["colors"]);
        } else {
            self._localMaterials = [];
            self._localVendors = [];
            self.allMaterials([]);
            self._updateVendorGroups([]);
            self.allColors([]);
        }
    };

    this.updateTemplateSpools = function (templateSpoolsData) {
        var spoolItemsArray = [];
        if (templateSpoolsData != null && templateSpoolsData.length != 0) {
            spoolItemsArray = ko.utils.arrayMap(templateSpoolsData, function (spoolData) {
                var result = self.createSpoolItemForTable(spoolData);
                return result;
            });
        }
        self.templateSpools(spoolItemsArray);
    };

    this.showDialog = function (
        spoolItem,
        closeDialogHandler,
        isLoadedInTool,
        u1RfidContext
    ) {
        self.autoUpdateEnabled = false;
        self.closeDialogHandler = closeDialogHandler;
        // is this spool currently loaded into a tool slot? -> block deletion (see delete button binding)
        self.isLoadedInTool(isLoadedInTool === true);
        // get the current tool caunt
        self.allToolIndices([]);
        var toolCount = self.printerProfilesViewModel
            .currentProfileData()
            .extruder.count();
        for (var toolIndex = 0; toolIndex < toolCount; toolIndex++) {
            self.allToolIndices.push(toolIndex);
        }

        // initial coloring
        self._reColorFilamentIcon(self.spoolItemForEditing.color());

        // prospective id for the {id} display name variable preview (issue #49)
        self._refreshNextSpoolId();

        if (spoolItem == null) {
            // New Spool
            self.isExistingSpool(false);
            // reset values for a new spool
            self._updateActiveSpoolItem({});
            self.spoolItemForEditing.isInActive(false);

            // Force the current day on new spools
            self.spoolItemForEditing.purchasedOnKO(moment().format(FORMAT_DATE));

            // Prefill diameter with the de-facto consumer standard of 1.75mm
            self.spoolItemForEditing.diameter(1.75);
        } else {
            self.isExistingSpool(true);
            // Make a copy of provided spoolItem
            var spoolItemCopy = ko.mapping.toJS(spoolItem);
            self._updateActiveSpoolItem(spoolItemCopy);
        }
        self.spoolItemForEditing.drivenScope(COMBINED); // default calculation mode
        self.spoolItemForEditing.isSpoolVisible(true);
        self._spoolmanTemperatureEdited = {tool: false, bed: false};
        self._spoolmanColorEdited = false;
        self._spoolmanFinishEdited = false;
        self._loadSpoolmanVendors();
        self._loadSpoolmanMaterials();
        self._loadSpoolmanProducts();

        // Opened from a detected U1 RFID tag: prefill the same fields the wizard does,
        // via the shared module so both dialogs stay in step.
        self.isU1RfidFlow(u1RfidContext != null);
        if (u1RfidContext != null) {
            // _updateActiveSpoolItem({}) above already named the color from its red
            // placeholder default (SpoolItem.update(): DEFAULT_COLOR = "#ff0000" ->
            // colorNameForSpoolColor() -> "red") before this ever runs. Clear it so
            // applyToSpoolItem()'s "only fill in colorName when it's still empty" check
            // isn't fooled by that leftover into skipping the tag's actual color name -
            // same fix as the wizard's _applyU1RfidPrefill() needed.
            if (typeof self.spoolItemForEditing.colorName === "function") {
                self.spoolItemForEditing.colorName("");
            }
            SPOOLMANAGER_U1RFID.applyToSpoolItem(
                self.spoolItemForEditing,
                u1RfidContext.metadata || {},
                u1RfidContext.uid,
                u1RfidContext.rfidTagKey,
                {
                    applyColor: function (colorValue) {
                        // same path the SpoolmanDB prefill uses (_applySpoolmanColor):
                        // the editor's pickers and the stored value both need updating
                        self.spoolItemForEditing.applyColorToEditor(colorValue);
                        self.spoolItemForEditing.color(colorValue);
                        self._reColorFilamentIcon(colorValue);
                    }
                }
            );
        }

        self.refreshU1RfidUnknownTags();

        // freeze the simple-view weight-field visibility for as long as this dialog is open
        self._snapshotSpoolInUse();

        self.spoolDialog
            .modal({
                minHeight: function () {
                    return Math.max($.fn.modal.defaults.maxHeight() - 180, 250);
                },
                keyboard: false,
                clickClose: true,
                showClose: false,
                backdrop: "static"
            })
            .css({
                "width": "auto",
                "margin-left": function () {
                    return -($(this).width() / 2);
                }
            });

        self.autoUpdateEnabled = true;
    };

    self.copySpoolItem = function () {
        self._copySpoolItemForEditing(self.spoolItemForEditing);
    };

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
            "usedPercentage"
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
            "remainingCombinedWeight"
        ].concat(defaultExcludedNumericFields);

        var allFieldNames = Object.keys(spoolItem);
        var excludedFieldsFromSettings = self.pluginSettings.excludedFromTemplateCopy();
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
        if (copiedDisplayName && copiedDisplayName.indexOf("{") !== -1) {
            self.spoolItemForEditing.displayName(
                self._substituteDisplayNameVariables(copiedDisplayName)
            );
        }

        self._suppressTemplateCombo = false;

        // close dialog
        self.templateSpoolDialog.modal("hide");

        // simple view: if the template brought data into fields the simple view hides, warn the user
        // and offer to switch to the full view (issue #1)
        self._warnIfTemplateHasSimpleHiddenData();
    };

    self._copySpoolItemForEditing = function (spoolItem) {
        self.isExistingSpool(false);
        self._refreshNextSpoolId();
        let spoolItemCopy = ko.mapping.toJS(spoolItem);
        self._updateActiveSpoolItem(spoolItemCopy);
        self.spoolItemForEditing.isTemplate(false);
        // This sets isActive as well
        self.spoolItemForEditing.isInActive(false);
        self.spoolItemForEditing.databaseId(null);
        self.spoolItemForEditing.isSpoolVisible(true);
    };

    // Fields worth naming in the conflict dialog. Weights first: a scale writing back a
    // measurement is the common source of a concurrent change.
    self._conflictRelevantFields = [
        {key: "remainingWeight", label: "Remaining weight"},
        {key: "usedWeight", label: "Used weight"},
        {key: "totalWeight", label: "Total weight"},
        {key: "spoolWeight", label: "Empty spool weight"},
        {key: "displayName", label: "Display name"},
        {key: "colorName", label: "Color"},
        {key: "material", label: "Material"}
    ];

    // Compares what the dialog holds against the server's current state and returns a
    // human readable list of the differences ("Remaining weight: 612.4 -> 0.0").
    self._describeConflictChanges = function (currentSpool) {
        var changes = [];
        if (currentSpool == null) {
            return changes;
        }
        self._conflictRelevantFields.forEach(function (field) {
            var mine = self.spoolItemForEditing[field.key];
            if (typeof mine !== "function") {
                return;
            }
            var myValue = mine();
            var serverValue = currentSpool[field.key];
            // both sides are compared as strings: the API returns numbers formatted as
            // strings, while the observables may hold real numbers
            var myText = myValue == null ? "" : String(myValue);
            var serverText = serverValue == null ? "" : String(serverValue);
            if (myText !== serverText && (myText.length > 0 || serverText.length > 0)) {
                changes.push(
                    field.label +
                        ": " +
                        (serverText || "-") +
                        " (server) vs. " +
                        (myText || "-") +
                        " (yours)"
                );
            }
        });
        return changes;
    };

    self._handleSaveConflict = function (conflict) {
        if (conflict.type === "deleted") {
            // nothing left to save into - the only sane outcome is to close and refresh
            showConfirmationDialog({
                title: "Spool no longer exists",
                message:
                    conflict.message ||
                    "This spool was deleted while you were editing it.",
                question:
                    "Your changes cannot be saved. Close the dialog and refresh the list?",
                cancel: "Keep dialog open",
                proceed: "Close and refresh",
                proceedClass: "primary",
                onproceed: function () {
                    self.spoolItemForEditing.isSpoolVisible(false);
                    self.spoolDialog.modal("hide");
                    self.closeDialogHandler(true);
                },
                nofade: true
            });
            return;
        }

        var currentSpool = conflict.currentSpool;
        var changes = self._describeConflictChanges(currentSpool);
        var message =
            conflict.message ||
            "This spool was modified elsewhere while you were editing it.";
        if (changes.length > 0) {
            message += "\n\nDifferences:\n- " + changes.join("\n- ");
        }

        showConfirmationDialog({
            title: "Spool was modified elsewhere",
            message: message,
            question: "Your changes have NOT been saved yet. What should happen?",
            cancel: "Keep editing",
            proceed: ["Discard mine, reload", "Overwrite with mine"],
            proceedClass: "primary",
            onproceed: function (buttonIndex) {
                if (buttonIndex === 0) {
                    // take the server state into the dialog, dropping the local edits
                    if (currentSpool != null) {
                        self._updateActiveSpoolItem(currentSpool);
                    } else {
                        self.spoolDialog.modal("hide");
                        self.closeDialogHandler(true);
                    }
                    return;
                }
                // adopt the server's version so the optimistic lock passes, then save again.
                // Deliberately a separate, explicit choice - this discards the other change.
                if (currentSpool != null && self.spoolItemForEditing.version != null) {
                    self.spoolItemForEditing.version(currentSpool.version);
                }
                self.saveSpoolItem();
            },
            nofade: true
        });
    };

    self.saveSpoolItem = function () {
        // Input validation
        var displayName = self.spoolItemForEditing.displayName();
        if (!displayName || displayName.trim().length === 0) {
            SPOOLMANAGER_DIALOGS.notify({
                title: "Missing display name",
                message: "Please enter a display name before saving the spool.",
                type: "error"
            });
            return;
        }
        // workaround
        self.spoolItemForEditing.costUnit(self.pluginSettings.currencySymbol());

        var noteText = self.noteEditor.getText();
        var noteDeltaFormat = self.noteEditor.getContents();
        var noteHtml = self.noteEditor.getHtml();

        // read current note values and push to item, because there is no 2-way binding
        self.spoolItemForEditing.noteText(noteText);
        self.spoolItemForEditing.noteDeltaFormat(noteDeltaFormat);
        self.spoolItemForEditing.noteHtml(noteHtml);

        self.apiClient.callSaveSpool(
            self.spoolItemForEditing,
            function (success, validationErrors, conflict) {
                if (conflict != null) {
                    // someone else changed this spool while the dialog was open (e.g. a scale
                    // writing a measured weight via the API). The save did NOT happen - explain
                    // the situation and let the user decide, instead of silently dropping the edit.
                    self._handleSaveConflict(conflict);
                    return;
                }
                if (success === false) {
                    // server rejected the save - keep the dialog open and tell the user why
                    var message = "Spool could not be saved.";
                    if (validationErrors && validationErrors.length > 0) {
                        var escapedErrors = validationErrors.map(
                            function (validationError) {
                                return SPOOLMANAGER_DIALOGS.escapeHtml(validationError);
                            }
                        );
                        message += SPOOLMANAGER_DIALOGS.buildHtmlList(escapedErrors);
                    }
                    SPOOLMANAGER_DIALOGS.notify({
                        title: "Save failed",
                        message: message,
                        type: "error"
                    });
                    return;
                }
                self.spoolItemForEditing.isSpoolVisible(false);
                self.spoolDialog.modal("hide");
                if (
                    self.spoolItemForEditing.selectedForTool() != undefined &&
                    self.printerStateViewModel.isPrinting()
                ) {
                    // spool that is currently printed from was updated - warn
                    console.log(self.spoolItemForEditing.selectedForTool());
                    SPOOLMANAGER_DIALOGS.notify({
                        title: "Changes not applied to the running print",
                        message:
                            "A print is running, so the changes are not applied automatically. " +
                            "Re-select the spool manually to apply them.",
                        type: "info",
                        autoclose: false
                    });
                    self.closeDialogHandler(true);
                } else if (self.spoolItemForEditing.selectedForTool() != undefined) {
                    // spool that is currently selected for printing was updated - refresh
                    self.closeDialogHandler(
                        true,
                        "selectSpoolForPrinting",
                        self.spoolItemForEditing
                    );
                } else {
                    // some other spool was updated - not relevant
                    self.closeDialogHandler(true);
                }
            }
        );
    };

    self.deleteSpoolItem = function () {
        // safety net: a spool loaded into a tool must not be deleted (button is disabled, but guard the action too)
        if (self.isLoadedInTool()) {
            return;
        }
        var spoolName = self.spoolItemForEditing.displayName();
        var spoolLabel =
            spoolName != null && spoolName != ""
                ? "'" + SPOOLMANAGER_DIALOGS.escapeHtml(spoolName) + "'"
                : "This spool";

        SPOOLMANAGER_DIALOGS.confirmDanger({
            title: "Delete spool",
            message:
                spoolLabel +
                " will be permanently removed from the database. This cannot be undone.",
            question: "Do you really want to delete this spool?",
            cancel: "Keep spool",
            proceed: "Delete"
        }).then(function (confirmed) {
            if (confirmed != true) {
                return;
            }
            self.apiClient.callDeleteSpool(
                self.spoolItemForEditing.databaseId(),
                function (responseData) {
                    self.spoolItemForEditing.isSpoolVisible(false);
                    self.spoolDialog.modal("hide");
                    self.closeDialogHandler(true);
                }
            );
        });
    };

    // Adapted from mdziekon/OctoPrint-SpoolManager PR #29 (GH-24): the "Select for printing" button now
    // passes the chosen tool explicitly instead of relying on a separate <select>.
    // Kept backwards-compatible: falls back to selectedForTool() when no toolIdx is supplied.
    self.selectSpoolItemForPrintingOnTool = function (params) {
        var toolIdx =
            params && params.toolIdx !== undefined
                ? params.toolIdx
                : self.spoolItemForEditing.selectedForTool();
        self.spoolItemForEditing.isSpoolVisible(false);
        self.spoolDialog.modal("hide");
        self.closeDialogHandler(
            false,
            "selectSpoolForPrinting",
            self.spoolItemForEditing,
            toolIdx
        );
    };

    // Template-combobox handlers (issue #48)
    self.onDisplayNameFocus = function () {
        if (self.isTemplateComboAvailable()) {
            self.templateComboFilter("");
            self.templateComboVisible(true);
        }
        return true;
    };

    self.onDisplayNameBlur = function () {
        self.templateComboVisible(false);
        return true;
    };

    self.toggleTemplateCombo = function (data, event) {
        if (self.isTemplateComboAvailable()) {
            self.templateComboFilter("");
            self.templateComboVisible(!self.templateComboVisible());
        }
        // prevent the input from losing focus
        return false;
    };

    self.selectTemplateFromCombo = function (spoolItem) {
        self.templateComboVisible(false);
        self.copySpoolItemFromTemplate(spoolItem);
    };

    self.selectAndCopyTemplateSpool = function () {
        /* needed for Filter-Search dropdown-menu */
        $(".dropdown-menu.keep-open").click(function (e) {
            e.stopPropagation();
        });

        self.templateSpoolDialog.modal({
            minHeight: function () {
                return Math.max($.fn.modal.defaults.maxHeight() - 80, 250);
            },
            show: true
        });
    };
}
