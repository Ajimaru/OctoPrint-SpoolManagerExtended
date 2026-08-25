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


# ---------------------------------------------------------------------------------------
# Qidi
#
# Ported from OpenRFID src/tag/qidi/processor.py, itself adapted from
# https://github.com/TinkerBarn/BoxRFID.
#
# Qidi tags are Mifare Classic 1K written with the *factory* key (FFFFFFFFFFFF), so any
# blank Classic tag authenticates just as well. Recognition therefore rests entirely on
# the content in sector 1 being plausible - see QidiTagParser for the checks.
QIDI_MATERIALS = {
    0x01: "PLA",
    0x02: "ABS",
    0x03: "PETG",
    0x04: "TPU",
    0x05: "PA",
    0x06: "PC",
    0x07: "ASA",
    0x08: "PVA",
    0x09: "HIPS",
    0x0A: "PP",
    0x0B: "PEEK",
    0x0C: "PEI",
}

# The tag carries no nominal weight, so the standard spool size stands in for it.
QIDI_DEFAULT_WEIGHT_G = 1000


# ---------------------------------------------------------------------------------------
# Snapmaker
#
# Ported from paxx12-snapmaker-u1/spool-link-apps (GPL-3.0), file
# android-app/app/src/main/java/dev/pages/paxx12/spoollink/formats/SnapmakerFormat.kt
# (repository state 2026-07-30). See THIRD_PARTY_NOTICES.md.
SNAPMAKER_MAIN_TYPES = {
    1: "PLA",
    2: "PETG",
    3: "ABS",
    4: "TPU",
    5: "PVA",
    6: "ASA",
    9: "PA",
    10: "PA-CF",
    11: "PA-GF",
    12: "PC",
    20: "PLA-CF",
    22: "PEBA",
    23: "TPE",
}

SNAPMAKER_SUB_TYPES = {
    1: "Basic",
    2: "Matte",
    3: "SnapSpeed",
    4: "Silk",
    5: "Support",
    6: "HF",
    7: "95A",
    8: "95A HF",
    9: "90A",
    10: "85A",
    11: "Wood",
    12: "Translucent",
    13: "Full Spectrum",
}


# ---------------------------------------------------------------------------------------
# TigerTag
#
# Lookup tables from TigerTag-Project/TigerTag-SDK-Python (`tigertag/database/*.json`),
# Apache-2.0, Copyright TigerTag Corp. 2025-2026. Shipped as data under common/tagdata/;
# see THIRD_PARTY_NOTICES.md.
#
# The ids are not sequential and carry no structure, so they have to be looked up - there is
# nothing to derive them from. Unknown ids degrade to None here and are surfaced as
# "Unknown(<id>)" rather than failing the parse: a table that ages should cost a label, not
# the whole tag.
import json as _json
import os as _os

_TAG_DATA_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tagdata")
_tigerTagIds = None


def _loadTigerTagIds():
    global _tigerTagIds
    if _tigerTagIds is None:
        try:
            with open(
                _os.path.join(_TAG_DATA_DIR, "tigertag_ids.json"), "rb"
            ) as handle:
                _tigerTagIds = _json.loads(handle.read().decode("utf-8"))
        except (IOError, OSError, ValueError):
            # Missing or unreadable data file must not break tag reading; every lookup then
            # degrades to "Unknown(<id>)", which is the same path an outdated table takes.
            _tigerTagIds = {}
    return _tigerTagIds


def tigerTagLabel(sectionName, identifier):
    """Label for a TigerTag id, or None when the id is not in the shipped table."""
    if identifier is None:
        return None
    section = _loadTigerTagIds().get(sectionName) or {}
    return section.get(str(identifier))


def tigerTagDiameterMm(diameterId):
    """Nominal diameter in mm; falls back to 1.75 when the id is unknown."""
    label = tigerTagLabel("id_diameter", diameterId)
    try:
        return float(label)
    except (TypeError, ValueError):
        return 1.75


# Multiplier to grams per unit label. Length units cannot be converted without a density,
# so they yield None rather than a wrong number.
_TIGERTAG_UNIT_TO_GRAMS = {"g": 1.0, "kg": 1000.0}


def tigerTagWeightGrams(measure, unitId):
    """Filament quantity in grams, or None when the tag states it as a length.

    The measure field is a bare number - the unit lives in a separate id. Assuming grams
    would be wrong by a factor of 1000 for a tag that says kilograms, so an unknown or
    non-mass unit returns None and leaves the weight unset instead of guessing.
    """
    if measure is None:
        return None
    unitLabel = tigerTagLabel("id_measure_unit", unitId)
    multiplier = _TIGERTAG_UNIT_TO_GRAMS.get(unitLabel)
    if multiplier is None:
        return None
    return int(round(measure * multiplier))
