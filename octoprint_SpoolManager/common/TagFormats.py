# coding=utf-8

import datetime

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
# OpenPrintTag (https://github.com/OpenPrintTag/openprinttag-specification) is a vendor-neutral
# format, NFC-V only: it puts the spool data itself on the tag in CBOR (three regions -
# meta/main/aux - in one NDEF record, MIME "application/vnd.openprinttag"). Unlike the other
# NFC-V formats, the FIRMWARE does the CBOR encoding, not this plugin - we send the same flat
# JSON payload as nfcvExtended/nfcvOpenSpool and the firmware picks which fields fit. Our own
# encoder in openprinttag.py is not on this write path; it exists only to preview/verify the
# mapping (see getOpenPrintTagPayload in SpoolManagerAPI.py) and as an independent reference
# implementation to diff a real written tag against.
#
# Two behaviours that make OpenPrintTag different from the other NFC-V formats:
#  - the spool's SpoolManager database id is NOT stored on the tag (the spec has no field for
#    it) - a spool must be resolvable by the tag's own UID instead. Reading falls back to
#    GET /spool/byCode/<uid>, which resolves via the last-4-hex-chars rfidTagKey (see
#    U1RfidManager.deriveRfidTagKey()) - the same mechanism used for Snapmaker U1 tags.
#  - capacity overflow is a hard failure on write (no partial/field-dropped write like
#    OpenSpool) - a spool with a full field set needs a large NFC-V tag (SLIX2/ST25DV,
#    ~316 bytes+); it will not fit on a 112-byte SLI-X.
#
# NTAG213/215/216 mirror NFC-V's choice as of the firmware's "ntagExtended" layout: a global
# preference (SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT), sent as "preferredNtagFormat" alongside
# "preferredNfcvFormat" (two independent parameters - a user may want Extended on NFC-V but
# OpenSpool on NTAG, or vice versa). Extended is an own binary page layout (own NDEF-less
# framing, CRC-8-checked, commit-marker page written last so an interrupted write reads back
# as "no extended data" rather than corrupt), carrying the same v1/v2/v3 field set as the
# Mifare Classic/NFC-V extended formats. NTAG213 has no Extended option - confirmed against
# the firmware team: it rejects an Extended write on NTAG213 (or any unrecognized NTAG
# sub-type) outright rather than attempting a partial write, since the v3 field set alone
# needs more room than a 213 has.

TAG_FORMAT_SPOOL_ID_NTAG = "spoolIdNtag"
TAG_FORMAT_OCTOSCALE_EXTENDED = "octoscaleExtended"
TAG_FORMAT_OPENSPOOL = "openSpool"
TAG_FORMAT_NFCV_EXTENDED = "nfcvExtended"
TAG_FORMAT_NFCV_OPENSPOOL = "nfcvOpenSpool"
TAG_FORMAT_NFCV_OPENPRINTTAG = "nfcvOpenPrintTag"
TAG_FORMAT_NTAG_EXTENDED = "ntagExtended"


def _buildSpoolIdPayload(spoolModel):
    return {"databaseId": spoolModel.databaseId}


def resolveTemperatureRange(spoolModel):
    # A spool may only have the single "target" temperature set (temperature/bedTemperature)
    # without an explicit min/max range - in that case both ends of the range fall back to the
    # target value instead of being left empty. Shared by _buildFullSpoolPayload() (OctoScale's
    # own extended/OpenSpool JSON) and OpenPrintTag.spoolModelToFields() (issue #56) so preview
    # and write payload can never disagree on what "the" temperature range for a spool is.
    minTemperature = spoolModel.minTemperature
    maxTemperature = spoolModel.maxTemperature
    if minTemperature is None:
        minTemperature = spoolModel.temperature
    if maxTemperature is None:
        maxTemperature = spoolModel.temperature

    minBedTemperature = spoolModel.minBedTemperature
    maxBedTemperature = spoolModel.maxBedTemperature
    if minBedTemperature is None:
        minBedTemperature = spoolModel.bedTemperature
    if maxBedTemperature is None:
        maxBedTemperature = spoolModel.bedTemperature

    return {
        "minTemperature": minTemperature,
        "maxTemperature": maxTemperature,
        "minBedTemperature": minBedTemperature,
        "maxBedTemperature": maxBedTemperature,
    }


_EPOCH_DATE = datetime.date(1970, 1, 1)


def _epochDaysOrNone(value):
    # Integer days since 1970-01-01 UTC, not an ISO string - the firmware's v3 extended
    # layout parses these with ArduinoJson's `doc["firstUse"] | -1L` (ints only; a JSON
    # string there silently falls back to -1L / "unset" instead of erroring, which is why
    # this must match exactly, confirmed against src/main.cpp:1090 with the OctoScale team).
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return (value - _EPOCH_DATE).days
    return None


def _buildFullSpoolPayload(spoolModel):
    # Every field the firmware knows how to place on an extended Mifare Classic tag or in
    # an OpenSpool NDEF/JSON record. The firmware picks which of these fit (and which format
    # applies) based on the tag actually on the reader - this plugin does not decide that.
    #
    # v3 fields (remainingWeight..displayName below): added after a field-list request from
    # the OctoScale team, matching the exact camelCase keys their /nfcwritespool handler
    # parses (src/main.cpp:1085-1096) for the extended Mifare Classic v3 layout. None of
    # these have an enforced max length/range on our side (see that conversation) - the
    # firmware is expected to truncate/reject on overflow rather than assume a cap here.
    temperatureRange = resolveTemperatureRange(spoolModel)
    minTemperature = temperatureRange["minTemperature"]
    maxTemperature = temperatureRange["maxTemperature"]
    minBedTemperature = temperatureRange["minBedTemperature"]
    maxBedTemperature = temperatureRange["maxBedTemperature"]

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
        "minTemperature": minTemperature,
        "maxTemperature": maxTemperature,
        "bedTemperature": spoolModel.bedTemperature,
        "minBedTemperature": minBedTemperature,
        "maxBedTemperature": maxBedTemperature,
        "remainingWeight": spoolModel.remainingWeight,
        "totalLength": spoolModel.totalLength,
        "usedLength": spoolModel.usedLength,
        "code": spoolModel.code,
        "batchNumber": spoolModel.batchNumber,
        "purchasedFrom": spoolModel.purchasedFrom,
        "finish": spoolModel.finish,
        "displayName": spoolModel.displayName,
        "firstUse": _epochDaysOrNone(spoolModel.firstUse),
        "lastUse": _epochDaysOrNone(spoolModel.lastUse),
        "purchasedOn": _epochDaysOrNone(spoolModel.purchasedOn),
        "cost": spoolModel.cost,
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
    TAG_FORMAT_NTAG_EXTENDED: {
        "id": TAG_FORMAT_NTAG_EXTENDED,
        "label": "Extended (NTAG)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes every field OctoScale knows onto an NTAG215/216 tag in an "
        "OctoScale-specific binary layout, in addition to the database id. NTAG213 has too "
        "little room for the full field set - the firmware rejects an Extended write there "
        "outright rather than dropping fields, so this format is only offered for "
        "NTAG215/216.",
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
    TAG_FORMAT_NFCV_OPENPRINTTAG: {
        "id": TAG_FORMAT_NFCV_OPENPRINTTAG,
        "label": "OpenPrintTag (NFC-V)",
        "supported": True,
        "buildPayload": _buildFullSpoolPayload,
        "description": "Writes an OpenPrintTag-format CBOR/NDEF record onto an ISO15693/NFC-V "
        "tag, a vendor-neutral open standard readable by other OpenPrintTag-aware tools/"
        "printers, not just OctoScale. Unlike the other NFC-V formats: the spool's database "
        "id is NOT stored on the tag (matching happens via the tag's own UID, see "
        "rfidTagKey), and a spool that doesn't fit fails the write outright instead of "
        "dropping fields - needs a large NFC-V tag (SLIX2/ST25DV, not a 112-byte SLI-X).",
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
#   tagType "ntag"            -> writeFormat "openSpool" or "ntagExtended", depending on
#                                 SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT (a global setting, not
#                                 chosen per write, mirrors the NFC-V preference below). No
#                                 sub-variant in tagType itself - that detail lives in
#                                 formatLabel/capacityBytes instead, which is why Extended is
#                                 offered here regardless of NTAG213/215/216: the firmware is
#                                 the one that rejects it outright on a 213.
#   tagType "nfcv"            -> writeFormat "nfcvExtended", "nfcvOpenSpool" or
#                                 "nfcvOpenPrintTag", depending on
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
    "openPrintTag": TAG_FORMAT_NFCV_OPENPRINTTAG,
}

NTAG_FORMAT_SETTING_TO_TAG_FORMAT = {
    "openSpool": TAG_FORMAT_OPENSPOOL,
    "extended": TAG_FORMAT_NTAG_EXTENDED,
}


def formatForTagType(tagType, nfcvFormatSetting=None, ntagFormatSetting=None):
    if tagType == "nfcv":
        return NFCV_FORMAT_SETTING_TO_TAG_FORMAT.get(
            nfcvFormatSetting, TAG_FORMAT_NFCV_EXTENDED
        )
    if tagType == "ntag":
        return NTAG_FORMAT_SETTING_TO_TAG_FORMAT.get(
            ntagFormatSetting, TAG_FORMAT_OPENSPOOL
        )
    return TAG_TYPE_TO_FORMAT.get(tagType, TAG_FORMAT_SPOOL_ID_NTAG)
