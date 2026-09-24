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
function SpoolManagerExtendedEditSpoolDialog() {
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
        dryingTemperature: "Drying temperature",
        dryingTime: "Drying time",
        td: "Transmission distance",
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
        if (
            !product ||
            !product.finish ||
            self._spoolmanFinishEdited ||
            self.isU1RfidFlow()
        ) {
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
        {field: "dryingTemperature", label: "Drying temperature"},
        {field: "dryingTime", label: "Drying time"},
        {field: "td", label: "Transmission distance"},
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
        self.octoScaleTagWriter.start(
            self.spoolItemForEditing.databaseId(),
            self.spoolItemForEditing
        );
    };

    // A new, unsaved spool has no database id to write, but reading a vendor tag to prefill
    // the form is still useful (previously only offered inside the Add Spool wizard). Starts
    // the same writer with no target id - canWrite() already requires targetDatabaseId() to
    // be set, so this session can only ever read, never write.
    this.startTagReading = function () {
        if (self.octoScaleTagWriter == null || self.isExistingSpool() === true) {
            return;
        }
        self.octoScaleTagWriter.start(null, self.spoolItemForEditing);
    };

    this.stopTagWriting = function () {
        if (self.octoScaleTagWriter != null) {
            self.octoScaleTagWriter.stop();
        }
    };

    // Copies the values just read off a vendor tag into the form. Only ever on an explicit
    // click: the read result is a suggestion, and the user may well have opened the dialog
    // to change something else entirely.
    //
    // Used for a brand-new, unsaved spool (see startTagReading()) - there is no meaningful
    // "database value" to compare against yet, so the field-by-field checkbox review below
    // (showReadTagImportDialog) would just show every row as "(not set) -> tag value" and
    // add a click for nothing. An existing spool goes through that dialog instead.
    this._applyAllReadTagValues = function () {
        if (self.octoScaleTagWriter == null) {
            return;
        }
        var result = self.octoScaleTagWriter.readTagResult();
        if (result == null || result.parsed !== true) {
            return;
        }
        // The tag describes the physical spool in front of the user, so it must win over a
        // catalog guess - same reasoning as the U1 flow, which disables the SpoolmanDB
        // dropdown for the lifetime of the dialog.
        self.isU1RfidFlow(true);
        // Clear the placeholder-derived color name first, otherwise the "only fill when
        // empty" check below sees a leftover ("red" from the #ff0000 default) and skips the
        // tag's actual color.
        if (typeof self.spoolItemForEditing.colorName === "function") {
            self.spoolItemForEditing.colorName("");
        }
        SPOOLMANAGER_U1RFID.applyTagFieldsToSpoolItem(
            self.spoolItemForEditing,
            result.fields || {},
            result.uid,
            result.rfidTagKey,
            {
                applyColor: function (colorValue) {
                    self.spoolItemForEditing.applyColorToEditor(colorValue);
                    self.spoolItemForEditing.color(colorValue);
                    self._reColorFilamentIcon(colorValue);
                }
            }
        );
        // applyTagFieldsToSpoolItem() only ever writes totalCombinedWeight - see the same
        // note on the u1RfidContext prefill in showDialog(). drivenScope defaults to
        // COMBINED, so without this the "Initial" (totalWeight) field a simple-mode user
        // actually looks at stays empty even though a weight was read off the tag.
        self.updateFilamentInitialWithScopes();
        self.octoScaleTagWriter.clearReadTagResult();
    };

    // Entry point bound in the template's "Use these values" button - routes to the
    // no-comparison-needed path for a new spool, or opens the field-by-field review dialog
    // for an existing one, where the target spool's current database values are known and
    // worth showing side by side.
    this.applyReadTagValues = function () {
        if (self.isExistingSpool() === true) {
            self.showReadTagImportDialog();
        } else {
            self._applyAllReadTagValues();
        }
    };

    ///////////////////////////////////////////////////////////////////////////////////////////////////////////// READ TAG IMPORT DIALOG (existing spool)

    // The compare rows, each carrying its own ko.observable(boolean) "selected" property
    // (attached in showReadTagImportDialog below) so the dialog's checkboxes can bind
    // "checked: selected" directly on the foreach's $data.
    //
    // Deliberately NOT "checked: someFn($data.key)" (a function call reading a selection
    // map keyed elsewhere) - tested against this exact Knockout version (3.5.1) inside a
    // <table>/<tbody data-bind="foreach">/<tr> structure: a data-bind expression that calls
    // a function with an argument in a table-based foreach loses its surrounding binding
    // context entirely once the row template is cloned for a non-empty array, throwing
    // "X is not defined" for the function name. A bound property access (no call, no
    // parens) on $data works fine in the same structure - so the selection state has to
    // live *on* each row object, not be looked up via one.
    //
    // Snapshotted at dialog-open time (see showReadTagImportDialog), NOT a live computed
    // over octoScaleTagWriter.readTagCompareRows(): that would drop the per-row
    // observables out from under the open dialog the instant anything caused it to
    // re-evaluate (e.g. a stray NFC poll tick), which is a live re-render of the whole
    // table for a dialog whose backing tag reading is already finished and frozen.
    this.readTagImportRows = ko.observableArray([]);
    // The read result the rows above were built from, frozen the same way - if a different
    // tag ends up on the reader while this dialog is open (background polling keeps
    // running), applySelectedReadTagValues must still apply values from the tag the user is
    // actually looking at, not whatever is on the reader by the time they click Import.
    this._readTagImportResult = null;

    // True once every visible row is checked - drives the "select all" checkbox's own
    // checked state (three-way: all/none/mixed collapses to a boolean, mixed reads as
    // unchecked so clicking it always means "select everything").
    this.readTagImportAllSelected = ko.pureComputed(function () {
        var rows = self.readTagImportRows();
        if (rows.length === 0) {
            return false;
        }
        return rows.every(function (row) {
            return row.selected() === true;
        });
    });

    this.selectAllReadTagFields = function () {
        self.readTagImportRows().forEach(function (row) {
            row.selected(true);
        });
    };

    this.deselectAllReadTagFields = function () {
        self.readTagImportRows().forEach(function (row) {
            row.selected(false);
        });
    };

    this.toggleAllReadTagFields = function () {
        if (self.readTagImportAllSelected()) {
            self.deselectAllReadTagFields();
        } else {
            self.selectAllReadTagFields();
        }
    };

    // Opened from the "Use these values" click on an existing spool (see applyReadTagValues
    // above). Defaults the selection to exactly the rows that differ from the spool's
    // current values - matching a tag to a spool that's already correct should not require
    // unchecking a screenful of identical fields first.
    this.showReadTagImportDialog = function () {
        var sourceRows =
            self.octoScaleTagWriter != null
                ? self.octoScaleTagWriter.readTagCompareRows()
                : [];
        self._readTagImportResult =
            self.octoScaleTagWriter != null
                ? self.octoScaleTagWriter.readTagResult()
                : null;
        var rows = sourceRows.map(function (row) {
            return {
                key: row.key,
                label: row.label,
                tagValueText: row.tagValueText,
                dbValueText: row.dbValueText,
                differs: row.differs,
                selected: ko.observable(row.differs === true)
            };
        });
        self.readTagImportRows(rows);
        $("#dialog_read_tag_import").modal("show");
    };

    this.closeReadTagImportDialog = function () {
        $("#dialog_read_tag_import").modal("hide");
    };

    // Writes only the checked rows into the form, then closes the dialog and discards the
    // read result the same way the direct-apply path does.
    this.applySelectedReadTagValues = function () {
        if (self.octoScaleTagWriter == null) {
            return;
        }
        // The tag the dialog's rows were built from, not whatever is on the reader now -
        // see the note on _readTagImportResult above.
        var result = self._readTagImportResult;
        if (result == null || result.parsed !== true) {
            self.closeReadTagImportDialog();
            return;
        }
        // Collapse the rows' individual selected() observables into the plain
        // {fieldKey: boolean} shape applySelectedTagFieldsToSpoolItem expects.
        var selection = {};
        self.readTagImportRows().forEach(function (row) {
            selection[row.key] = row.selected() === true;
        });
        // Nothing checked - closing without touching the form is a legitimate outcome
        // ("actually, I don't want any of this"), not an error.
        var hasSelection = Object.keys(selection).some(function (key) {
            return selection[key] === true;
        });
        if (hasSelection) {
            self.isU1RfidFlow(true);
            if (
                selection["color"] === true &&
                typeof self.spoolItemForEditing.colorName === "function"
            ) {
                self.spoolItemForEditing.colorName("");
            }
            SPOOLMANAGER_U1RFID.applySelectedTagFieldsToSpoolItem(
                self.spoolItemForEditing,
                result.fields || {},
                selection,
                result.uid,
                result.rfidTagKey,
                {
                    applyColor: function (colorValue) {
                        self.spoolItemForEditing.applyColorToEditor(colorValue);
                        self.spoolItemForEditing.color(colorValue);
                        self._reColorFilamentIcon(colorValue);
                    }
                }
            );
            if (selection["totalWeight"] === true) {
                self.updateFilamentInitialWithScopes();
            }
        }
        self.octoScaleTagWriter.clearReadTagResult();
        self.closeReadTagImportDialog();
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
        // "Displayname", "color name", "total weight", "material", "vendor", "diameter"
        return (
            self.isDisplayNamePresent() &&
            self.isColorNamePresent() &&
            self.isTotalCombinedWeightPresent() &&
            self.isMaterialPresent() &&
            self.isVendorPresent() &&
            self.isDiameterPresent()
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

    self.isMaterialPresent = function () {
        return SPOOLMANAGER_UTILS.isMaterialPresent(self.spoolItemForEditing);
    };

    self.isVendorPresent = function () {
        return SPOOLMANAGER_UTILS.isVendorPresent(self.spoolItemForEditing);
    };

    self.isDiameterPresent = function () {
        return SPOOLMANAGER_UTILS.isDiameterPresent(self.spoolItemForEditing);
    };

    // builds (or refreshes) an SVG checkerboard <pattern> in the filament svg's
    // <defs> and returns the url(#..) reference. tintColor (optional) is layered
    // half-transparent over the checkerboard to render "tinted translucent".
    //
    // patternIndex exists because a transparent spool can carry up to three colors: each
    // one needs its own tinted pattern, and an SVG fill can only reference a pattern by id.
    // A single fixed id would mean every stripe shows whichever tint was built last - which
    // is exactly how a three-color translucent spool used to render as one flat color.
    this._ensureTranslucentPattern = function (tintColor, patternIndex) {
        var svgRoot = $("#spmx-svg-filament").closest("svg");
        var svgNS = "http://www.w3.org/2000/svg";
        var defs = svgRoot.children("defs");
        if (defs.length === 0) {
            defs = $(document.createElementNS(svgNS, "defs"));
            svgRoot.prepend(defs);
        }
        var patternId =
            "translucentIconPattern" + (patternIndex != null ? "-" + patternIndex : "");
        // rebuild the pattern each call so the tint stays in sync
        defs.find("#" + patternId).remove();
        var cell = 24; // checker cell size in svg user units
        var pattern = document.createElementNS(svgNS, "pattern");
        pattern.setAttribute("id", patternId);
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
        return "url(#" + patternId + ")";
    };

    // Drops every pattern a previous call left behind. Needed because the number of
    // patterns follows the spool's color count: going from a three-color translucent spool
    // to a one-color one would otherwise leave -1 and -2 orphaned in <defs>.
    this._clearTranslucentPatterns = function () {
        $("#spmx-svg-filament")
            .closest("svg")
            .children("defs")
            .find("[id^='translucentIconPattern']")
            .remove();
    };

    this._reColorFilamentIcon = function (newColor) {
        var colorParts = SPOOLMANAGER_UTILS.parseSpoolColor(newColor);
        var rectColors;
        var strokeColor;
        // unconditional: switching a spool from translucent to a solid color has to take
        // the old patterns with it, not just stop referencing them
        self._clearTranslucentPatterns();
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
            // translucent: render the filament as a checkerboard, tinted with every color
            // the spool carries (not just the first - a "transparent:#a;#b;#c" spool has to
            // show all three, the same way the list swatch does via spmSpoolColorCss).
            if (colorParts.isUntinted) {
                rectColors = [self._ensureTranslucentPattern(null, 0)];
                strokeColor = "#c8c8c8";
            } else {
                rectColors = colorParts.colors.map(function (color, colorIndex) {
                    return self._ensureTranslucentPattern(color, colorIndex);
                });
                strokeColor = colorParts.colors[0];
            }
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
        var svgIcon = $("#spmx-svg-filament");
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

        self.spoolDialog = $("#spmx-dialog_spool_edit");
        self.templateSpoolDialog = $("#spmx-dialog_template_spool_selection");

        // OctoScale: weighing and NFC tag writing straight from the dialog, so a spool can be
        // weighed or tagged without going through the wizard. Shared implementation, see
        // SpoolManager-OctoScale.js.
        self.octoScaleWeighing = new SpoolManagerOctoScaleWeighing(
            apiClient,
            pluginSettings
        );
        self.octoScaleTagWriter = new SpoolManagerOctoScaleTagWriter(
            apiClient,
            pluginSettings
        );

        // The "this tag belongs to someone else / looks like a manufacturer tag" warnings
        // used to sit inline in the form, easy to miss next to the Write tag button and
        // visually indistinguishable from the "Read tag" affordance right below them. A
        // confirmation modal (OctoPrint's own showConfirmationDialog, same one used
        // elsewhere in this dialog) makes the choice explicit instead.
        //
        // Only ever triggered from the "Write tag" click (see writeTagWithConfirmation
        // below) - reading/comparing a tag must never pop up a modal on its own. An earlier
        // version showed it as soon as a foreign/occupied tag was merely detected, which
        // fired even while the user only wanted to read the tag or look at the diff (it
        // can never actually write anyway, canWrite() blocks that until confirmed) - a false
        // "destroy this?" alarm for a purely read-only look.
        var overwriteConfirmDialog = null;
        var overwriteConfirmDialogUid = null;
        var closeOverwriteConfirmDialog = function () {
            if (overwriteConfirmDialog != null) {
                overwriteConfirmDialog.modal("hide");
                overwriteConfirmDialog = null;
                overwriteConfirmDialogUid = null;
            }
        };
        // Safety net for the narrow window between opening the modal and the user acting on
        // it: if a different tag ends up on the reader in the meantime, "Overwrite anyway"
        // must not silently apply to a tag the user never saw the question for.
        self.octoScaleTagWriter.tagUid.subscribe(function (newUid) {
            if (overwriteConfirmDialog != null && overwriteConfirmDialogUid !== newUid) {
                closeOverwriteConfirmDialog();
            }
        });
        // Resolves the pending overwrite question (if any) before writing, then writes.
        // Returns without writing if a modal had to be shown - writeTag() is called from
        // its onproceed instead, once the user actually confirms.
        self.writeTagWithConfirmation = function () {
            var writer = self.octoScaleTagWriter;
            // The hard blockers only (no tag, no target spool, already writing) - the two
            // confirmation flags are exactly what this function still has to resolve, so
            // canWrite() itself (which requires them already set) would refuse right here.
            if (writer.canAttemptWrite() != true) {
                return;
            }
            // Unsaved edits first, before the overwrite/foreign-tag questions: the write
            // takes its data from the *stored* spool (writeTag sends only the databaseId,
            // the backend re-loads it), so unsaved changes would silently not make it onto
            // the tag - while the diff table above the button shows them, because it reads
            // the live form. Asked up front so nobody clicks through two confirmations
            // before finding out they have to save first.
            var unsavedChanges = self._getUnsavedChanges();
            if (unsavedChanges.length > 0) {
                SPOOLMANAGER_DIALOGS.confirm({
                    title: "Unsaved changes",
                    message:
                        self._buildUnsavedChangesMessage(unsavedChanges) +
                        "<p>Only saved values are written to the tag.</p>",
                    question: "Save the spool now and then write the tag?",
                    cancel: "Cancel",
                    proceed: "Save and write",
                    proceedClass: "primary"
                }).then(function (confirmed) {
                    if (confirmed != true) {
                        return;
                    }
                    self.saveSpoolItem({
                        // the tag writer lives in this dialog and polls the device - closing
                        // it here would tear down the very write we are about to perform
                        keepDialogOpen: true,
                        onSaved: function () {
                            // nothing is unsaved any more, so this re-entry falls through
                            // to the overwrite/foreign-tag handling below
                            self.writeTagWithConfirmation();
                        }
                    });
                });
                return;
            }
            if (
                writer.needsOverwriteConfirmation() &&
                writer.overwriteConfirmed() != true
            ) {
                overwriteConfirmDialogUid = writer.tagUid();
                overwriteConfirmDialog = showConfirmationDialog({
                    title: "Overwrite this tag?",
                    message: writer.overwriteWarningText(),
                    question: "Overwrite it with this spool's data anyway?",
                    cancel: "Cancel",
                    proceed: "Overwrite anyway",
                    proceedClass: "warning",
                    onproceed: function () {
                        writer.confirmOverwrite();
                        // Re-enter rather than writing directly: a tag can need BOTH this
                        // confirmation and the foreign-tag one below (an unrecognized format
                        // that still carries an id). Calling writeTag() here left canWrite()
                        // false on the foreign branch, so the write was silently dropped and
                        // the second question was never asked - the click looked like a dead
                        // button. Re-entering falls through to whatever is still unconfirmed,
                        // exactly as the unsaved-changes branch above already does.
                        self.writeTagWithConfirmation();
                    },
                    onclose: function () {
                        overwriteConfirmDialog = null;
                        overwriteConfirmDialogUid = null;
                    },
                    nofade: true
                });
                return;
            }
            if (writer.isPossiblyForeignTag() && writer.foreignTagConfirmed() != true) {
                overwriteConfirmDialogUid = writer.tagUid();
                // A tag holding nothing but an unverifiable number is not a manufacturer tag,
                // and the overwrite question just answered already described it accurately.
                // Asking a second time under the "vendor tag" heading would contradict that
                // text and state something untrue about the tag, so the confirmation the user
                // already gave stands for this write.
                if (writer.hasUnverifiableLegacyId()) {
                    overwriteConfirmDialog = null;
                    overwriteConfirmDialogUid = null;
                    writer.confirmForeignTagOverwrite();
                    writer.writeTag();
                    return;
                }
                if (writer.vendorTagWriteEnabled() != true) {
                    // The setting is a hard "never" - no "Overwrite anyway" escape hatch
                    // here, just an explanation of why the button did nothing.
                    overwriteConfirmDialog = showConfirmationDialog({
                        title: "Cannot write this tag",
                        message: writer.foreignTagWarningText(),
                        question:
                            "Writing over vendor tags is disabled in the SpoolManager settings.",
                        cancel: "OK",
                        proceed: [],
                        onclose: function () {
                            overwriteConfirmDialog = null;
                            overwriteConfirmDialogUid = null;
                        },
                        nofade: true
                    });
                    return;
                }
                overwriteConfirmDialog = showConfirmationDialog({
                    title: "Overwrite this tag?",
                    message: writer.foreignTagWarningText(),
                    question: "Overwrite it with this spool's data anyway?",
                    cancel: "Cancel",
                    proceed: "Overwrite anyway",
                    proceedClass: "danger",
                    onproceed: function () {
                        writer.confirmForeignTagOverwrite();
                        writer.writeTag();
                    },
                    onclose: function () {
                        overwriteConfirmDialog = null;
                        overwriteConfirmDialogUid = null;
                    },
                    nofade: true
                });
                return;
            }
            writer.writeTag();
        };

        // Keep the "these values are not saved yet" hint above the diff table honest. The
        // diff recomputes whenever the tag poll or a form field changes, which is exactly
        // when the hint is on screen and might have gone stale.
        self.octoScaleTagWriter.tagValueDiff.subscribe(function () {
            self.refreshUnsavedChangesFlag();
        });

        // On an existing spool, a successful tag read jumps straight to the field-by-field
        // review dialog instead of leaving the inline "Use these values" summary sitting in
        // the form waiting for a second click - the inline table only exists for the
        // no-comparison-possible new-spool case now (see applyReadTagValues/
        // _applyAllReadTagValues), so requiring the same extra click for an existing spool
        // just added a redundant step in front of a dialog the user always wants here.
        self.octoScaleTagWriter.readTagResult.subscribe(function (result) {
            if (
                result != null &&
                result.parsed === true &&
                self.isExistingSpool() === true
            ) {
                self.showReadTagImportDialog();
            }
        });

        // Guard against losing edits: X and "Close" carry data-dismiss="modal" and Esc is
        // handled by Bootstrap itself, so none of them ever reaches our code - a click
        // handler on the buttons would miss at least one of the three. The modal's own
        // "hide" event is the one point all of them pass through, and it can be cancelled.
        self.spoolDialog.on("hide", function (event) {
            if (self._confirmDiscardUnsavedChanges() !== true) {
                event.preventDefault();
            }
        });

        // closing the dialog (Save, Close, Esc) must not leave the device pollers running
        self.spoolDialog.on("hidden", function () {
            // the close went through - the next one has to prove itself again
            self._allowDialogClose = false;
            closeOverwriteConfirmDialog();
            self.closeReadTagImportDialog();
            self.octoScaleWeighing.stop();
            self.octoScaleTagWriter.stop();
        });

        // Adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10): note editor is created
        // via the static factory instead of instantiating Quill inline
        self.noteEditor = SpoolManagerExtendedComponentFactory.createNoteEditor(
            "spmx-spool-note-editor"
        );

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
        // Learn the firmware verdict before anything is clicked, so a blocked control is
        // already blocked (and says why) rather than only after the first failed attempt.
        if (self.isOctoScaleEnabled() && self.octoScaleWeighing != null) {
            self.octoScaleWeighing.refreshFirmwareVerdict();
            self.octoScaleTagWriter.refreshFirmwareVerdict();
        }
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

        // (the icon is coloured at the end of this function, once the spool data is in -
        //  see the _reColorFilamentIcon call below)

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
            // applyToSpoolItem() only ever writes totalCombinedWeight (see its own comment
            // on why), and the drivenScope-driven subscribers only derive totalWeight from
            // it while drivenScope is FILAMENT - but the dialog just above set it to
            // COMBINED. Without this, the prefilled weight only shows up in "Initial total"
            // and the plain "Initial" (totalWeight) field a simple-mode user actually looks
            // at stays empty.
            self.updateFilamentInitialWithScopes();
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
        // Baseline for the unsaved-changes detection. Taken here at the very end rather
        // than in _updateActiveSpoolItem(), because showDialog() keeps writing to the form
        // afterwards (drivenScope, the default diameter and purchase date for new spools,
        // the U1 RFID prefill) - snapshotting earlier would report all of that as if the
        // user had typed it.
        self._resetFormSnapshot();

        // Colour the filament icon LAST, for the same reason: run earlier (as this used to,
        // right at the top) and it paints the previous spool's colour - or SpoolItem's red
        // DEFAULT_COLOR placeholder on a fresh form. The color.subscribe() handler was meant
        // to correct that once the real value arrived, but knockout does not notify when an
        // observable is re-assigned a primitive it already holds, so reopening the same
        // spool left the stale paint on screen.
        self._reColorFilamentIcon(self.spoolItemForEditing.color());
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
        // Repaint explicitly: copying replaces every form value while the dialog is already
        // open, and color.subscribe() stays silent when the copied colour happens to equal
        // the one already in the observable (same knockout behaviour as in showDialog).
        self._reColorFilamentIcon(self.spoolItemForEditing.color());
    };

    // ----------------- begin: unsaved-changes detection
    //
    // Two problems shared one root cause: the dialog had no idea whether anything had been
    // edited. Writing an NFC tag sends only the databaseId and the server re-loads the
    // *stored* spool (see SpoolManagerAPI.writeOctoScaleTag), so unsaved edits silently did
    // not reach the tag - while the diff table right above the button showed those very
    // edits, because it reads the live observables. Closing the dialog dropped them without
    // a word. Both are fixed from one snapshot taken once the form has been populated.
    //
    // Whitelist, not blacklist: only fields the server actually persists are compared
    // (mirrors _updateSpoolModelFromJSONData in SpoolManagerAPI.py). The view model carries
    // plenty of observables that are pure UI state (isSpoolVisible, drivenScope), derived
    // (absoluteTemperature, the *DateKO/*TimeKO split inputs, finishSelection/
    // finishCustomText feeding "finish") or catalog data (allLabels) - comparing those
    // would report changes the user never made. A blacklist would silently start producing
    // false positives the day someone adds another derived observable; this way a newly
    // added *persisted* field is merely not watched yet, which is the harmless direction.
    //
    // Note the date fields: the server reads firstUseKO/lastUseKO/purchasedOnKO, NOT
    // firstUse/lastUse/purchasedOn (those are the picker-owned display observables), so
    // the KO ones are what has to be watched here.
    self._dirtyRelevantFields = [
        "isTemplate",
        "isActive",
        "displayName",
        "vendor",
        "material",
        "materialCharacteristic",
        "density",
        "diameter",
        "diameterTolerance",
        "colorName",
        "color",
        "finish",
        "flowRateCompensation",
        "temperature",
        "minTemperature",
        "maxTemperature",
        "bedTemperature",
        "minBedTemperature",
        "maxBedTemperature",
        "enclosureTemperature",
        "dryingTemperature",
        "dryingTime",
        "td",
        "offsetTemperature",
        "offsetBedTemperature",
        "offsetEnclosureTemperature",
        "totalWeight",
        "spoolWeight",
        "remainingWeight",
        "totalLength",
        "usedLength",
        "usedWeight",
        "code",
        "rfidTagKey",
        "batchNumber",
        "firstUseKO",
        "lastUseKO",
        "purchasedOnKO",
        "purchasedFrom",
        "cost",
        "costUnit",
        "labels"
    ];

    // Labels for the warning dialogs. Reuses the OctoScale diff table's labels where they
    // exist instead of maintaining a second copy of the same strings; listed here are only
    // the fields that table has no reason to carry (a tag stores no labels or note).
    self._dirtyFieldLabels = {
        isTemplate: "Template",
        isActive: "Active",
        materialCharacteristic: "Material characteristic",
        rfidTagKey: "RFID tag key",
        firstUseKO: "First use",
        lastUseKO: "Last use",
        purchasedOnKO: "Purchased on",
        costUnit: "Cost unit",
        labels: "Labels",
        noteText: "Note"
    };

    self._dirtyFieldLabel = function (key) {
        if (self._dirtyFieldLabels[key] != null) {
            return self._dirtyFieldLabels[key];
        }
        for (var index = 0; index < OCTOSCALE_TAG_DIFF_FIELDS.length; index++) {
            if (OCTOSCALE_TAG_DIFF_FIELDS[index].key === key) {
                return OCTOSCALE_TAG_DIFF_FIELDS[index].label;
            }
        }
        return key;
    };

    // Normalises a value into the string form used for comparison. Same rule as
    // _describeConflictChanges(): the API hands numbers back as strings while the
    // observables hold real numbers, so a plain !== would flag every untouched number.
    // Arrays (labels) are joined, so a re-created but identical array is not a change.
    self._dirtyValueToText = function (value) {
        if (value == null) {
            return "";
        }
        if (Array.isArray(value)) {
            return value
                .map(function (entry) {
                    return entry == null ? "" : String(entry);
                })
                .join(" ");
        }
        return String(value);
    };

    // Snapshot of everything a save would persist, in comparison form.
    self._captureFormState = function () {
        var state = {};
        if (self.spoolItemForEditing == null) {
            return state;
        }
        self._dirtyRelevantFields.forEach(function (key) {
            var observable = self.spoolItemForEditing[key];
            if (typeof observable !== "function") {
                return;
            }
            state[key] = self._dirtyValueToText(ko.unwrap(observable));
        });
        // The note editor is not two-way bound: Quill's content only reaches the
        // observables inside saveSpoolItem(). Reading it straight from the editor is
        // therefore the only way an edited note shows up as a change at all.
        if (self.noteEditor != null) {
            state.noteText = self._dirtyValueToText(self.noteEditor.getText());
        }
        return state;
    };

    // Human readable list of what differs from the snapshot ("Drying time: 8 -> 6").
    // An empty list means there is nothing to lose.
    self._getUnsavedChanges = function () {
        var changes = [];
        if (self._formSnapshot == null) {
            return changes;
        }
        var currentState = self._captureFormState();
        Object.keys(currentState).forEach(function (key) {
            var before = self._formSnapshot[key];
            var after = currentState[key];
            if (before === after) {
                return;
            }
            // a field that was not part of the snapshot yet and is still empty is no change
            if (before == null && after === "") {
                return;
            }
            changes.push(
                self._dirtyFieldLabel(key) +
                    ": " +
                    (self._dirtyValueToText(before) || "-") +
                    " -> " +
                    (after || "-")
            );
        });
        return changes;
    };

    // Called wherever the form has been (re)populated programmatically or saved
    // successfully: from that point on, further changes belong to the user.
    // For the template only. Deliberately NOT a ko.computed: _captureFormState() also reads
    // the Quill note editor, which is not an observable, so a computed could not track it
    // and would go stale for exactly the field that is hardest to notice. Refreshed where
    // it is actually looked at - when the NFC panel re-renders its diff.
    self.hasUnsavedChanges = ko.observable(false);
    self.refreshUnsavedChangesFlag = function () {
        self.hasUnsavedChanges(self._getUnsavedChanges().length > 0);
    };

    self._resetFormSnapshot = function () {
        self._formSnapshot = self._captureFormState();
        self.hasUnsavedChanges(false);
    };

    self._buildUnsavedChangesMessage = function (changes) {
        var shownChanges = changes.slice(0, 12);
        if (changes.length > shownChanges.length) {
            shownChanges.push(
                "... and " + (changes.length - shownChanges.length) + " more"
            );
        }
        return (
            "This spool has changes that have not been saved yet:" +
            SPOOLMANAGER_DIALOGS.buildHtmlList(
                shownChanges.map(function (change) {
                    return SPOOLMANAGER_DIALOGS.escapeHtml(change);
                })
            )
        );
    };

    // Closes the dialog from code. Every internal close path goes through here so the
    // "unsaved changes" guard on the modal's hide event can tell an intentional close
    // (save, delete, conflict resolution, select-for-printing) apart from the user
    // dismissing the dialog via X / Close / Esc. The flag is cleared again by the "hidden"
    // handler, i.e. once the close has actually happened.
    self._allowDialogClose = false;
    self._closeSpoolDialog = function () {
        self._allowDialogClose = true;
        self.spoolDialog.modal("hide");
    };

    // Asks before throwing away unsaved edits. Returns true when the caller may proceed
    // with closing, false when a dialog was raised instead and the close has to be aborted.
    self._confirmDiscardUnsavedChanges = function () {
        if (self._allowDialogClose === true) {
            // closing on our own terms (save/delete/...) - nothing was lost
            return true;
        }
        var changes = self._getUnsavedChanges();
        if (changes.length === 0) {
            return true;
        }
        SPOOLMANAGER_DIALOGS.choose({
            title: "Unsaved changes",
            message: self._buildUnsavedChangesMessage(changes),
            question: "Close the dialog and discard these changes?",
            cancel: "Keep editing",
            proceed: ["Save and close", "Discard changes"],
            proceedClass: "primary"
        }).then(function (buttonIndex) {
            if (buttonIndex === 0) {
                // saveSpoolItem() closes the dialog itself on success, and keeps it open
                // (with an explanation) when validation or the server rejects the save
                self.saveSpoolItem();
                return;
            }
            if (buttonIndex === 1) {
                self._closeSpoolDialog();
            }
            // null = "Keep editing" / Esc / backdrop: leave the dialog exactly as it is
        });
        return false;
    };
    // ----------------- end: unsaved-changes detection

    // Fields worth naming in the conflict dialog. Weights first: a scale writing back a
    // measurement is the common source of a concurrent change.
    self._conflictRelevantFields = [
        {key: "remainingWeight", label: "Remaining weight", numeric: true},
        {key: "usedWeight", label: "Used weight", numeric: true},
        {key: "totalWeight", label: "Total weight", numeric: true},
        {key: "spoolWeight", label: "Empty spool weight", numeric: true},
        {key: "displayName", label: "Display name"},
        {key: "colorName", label: "Color"},
        {key: "material", label: "Material"},
        // The RFID teach-in (rfidTagKey POST handler) saves the spool server-side as soon
        // as a new tag UID is read - a write that can happen while this dialog is still
        // open (e.g. "write tag" reads the tag back for verification), bumping the version
        // without the form knowing. That is exactly the case that produced a conflict
        // dialog with no visible reason: the version had moved, but the only field that
        // actually changed (rfidTagKey) was not in this list, so the diff came up empty.
        {key: "rfidTagKey", label: "RFID tag key"}
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
            // numeric fields are compared as numbers: the API returns weights formatted as
            // strings (e.g. "1000.0"), while the observables hold real numbers (1000) - a
            // plain string comparison would flag that formatting difference as a conflict
            var myText = myValue == null ? "" : String(myValue);
            var serverText = serverValue == null ? "" : String(serverValue);
            var isDifferent;
            if (field.numeric) {
                var myNumber =
                    myValue == null || myValue === "" ? null : parseFloat(myValue);
                var serverNumber =
                    serverValue == null || serverValue === ""
                        ? null
                        : parseFloat(serverValue);
                // NaN !== NaN, but neither side should legitimately be NaN here (parseFloat
                // only runs on non-null/non-empty input) - falls back to a text diff instead
                // of silently treating "invalid number" as "equal"
                isDifferent =
                    isNaN(myNumber) || isNaN(serverNumber)
                        ? myText !== serverText
                        : myNumber !== serverNumber;
            } else {
                isDifferent = myText !== serverText;
            }
            if (isDifferent && (myText.length > 0 || serverText.length > 0)) {
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
                    self._closeSpoolDialog();
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
                        // the form now shows the server's state, so that is the new baseline
                        self._resetFormSnapshot();
                        self._reColorFilamentIcon(self.spoolItemForEditing.color());
                    } else {
                        self._closeSpoolDialog();
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

    // options (all optional):
    //   keepDialogOpen: true  -> save without closing the dialog. Used by the "save, then
    //                            write the tag" path, which needs the dialog (and with it
    //                            the tag writer and its device polling) to stay alive.
    //   onSaved: function     -> called ONLY after the spool really reached the database.
    //                            Every early return below (validation, server rejection,
    //                            unresolved conflict) deliberately leaves it uncalled, so a
    //                            caller that chains an action onto the save cannot act on a
    //                            save that never happened.
    self.saveSpoolItem = function (options) {
        var saveOptions = options != null ? options : {};
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
        // Material/vendor/diameter mandatory since 2026-08-25 (server enforces this too,
        // see SpoolManagerAPI.py's _updateSpoolModelFromJSONData - this check just avoids
        // a round trip for the common case of forgetting one while filling in the form).
        // Skipped for templates, same exemption the server applies. Reuses the same
        // SPOOLMANAGER_UTILS rules isFormValidForSubmit()/_isEveryMandatoryFieldValid()
        // already gate the Save button on, rather than duplicating the field logic here.
        if (!self.spoolItemForEditing.isTemplate()) {
            if (!self.isMaterialPresent()) {
                SPOOLMANAGER_DIALOGS.notify({
                    title: "Missing material",
                    message: "Please enter a material before saving the spool.",
                    type: "error"
                });
                return;
            }
            if (!self.isVendorPresent()) {
                SPOOLMANAGER_DIALOGS.notify({
                    title: "Missing vendor",
                    message: "Please enter a vendor before saving the spool.",
                    type: "error"
                });
                return;
            }
            if (!self.isDiameterPresent()) {
                SPOOLMANAGER_DIALOGS.notify({
                    title: "Missing diameter",
                    message: "Please enter a diameter before saving the spool.",
                    type: "error"
                });
                return;
            }
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
            function (success, validationErrors, conflict, savedSpool, serverError) {
                if (conflict != null) {
                    // someone else changed this spool while the dialog was open (e.g. a scale
                    // writing a measured weight via the API). The save did NOT happen - explain
                    // the situation and let the user decide, instead of silently dropping the edit.
                    self._handleSaveConflict(conflict);
                    return;
                }
                if (success === false) {
                    // server rejected the save - keep the dialog open and tell the user why
                    var message = serverError
                        ? SPOOLMANAGER_DIALOGS.escapeHtml(serverError)
                        : "Spool could not be saved.";
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
                // the server bumps the optimistic-lock version on every successful save -
                // adopt it now, otherwise a second save right after this one (e.g. the NFC
                // "write tag" flow, which saves once before writing and once more for the
                // rest of the form) always conflicts against the version this very save just
                // made stale, reporting "modified elsewhere" for no external change at all
                if (savedSpool != null && savedSpool.version != null) {
                    self.spoolItemForEditing.version(savedSpool.version);
                }
                // saved successfully - the form now matches the database again, so the
                // unsaved-changes warnings must not fire for the edits just persisted
                self._resetFormSnapshot();
                if (saveOptions.keepDialogOpen === true) {
                    // Caller wants to keep working in the dialog (see the tag-write path).
                    // The follow-up notifications below all concern the closed-dialog flow
                    // (they hand control back to closeDialogHandler), so none of them apply.
                    if (typeof saveOptions.onSaved === "function") {
                        saveOptions.onSaved();
                    }
                    return;
                }
                self.spoolItemForEditing.isSpoolVisible(false);
                self._closeSpoolDialog();
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
                if (typeof saveOptions.onSaved === "function") {
                    saveOptions.onSaved();
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
                    self._closeSpoolDialog();
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
        self._closeSpoolDialog();
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
