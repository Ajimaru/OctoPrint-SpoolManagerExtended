# coding=utf-8

# OpenPrintTag support (https://github.com/OpenPrintTag/openprinttag-specification), issue #56.
#
# Status: mapping and encoders are complete, unit tested, and confirmed against the OctoScale
# firmware team's own implementation (src/openprinttag.h) - not just the spec documentation.
# This module is NOT on the write path though: OctoScale's firmware does the CBOR/NDEF
# encoding itself (see TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG - we send the same flat JSON
# payload as the other NFC-V formats and the firmware picks/encodes what fits). This module
# serves two purposes instead:
#  - the read-only preview endpoint (getOpenPrintTagPayload in SpoolManagerAPI.py), so a user
#    can see what an OpenPrintTag write would contain before burning a tag
#  - an independent reference implementation, so a tag actually written by OctoScale can be
#    diffed byte-for-byte against what this module would produce
#
# Encoding chain: spool -> named field dict -> three CBOR maps (meta/main/aux, each with
# integer keys) -> concatenated into one NDEF record (MIME "application/vnd.openprinttag").
#
# Region layout confirmed against OctoScale's src/openprinttag.h (both write and read path):
# one NDEF record, payload = [meta][main][aux], meta and aux are FIXED size (OPT_META_SIZE=24,
# OPT_AUX_SIZE=32), main fills the space between them. The meta region is itself a CBOR map
# with keys 0-3 (see META_KEY_* below) giving the offset/size of the main and aux regions,
# both counted from the start of the NDEF payload (not from the tag start, not from the end
# of meta).

import struct

OPENPRINTTAG_MIME_TYPE = "application/vnd.openprinttag"

# The reference tag (SLIX2) holds ~316 bytes; the spec allows up to 512 bytes per section.
MAX_SECTION_SIZE_BYTES = 512

SECTION_META = "meta"
SECTION_MAIN = "main"
SECTION_AUX = "aux"

# Fixed sizes for the meta/aux regions, confirmed against src/openprinttag.h. Main fills
# whatever room is left after these two.
OPT_META_SIZE = 24
OPT_AUX_SIZE = 32

# Meta is not a free-form field map like main/aux - it is always exactly these four
# offset/size integers, describing where main and aux sit within the NDEF payload.
META_KEY_MAIN_REGION_OFFSET = 0
META_KEY_MAIN_REGION_SIZE = 1
META_KEY_AUX_REGION_OFFSET = 2
META_KEY_AUX_REGION_SIZE = 3

# Integer keys transcribed from the OpenPrintTag specification's main_fields.yaml, confirmed
# against OctoScale firmware's src/openprinttag.h - these are the keys OctoScale's own writer
# uses today. Fields with no spec equivalent (see UNMAPPED_FIELD_NAMES below) are intentionally
# absent here rather than given an invented key.
FIELD_KEY_MAP = {
    SECTION_MAIN: {
        "material_class": 8,
        "material_type": 9,
        "material_name": 10,
        "brand_name": 11,
        "nominal_netto_full_weight": 16,
        "empty_container_weight": 18,
        "primary_color": 19,
        "density": 29,
        "filament_diameter": 30,
        "min_print_temperature": 34,
        "max_print_temperature": 35,
        "preheat_temperature": 36,
        "min_bed_temperature": 37,
        "max_bed_temperature": 38,
    },
    # Aux carries none of our fields today (see UNMAPPED_FIELD_NAMES) - present so
    # encodeSection(SECTION_AUX, {}) encodes an empty map instead of raising, keeping the
    # region layout ([meta][main][aux]) intact even though aux is currently always empty.
    SECTION_AUX: {},
}

# Fields our SpoolModel carries that have no OpenPrintTag key at all (confirmed with the
# OctoScale/spec team - not a gap to fill in later, the spec simply has no equivalent).
# colorName: spec only has primary_color as RGB, no free-text name.
# remainingWeight: spec has nominal/actual *full* weight, no "remaining" concept.
# enclosureTemperature: no spec field.
# code (our serial/barcode field): closest spec field (brand_specific_instance_id, key 5) is
#   manufacturer-assigned, not our database's serial - using it would misrepresent the value.
# batchNumber: no lot/batch field in the spec.
# purchasedOn: spec has manufactured_date/expiration_date, neither means "date we bought it".
UNMAPPED_FIELD_NAMES = (
    "colorName",
    "remainingWeight",
    "enclosureTemperature",
    "serialNumber",
    "batchNumber",
    "purchasedOn",
)

# material_type enum (key 9), abbreviation -> index, transcribed from the specification's
# material_type_enum.yaml (confirmed with the OctoScale/spec team, values 0-42). Matching is
# exact-abbreviation-only (see materialTypeIndex()) - the enum is normative, so "PLA Silk"
# must not become PLA.
MATERIAL_TYPE_BY_ABBREVIATION = {
    "PLA": 0,
    "PETG": 1,
    "TPU": 2,
    "ABS": 3,
    "ASA": 4,
    "PC": 5,
    "PCTG": 6,
    "PP": 7,
    "PA6": 8,
    "PA11": 9,
    "PA12": 10,
    "PA66": 11,
    "CPE": 12,
    "TPE": 13,
    "HIPS": 14,
    "PHA": 15,
    "PET": 16,
    "PEI": 17,
    "PBT": 18,
    "PVB": 19,
    "PVA": 20,
    "PEKK": 21,
    "PEEK": 22,
    "BVOH": 23,
    "TPC": 24,
    "PPS": 25,
    "PPSU": 26,
    "PVC": 27,
    "PEBA": 28,
    "PVDF": 29,
    "PPA": 30,
    "PCL": 31,
    "PES": 32,
    "PMMA": 33,
    "POM": 34,
    "PPE": 35,
    "PS": 36,
    "PSU": 37,
    "TPI": 38,
    "SBS": 39,
    "OBC": 40,
    "EVA": 41,
    "PA612": 42,
}

MATERIAL_CLASS_FFF = 0

MATERIAL_NAME_MAX_BYTES = 63
BRAND_NAME_MAX_BYTES = 31


class UnresolvedFieldKeyError(Exception):
    # Raised when a field has no confirmed OpenPrintTag integer key yet. With FIELD_KEY_MAP
    # now complete for everything spoolModelToFields() produces, this only fires if a field
    # is added to one without the other - a programming error, not an expected runtime state.
    pass


##~~ CBOR (RFC 8949) - only the subset the tag payload needs


class Float32(object):
    # Wraps a value that must be CBOR-encoded as IEEE 754 binary32 (CBOR major type 7,
    # additional info 26) instead of the default binary64 - the OpenPrintTag spec requires
    # float32 for weights/density/diameter (keys 16/18/29/30). Deliberately not a change to
    # encodeCBOR()'s default `float` handling: that stays float64 so the existing RFC 8949
    # test vectors keep meaning what they say, and so an ordinary Python float elsewhere in
    # this module (or a future field) doesn't silently lose precision.
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def _encodeHead(majorType, value):
    prefix = majorType << 5
    if value < 24:
        return struct.pack(">B", prefix | value)
    if value < 0x100:
        return struct.pack(">BB", prefix | 24, value)
    if value < 0x10000:
        return struct.pack(">BH", prefix | 25, value)
    if value < 0x100000000:
        return struct.pack(">BI", prefix | 26, value)
    return struct.pack(">BQ", prefix | 27, value)


def encodeCBOR(value):
    if value is None:
        return b"\xf6"
    if value is True:
        return b"\xf5"
    if value is False:
        return b"\xf4"
    if isinstance(value, Float32):
        return b"\xfa" + struct.pack(">f", value.value)
    if isinstance(value, int):
        if value >= 0:
            return _encodeHead(0, value)
        return _encodeHead(1, -value - 1)
    if isinstance(value, float):
        return b"\xfb" + struct.pack(">d", value)
    if isinstance(value, bytes):
        return _encodeHead(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _encodeHead(3, len(encoded)) + encoded
    if isinstance(value, (list, tuple)):
        result = _encodeHead(4, len(value))
        for item in value:
            result += encodeCBOR(item)
        return result
    if isinstance(value, dict):
        # canonical ordering: integer keys ascending, so the same spool always encodes
        # to the same bytes (makes the unit tests and a tag diff meaningful)
        items = sorted(
            value.items(), key=lambda item: (isinstance(item[0], str), item[0])
        )
        result = _encodeHead(5, len(items))
        for key, item in items:
            result += encodeCBOR(key)
            result += encodeCBOR(item)
        return result
    raise TypeError("Cannot CBOR encode value of type " + str(type(value)))


##~~ NDEF


def buildNDEFMessage(payload, mimeType=OPENPRINTTAG_MIME_TYPE):
    # Single MIME record, TNF 0x02, both MB and ME set because it is the only record.
    typeBytes = mimeType.encode("ascii")
    isShortRecord = len(payload) < 256

    header = (
        0xC2 if not isShortRecord else 0xD2
    )  # MB|ME|TNF=2, SR set for short records
    record = struct.pack(">BB", header, len(typeBytes))
    if isShortRecord:
        record += struct.pack(">B", len(payload))
    else:
        record += struct.pack(">I", len(payload))
    record += typeBytes
    record += payload
    return record


##~~ Mapping


def _toFloat32(value):
    if value is None:
        return None
    try:
        return Float32(float(value))
    except (TypeError, ValueError):
        return None


def _toInt(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _truncateUtf8(value, maxBytes):
    # Truncates on a UTF-8 codepoint boundary rather than a raw byte cut, which could split
    # a multi-byte character and produce invalid UTF-8 / mojibake at the tail.
    if value is None:
        return None
    encoded = value.encode("utf-8")
    if len(encoded) <= maxBytes:
        return value
    return encoded[:maxBytes].decode("utf-8", errors="ignore")


def isOverUtf8ByteLimit(value, maxBytes):
    if value is None:
        return False
    return len(value.encode("utf-8")) > maxBytes


def _primaryColorBytes(spoolModel):
    # 3-byte RGB per the spec (key 19) - alpha, if present in an 8-hex-digit input, is
    # discarded rather than guessed at. An unparsable/unset color becomes an absent key,
    # never a guessed #000000 - black must mean "the spool's color really is black".
    color = getattr(spoolModel, "color", None)
    if color is None:
        return None
    text = str(color).strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) not in (6, 8):
        return None
    try:
        rgbBytes = bytes(
            int(text[index : index + 2], 16) for index in (0, 2, 4)
        )
    except ValueError:
        return None
    return rgbBytes


def materialTypeIndex(material):
    # Exact-abbreviation match only, per the OctoScale/spec team's guidance - the enum is
    # normative, so fuzzy/prefix matching ("PLA Silk" -> PLA) would misrepresent the value.
    # No match means the key is simply omitted; material_name (key 10) still carries the
    # free text either way.
    if material is None:
        return None
    text = str(material).strip().upper()
    if not text:
        return None
    return MATERIAL_TYPE_BY_ABBREVIATION.get(text)


def spoolModelToFields(spoolModel):
    # Named-field view of a spool in OpenPrintTag spec terms. Values the spool does not carry
    # are dropped instead of written as null, so an unconfigured field never claims "0 grams".
    # Temperature range resolution (falling back to the single "target" temperature when no
    # explicit min/max is set) is shared with the other NFC-V formats via
    # TagFormats.resolveTemperatureRange() so preview and write payload can never disagree.
    from . import TagFormats  # local import: avoid a module-level cycle with TagFormats

    temperatureRange = TagFormats.resolveTemperatureRange(spoolModel)

    material = getattr(spoolModel, "material", None)
    materialName = _truncateUtf8(material, MATERIAL_NAME_MAX_BYTES)
    brandName = _truncateUtf8(getattr(spoolModel, "vendor", None), BRAND_NAME_MAX_BYTES)

    mainFields = {
        "material_class": MATERIAL_CLASS_FFF,
        "material_type": materialTypeIndex(material),
        "material_name": materialName,
        "brand_name": brandName,
        "nominal_netto_full_weight": _toFloat32(
            getattr(spoolModel, "totalWeight", None)
        ),
        "empty_container_weight": _toFloat32(
            getattr(spoolModel, "spoolWeight", None)
        ),
        "primary_color": _primaryColorBytes(spoolModel),
        "density": _toFloat32(getattr(spoolModel, "density", None)),
        "filament_diameter": _toFloat32(getattr(spoolModel, "diameter", None)),
        "min_print_temperature": _toInt(temperatureRange["minTemperature"]),
        "max_print_temperature": _toInt(temperatureRange["maxTemperature"]),
        "preheat_temperature": _toInt(getattr(spoolModel, "temperature", None)),
        "min_bed_temperature": _toInt(temperatureRange["minBedTemperature"]),
        "max_bed_temperature": _toInt(temperatureRange["maxBedTemperature"]),
    }

    fields = {
        SECTION_MAIN: dict(
            (key, value) for key, value in mainFields.items() if value is not None
        )
    }
    return fields


def getUnresolvedFieldNames(fields):
    # Field names present in the data but lacking a confirmed integer key - now only fires on
    # a programming error (a field added to spoolModelToFields() without a FIELD_KEY_MAP
    # entry), since the map is otherwise complete. Kept as a guard, not a status indicator.
    unresolved = []
    for sectionName, sectionFields in fields.items():
        keyMap = FIELD_KEY_MAP.get(sectionName, {})
        for fieldName in sectionFields.keys():
            if keyMap.get(fieldName) is None:
                unresolved.append(sectionName + "." + fieldName)
    return sorted(unresolved)


def getDroppedFieldNames(spoolModel):
    # The honest, user-facing list of values this spool has that will NOT survive a write to
    # an OpenPrintTag, because the specification has no field for them at all.
    dropped = []
    for fieldName in UNMAPPED_FIELD_NAMES:
        value = getattr(spoolModel, fieldName, None)
        if fieldName == "serialNumber":
            value = getattr(spoolModel, "code", None)
        if value is not None and str(value).strip():
            dropped.append(fieldName)
    return dropped


def getTruncatedFieldNames(spoolModel):
    # material_name/brand_name are silently shortened by spoolModelToFields() to fit the
    # spec's byte caps; surface which ones so a preview can warn before a write hard-fails
    # or silently loses text the firmware would have truncated differently.
    truncated = []
    if isOverUtf8ByteLimit(
        getattr(spoolModel, "material", None), MATERIAL_NAME_MAX_BYTES
    ):
        truncated.append("material_name")
    if isOverUtf8ByteLimit(getattr(spoolModel, "vendor", None), BRAND_NAME_MAX_BYTES):
        truncated.append("brand_name")
    return truncated


def isEncodingComplete(fields):
    return len(getUnresolvedFieldNames(fields)) == 0


def encodeSection(sectionName, sectionFields):
    keyMap = FIELD_KEY_MAP.get(sectionName)
    if keyMap is None:
        raise UnresolvedFieldKeyError(
            "Unknown OpenPrintTag section '" + str(sectionName) + "'"
        )

    cborMap = {}
    for fieldName, value in sectionFields.items():
        integerKey = keyMap.get(fieldName)
        if integerKey is None:
            raise UnresolvedFieldKeyError(
                "No confirmed OpenPrintTag key for '"
                + sectionName
                + "."
                + fieldName
                + "'. Transcribe it from the specification's data/ directory first."
            )
        cborMap[integerKey] = value

    encoded = encodeCBOR(cborMap)
    if len(encoded) > MAX_SECTION_SIZE_BYTES:
        raise ValueError(
            "OpenPrintTag section '"
            + sectionName
            + "' is "
            + str(len(encoded))
            + " bytes, the specification allows at most "
            + str(MAX_SECTION_SIZE_BYTES)
            + "."
        )
    return encoded


def _buildMetaRegion(mainRegionSize, auxRegionSize):
    # Meta is a fixed OPT_META_SIZE-byte region holding only the offset/size of main and aux,
    # both counted from the start of the NDEF payload - confirmed against OctoScale's actual
    # firmware code, not just the spec text.
    mainOffset = OPT_META_SIZE
    auxOffset = mainOffset + mainRegionSize
    metaMap = {
        META_KEY_MAIN_REGION_OFFSET: mainOffset,
        META_KEY_MAIN_REGION_SIZE: mainRegionSize,
        META_KEY_AUX_REGION_OFFSET: auxOffset,
        META_KEY_AUX_REGION_SIZE: auxRegionSize,
    }
    encoded = encodeCBOR(metaMap)
    if len(encoded) > OPT_META_SIZE:
        raise ValueError(
            "OpenPrintTag meta region encoded to "
            + str(len(encoded))
            + " bytes, expected to fit within "
            + str(OPT_META_SIZE)
            + "."
        )
    return encoded + b"\x00" * (OPT_META_SIZE - len(encoded))


def encodeFields(fields):
    # Full payload: [meta][main][aux], meta and aux at their fixed sizes, main is whatever it
    # encodes to. We only ever populate SECTION_MAIN today (see UNMAPPED_FIELD_NAMES for what
    # would have gone in aux if the spec defined equivalents) - aux is still emitted, empty,
    # so the region layout matches what real OpenPrintTag readers expect.
    mainEncoded = encodeSection(SECTION_MAIN, fields.get(SECTION_MAIN, {}))
    auxEncoded = encodeSection(SECTION_AUX, fields.get(SECTION_AUX, {}))
    auxEncoded = auxEncoded + b"\x00" * max(0, OPT_AUX_SIZE - len(auxEncoded))
    if len(auxEncoded) > OPT_AUX_SIZE:
        raise ValueError(
            "OpenPrintTag aux region encoded to "
            + str(len(auxEncoded))
            + " bytes, expected to fit within "
            + str(OPT_AUX_SIZE)
            + "."
        )
    metaEncoded = _buildMetaRegion(len(mainEncoded), OPT_AUX_SIZE)
    return metaEncoded + mainEncoded + auxEncoded


def buildTagPayload(spoolModel):
    # Complete NDEF message for a spool. Raises UnresolvedFieldKeyError only on a programming
    # error now (see getUnresolvedFieldNames) - callers that only need the named-field preview
    # should use spoolModelToFields() instead, which never raises.
    fields = spoolModelToFields(spoolModel)
    return buildNDEFMessage(encodeFields(fields))
