# coding=utf-8

# Which payload gets written onto an NFC tag.
#
# OctoScale's reader is a PN5180 (NOT a PN532 - an earlier version of this comment was
# wrong): it speaks both ISO 14443A (NTAG/Mifare Classic) and ISO 15693/NFC-V. The firmware
# decides the concrete on-tag byte layout for the detected tag type; this plugin only
# supplies the spool's field values and lets the firmware pick/report the format actually
# used (see /nfcwritespool). Historically only the database id was written (kept below as
# TAG_FORMAT_SPOOL_ID_NTAG, still the default/fallback for older firmware); newer firmware
# additionally supports an OctoScale-specific extended Mifare Classic layout, OpenSpool
# (NDEF/JSON) on NTAG213/215/216, and (added on the firmware side beyond the original
# design - ISO15693/NFC-V tags turned out to have ample room too) the same extended field
# set on NFC-V/ISO15693 tags.
#
# OpenPrintTag (https://github.com/OpenPrintTag/openprinttag-specification) is a separate,
# still-unsupported format: it puts the spool data itself on the tag in CBOR, but the
# integer field keys in openprinttag.py's FIELD_KEY_MAP are not yet transcribed from the
# spec, so encodeSection() refuses to encode. The format is registered here as unsupported
# so the UI can name it and the encoder can be exercised/tested ahead of that transcription.

TAG_FORMAT_SPOOL_ID_NTAG = "spoolIdNtag"
TAG_FORMAT_OCTOSCALE_EXTENDED = "octoscaleExtended"
TAG_FORMAT_OPENSPOOL = "openSpool"
TAG_FORMAT_NFCV_EXTENDED = "nfcvExtended"
TAG_FORMAT_NFCV_OPENSPOOL = "nfcvOpenSpool"
TAG_FORMAT_OPENPRINTTAG = "openPrintTag"


def _buildSpoolIdPayload(spoolModel):
    return {"databaseId": spoolModel.databaseId}


def _buildFullSpoolPayload(spoolModel):
    # Every field the firmware knows how to place on an extended Mifare Classic tag or in
    # an OpenSpool NDEF/JSON record. The firmware picks which of these fit (and which format
    # applies) based on the tag actually on the reader - this plugin does not decide that.
    return {
        "databaseId": spoolModel.databaseId,
        "material": spoolModel.material,
        "vendor": spoolModel.vendor,
        "color": spoolModel.color,
        "colorName": spoolModel.colorName,
        "diameter": spoolModel.diameter,
        "density": spoolModel.density,
        "totalWeight": spoolModel.totalWeight,
        "spoolWeight": spoolModel.spoolWeight,
        "usedWeight": spoolModel.usedWeight,
        "temperature": spoolModel.temperature,
        "bedTemperature": spoolModel.bedTemperature,
    }


TAG_FORMATS = {
    TAG_FORMAT_SPOOL_ID_NTAG: {
        "id": TAG_FORMAT_SPOOL_ID_NTAG,
        "label": "Spool ID",
        "supported": True,
        "buildPayload": _buildSpoolIdPayload,
        "description": "Writes only the database id; all spool data stays in SpoolManager.",
    },
    TAG_FORMAT_OCTOSCALE_EXTENDED: {
        "id": TAG_FORMAT_OCTOSCALE_EXTENDED,
        "label": "Extended (Mifare Classic)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes material, vendor, color, weights and temperatures onto a "
        "Mifare Classic 1K tag in an OctoScale-specific layout, in addition to the "
        "database id.",
    },
    TAG_FORMAT_OPENSPOOL: {
        "id": TAG_FORMAT_OPENSPOOL,
        "label": "OpenSpool (NTAG)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes an OpenSpool-format NDEF/JSON record onto an NTAG213/215/216 "
        "tag, readable by phones and other OpenSpool-aware devices.",
    },
    TAG_FORMAT_NFCV_EXTENDED: {
        "id": TAG_FORMAT_NFCV_EXTENDED,
        "label": "Extended (NFC-V)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes material, vendor, color, weights and temperatures onto an "
        "ISO15693/NFC-V tag in the same OctoScale-specific field layout as the Mifare "
        "Classic extended format, in addition to the database id.",
    },
    TAG_FORMAT_NFCV_OPENSPOOL: {
        "id": TAG_FORMAT_NFCV_OPENSPOOL,
        "label": "OpenSpool (NFC-V)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes an OpenSpool-format NDEF/JSON record onto an ISO15693/NFC-V "
        "tag (NFC Forum Type 5), readable by phone NFC apps. Fewer fields than the "
        "extended NFC-V format (no colorName/density/spoolWeight/usedWeight - the "
        "OpenSpool schema doesn't define them), and NOT readable by Snapmaker U1/paxx12 "
        "firmware (its hardware has no ISO15693 support at all - a printer-side "
        "limitation, not fixable by tag formatting).",
    },
    TAG_FORMAT_OPENPRINTTAG: {
        "id": TAG_FORMAT_OPENPRINTTAG,
        "label": "OpenPrintTag",
        # not a hardware limitation (the PN5180 can drive NFC-V) - blocked on
        # FIELD_KEY_MAP's unresolved integer keys in openprinttag.py, see module docstring
        "supported": False,
        "buildPayload": None,
        "description": "Stores the spool data on the tag itself using the OpenPrintTag "
        "specification. Not yet available - the field key mapping is incomplete.",
    },
}


def getTagFormat(formatId):
    return TAG_FORMATS.get(formatId)


def isSupported(formatId):
    tagFormat = getTagFormat(formatId)
    return tagFormat is not None and tagFormat["supported"]


# Maps a tag type reported by OctoScale's /nfcprobe (field "tagType") to the format this
# plugin expects the firmware to use when writing. The firmware makes the authoritative
# choice per-tag (this is only used to preselect/display the expected format before a
# write is attempted); confirmed against the live firmware source
# (main.cpp's /nfcprobe and /nfc5180 handlers) rather than assumed:
#   tagType "mifareClassic1k" -> writeFormat "octoscaleExtended"  (only option - MAD-based
#                                 NDEF on Mifare Classic was evaluated and rejected: it's a
#                                 proprietary NXP mapping, never readable on iPhone, only on
#                                 some Android NFC chipsets, and paxx12/Snapmaker U1 routes
#                                 SAK 0x08 straight to its own proprietary parser regardless)
#   tagType "ntag"            -> writeFormat "openSpool"       (NTAG213/215/216, no
#                                 sub-variant in tagType - that detail lives in
#                                 formatLabel/capacityBytes instead)
#   tagType "nfcv"            -> writeFormat "nfcvExtended" or "nfcvOpenSpool", depending on
#                                 SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT (a global setting, not
#                                 chosen per write - see the settings template). NFC-V/
#                                 ISO15693 is NFC Forum Type 5, a real standard phones can
#                                 read, unlike Mifare Classic's MAD-NDEF above - but it still
#                                 buys zero Snapmaker U1 compatibility (the U1 hardware has
#                                 no ISO15693 support at all, independent of tag formatting)
#   tagType "unknown"/anything else -> writeFormat "spoolIdNtag" (id-only fallback)
TAG_TYPE_TO_FORMAT = {
    "mifareClassic1k": TAG_FORMAT_OCTOSCALE_EXTENDED,
    "ntag": TAG_FORMAT_OPENSPOOL,
    "nfcv": TAG_FORMAT_NFCV_EXTENDED,
}

NFCV_FORMAT_SETTING_TO_TAG_FORMAT = {
    "extended": TAG_FORMAT_NFCV_EXTENDED,
    "openSpool": TAG_FORMAT_NFCV_OPENSPOOL,
}


def formatForTagType(tagType, nfcvFormatSetting=None):
    if tagType == "nfcv":
        return NFCV_FORMAT_SETTING_TO_TAG_FORMAT.get(
            nfcvFormatSetting, TAG_FORMAT_NFCV_EXTENDED
        )
    return TAG_TYPE_TO_FORMAT.get(tagType, TAG_FORMAT_SPOOL_ID_NTAG)
