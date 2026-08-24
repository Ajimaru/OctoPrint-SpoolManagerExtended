# coding=utf-8

# Lookup tables for the ported vendor tag parsers.
#
# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0,
# src/tag/openspool/constants.py and src/tag/spoolease/constants.py.
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
#
# Divergences from upstream: the per-vendor FilamentTypeExtendedData class is replaced by
# plain (bedTempC, dryingTempC, dryingTimeHours) tuples - nothing here needs behaviour.
#
# These values are *defaults per material*, not data from the tag: neither OpenSpool nor
# SpoolEase stores bed or drying temperatures, so upstream fills them in from the filament
# type. They are suggestions, and the UI must not present them as if the tag said so.

from __future__ import annotations

# material -> (bedTempC, dryingTempC, dryingTimeHours)
OPENSPOOL_TYPE_DEFAULTS = {
    "PLA": (60.0, 50.0, 8.0),
    "PETG": (70.0, 65.0, 8.0),
    "ABS": (100.0, 80.0, 8.0),
    "TPU": (50.0, 70.0, 8.0),
    "NYLON": (100.0, 80.0, 8.0),
}

# SpoolEase spells a few materials with its own suffixes; map them onto the base name.
SPOOLEASE_TYPE_ALIASES = {
    "ABS-S": "ABS",
    "PA-S": "PA",
    "PLA-S": "PLA",
    "TPU-AMS": "TPU",
}

SPOOLEASE_TYPE_DEFAULTS = {
    "ABS": (95.0, 80.0, 8.0),
    "ABS-GF": (95.0, 80.0, 8.0),
    "ABS-S": (95.0, 80.0, 8.0),
    "ASA": (95.0, 80.0, 8.0),
    "ASA-AERO": (85.0, 80.0, 8.0),
    "ASA-CF": (95.0, 80.0, 8.0),
    "BVOH": (60.0, 60.0, 6.0),
    "EVA": (55.0, 50.0, 6.0),
    "HIPS": (100.0, 70.0, 6.0),
    "PA": (110.0, 80.0, 10.0),
    "PA-CF": (110.0, 80.0, 10.0),
    "PA-GF": (110.0, 80.0, 10.0),
    "PA-S": (110.0, 80.0, 10.0),
    "PA6-CF": (110.0, 80.0, 10.0),
    "PC": (100.0, 80.0, 8.0),
    "PCTG": (80.0, 65.0, 6.0),
    "PE": (60.0, 60.0, 6.0),
    "PE-CF": (60.0, 60.0, 6.0),
    "PET-CF": (85.0, 80.0, 10.0),
    "PETG": (70.0, 65.0, 8.0),
    "PETG-CF": (70.0, 65.0, 8.0),
    "PHA": (60.0, 50.0, 6.0),
    "PLA": (55.0, 50.0, 8.0),
    "PLA-AERO": (45.0, 50.0, 8.0),
    "PLA-CF": (60.0, 55.0, 8.0),
    "PLA-S": (45.0, 50.0, 8.0),
    "PP": (90.0, 70.0, 12.0),
    "PP-CF": (90.0, 70.0, 12.0),
    "PP-GF": (90.0, 70.0, 12.0),
    "PPA-CF": (110.0, 120.0, 10.0),
    "PPA-GF": (110.0, 120.0, 10.0),
    "PPS": (110.0, 120.0, 10.0),
    "PPS-CF": (110.0, 120.0, 10.0),
    "PVA": (45.0, 60.0, 8.0),
    "TPU": (38.0, 70.0, 8.0),
    "TPU-AMS": (33.0, 70.0, 8.0),
}

# Sentinel upstream uses when a tag carries no manufacturing date at all.
NO_MANUFACTURING_DATE = "0001-01-01"


# --- Anycubic ---------------------------------------------------------------------------
# Ported from src/tag/anycubic/processor.py (itself adapted from
# https://github.com/DnG-Crafts/ACE-RFID). The tag stores filament length, not weight;
# these are the lengths Anycubic ships and what they weigh.
ANYCUBIC_LENGTH_M_TO_WEIGHT_G = {
    330: 1000,
    247: 750,
    198: 600,
    165: 500,
    82: 250,
}
ANYCUBIC_DEFAULT_WEIGHT_G = 1000


# --- Elegoo -----------------------------------------------------------------------------
# Ported from src/tag/elegoo/constants.py. The tag identifies a material by a
# (materialId, modifierId) pair; this resolves it to a name plus modifier list.
# Upstream folds a "6"/"12" modifier into the type name (PA + "6" -> "PA6"), which is done
# in _elegooMaterial() below rather than in a class.
_ELEGOO_MODIFIERS = {
    0x00: ("PLA", {
        0x00: [], 0x01: ["+"], 0x02: ["Pro"], 0x03: ["Silk"], 0x04: ["CF"],
        0x05: ["Carbon"], 0x06: ["Matte"], 0x07: ["Fluo"], 0x08: ["Wood"],
        0x09: ["Basic"], 0x0A: ["RAPID", "+"], 0x0B: ["Marble"], 0x0C: ["Galaxy"],
        0x0D: ["Red", "Copper"], 0x0E: ["Sparkle"],
    }),
    0x01: ("PETG", {
        0x00: [], 0x01: ["CF"], 0x02: ["GF"], 0x03: ["Pro"],
        0x04: ["Translucent"], 0x05: ["RAPID"],
    }),
    0x02: ("ABS", {0x00: [], 0x01: ["GF"]}),
    0x03: ("TPU", {0x00: [], 0x01: ["95A"], 0x02: ["RAPID", "95A"]}),
    0x04: ("PA", {
        0x00: [], 0x01: ["CF"], 0x03: ["HT", "CF"], 0x04: ["6"],
        0x05: ["6", "CF"], 0x06: ["12"], 0x07: ["12", "CF"],
    }),
    0x05: ("CPE", {0x00: []}),
    0x06: ("PC", {0x00: [], 0x01: ["TG"], 0x02: ["FR"]}),
    0x07: ("PVA", {0x00: []}),
    0x08: ("ASA", {0x00: []}),
    0x09: ("BVOH", {0x00: []}),
    0x0A: ("EVA", {0x00: []}),
    0x0B: ("HIPS", {0x00: []}),
    0x0C: ("PP", {0x00: [], 0x01: ["CF"], 0x02: ["GF"]}),
    0x0D: ("PPA", {0x00: [], 0x01: ["CF"], 0x02: ["GF"]}),
    0x0E: ("PPS", {0x00: [], 0x02: ["CF"]}),
}


def elegooMaterial(materialId, modifierId):
    """(typeName, [modifiers]) for an Elegoo material pair, or None if unknown."""
    entry = _ELEGOO_MODIFIERS.get(materialId)
    if entry is None:
        return None
    typeName, modifiers = entry
    if modifierId not in modifiers:
        return None

    resolved = list(modifiers[modifierId])
    # PA + "6" is really PA6 - the digit belongs to the type name, not the modifier list.
    for digit in ("6", "12"):
        if digit in resolved:
            typeName += digit
            resolved.remove(digit)
    return typeName, resolved
