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
    // duplicated _evalFilterLabel() in SpoolSelectionTableComp and TableItemHelper
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
            colors: [defaultColor],
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

        var colors = (parts.colors && parts.colors.length > 0) ? parts.colors : [SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT];
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

    // The three fields a spool cannot be tracked without. Both the edit dialog and the wizard
    // gate saving on these, so the rule lives here rather than in each of them.
    // Takes the SpoolItem, reads the observables itself.
    isDisplayNamePresent: function (spoolItem) {
        return ((spoolItem.displayName() || "").trim().length > 0);
    },

    isColorNamePresent: function (spoolItem) {
        return ((spoolItem.colorName() || "").trim().length > 0);
    },

    isTotalCombinedWeightPresent: function (spoolItem) {
        var value = parseFloat(spoolItem.totalCombinedWeight());
        return !isNaN(value) && value > 0;
    },

    areMandatorySpoolFieldsPresent: function (spoolItem) {
        return (
            SPOOLMANAGER_UTILS.isDisplayNamePresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isColorNamePresent(spoolItem) &&
            SPOOLMANAGER_UTILS.isTotalCombinedWeightPresent(spoolItem)
        );
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
        if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) || /^\/\//.test(trimmed) ||
            trimmed.charAt(0) === "#" || trimmed.charAt(0) === "/") {
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
            if (op != null && op.attributes != null && typeof op.attributes.link === "string") {
                op.attributes.link = SPOOLMANAGER_UTILS.normalizeLinkUrl(op.attributes.link);
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
            link.setAttribute("href", SPOOLMANAGER_UTILS.normalizeLinkUrl(link.getAttribute("href")));
            link.setAttribute("target", "_blank");
            link.setAttribute("rel", "noopener noreferrer");
        }
        return container.innerHTML;
    },
};

// Expose to jinja templates (used by the "Last/First use" column binding in SpoolManager_tab.jinja2)
window.getDateFromAttribute = SPOOLMANAGER_UTILS.getDateFromAttribute;
