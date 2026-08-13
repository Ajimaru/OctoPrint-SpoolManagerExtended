# coding=utf-8

# OpenPrintTag support (https://github.com/OpenPrintTag/openprinttag-specification), issue #56.
#
# Status: the mapping and the encoders below are complete and unit tested, but the tag cannot be
# written yet. NOT a hardware limitation anymore - OctoScale's reader is a PN5180, which already
# reads and writes ISO 15693 / NFC-V (reference chip for OpenPrintTag: NXP ICODE SLIX2). The real
# blocker is FIELD_KEY_MAP below: the integer keys are not yet transcribed from the specification,
# so encodeSection() refuses to encode. Until that's done the payload is exposed read-only through
# the API so the mapping can be verified against real spools ahead of enabling writes.
#
# Encoding chain: spool -> named field dict -> CBOR map with integer keys -> NDEF record
# (MIME type "application/vnd.openprinttag").
#
# IMPORTANT: the integer keys in FIELD_KEY_MAP below are NOT confirmed against the specification.
# The spec ships them machine-readable in its "data" directory; they must be transcribed from
# there before a tag is written, otherwise readers will misinterpret the payload. Everything that
# only needs the *named* fields (the API preview) works regardless.

import struct

OPENPRINTTAG_MIME_TYPE = "application/vnd.openprinttag"

# The reference tag (SLIX2) holds ~316 bytes; the spec allows up to 512 bytes per section.
MAX_SECTION_SIZE_BYTES = 512

SECTION_META = "meta"
SECTION_MAIN = "main"
SECTION_AUX = "aux"

# TODO: transcribe the real integer keys from the specification repository's data/ directory
# before enabling tag writing. None means "key unknown" - encodeSection() refuses to encode
# a field whose key is still unresolved rather than inventing a number.
FIELD_KEY_MAP = {
    SECTION_META: {"version": None, "tagId": None},
    SECTION_MAIN: {
        "materialName": None,
        "colorName": None,
        "colorHex": None,
        "diameter": None,
        "density": None,
        "vendorName": None,
        "netWeight": None,
        "spoolWeight": None,
    },
    SECTION_AUX: {
        "remainingWeight": None,
        "nozzleTemperature": None,
        "bedTemperature": None,
        "enclosureTemperature": None,
        "serialNumber": None,
        "batchNumber": None,
        "purchasedOn": None,
    },
}


class UnresolvedFieldKeyError(Exception):
    # Raised when a field has no confirmed OpenPrintTag integer key yet.
    pass


##~~ CBOR (RFC 8949) - only the subset the tag payload needs


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


def _colorHex(spoolModel):
    color = getattr(spoolModel, "color", None)
    if color is None or not str(color).strip():
        return None
    return str(color).strip()


def _isoDate(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _remainingWeight(spoolModel):
    remaining = getattr(spoolModel, "remainingWeight", None)
    if remaining is not None:
        return remaining
    totalWeight = getattr(spoolModel, "totalWeight", None)
    usedWeight = getattr(spoolModel, "usedWeight", None)
    if totalWeight is None:
        return None
    return totalWeight - (usedWeight if usedWeight is not None else 0)


def spoolModelToFields(spoolModel):
    # Named-field view of a spool in OpenPrintTag terms. Values that the spool does not carry
    # are dropped instead of written as null, so an unconfigured field never claims "0 grams".
    fields = {
        SECTION_META: {"version": 1, "tagId": spoolModel.databaseId},
        SECTION_MAIN: {
            "materialName": getattr(spoolModel, "material", None),
            "colorName": getattr(spoolModel, "colorName", None),
            "colorHex": _colorHex(spoolModel),
            "diameter": getattr(spoolModel, "diameter", None),
            "density": getattr(spoolModel, "density", None),
            "vendorName": getattr(spoolModel, "vendor", None),
            "netWeight": getattr(spoolModel, "totalWeight", None),
            "spoolWeight": getattr(spoolModel, "spoolWeight", None),
        },
        SECTION_AUX: {
            "remainingWeight": _remainingWeight(spoolModel),
            "nozzleTemperature": getattr(spoolModel, "temperature", None),
            "bedTemperature": getattr(spoolModel, "bedTemperature", None),
            "enclosureTemperature": getattr(spoolModel, "enclosureTemperature", None),
            "serialNumber": getattr(spoolModel, "code", None),
            "batchNumber": getattr(spoolModel, "batchNumber", None),
            "purchasedOn": _isoDate(getattr(spoolModel, "purchasedOn", None)),
        },
    }

    for sectionName in list(fields.keys()):
        fields[sectionName] = dict(
            (key, value)
            for key, value in fields[sectionName].items()
            if value is not None
        )
    return fields


def getUnresolvedFieldNames(fields):
    # Field names present in the data but still lacking a confirmed integer key.
    unresolved = []
    for sectionName, sectionFields in fields.items():
        keyMap = FIELD_KEY_MAP.get(sectionName, {})
        for fieldName in sectionFields.keys():
            if keyMap.get(fieldName) is None:
                unresolved.append(sectionName + "." + fieldName)
    return sorted(unresolved)


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


def encodeFields(fields):
    # Full payload: a CBOR map of sections, each section itself a CBOR map with integer keys.
    sections = {}
    for sectionName, sectionFields in fields.items():
        if not sectionFields:
            continue
        sections[sectionName] = encodeSection(sectionName, sectionFields)
    return encodeCBOR(sections)


def buildTagPayload(spoolModel):
    # Complete NDEF message for a spool. Raises UnresolvedFieldKeyError while the key map
    # is still incomplete - callers that only need the preview should use spoolModelToFields().
    fields = spoolModelToFields(spoolModel)
    return buildNDEFMessage(encodeFields(fields))
