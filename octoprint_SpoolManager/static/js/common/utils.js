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
};

// Expose to jinja templates (used by the "Last/First use" column binding in SpoolManager_tab.jinja2)
window.getDateFromAttribute = SPOOLMANAGER_UTILS.getDateFromAttribute;
