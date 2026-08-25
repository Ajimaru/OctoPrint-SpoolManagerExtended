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
#
# TigerTag (TAG_FORMAT_NTAG_TIGERTAG, "ntagTigerTag") is a fourth NTAG option, alongside
# OpenSpool/Extended as a third value of the same SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT
# preference - it targets the same physical NTAG213/215/216 tag class, so a fourth
# independent settings key would just duplicate that choice. It writes TigerTag STANDARD
# only (unsigned, magic 0x5BF59264, 80 bytes across pages 0x04-0x17): CC-BY-4.0 open spec,
# explicit royalty-free right to implement, no secret key required. TigerTag+ (signed,
# magic 0xBC0FCB97, needs a private ECDSA-P256 key TigerTag does not hand out to third
# parties) is explicitly out of scope and this plugin never attempts to produce it.
#
# Unlike the other NTAG formats, the payload the firmware needs is not free text - the
# on-tag fields are numeric ids (material/brand/aspect/type/diameter/measure-unit) drawn
# from TigerTag's own curated tables. Per the design agreed with the OctoScale firmware
# team: the FIRMWARE does not know those tables (that would be a data copy that can drift
# out of sync with TigerTag's registry) - this plugin resolves the spool's text
# material/vendor/etc. against common/tagdata (TigerTagIdService, see
# FilamentTagConstants.tigerTagIdForLabel) and sends already-resolved integers
# (tigerTagMaterialId, tigerTagBrandId, ...) alongside the usual flat payload; the firmware
# only packs bytes. An id that cannot be resolved (TigerTag's brand/material tables are
# curated and will not cover every string a user has typed into SpoolManager) is omitted
# from the payload rather than guessed - same "None stays None" convention as the rest of
# this file.
#
# "supported" is True since the OctoScale firmware team confirmed a real write-then-read
# round trip on physical hardware (NTAG215, previously blank): all 6 ids plus
# color/totalWeight/temperatureMin-Max/bedTemperatureMin-Max/dryingTemperature/dryingTime
# written and read back byte-for-byte, magic 0x5BF59264 at offset 0 as specified,
# unsupportedFields empty, tag left in a clean "empty" occupancy state afterwards.
# pn5180WriteNtagTigerTag/pn5180ReadNtagTigerTag in pn5180nfc.h, dispatched from
# main.cpp's /nfcwritespool and /nfcprobe handlers; "tigerTag" is a valid
# preferredNtagFormat value (main.cpp:1698 no longer coerces it to "openSpool").
#
# Read-back reuses the plugin's existing generic vendor-tag matching: TigerTag tags carry
# no SpoolManager database id (same situation as OpenPrintTag), so an already-known spool
# is found via the tag's own UID -> rfidTagKey, same path as every other vendor format -
# no TigerTag-specific matching code was needed on this plugin's side.
#
# Known layout limitation, not a bug: totalWeight and totalLength share the tag's single
# 3-byte "measure" slot on the firmware side. Both are always present in this plugin's
# payload; the firmware keeps totalWeight and silently drops totalLength when both are
# set. See the comment on _buildTigerTagPayload below for why this needs no fix here.

TAG_FORMAT_SPOOL_ID_NTAG = "spoolIdNtag"
TAG_FORMAT_OCTOSCALE_EXTENDED = "octoscaleExtended"
TAG_FORMAT_OPENSPOOL = "openSpool"
TAG_FORMAT_NFCV_EXTENDED = "nfcvExtended"
TAG_FORMAT_NFCV_OPENSPOOL = "nfcvOpenSpool"
TAG_FORMAT_NFCV_OPENPRINTTAG = "nfcvOpenPrintTag"
TAG_FORMAT_NTAG_EXTENDED = "ntagExtended"
TAG_FORMAT_NTAG_TIGERTAG = "ntagTigerTag"


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


def _minuteOfDayOrNone(value):
    # Minutes since midnight (0-1439), written *alongside* the day field above rather than
    # replacing it. Together they describe the full timestamp; on their own each stays
    # meaningful, which is what makes this safe across firmware versions: a reader that
    # does not know this field behaves exactly as before (midnight), and a tag written by
    # older firmware simply has no such field to read.
    #
    # Minutes since the *epoch* would have been the obvious encoding and is wrong here: the
    # firmware carries these fields as uint16 on the wire, where ~29.8 million overflows and
    # is written as the 0xFFFF "not set" sentinel - every timestamp would have come back as
    # "no date at all". Minute-of-day maxes out at 1439, far below that.
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.hour * 60 + value.minute
    # A plain date has no time of day; the day field alone already says midnight.
    return None


def _dryingTimeMinutesOrNone(hours):
    # SpoolManager stores drying time in hours, every tag format that carries it expects
    # minutes. None stays None so an unset field is not written as "0 minutes".
    if hours is None:
        return None
    try:
        return int(hours) * 60
    except (TypeError, ValueError):
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
        # Time of day for the three date fields above, which carry the day only. Without
        # these, rewriting an unchanged spool always showed a spurious "23:14 -> 00:00"
        # difference. Firmware that does not know these names ignores them
        # (doc["name"] | default), and a tag written by such firmware has no such field to
        # read back - in both directions the result is the previous day-only behaviour.
        "firstUseMinuteOfDay": _minuteOfDayOrNone(spoolModel.firstUse),
        "lastUseMinuteOfDay": _minuteOfDayOrNone(spoolModel.lastUse),
        "purchasedOnMinuteOfDay": _minuteOfDayOrNone(spoolModel.purchasedOn),
        "cost": spoolModel.cost,
        # v12 fields. Sending them is safe on every firmware: /nfcwritespool reads only the
        # names it knows (doc["name"] | default) and ignores the rest - confirmed against
        # the firmware source rather than assumed, since this payload goes out on every
        # write, including for users who never enable tag reading.
        "dryingTemperature": spoolModel.dryingTemperature,
        # MINUTES, not hours: the OpenPrintTag spec stores drying time in minutes
        # (main_fields.yaml key 58, example 480 = 8 h) and the firmware writes what it is
        # given without converting. SpoolManager stores hours, so the conversion has to
        # happen here - otherwise 8 hours reach the tag as 8 minutes. Same conversion as
        # OpenPrintTag._dryingTimeMinutes(), which feeds the preview endpoint; both have to
        # agree or the preview would show something the write does not produce.
        "dryingTime": _dryingTimeMinutesOrNone(spoolModel.dryingTime),
        "td": spoolModel.td,
    }


def _buildTigerTagPayload(spoolModel):
    # Same flat payload as every other format (firmware ignores keys it doesn't know,
    # see _buildFullSpoolPayload's own comment on that point) plus TigerTag's own
    # numeric ids, resolved from the spool's text fields against TigerTag's curated
    # tables. Imported here rather than at module level to avoid a hard dependency from
    # TagFormats (payload shapes) onto FilamentTagConstants (id lookup) at import time -
    # mirrors how the rest of this file treats field resolution as the caller's problem.
    #
    # Firmware note (confirmed with the OctoScale team once pn5180WriteNtagTigerTag
    # existed): totalWeight and totalLength share the tag's single 3-byte "measure" slot -
    # both are always present in this payload (from _buildFullSpoolPayload below), and the
    # firmware keeps totalWeight and drops totalLength whenever both are set, rather than
    # picking whichever arrived last or erroring. Nothing to do here on our side: the
    # priority (weight over length) already matches what every other field on this tag
    # means (grams, via tigerTagMeasureUnitId="g" below) - just don't be surprised if a
    # spool tracked by length only shows an empty measure back after a TigerTag write.
    from octoprint_SpoolManager.common.FilamentTagConstants import tigerTagIdForLabel

    payload = _buildFullSpoolPayload(spoolModel)
    payload.update(
        {
            "tigerTagMaterialId": tigerTagIdForLabel("id_material", spoolModel.material),
            "tigerTagBrandId": tigerTagIdForLabel("id_brand", spoolModel.vendor),
            "tigerTagAspectId": tigerTagIdForLabel("id_aspect", spoolModel.finish),
            # Always "Filament" (id 142 in TigerTag's id_type table): SpoolManager only
            # ever manages filament spools, never resin/accessories/spare parts, so there
            # is no SpoolModel field this could meaningfully resolve against - fixed like
            # tigerTagMeasureUnitId below, not omitted. A prior version of this builder
            # left this key out entirely, which the firmware treated as making the whole
            # 6-id group invalid rather than just this one field unset - see the
            # incident this fix addresses.
            "tigerTagTypeId": tigerTagIdForLabel("id_type", "Filament"),
            "tigerTagDiameterId": tigerTagIdForLabel(
                "id_diameter",
                None if spoolModel.diameter is None else str(spoolModel.diameter),
            ),
            # Always grams: SpoolManager stores weight fields in grams throughout, so the
            # unit id is fixed rather than resolved from anything on the spool.
            "tigerTagMeasureUnitId": tigerTagIdForLabel("id_measure_unit", "g"),
        }
    )
    return payload


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
    TAG_FORMAT_NTAG_TIGERTAG: {
        "id": TAG_FORMAT_NTAG_TIGERTAG,
        "label": "TigerTag (NTAG)",
        # Verified with a real write-then-read round trip on physical hardware
        # (NTAG215) by the OctoScale firmware team - see the module comment above.
        "supported": True,
        "buildPayload": _buildTigerTagPayload,
        "description": "Writes TigerTag Standard (unsigned, open spec) onto an "
        "NTAG213/215/216 tag - material, brand, aspect, diameter and weight unit as "
        "TigerTag's own numeric ids, plus the common field set every format carries. "
        "Fields whose value cannot be matched to a TigerTag id are left off the tag "
        "rather than guessed. TigerTag+ (signed) is not supported.",
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
    "tigerTag": TAG_FORMAT_NTAG_TIGERTAG,
}


def formatForTagType(tagType, nfcvFormatSetting=None, ntagFormatSetting=None):
    # Centralized guard: a recognized-but-unsupported preference (currently only
    # "tigerTag" - see TAG_FORMATS[TAG_FORMAT_NTAG_TIGERTAG]["supported"]) must never be
    # surfaced as "this is what will be written", in preview or in the actual write - the
    # firmware would silently write something else instead (main.cpp:1698). Falling back
    # to the same default the setting would resolve to if unset keeps this function's
    # result always something the firmware can actually produce.
    if tagType == "nfcv":
        formatId = NFCV_FORMAT_SETTING_TO_TAG_FORMAT.get(
            nfcvFormatSetting, TAG_FORMAT_NFCV_EXTENDED
        )
        return formatId if isSupported(formatId) else TAG_FORMAT_NFCV_EXTENDED
    if tagType == "ntag":
        formatId = NTAG_FORMAT_SETTING_TO_TAG_FORMAT.get(
            ntagFormatSetting, TAG_FORMAT_OPENSPOOL
        )
        return formatId if isSupported(formatId) else TAG_FORMAT_OPENSPOOL
    return TAG_TYPE_TO_FORMAT.get(tagType, TAG_FORMAT_SPOOL_ID_NTAG)
