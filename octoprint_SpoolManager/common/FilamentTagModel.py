# coding=utf-8

# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0,
# src/filament/generic.py, src/filament/valid_materials.py and src/tag/tag_types.py.
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
#
# Divergences from upstream:
#  - GenericFilament's constructor no longer raises ValueError when the filament type is
#    not in VALID_BASE_MATERIALS. Vendors ship names the list does not know ("PLA Basic",
#    "Support for PLA"), and refusing the whole tag over the name would throw away the
#    color, weight and temperatures that were read just fine. The type string is kept and
#    `typeRecognized` records whether it was known, so the UI can present it as a
#    suggestion rather than a fact.
#  - pretty_text() dropped (logging only).
#  - TagType is an int-valued enum resolved from the tagType strings OctoScale reports,
#    with the SAK lookup kept as a fallback. Upstream's `match` statement is Python 3.10+,
#    this plugin targets 3.9.

from __future__ import annotations

import hashlib

# Materials the OpenRFID parsers treat as known base types. Used to flag - not to reject -
# a filament type read off a tag.
VALID_BASE_MATERIALS = [
    "ABS", "ABS-CF", "ABS-GF",
    "ASA", "ASA-CF", "ASA-GF", "ASA-AERO",
    "BVOH", "CoPE", "EVA", "FLEX", "HIPS",
    "PA", "PA-CF", "PA-GF",
    "PA6", "PA6-CF", "PA6-GF",
    "PA11", "PA11-CF", "PA11-GF",
    "PA12", "PA12-CF", "PA12-GF",
    "PAHT", "PAHT-CF", "PAHT-GF",
    "PC", "PC-ABS", "PC-CF", "PC-PBT",
    "PCL", "PCTG",
    "PE", "PE-CF", "PE-GF",
    "PEI-1010", "PEI-1010-CF", "PEI-1010-GF",
    "PEI-9085", "PEI-9085-CF", "PEI-9085-GF",
    "PEEK", "PEEK-CF", "PEEK-GF",
    "PEKK", "PEKK-CF",
    "PES",
    "PET", "PET-CF", "PET-GF",
    "PETG", "PETG-CF", "PETG-GF",
    "PHA", "PI",
    "PLA", "PLA-AERO", "PLA-CF",
    "POM",
    "PP", "PP-CF", "PP-GF",
    "PPA-CF", "PPA-GF",
    "PPS", "PPS-CF",
    "PPSU", "PSU",
    "PVA", "PVB", "PVDF",
    "SBS", "TPI", "TPU",
]


class TagType(object):
    # Plain constants rather than enum.Enum: they are only ever compared, and this keeps
    # the ported parser bodies (which do `scan_result.tag_type != TagType.MifareUltralight`)
    # working unchanged.
    UNKNOWN = 0xFF
    MIFARE_CLASSIC_1K = 0x08
    MIFARE_ULTRALIGHT = 0x00

    # Names kept in OpenRFID's spelling so ported parser bodies need no edits.
    Unknown = UNKNOWN
    MifareClassic1k = MIFARE_CLASSIC_1K
    MifareUltralight = MIFARE_ULTRALIGHT


# What OctoScale's /nfcprobe reports in its "tagType" field. NTAG and Mifare Ultralight are
# the same protocol class as far as the parsers are concerned - the firmware buckets both
# as "ntag" (verified: it tests explicitly for the Mifare Classic SAKs and lets everything
# else fall through to the NTAG branch).
_OCTOSCALE_TAG_TYPES = {
    "mifareClassic1k": TagType.MIFARE_CLASSIC_1K,
    "ntag": TagType.MIFARE_ULTRALIGHT,
}


def tagTypeFromOctoScale(tagTypeString):
    if tagTypeString is None:
        return TagType.UNKNOWN
    return _OCTOSCALE_TAG_TYPES.get(str(tagTypeString), TagType.UNKNOWN)


def tagTypeFromSak(sak):
    # Fallback for firmware that reports a SAK but no tagType. Accepts bytes or int.
    if sak is None:
        return TagType.UNKNOWN
    if isinstance(sak, (bytes, bytearray)):
        if sak == b"\x08":
            return TagType.MIFARE_CLASSIC_1K
        if sak == b"\x04\x00" or sak == b"\x00":
            return TagType.MIFARE_ULTRALIGHT
        return TagType.UNKNOWN
    if sak == 0x08:
        return TagType.MIFARE_CLASSIC_1K
    if sak == 0x00:
        return TagType.MIFARE_ULTRALIGHT
    return TagType.UNKNOWN


def to_rgba(argb):
    """0xAARRGGBB -> 0xRRGGBBAA."""
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return (r << 24) | (g << 16) | (b << 8) | a


class ScanResult(object):
    # Only tag_type and uid are ever read by the parsers (verified across every processor
    # ported here); atqa/bcc/sak exist for diagnostics and are optional.
    def __init__(self, tag_type, uid, atqa=None, bcc=None, sak=None):
        self.tag_type = tag_type
        self.uid = uid
        self.atqa = atqa
        self.bcc = bcc
        self.sak = sak

    @property
    def uidHex(self):
        if self.uid is None:
            return None
        return self.uid.hex().upper()

    def to_dict(self):
        return {
            "tagType": self.tag_type,
            "uid": self.uidHex,
        }


class GenericFilament(object):
    """One filament as read off a tag, normalized across all vendor formats."""

    def __init__(
        self,
        source_processor,
        unique_id,
        manufacturer,
        type,  # noqa: A002 - name kept from upstream so ported parser bodies still fit
        modifiers,
        colors,  # list of 0xAARRGGBB ints
        diameter_mm,
        weight_grams,
        hotend_min_temp_c,
        hotend_max_temp_c,
        bed_temp_c,
        drying_temp_c,
        drying_time_hours,
        manufacturing_date,  # ISO 8601 date string
        td=0.0,  # transmission distance in mm, for HueForge/OrcaSlicer
    ):
        self.source_processor = source_processor
        self.unique_id = unique_id
        self.manufacturer = manufacturer
        self.type = type
        self.modifiers = list(modifiers) if modifiers else []
        self.colors = list(colors) if colors else []
        self.diameter_mm = diameter_mm
        self.weight_grams = weight_grams
        self.hotend_min_temp_c = hotend_min_temp_c
        self.hotend_max_temp_c = hotend_max_temp_c
        self.bed_temp_c = bed_temp_c
        self.drying_temp_c = drying_temp_c
        self.drying_time_hours = drying_time_hours
        self.manufacturing_date = manufacturing_date
        self.td = td

        # Upstream folds these two into the type name; a "PLA" with a "CF" modifier is
        # really "PLA-CF" and that is how the material lists spell it.
        if "CF" in self.modifiers:
            self.type = (self.type or "") + "-CF"
            self.modifiers.remove("CF")
        if "GF" in self.modifiers:
            self.type = (self.type or "") + "-GF"
            self.modifiers.remove("GF")

        # See the divergence note at the top: unknown types are flagged, never rejected.
        self.typeRecognized = self.type in VALID_BASE_MATERIALS

    @property
    def rgba(self):
        if not self.colors:
            return 0x00000000
        return to_rgba(self.colors[0])

    def to_dict(self):
        return {
            "sourceProcessor": self.source_processor,
            "uniqueId": self.unique_id,
            "manufacturer": self.manufacturer,
            "type": self.type,
            "typeRecognized": self.typeRecognized,
            "modifiers": self.modifiers,
            "colors": self.colors,
            "diameterMm": self.diameter_mm,
            "weightGrams": self.weight_grams,
            "hotendMinTempC": self.hotend_min_temp_c,
            "hotendMaxTempC": self.hotend_max_temp_c,
            "bedTempC": self.bed_temp_c,
            "dryingTempC": self.drying_temp_c,
            "dryingTimeHours": self.drying_time_hours,
            "manufacturingDate": self.manufacturing_date,
            "td": self.td,
        }

    @staticmethod
    def generate_unique_id(*args):
        # Identifies a filament *product* (vendor/type/color), not a physical spool: two
        # identical spools produce the same id. Never use it to match a spool - that is
        # what the tag UID is for.
        strings = "|".join([str(arg) for arg in args])
        return hashlib.sha256(strings.encode("utf-8")).hexdigest()
