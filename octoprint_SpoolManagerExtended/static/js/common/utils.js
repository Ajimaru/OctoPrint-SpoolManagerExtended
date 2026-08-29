// Shared helpers file adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10).
// getDateFromAttribute keeps our proven formatOnlyDate() logic (upstream's version has an
// inverted empty-check and always returns ""); normalizeMaterialKey is our own addition.
/**
 * Defined without specifier to be globally accessible
 */
SPOOLMANAGER_UTILS = {
    // Returns only the date part ("DD.MM.YYYY") of a "DD.MM.YYYY HH:mm" observable attribute
    getDateFromAttribute: function (data, attributeName) {
        var dateValue = data[attributeName];
        if (dateValue != null && dateValue() != null && dateValue() != "") {
            return dateValue().split(" ")[0];
        }
        return "";
    },

    /**
     * @param {Number} value
     * @param {Number} precision
     */
    roundWithPrecision: function (value, precision) {
        var increments = Math.pow(10, precision);
        return Math.round((value + Number.EPSILON) * increments) / increments;
    },

    // Returns the filter-selection counter label for a catalog: "all" when every catalog
    // option is selected, otherwise the number of selected options. Consolidated from the
    // duplicated _evalFilterLabel() in SpoolManagerExtendedSpoolSelectionTableComp and SpoolManagerExtendedTableItemHelper
    // (adopted from mdziekon PR #23, adapted to the SPOOLMANAGER_UTILS object convention).
    buildFilterSelectionsCounter: function (allArray, selectionArray) {
        var selectionCount = 0;
        for (let item of allArray) {
            if (selectionArray.indexOf(item) != -1) {
                selectionCount++;
            }
        }
        var allSelected = selectionCount == allArray.length;
        return allSelected == true ? "all" : selectionArray.length;
    },

    // Replaces a filter's selection array with every entry of its catalog, mapped through
    // idMapper when the selection stores something other than the raw catalog entry (colors
    // are selected by colorId, materials/vendors by their plain value - pass idMapper only
    // for colors). Written as a single selectedKo(...) call so KO fires one change
    // notification, unlike the old push()+valueHasMutated() color branch that fired twice.
    // Adopted from mdziekon PR #15 (handleSelectAll*), generalized into one function.
    selectAllIntoFilter: function (allKo, selectedKo, idMapper) {
        var allEntries = allKo();
        var newSelection =
            typeof idMapper === "function"
                ? allEntries.map(idMapper)
                : allEntries.slice();
        selectedKo(newSelection);
    },

    // Builds the two-way "select/deselect all" computed for a catalog filter. read() reuses
    // buildFilterSelectionsCounter() so the checkbox can never disagree with the counter label
    // next to it (upstream's read() instead compares selectedKo().length === allKo().length,
    // which false-positives when e.g. a renamed color keeps the count equal but the actual
    // entries differ). write() replaces the whole selection via selectAllIntoFilter() when
    // checked, or clears it.
    //
    // The throttle:1 is required: the checked: binding re-reads the computed immediately after
    // its own write(), and without throttling KO can re-evaluate mid-write and snap the
    // checkbox back to its previous state.
    // Adopted from mdziekon PR #15 (showAll*ForFilter computed).
    buildShowAllForFilterKo: function (allKo, selectedKo, idMapper) {
        return ko
            .computed({
                read: function () {
                    var allEntries =
                        typeof idMapper === "function" ? allKo().map(idMapper) : allKo();
                    return (
                        SPOOLMANAGER_UTILS.buildFilterSelectionsCounter(
                            allEntries,
                            selectedKo()
                        ) === "all"
                    );
                },
                write: function (isChecked) {
                    if (isChecked) {
                        SPOOLMANAGER_UTILS.selectAllIntoFilter(
                            allKo,
                            selectedKo,
                            idMapper
                        );
                    } else {
                        selectedKo.removeAll();
                    }
                }
            })
            .extend({throttle: 1});
    },

    // Splits a stored color value into its parts. The persisted format is one of
    // "rainbow", "transparent", "transparent:#hex[;#hex...]" or "#hex[;#hex...]".
    // Shared by the edit dialog's color pickers and the wizard's single picker so both
    // agree on what a stored value means.
    parseSpoolColor: function (colorValue) {
        var defaultColor = SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT;
        var result = {
            isRainbow: false,
            isTransparent: false,
            // transparent without any base tint (plain "transparent", no ":#hex")
            isUntinted: false,
            colors: [defaultColor]
        };
        if (colorValue == null) {
            return result;
        }

        var rawValue = "" + colorValue;
        if (rawValue.toLowerCase() === SPOOLMANAGER_CONSTANTS.COLORS.RAINBOW) {
            result.isRainbow = true;
            return result;
        }

        var transparentPrefix = SPOOLMANAGER_CONSTANTS.COLORS.TRANSPARENT;
        if (rawValue.toLowerCase().indexOf(transparentPrefix) === 0) {
            result.isTransparent = true;
            rawValue = rawValue.substr(transparentPrefix.length);
            if (rawValue.indexOf(":") === 0) {
                rawValue = rawValue.substr(1);
            }
            if (rawValue.length === 0) {
                result.isUntinted = true;
                return result;
            }
        }

        var colors = rawValue.split(";").filter(function (entry) {
            return entry.length > 0;
        });
        if (colors.length > 0) {
            result.colors = colors;
        }
        return result;
    },

    // Inverse of parseSpoolColor: builds the value that gets persisted in the "color" field.
    composeSpoolColor: function (parts) {
        if (parts.isRainbow === true) {
            return SPOOLMANAGER_CONSTANTS.COLORS.RAINBOW;
        }
        if (parts.isTransparent === true && parts.isUntinted === true) {
            return SPOOLMANAGER_CONSTANTS.COLORS.TRANSPARENT;
        }

        var colors =
            parts.colors && parts.colors.length > 0
                ? parts.colors
                : [SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT];
        var composed = colors
            .map(function (entry) {
                return entry || SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT;
            })
            .join(";");

        if (parts.isTransparent === true) {
            composed = SPOOLMANAGER_CONSTANTS.COLORS.TRANSPARENT + ":" + composed;
        }
        return composed;
    },

    // Derives the color name to suggest for a stored color value. Returns null when there is no
    // sensible suggestion, in which case callers must leave the existing name alone.
    //
    // Shared by the edit dialog, the wizard and SpoolItem.update() so that picking a color leads
    // to the same name everywhere.
    colorNameForSpoolColor: function (colorValue) {
        var colorParts = SPOOLMANAGER_UTILS.parseSpoolColor(colorValue);
        if (colorParts.isRainbow === true) {
            return "Rainbow";
        }

        var transparentPrefix = colorParts.isTransparent === true ? "Transparent" : "";
        if (colorParts.isTransparent === true && colorParts.isUntinted === true) {
            // no base tint to name, the spool is simply clear
            return transparentPrefix;
        }
        if (colorParts.colors.length > 1) {
            // multi-color: no single name describes it, keep whatever the user typed
            return null;
        }

        var baseName = tinycolor(colorParts.colors[0]).toName();
        if (baseName != false) {
            return transparentPrefix ? transparentPrefix + " " + baseName : baseName;
        }
        // a hex value without a known name still tells us it is transparent
        return transparentPrefix ? transparentPrefix : null;
    },

    // The three fields a spool cannot be tracked without. Both the edit dialog and the wizard
    // gate saving on these, so the rule lives here rather than in each of them.
    // Takes the SpoolItem, reads the observables itself.
    isDisplayNamePresent: function (spoolItem) {
        return (spoolItem.displayName() || "").trim().length > 0;
    },

    isColorNamePresent: function (spoolItem) {
        return (spoolItem.colorName() || "").trim().length > 0;
    },

    isTotalCombinedWeightPresent: function (spoolItem) {
        var value = parseFloat(spoolItem.totalCombinedWeight());
        return !isNaN(value) && value > 0;
    },

    // Mandatory since 2026-08-25, mirroring the server-side check added to
    // SpoolManagerAPI.py's _updateSpoolModelFromJSONData: requested by the OctoScale
    // firmware team after a TigerTag write failed for a spool missing material/vendor -
    // TigerTag's firmware only accepts a write when material/brand/diameter (plus the
    // fixed type/measureUnit) all resolve, and a filament tag without a material is
    // barely meaningful anyway. Enforced here, where the user is already filling in the
    // form, rather than as special-case handling on the firmware side.
    isMaterialPresent: function (spoolItem) {
        return (spoolItem.material() || "").trim().length > 0;
    },

    isVendorPresent: function (spoolItem) {
        return (spoolItem.vendor() || "").trim().length > 0;
    },

    isDiameterPresent: function (spoolItem) {
        var value = parseFloat(spoolItem.diameter());
        return !isNaN(value) && value > 0;
    },

    areMandatorySpoolFieldsPresent: function (spoolItem) {
        return (
            SPOOLMANAGER_UTILS.isDisplayNamePresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isColorNamePresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isTotalCombinedWeightPresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isMaterialPresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isVendorPresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isDiameterPresent(spoolItem)
        );
    },

    ///////////////////////////////////////////////////////////////////////////////// DISPLAY UNITS
    // Weights are ALWAYS stored in grams (see SettingsKeys.SETTINGS_KEY_WEIGHT_UNIT); the setting
    // only picks how they are shown. Nothing in here may reach the api client or the database -
    // every validator, every payload and every SpoolItem observable keeps working in grams.
    //
    // SpoolManager.js and SpoolManager-EditSpoolDialog.js still carry their own older copies of
    // these tables from before this file existed. They are left alone on purpose; new code uses
    // the functions here.

    WEIGHT_UNIT_FACTORS: {g: 1, kg: 1000},
    UNIT_DISPLAY_DECIMALS: {mm: 1, cm: 2, m: 3, g: 1, kg: 3},

    // pluginSettings may still be null while a dialog is constructed but not yet bound, and an
    // unknown unit from a hand-edited config must not produce NaN, so both fall back to grams.
    selectedWeightUnit: function (pluginSettings) {
        var unit =
            pluginSettings != null && pluginSettings.weightUnit != null
                ? pluginSettings.weightUnit()
                : "g";
        return SPOOLMANAGER_UTILS.WEIGHT_UNIT_FACTORS[unit] ? unit : "g";
    },

    convertWeightForDisplay: function (rawGram, unit) {
        var value = parseFloat(rawGram);
        if (isNaN(value)) {
            return rawGram;
        }
        var factor = SPOOLMANAGER_UTILS.WEIGHT_UNIT_FACTORS[unit];
        return parseFloat(
            (value / factor).toFixed(SPOOLMANAGER_UTILS.UNIT_DISPLAY_DECIMALS[unit])
        );
    },

    // "1234.5 g" / "1.234 kg" - with a space, unlike the older copy in SpoolManager.js
    formatWeightForDisplay: function (rawGram, pluginSettings) {
        if (rawGram == null || rawGram === "") {
            return "";
        }
        if (isNaN(parseFloat(rawGram))) {
            return "" + rawGram;
        }
        var unit = SPOOLMANAGER_UTILS.selectedWeightUnit(pluginSettings);
        return SPOOLMANAGER_UTILS.convertWeightForDisplay(rawGram, unit) + " " + unit;
    },

    // Two-way computed over a base observable holding grams: read() divides into the display unit,
    // write() multiplies back. Bind inputs to this, never to the base observable, and keep writing
    // the base observable programmatically (OctoScale, template copy) - those writes bypass write()
    // and stay exact, since knockout only calls it on an actual user change.
    makeWeightDisplayKo: function (baseKo, unitFunction) {
        return ko.pureComputed({
            read: function () {
                return SPOOLMANAGER_UTILS.convertWeightForDisplay(
                    baseKo(),
                    unitFunction()
                );
            },
            write: function (newValue) {
                var value = parseFloat(newValue);
                if (isNaN(value)) {
                    // pass non-numeric input through untouched so the field's own validation sees it
                    baseKo(newValue);
                    return;
                }
                var factor = SPOOLMANAGER_UTILS.WEIGHT_UNIT_FACTORS[unitFunction()];
                // toFixed(1) mirrors the edit dialog's converter so both round identically
                baseKo(parseFloat((value * factor).toFixed(1)));
            }
        });
    },

    // Normalizes a material display name to a MATERIALS_DENSITY_MAPPING key:
    // "Flexible (TPU)" -> "FLEXIBLE_TPU", "PC/ABS" -> "PC_ABS", "PLA+" -> "PLA_PLUS"
    normalizeMaterialKey: function (materialName) {
        if (!materialName) {
            return "";
        }
        return materialName
            .trim()
            .toUpperCase()
            .replace(/\+/g, "_PLUS")
            .replace(/[^A-Z0-9]+/g, "_")
            .replace(/^_+|_+$/g, "");
    },

    // Quill 2 lets a scheme-less link like "web.de" through unchanged: its sanitize() probes the
    // value by assigning it to a throwaway <a>, and the browser resolves that against the current
    // page - so the scheme it inspects is OctoPrint's own "http", the protocol whitelist passes,
    // and the raw "web.de" ends up in the href. On click the browser resolves relatively again
    // and lands on http://<octoprint>/web.de instead of the site the user meant.
    // A spool note never has a sensible relative target, so a value without a scheme is treated
    // as an external host.
    normalizeLinkUrl: function (url) {
        if (url == null) {
            return url;
        }
        var trimmed = ("" + url).trim();
        if (trimmed.length === 0) {
            return trimmed;
        }
        // Explicit scheme ("https:", "mailto:", "tel:"), protocol-relative ("//host"), a fragment
        // or a root path - all deliberate, leave them alone
        if (
            /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) ||
            /^\/\//.test(trimmed) ||
            trimmed.charAt(0) === "#" ||
            trimmed.charAt(0) === "/"
        ) {
            return trimmed;
        }
        return "https://" + trimmed;
    },

    // Repairs link attributes in a stored Quill delta before it is loaded into the editor.
    // setContents() writes the stored attributes straight into the document without running them
    // through the Link blot's sanitize(), so a note saved before the normalisation landed would
    // keep its scheme-less href and still open relative to the OctoPrint page.
    repairNoteDeltaLinks: function (delta) {
        if (delta == null || Array.isArray(delta.ops) === false) {
            return delta;
        }
        delta.ops.forEach(function (op) {
            if (
                op != null &&
                op.attributes != null &&
                typeof op.attributes.link === "string"
            ) {
                op.attributes.link = SPOOLMANAGER_UTILS.normalizeLinkUrl(
                    op.attributes.link
                );
            }
        });
        return delta;
    },

    // Repairs stored note markup before it reaches the "html:" binding in the spool table:
    // scheme-less hrefs get https://, and every link is forced to open in a new tab. Quill sets
    // target/rel when it creates a link, but notes written by older versions may carry neither,
    // and an in-place navigation would tear down the whole OctoPrint UI.
    repairNoteHtmlLinks: function (noteHtml) {
        if (noteHtml == null || noteHtml.indexOf("<a") === -1) {
            return noteHtml;
        }
        var container = document.createElement("div");
        container.innerHTML = noteHtml;
        var links = container.querySelectorAll("a[href]");
        for (var index = 0; index < links.length; index++) {
            var link = links[index];
            // getAttribute, not .href: the property is already browser-resolved and would hand
            // back http://<octoprint>/web.de, hiding the very value that needs fixing
            link.setAttribute(
                "href",
                SPOOLMANAGER_UTILS.normalizeLinkUrl(link.getAttribute("href"))
            );
            link.setAttribute("target", "_blank");
            link.setAttribute("rel", "noopener noreferrer");
        }
        return container.innerHTML;
    }
};

// Shared prefill logic for RFID tags reported by a Snapmaker U1 (filament_detect).
// Lives here because BOTH the add-spool wizard and the edit dialog prefill from a tag -
// two copies of these field rules would drift apart.
//
// The backend already dropped the firmware's "unset" values (0 / "NONE"), so anything
// present here is meaningful. Colors arrive as integer RGB values (RGB_1..RGB_5).
SPOOLMANAGER_U1RFID = {
    // 16737792 -> "#FF6600"
    intToHexColor: function (value) {
        var numeric = parseInt(value, 10);
        if (isNaN(numeric) || numeric < 0) {
            return null;
        }
        var hex = (numeric & 0xffffff).toString(16).toUpperCase();
        while (hex.length < 6) {
            hex = "0" + hex;
        }
        return "#" + hex;
    },

    // Builds the SpoolManager color code ("#RRGGBB" or ";"-separated for multi-color)
    // out of the tag's RGB_1..RGB_5 fields. COLOR_NUMS says how many are meaningful.
    buildColorValue: function (metadata) {
        if (metadata == null) {
            return null;
        }
        var colorCount = parseInt(metadata["COLOR_NUMS"], 10);
        if (isNaN(colorCount) || colorCount < 1) {
            colorCount = 1;
        }
        // the edit dialog / wizard only handle up to 3 color slots
        colorCount = Math.min(colorCount, 3);
        var colors = [];
        for (var index = 1; index <= colorCount; index++) {
            var hexColor = SPOOLMANAGER_U1RFID.intToHexColor(metadata["RGB_" + index]);
            if (hexColor != null) {
                colors.push(hexColor);
            }
        }
        if (colors.length === 0) {
            return null;
        }
        return colors.join(";");
    },

    // Material label from MAIN_TYPE (+ SUB_TYPE when it adds information).
    buildMaterial: function (metadata) {
        if (metadata == null) {
            return null;
        }
        var mainType = metadata["MAIN_TYPE"];
        if (mainType == null || String(mainType).trim() === "") {
            return null;
        }
        return String(mainType).trim();
    },

    buildVendor: function (metadata) {
        if (metadata == null) {
            return null;
        }
        var vendor = metadata["VENDOR"] || metadata["MANUFACTURER"];
        if (vendor == null || String(vendor).trim() === "") {
            return null;
        }
        return String(vendor).trim();
    },

    // Applies tag metadata onto a spool item (wizard's spoolItemForCreation or the edit
    // dialog's spoolItemForEditing). Only writes fields the tag actually carries and
    // never clears an existing value - this is a suggestion, not a takeover.
    //
    // `rfidTagKey` is the stable matching key derived by the backend
    // (U1RfidManager.deriveRfidTagKey(), last 4 hex chars of the UID) and passed through
    // from the u1RfidUnknownTag push / getUnknownTags() response - NOT recomputed here,
    // so there is exactly one place that owns the derivation rule.
    //
    // `options.applyColor` receives the composed color code, because the two dialogs
    // drive their color pickers differently.
    applyToSpoolItem: function (spoolItem, metadata, uid, rfidTagKey, options) {
        if (spoolItem == null || metadata == null) {
            return;
        }
        options = options || {};

        var setIfPresent = function (fieldName, value) {
            if (value == null || value === "") {
                return;
            }
            var observable = spoolItem[fieldName];
            if (typeof observable === "function") {
                observable(value);
            }
        };

        setIfPresent("vendor", SPOOLMANAGER_U1RFID.buildVendor(metadata));
        var materialName = SPOOLMANAGER_U1RFID.buildMaterial(metadata);
        setIfPresent("material", materialName);

        // The tag has no density field, only a material name - look it up the same way
        // the wizard's own material picker does (_suggestDensityForMaterial), so a
        // material prefilled from the tag doesn't silently leave density empty. Both
        // dialogs get this via applyToSpoolItem() rather than each reimplementing it -
        // the edit dialog had no material->density suggestion of its own at all.
        if (materialName != null && typeof spoolItem.density === "function") {
            var currentDensity = parseFloat(spoolItem.density());
            if (isNaN(currentDensity) || currentDensity <= 0) {
                var suggestedDensity =
                    SPOOLMANAGER_CONSTANTS.MATERIALS_DENSITY_MAPPING[
                        SPOOLMANAGER_UTILS.normalizeMaterialKey(materialName)
                    ];
                if (suggestedDensity) {
                    spoolItem.density(suggestedDensity);
                }
            }
        }

        // Temperatures: prefer the tag's max hotend temp, fall back to the min.
        var hotendTemp = metadata["HOTEND_MAX_TEMP"] || metadata["HOTEND_MIN_TEMP"];
        setIfPresent("temperature", hotendTemp);
        setIfPresent("bedTemperature", metadata["BED_TEMP"]);

        // WEIGHT is the tag's *nominal* filament amount (e.g. 1000 g on a fresh spool),
        // not what is left - good enough as a starting point, and the mandatory weight
        // step (or a later scale reading) can always refine it.
        //
        // Writing totalWeight directly does NOT satisfy the wizard's weight requirement:
        // isTotalCombinedWeightPresent() (and thus canGoNext() on the weight step) checks
        // totalCombinedWeight, a separate observable that only derives from totalWeight
        // during the initial update() from a server payload - not on live edits like this
        // one. So the nominal weight must go into totalCombinedWeight instead; the wizard's
        // own _syncFilamentWeight() (called right after this) then derives totalWeight
        // back out of it, same as if the user had typed the combined weight by hand.
        var nominalWeight = parseFloat(metadata["WEIGHT"]);
        if (
            !isNaN(nominalWeight) &&
            nominalWeight > 0 &&
            typeof spoolItem.totalCombinedWeight === "function"
        ) {
            var existingSpoolWeight = parseFloat(spoolItem.spoolWeight());
            if (isNaN(existingSpoolWeight)) {
                existingSpoolWeight = 0;
            }
            spoolItem.totalCombinedWeight(nominalWeight + existingSpoolWeight);
        }

        // `code` is deliberately NOT set to the UID here: it's a free-text field a spool
        // may already carry its own, unrelated serial number in, and none of the fields
        // filament_detect exposes (SKU, MF_DATE, ...) is an actual serial number - they're
        // product/batch data, identical across every spool of the same item (see
        // U1RfidManager's SKU-collision note). `rfidTagKey` is the only thing
        // U1RfidManager actually looks tags up by - see the PRELIMINARY collision note on
        // SpoolModel.rfidTagKey / deriveRfidTagKey().
        setIfPresent("rfidTagKey", rfidTagKey);

        var colorValue = SPOOLMANAGER_U1RFID.buildColorValue(metadata);
        if (colorValue != null && typeof options.applyColor === "function") {
            // Both dialogs already suggest a name as part of applying the color (the
            // wizard's _applySuggestedColorName / the edit dialog's equivalent), using
            // the SAME casing convention as every other source (SpoolmanDB, manual
            // picker): tinycolor().toName() as-is, e.g. "red", never "Red". Do not
            // duplicate that write here - two writers for one field is how the previous
            // version ended up fighting itself and leaving stale casing.
            options.applyColor(colorValue);
        }
        // colorName is mandatory and must never be left at whatever a previous run put
        // there. The suggestion above only covers CSS-known colors though - a spool like
        // #080A0D (near-black) has no tinycolor name, so the mandatory field would stay
        // empty (or stale). Only step in for that gap, and only after the dialog's own
        // suggestion had its chance to run.
        if (colorValue != null && typeof spoolItem.colorName === "function") {
            var nameAfterSuggestion = (spoolItem.colorName() || "").trim();
            if (nameAfterSuggestion === "") {
                spoolItem.colorName(SPOOLMANAGER_U1RFID.buildColorName(colorValue));
            }
        }
    },

    // Applies vendor tag fields (from POST /octoscale/readTag) onto a spool item.
    //
    // Sibling of applyToSpoolItem above, and deliberately not a rewrite of it: that one
    // takes the Snapmaker U1's own metadata keys (HOTEND_MAX_TEMP, RGB_1, ...), this one
    // takes the flat camelCase field names the backend already speaks - the same names
    // _buildFullSpoolPayload() writes to a tag. Same "suggestion, not a takeover" rule:
    // only fields the tag actually carries are written, nothing is ever cleared.
    applyTagFieldsToSpoolItem: function (spoolItem, fields, uid, rfidTagKey, options) {
        if (spoolItem == null || fields == null) {
            return;
        }
        options = options || {};

        var setIfPresent = function (fieldName, value) {
            if (value == null || value === "") {
                return;
            }
            var observable = spoolItem[fieldName];
            if (typeof observable === "function") {
                observable(value);
            }
        };

        // Everything except the three fields below is a straight write - the backend has
        // already done the unit conversions and dropped values the tag did not carry.
        var directFields = [
            "vendor",
            "material",
            "materialCharacteristic",
            "diameter",
            "temperature",
            "minTemperature",
            "maxTemperature",
            "bedTemperature",
            "minBedTemperature",
            "maxBedTemperature",
            "dryingTemperature",
            "dryingTime",
            "td",
            // Fields no vendor tag carries, but OctoScale's own extended format does (see
            // FilamentTagParsers.py's OctoScaleExtended*TagParser classes) - the compare
            // table already showed these rows (OCTOSCALE_TAG_DIFF_FIELDS has always listed
            // them), but no vendor tag had ever populated them until now.
            "diameterTolerance",
            "enclosureTemperature",
            "offsetTemperature",
            "offsetBedTemperature",
            "offsetEnclosureTemperature",
            "spoolWeight",
            "usedWeight",
            "remainingWeight",
            "totalLength",
            "usedLength",
            "code",
            "batchNumber",
            "purchasedFrom",
            "finish",
            "displayName",
            "cost"
        ];
        directFields.forEach(function (fieldName) {
            setIfPresent(fieldName, fields[fieldName]);
        });

        // Density is not on any vendor tag - look it up from the material name, the same
        // way applyToSpoolItem does, so a tag-filled material does not leave it empty.
        if (fields["material"] != null && typeof spoolItem.density === "function") {
            var currentDensity = parseFloat(spoolItem.density());
            if (isNaN(currentDensity) || currentDensity <= 0) {
                var suggestedDensity =
                    SPOOLMANAGER_CONSTANTS.MATERIALS_DENSITY_MAPPING[
                        SPOOLMANAGER_UTILS.normalizeMaterialKey(fields["material"])
                    ];
                if (suggestedDensity) {
                    spoolItem.density(suggestedDensity);
                }
            }
        }

        // Same reasoning as in applyToSpoolItem: the wizard's weight step validates
        // totalCombinedWeight, which only derives from totalWeight during the initial
        // update() - so a live edit has to go the other way round.
        var nominalWeight = parseFloat(fields["totalWeight"]);
        if (
            !isNaN(nominalWeight) &&
            nominalWeight > 0 &&
            typeof spoolItem.totalCombinedWeight === "function"
        ) {
            var existingSpoolWeight = parseFloat(spoolItem.spoolWeight());
            if (isNaN(existingSpoolWeight)) {
                existingSpoolWeight = 0;
            }
            spoolItem.totalCombinedWeight(nominalWeight + existingSpoolWeight);
        }

        // `code` is deliberately left alone - see the note in applyToSpoolItem.
        setIfPresent("rfidTagKey", rfidTagKey);

        var colorValue = fields["color"];
        if (colorValue != null && typeof options.applyColor === "function") {
            options.applyColor(colorValue);
        }
        // Fill the mandatory colorName only if the dialog's own suggestion left it empty
        // (near-black shades have no CSS name) - never fight the dialog for that field.
        if (colorValue != null && typeof spoolItem.colorName === "function") {
            var nameAfterSuggestion = (spoolItem.colorName() || "").trim();
            if (nameAfterSuggestion === "") {
                spoolItem.colorName(SPOOLMANAGER_U1RFID.buildColorName(colorValue));
            }
        }
    },

    // Field-by-field variant of applyTagFieldsToSpoolItem above, for the "which values do
    // you want to import" checkbox dialog on an existing spool: only keys present in
    // selectedKeys (a plain object/array-like of {key: true}, or an array of key strings)
    // are written, everything else on the tag is left alone even though it was read.
    //
    // Deliberately a separate function rather than a selectedKeys parameter bolted onto
    // applyTagFieldsToSpoolItem: that one's "suggestion, not a takeover" semantics
    // (dropping null/empty tag values silently) is exactly right for the no-selection wizard
    // flow, but would make an unchecked box indistinguishable from a tag that simply didn't
    // carry that field - the selection has to be checked *before* falling back to "tag
    // didn't have it", not folded into the same condition.
    applySelectedTagFieldsToSpoolItem: function (
        spoolItem,
        fields,
        selectedKeys,
        uid,
        rfidTagKey,
        options
    ) {
        if (spoolItem == null || fields == null || selectedKeys == null) {
            return;
        }
        options = options || {};

        var isSelected;
        if (Array.isArray(selectedKeys)) {
            isSelected = function (key) {
                return selectedKeys.indexOf(key) !== -1;
            };
        } else {
            isSelected = function (key) {
                return selectedKeys[key] === true;
            };
        }

        var setIfSelected = function (fieldName, value) {
            if (!isSelected(fieldName) || value == null || value === "") {
                return;
            }
            var observable = spoolItem[fieldName];
            if (typeof observable === "function") {
                observable(value);
            }
        };

        var directFields = [
            "vendor",
            "material",
            "materialCharacteristic",
            "diameter",
            "temperature",
            "minTemperature",
            "maxTemperature",
            "bedTemperature",
            "minBedTemperature",
            "maxBedTemperature",
            "dryingTemperature",
            "dryingTime",
            "td",
            // Fields no vendor tag carries, but OctoScale's own extended format does (see
            // FilamentTagParsers.py's OctoScaleExtended*TagParser classes) - the compare
            // table already showed these rows (OCTOSCALE_TAG_DIFF_FIELDS has always listed
            // them), but no vendor tag had ever populated them until now.
            "diameterTolerance",
            "enclosureTemperature",
            "offsetTemperature",
            "offsetBedTemperature",
            "offsetEnclosureTemperature",
            "spoolWeight",
            "usedWeight",
            "remainingWeight",
            "totalLength",
            "usedLength",
            "code",
            "batchNumber",
            "purchasedFrom",
            "finish",
            "displayName",
            "cost"
        ];
        directFields.forEach(function (fieldName) {
            setIfSelected(fieldName, fields[fieldName]);
        });

        // "density" is its own checkbox row (see the compare-table builder in
        // SpoolManager-OctoScale.js) rather than an implicit side effect of selecting
        // material, unlike applyTagFieldsToSpoolItem's "suggestion" behaviour - a user
        // reviewing a list of checkboxes expects each row to control exactly itself.
        if (isSelected("density") && fields["material"] != null) {
            var suggestedDensity =
                SPOOLMANAGER_CONSTANTS.MATERIALS_DENSITY_MAPPING[
                    SPOOLMANAGER_UTILS.normalizeMaterialKey(fields["material"])
                ];
            if (suggestedDensity && typeof spoolItem.density === "function") {
                spoolItem.density(suggestedDensity);
            }
        }

        // Same totalWeight -> totalCombinedWeight indirection as applyTagFieldsToSpoolItem -
        // see its comment for why. "totalWeight" is the checkbox row's key (it's what the
        // compare table shows and what the tag actually carries); the derived form field is
        // totalCombinedWeight, and updateFilamentInitialWithScopes() (called by the caller
        // right after, same as elsewhere in this dialog) fills the visible "Initial" field.
        if (isSelected("totalWeight")) {
            var nominalWeight = parseFloat(fields["totalWeight"]);
            if (
                !isNaN(nominalWeight) &&
                nominalWeight > 0 &&
                typeof spoolItem.totalCombinedWeight === "function"
            ) {
                var existingSpoolWeight = parseFloat(spoolItem.spoolWeight());
                if (isNaN(existingSpoolWeight)) {
                    existingSpoolWeight = 0;
                }
                spoolItem.totalCombinedWeight(nominalWeight + existingSpoolWeight);
            }
        }

        if (isSelected("rfidTagKey")) {
            setIfSelected("rfidTagKey", rfidTagKey);
        }

        var colorValue = fields["color"];
        if (isSelected("color") && colorValue != null) {
            if (typeof options.applyColor === "function") {
                options.applyColor(colorValue);
            }
            if (typeof spoolItem.colorName === "function") {
                var nameAfterSuggestion = (spoolItem.colorName() || "").trim();
                if (nameAfterSuggestion === "") {
                    spoolItem.colorName(SPOOLMANAGER_U1RFID.buildColorName(colorValue));
                }
            }
        }
    },

    // Best-effort color name for a composed color code. Prefers the exact CSS name,
    // otherwise falls back to the nearest basic hue so the mandatory field is never left
    // empty (and never keeps a stale name from a previous wizard run).
    buildColorName: function (colorValue) {
        if (colorValue == null || colorValue === "") {
            return "";
        }
        if (colorValue.indexOf(";") >= 0) {
            return "Multi-color";
        }

        // colorNameForSpoolColor() already follows the codebase's convention (lowercase
        // CSS names like "red"; "Rainbow"/"Transparent"/"Multi-color" as the only
        // capitalized special cases) - reuse its result as-is, do not re-capitalize it.
        var exactName = null;
        if (typeof SPOOLMANAGER_UTILS.colorNameForSpoolColor === "function") {
            exactName = SPOOLMANAGER_UTILS.colorNameForSpoolColor(colorValue);
        }
        if (exactName != null && String(exactName).trim() !== "") {
            return exactName;
        }

        return SPOOLMANAGER_U1RFID.approximateColorName(colorValue);
    },

    // Rough hue/lightness classification for hex values CSS has no name for.
    approximateColorName: function (hexColor) {
        var match = /^#?([0-9a-f]{6})$/i.exec(String(hexColor).trim());
        if (match == null) {
            return "";
        }
        var numeric = parseInt(match[1], 16);
        var red = (numeric >> 16) & 0xff;
        var green = (numeric >> 8) & 0xff;
        var blue = numeric & 0xff;

        var max = Math.max(red, green, blue);
        var min = Math.min(red, green, blue);
        var delta = max - min;

        // near-greyscale first: hue is meaningless there. Lowercase throughout, matching
        // colorNameForSpoolColor()'s convention (tinycolor's CSS names are lowercase too).
        if (delta <= 20) {
            if (max <= 40) return "black";
            if (max >= 225) return "white";
            return max <= 128 ? "dark grey" : "grey";
        }
        if (max <= 45) {
            // dark enough that the hue is barely visible
            return "black";
        }

        var hue;
        if (max === red) {
            hue = ((green - blue) / delta) % 6;
        } else if (max === green) {
            hue = (blue - red) / delta + 2;
        } else {
            hue = (red - green) / delta + 4;
        }
        hue = Math.round(hue * 60);
        if (hue < 0) {
            hue += 360;
        }

        var hueName;
        if (hue < 15 || hue >= 345) hueName = "red";
        else if (hue < 45) hueName = "orange";
        else if (hue < 70) hueName = "yellow";
        else if (hue < 170) hueName = "green";
        else if (hue < 200) hueName = "cyan";
        else if (hue < 260) hueName = "blue";
        else if (hue < 290) hueName = "purple";
        else if (hue < 345) hueName = "magenta";

        if (max <= 90) {
            return "dark " + hueName;
        }
        if (min >= 170) {
            return "light " + hueName;
        }
        return hueName;
    },

    // Short human readable summary of a detected tag, for the popup text.
    describeTag: function (metadata) {
        if (metadata == null) {
            return "";
        }
        var parts = [];
        var vendor = SPOOLMANAGER_U1RFID.buildVendor(metadata);
        var material = SPOOLMANAGER_U1RFID.buildMaterial(metadata);
        if (vendor != null) {
            parts.push(vendor);
        }
        if (material != null) {
            parts.push(material);
        }
        if (metadata["SUB_TYPE"]) {
            parts.push(String(metadata["SUB_TYPE"]));
        }
        return parts.join(" ");
    }
};

// Expose to jinja templates (used by the "Last/First use" column binding in SpoolManager_tab.jinja2)
window.getDateFromAttribute = SPOOLMANAGER_UTILS.getDateFromAttribute;
