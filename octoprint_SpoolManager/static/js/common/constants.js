// Shared constants file structure adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10).
// MATERIALS_DENSITY_MAPPING uses our own SpoolmanDB-derived data instead of upstream's list.
/**
 * Defined without specifier to be globally accessible
 */
SPOOLMANAGER_CONSTANTS = {
    // Marks a spool whose weight came from an RFID tag's nominal value instead of a
    // scale reading (U1 RFID flow, where the spool is already loaded and cannot easily
    // be weighed). Stored in the existing `labels` field, so no schema migration is
    // needed; cleared on the first real weigh-in in the edit dialog.
    LABEL_WEIGHT_ESTIMATED: "weight-estimated",

    // Density values (g/cm3) derived from SpoolmanDB-Community
    // https://github.com/Icezaza2543/SpoolmanDB-Community (maintained fork of
    // https://github.com/Donkie/SpoolmanDB) - Copyright (c) 2024 Donkie, MIT License
    // Keys are the normalized material names, see SPOOLMANAGER_UTILS.normalizeMaterialKey().
    MATERIALS_DENSITY_MAPPING: {
        PLA: 1.24,
        PLA_PLUS: 1.24, // matches "PLA+" and legacy "PLA_plus"
        PLA_CF: 1.24,
        ABS: 1.04,
        ABS_PLUS: 1.06,
        ABS_T: 1.08,
        ABS_CF: 1.065,
        ASA: 1.05,
        ASA_CF: 1.12,
        PETG: 1.27,
        PETG_CF: 1.27,
        PCTG: 1.21,
        NYLON: 1.14,
        PA6: 1.13,
        PA11: 1.03,
        PA12: 1.01,
        PA_CF: 1.2,
        PA6_CF: 1.24,
        PA12_CF: 1.15,
        TPU: 1.21,
        TPU_85A: 1.12,
        TPU_90A: 1.185,
        TPU_95A: 1.21,
        TPE: 1.15,
        FLEXIBLE_TPE_32D: 1.1,
        FLEXIBLE_TPE_88A: 0.89,
        FPE: 2.16,
        PC: 1.2,
        PC_ABS: 1.19,
        PC_PBT: 1.2,
        PC_CF: 1.24,
        WOOD: 1.28,
        CARBON_FIBER: 1.3,
        HIPS: 1.03,
        PVA: 1.23,
        PVB: 1.1,
        BVOH: 1.25,
        PP: 0.9,
        PP_CF: 1.145,
        PP_GF: 1.03,
        POM: 1.4,
        PMMA: 1.18,
        PET: 1.38,
        PET_CF: 1.29,
        PBT: 1.31,
        PPS: 1.35,
        PPS_CF: 1.35,
        PVDF: 1.78,
        PEI_ULTEM: 1.27,
        PEKK: 1.28,
        PEEK: 1.32,
        PEEK_CF: 1.35,
        PPSU: 1.37,
        // aliases for alternative spellings (e.g. imported from Spoolman / typed manually)
        FLEXIBLE_TPU: 1.21,
        SEMI_FLEXIBLE_FPE: 2.16,
        POLYCARBONATE_PC: 1.2,
        POLYPROPYLENE_PP: 0.9,
        ACETAL_POM: 1.4,
        PEI: 1.27,
        PA: 1.14
    },
    DATES: {
        DISPLAY_FORMATS: {
            DATETIME_LOCAL: "YYYY-MM-DDTHH:mm",
            DATE: "YYYY-MM-DD"
        },
        PARSE_FORMATS: {
            DATETIME: "DD.MM.YYYY HH:mm",
            DATE: "DD.MM.YYYY"
        }
    },
    FILAMENT_STATS_CALC_MODES: {
        FILAMENT: "filament",
        COMBINED: "spool+filament",
        SPOOL: "spool"
    },
    DOM_SELECTORS: {
        SPOOL_DIALOG: "#dialog_spool_edit"
    },
    // Finish dropdown entries, shared by the edit dialog and the Add Spool Wizard so a new
    // finish only has to be added once. "custom" switches the UI to a free-text input; the
    // same value list is mirrored in SpoolItem's finish computed.
    FINISH_OPTIONS: [
        {text: "Silk", value: "silk"},
        {text: "Matt", value: "matt"},
        {text: "Glossy", value: "glossy"},
        {text: "Satin", value: "satin"},
        {text: "Sparkle", value: "sparkle"},
        {text: "Marble", value: "marble"},
        {text: "Metal", value: "metal"},
        {text: "Glow", value: "glow"},
        {text: "Custom…", value: "custom"}
    ],
    COLORS: {
        DEFAULT: "#ff0000",
        RAINBOW: "rainbow",
        TRANSPARENT: "transparent",
        // Swatch palette of the color picker. An array rather than a name->hex object
        // because the order is visible in the picker's grid, and object key order is
        // not something to rely on. The names double as the swatch tooltips and match
        // what tinycolor's toName() produces, so picking a swatch and letting the
        // dialog suggest a color name agree with each other.
        PALETTE: [
            {name: "white", hex: "#ffffff"},
            {name: "black", hex: "#000000"},
            {name: "red", hex: "#ff0000"},
            {name: "green", hex: "#008000"},
            {name: "blue", hex: "#0000ff"},
            {name: "yellow", hex: "#ffff00"},
            {name: "orange", hex: "#ffa500"},
            {name: "purple", hex: "#800080"},
            {name: "gray", hex: "#808080"},
            {name: "darkgray", hex: "#a9a9a9"},
            {name: "lightgray", hex: "#d3d3d3"},
            {name: "violet", hex: "#ee82ee"},
            {name: "pink", hex: "#ffc0cb"},
            {name: "brown", hex: "#a52a2a"},
            {name: "burlyWood", hex: "#deb887"},
            {name: "cyan", hex: "#00ffff"},
            {name: "magenta", hex: "#ff00ff"},
            {name: "lime", hex: "#00ff00"},
            {name: "navy", hex: "#000080"},
            {name: "teal", hex: "#008080"},
            {name: "olive", hex: "#808000"},
            {name: "maroon", hex: "#800000"},
            {name: "gold", hex: "#ffd700"},
            {name: "silver", hex: "#c0c0c0"},
            {name: "bronze", hex: "#cd7f32"},
            {name: "beige", hex: "#f5f5dc"},
            {name: "coral", hex: "#ff7f50"},
            {name: "turquoise", hex: "#40e0d0"},
            {name: "indigo", hex: "#4b0082"},
            {name: "khaki", hex: "#f0e68c"},
            {name: "salmon", hex: "#fa8072"},
            {name: "lavender", hex: "#e6e6fa"},
            {name: "mint", hex: "#3eb489"}
        ]
    }
};
