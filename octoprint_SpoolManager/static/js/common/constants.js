// Shared constants file structure adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10).
// MATERIALS_DENSITY_MAPPING uses our own SpoolmanDB-derived data instead of upstream's list.
/**
 * Defined without specifier to be globally accessible
 */
SPOOLMANAGER_CONSTANTS = {
    // Density values (g/cm3) derived from SpoolmanDB-Community
    // https://github.com/Icezaza2543/SpoolmanDB-Community (maintained fork of
    // https://github.com/Donkie/SpoolmanDB) - Copyright (c) 2024 Donkie, MIT License
    // Keys are the normalized material names, see SPOOLMANAGER_UTILS.normalizeMaterialKey().
    MATERIALS_DENSITY_MAPPING: {
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
    },
    DATES: {
        DISPLAY_FORMATS: {
            DATETIME_LOCAL: "YYYY-MM-DDTHH:mm",
            DATE: "YYYY-MM-DD",
        },
        PARSE_FORMATS: {
            DATETIME: "DD.MM.YYYY HH:mm",
            DATE: "DD.MM.YYYY",
        },
    },
    FILAMENT_STATS_CALC_MODES: {
        FILAMENT: "filament",
        COMBINED: "spool+filament",
        SPOOL: "spool",
    },
    DOM_SELECTORS: {
        SPOOL_DIALOG: "#dialog_spool_edit",
    },
    // Finish dropdown entries, shared by the edit dialog and the Add Spool Wizard so a new
    // finish only has to be added once. "custom" switches the UI to a free-text input; the
    // same value list is mirrored in SpoolItem's finish computed.
    FINISH_OPTIONS: [
        { text: "Silk", value: "silk" },
        { text: "Matt", value: "matt" },
        { text: "Marble", value: "marble" },
        { text: "Metal", value: "metal" },
        { text: "Glow", value: "glow" },
        { text: "Custom…", value: "custom" },
    ],
    COLORS: {
        DEFAULT: "#ff0000",
        RAINBOW: "rainbow",
        TRANSPARENT: "transparent",
    },
};
