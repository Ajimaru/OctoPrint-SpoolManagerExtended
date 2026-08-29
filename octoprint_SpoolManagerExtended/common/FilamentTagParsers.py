# coding=utf-8

# Vendor filament tag parsers and the dispatch that runs them.
#
# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0:
#   OpenSpoolTagParser   <- src/tag/openspool/processor.py  (itself adapted from
#                           https://github.com/paxx12/SnapmakerU1-Extended-Firmware)
#   SpoolEaseTagParser   <- src/tag/spoolease/processor.py
#   AnycubicTagParser    <- src/tag/anycubic/processor.py   (itself adapted from
#                           https://github.com/DnG-Crafts/ACE-RFID)
#   ElegooTagParser      <- src/tag/elegoo/processor.py
#   QidiTagParser        <- src/tag/qidi/processor.py       (itself adapted from
#                           https://github.com/TinkerBarn/BoxRFID)
#   readFilamentFromTag  <- src/runtime.py's process_mifare_* dispatch
#
# Derived from spool-link-apps (https://github.com/paxx12-snapmaker-u1/spool-link-apps),
# GPL-3.0, by paxx12-snapmaker-u1 / paxx12:
#   SnapmakerTagParser   <- android-app/app/src/main/java/dev/pages/paxx12/spoollink/
#                           formats/SnapmakerFormat.kt  (repository state 2026-07-30)
#   The byte offsets and the material/sub-type tables are that project's reverse
#   engineering result, not independent findings.
#
# Derived from TigerTag-SDK-Python (https://github.com/TigerTag-Project/TigerTag-SDK-Python),
# Apache-2.0, Copyright TigerTag Corp. 2025-2026:
#   TigerTagTagParser    <- tigertag/tag.py (offsets, magic values, field order)
#   the id tables under common/tagdata/  <- tigertag/database/*.json
#   The specification grants an explicit, irrevocable permission to implement it.
#
# Written against, but containing no code from, Bambu-Research-Group/RFID-Tag-Guide
# (BambuLabRfid.md), which carries no licence:
#   BambuTagParser       <- byte offsets and field meanings only, implemented independently
#
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md,
# 3rdPartySoftware/OpenRFID/LICENSE, 3rdPartySoftware/spool-link-apps/LICENSE and
# 3rdPartySoftware/TigerTag-SDK-Python/LICENSE.
#
# Divergences from upstream:
#  - parsers are plain classes with a parseTag() method instead of the ConfigurableEntity
#    hierarchy; there is no config-file machinery here.
#  - upstream raises ValueError when handed the wrong tag type (and the Elegoo parser even
#    names the wrong class in its message). Here a wrong type returns None like any other
#    rejection - the dispatch treats "not my format" uniformly, and an exception escaping
#    into a Flask handler would be a 500 for what is a routine outcome.
#  - the registry mirrors TagFormats.TAG_FORMATS (dict of dicts) so both tag-facing
#    registries in this plugin read the same way.

from __future__ import annotations

import json
import logging
import struct

try:
    from urllib import parse as urlparse
except ImportError:  # pragma: no cover - Python 2 never applies here
    urlparse = None

from . import FilamentTagBinary as Binary
from . import FilamentTagConstants as Constants
from . import FilamentTagKeys as Keys
from . import FilamentTagNdef as Ndef
from . import OpenPrintTag as OpenPrintTagModule
from .FilamentTagModel import GenericFilament, TagType

_logger = logging.getLogger(
    "octoprint.plugins.SpoolManager.common.FilamentTagParsers"
)


def _firstNumber(*values):
    """First value that is a usable number, else None.

    Tag fields arrive as JSON strings or numbers depending on the writer, and an absent
    field must stay distinguishable from a zero - hence None rather than a 0 default.
    """
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number < 0:  # -1 is the firmware's "not set" sentinel
            continue
        return number
    return None


def _parseColorHex(value, default=0xFFFFFF):
    """"RRGGBB" (or "#RRGGBB") -> the 24-bit int, ignoring any trailing alpha byte.

    OpenSpool's color_hex can be 8 hex digits ("RRGGBBAA") - the firmware itself writes
    whatever string it's given verbatim (no length check) but only ever reads the first
    three byte-pairs as RGB wherever it needs concrete bytes (confirmed against the
    firmware source, RED FALCON/octoscale-46: pn5180ParseColorHex takes exactly 3
    byte-pairs). Feeding the raw 8-digit string to int(..., 16) here used to produce the
    full 32-bit value instead, and a later `& 0xFFFFFF` mask then kept the LAST six hex
    digits rather than the first six - "FFFFFF00" (white, alpha 0) silently became
    "#FFFF00" (yellow). Slicing to the first 6 characters before parsing mirrors the
    firmware's own three-byte-pairs behaviour instead.
    """
    try:
        hexString = str(value).strip()
        if hexString.startswith("#"):
            hexString = hexString[1:]
        hexString = hexString[:6]
        return int(hexString, 16)
    except (ValueError, TypeError):
        return default


class OpenSpoolTagParser(object):
    """OpenSpool's NDEF/JSON record - the open format this plugin also writes.

    The NDEF/TLV parsing itself is carrier-independent (parseNdefRecords locates the
    capability container by scanning rather than assuming a fixed offset - Page 3 on
    NTAG, Block 0 on NFC-V - see FilamentTagNdef.py), so this same logic also backs
    OpenSpoolNfcvTagParser below for the NFC-V carrier. Only the accepted tag_type
    differs per subclass.
    """

    id = "openSpool"
    label = "OpenSpool"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != self.tagClass:
            return None

        errorCode, records = Ndef.parseNdefRecords(data)
        if errorCode != Ndef.NDEF_OK:
            return None

        for record in records:
            if record.mime_type == "application/json":
                filament = self._parsePayload(record.payload)
                if filament is not None:
                    return filament
        return None

    def _parsePayload(self, payload):
        if payload is None or not isinstance(payload, (bytes, bytearray)):
            return None

        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("protocol") != "openspool":
            return None

        brand = data.get("brand", "Generic")
        mainType = str(data.get("type", "PLA")).upper()
        subType = data.get("subtype", "")

        colorHex = _parseColorHex(data.get("color_hex", "FFFFFF"))
        try:
            alpha = max(0x00, min(0xFF, int(str(data.get("alpha", "FF")), 16)))
        except (ValueError, TypeError):
            alpha = 0xFF
        colorArgb = (alpha << 24) | colorHex

        try:
            diameterMm = float(data.get("diameter", 1.75))
        except (ValueError, TypeError):
            diameterMm = 1.75
        try:
            weightGrams = int(data.get("weight", 1000))
        except (ValueError, TypeError):
            weightGrams = 1000

        try:
            minTemp = int(data.get("min_temp", 0))
            maxTemp = int(data.get("max_temp", 0))
        except (ValueError, TypeError):
            return None
        if maxTemp < minTemp:
            # A range that runs backwards means the record is not what it claims to be.
            return None

        defaultBedTempC, dryingTempC, dryingTimeHours = (
            Constants.OPENSPOOL_TYPE_DEFAULTS.get(mainType, (0.0, 0.0, 0.0))
        )

        # The spec does define bed_min_temp/bed_max_temp, and OctoScale writes them - so
        # prefer what the tag actually says over the material-table guess. Upstream skipped
        # this because its own model carries only a single bed temperature; here a real
        # value must never be shadowed by a table lookup, or a written tag would read back
        # with a value it does not contain.
        bedTempC = _firstNumber(data.get("bed_max_temp"), data.get("bed_min_temp"))
        if bedTempC is None:
            bedTempC = defaultBedTempC

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "OpenSpool", brand, mainType, subType, colorArgb
            ),
            manufacturer=brand,
            type=mainType,
            modifiers=[subType] if subType else [],
            colors=[colorArgb],
            diameter_mm=diameterMm,
            weight_grams=weightGrams,
            hotend_min_temp_c=minTemp,
            hotend_max_temp_c=maxTemp,
            bed_temp_c=bedTempC,
            drying_temp_c=dryingTempC,
            drying_time_hours=dryingTimeHours,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


class OpenSpoolNfcvTagParser(OpenSpoolTagParser):
    """OpenSpool's NDEF/JSON record, written onto an NFC-V (ISO15693) tag instead of NTAG.

    Same on-tag format as OpenSpoolTagParser (see its docstring) - the NDEF/TLV layer this
    plugin's Ndef.parseNdefRecords() implements locates the capability container by
    scanning rather than assuming NTAG's fixed Page-3/offset-16 position, so it already
    handles NFC-V's CC-at-Block-0/NDEF-at-Block-1 layout unchanged. Confirmed against the
    firmware's NFC-V OpenSpool writer (pn5180WriteNfcvOpenSpool): same short-record MIME
    "application/json" header on both carriers, no per-carrier framing difference to
    account for here.
    """

    id = "nfcvOpenSpool"
    label = "OpenSpool (NFC-V)"
    tagClass = TagType.NFCV
    requiresKey = False


class OpenPrintTagParser(object):
    """OpenPrintTag's CBOR/NDEF record (NFC-V only) - see common/OpenPrintTag.py.

    Unlike the other NFC-V formats, this one carries no SpoolManager databaseId - matching
    an already-known spool happens via the tag's own UID (rfidTagKey), same as OpenSpool
    and every other vendor format without an id field.

    OpenPrintTag.py's decodePayload()/fieldsToSpoolValues() do the actual CBOR decoding
    and field-name mapping; this class only extracts the NDEF payload and adapts the
    result into a GenericFilament. Fields the spec has no equivalent for (colorName,
    remainingWeight, ...) simply never appear in fieldsToSpoolValues()'s output - see
    OpenPrintTag.UNMAPPED_FIELD_NAMES for the authoritative list.
    """

    id = "openPrintTag"
    label = "OpenPrintTag"
    tagClass = TagType.NFCV
    requiresKey = False

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.NFCV:
            return None

        errorCode, records = Ndef.parseNdefRecords(data)
        if errorCode != Ndef.NDEF_OK:
            return None

        for record in records:
            if record.mime_type == OpenPrintTagModule.OPENPRINTTAG_MIME_TYPE:
                filament = self._parsePayload(record.payload)
                if filament is not None:
                    return filament
        return None

    def _parsePayload(self, payload):
        if payload is None or not isinstance(payload, (bytes, bytearray)):
            return None

        try:
            fields = OpenPrintTagModule.decodePayload(bytes(payload))
        except (ValueError, IndexError, struct.error):
            return None
        if not fields:
            return None

        values = OpenPrintTagModule.fieldsToSpoolValues(fields)

        material = values.get("material")
        if material is None:
            return None
        vendor = values.get("vendor", "Generic")

        colorArgb = None
        color = values.get("color")
        if color is not None:
            colorArgb = (0xFF << 24) | _parseColorHex(color)

        minTemp = values.get("minTemperature")
        maxTemp = values.get("maxTemperature")
        if minTemp is None or maxTemp is None:
            preheat = values.get("temperature")
            if minTemp is None:
                minTemp = preheat
            if maxTemp is None:
                maxTemp = preheat
        if minTemp is None or maxTemp is None:
            return None
        if maxTemp < minTemp:
            return None

        bedTemp = values.get("minBedTemperature")
        if bedTemp is None:
            bedTemp = values.get("maxBedTemperature")
        bedMin = values.get("minBedTemperature")
        bedMax = values.get("maxBedTemperature")

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "OpenPrintTag", vendor, material, "", colorArgb or 0xFFFFFFFF
            ),
            manufacturer=vendor,
            type=str(material).upper(),
            modifiers=[],
            colors=[colorArgb] if colorArgb is not None else [],
            diameter_mm=values.get("diameter", 1.75),
            weight_grams=int(values.get("totalWeight", 1000)),
            hotend_min_temp_c=int(minTemp),
            hotend_max_temp_c=int(maxTemp),
            bed_temp_c=bedTemp if bedTemp is not None else 0.0,
            drying_temp_c=values.get("dryingTemperature", 0.0),
            drying_time_hours=values.get("dryingTime", 0.0),
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
            td=values.get("td", 0.0),
            bed_min_temp_c=bedMin,
            bed_max_temp_c=bedMax,
        )


class SpoolEaseTagParser(object):
    """SpoolEase encodes the spool into a tag.spoolease.io URL in an NDEF URI record."""

    id = "spoolEase"
    label = "SpoolEase"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
            return None

        errorCode, records = Ndef.parseNdefRecords(data)
        if errorCode != Ndef.NDEF_OK:
            return None

        for record in records:
            uri = record.uriText()
            if uri is None:
                continue
            filament = self._parseUri(uri)
            if filament is not None:
                return filament
        return None

    def _parseUri(self, url):
        if urlparse is None:
            return None
        try:
            parsed = urlparse.urlparse(url.strip())
            if parsed.netloc.lower() != "tag.spoolease.io":
                return None
            if not parsed.path.startswith("/S1"):
                return None

            query = urlparse.parse_qs(parsed.query, keep_blank_values=False)

            rawMaterial = (query.get("M", ["PLA"])[0] or "PLA").upper()
            material = Constants.SPOOLEASE_TYPE_ALIASES.get(rawMaterial, rawMaterial)
            subType = query.get("MS", [""])[0] or ""
            brand = query.get("B", ["Generic"])[0] or "Generic"
            colorField = query.get("CC", ["FFFFFFFF"])[0] or "FFFFFFFF"

            minTemp = self._requiredInt(query, "NN")
            maxTemp = self._requiredInt(query, "NX")
            weightGrams = self._optionalInt(query, "WL", 1000)
            if maxTemp < minTemp:
                return None

            colors = self._parseColors(colorField)

            bedTempC, dryingTempC, dryingTimeHours = (
                Constants.SPOOLEASE_TYPE_DEFAULTS.get(material, (0.0, 0.0, 0.0))
            )

            return GenericFilament(
                source_processor=self.id,
                unique_id=GenericFilament.generate_unique_id(
                    "SpoolEase", brand, material, subType, weightGrams, *colors
                ),
                manufacturer=brand,
                type=material,
                modifiers=[subType] if subType else [],
                colors=colors,
                diameter_mm=1.75,
                weight_grams=weightGrams,
                hotend_min_temp_c=minTemp,
                hotend_max_temp_c=maxTemp,
                bed_temp_c=bedTempC,
                drying_temp_c=dryingTempC,
                drying_time_hours=dryingTimeHours,
                manufacturing_date=Constants.NO_MANUFACTURING_DATE,
            )
        except (ValueError, TypeError):
            return None

    def _requiredInt(self, query, key):
        values = query.get(key)
        if not values or values[0] == "":
            raise ValueError("missing required field " + key)
        return int(values[0])

    def _optionalInt(self, query, key, default):
        values = query.get(key)
        if not values or values[0] == "":
            return default
        return int(values[0])

    def _parseColors(self, colorField):
        colors = []
        alpha = 0xFF
        for index, colorCode in enumerate(str(colorField).split(";")):
            if len(colors) >= 5:
                break
            colorCode = colorCode.strip()
            if not colorCode:
                continue
            rgb, parsedAlpha = self._parseRgbaHex(colorCode)
            colors.append((parsedAlpha << 24) | rgb)
            if index == 0:
                alpha = parsedAlpha
        if not colors:
            colors = [(alpha << 24) | 0xFFFFFF]
        return colors

    def _parseRgbaHex(self, value):
        hexString = str(value).strip()
        if hexString.startswith("#"):
            hexString = hexString[1:]
        if len(hexString) == 6:
            return int(hexString, 16), 0xFF
        if len(hexString) == 8:
            return int(hexString[:6], 16), int(hexString[6:], 16)
        raise ValueError("unsupported RGBA hex length: " + str(len(hexString)))


class AnycubicTagParser(object):
    """Anycubic's own binary page layout - fixed offsets, no NDEF involved.

    Ported from src/tag/anycubic/processor.py, itself adapted from
    https://github.com/DnG-Crafts/ACE-RFID.
    """

    id = "anycubic"
    label = "Anycubic"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    # The layout starts with this marker at 0x10; anything else is not an Anycubic tag.
    MAGIC_OFFSET = 0x10
    MAGIC = b"\x7B\x00\x65\x00"

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
            return None
        if data is None or len(data) < 0x7C:
            return None
        if Binary.extract_slice(data, self.MAGIC_OFFSET, 4) != self.MAGIC:
            return None

        sku = Binary.extract_string(data, 0x14, 0x10)
        brand = Binary.extract_string(data, 0x28, 0x10)
        filamentType = Binary.extract_string(data, 0x3C, 0x10)
        if filamentType is None or brand is None:
            return None

        # "PLA+ Silk" / "PETG-CF" both split into a base type plus modifiers.
        typeParts = filamentType.replace("-", " ").split()
        if typeParts and typeParts[0].endswith("+"):
            typeParts[0] = typeParts[0][:-1]
            typeParts.append("+")

        alpha = Binary.extract_byte(data, 0x50)
        blue = Binary.extract_byte(data, 0x51)
        green = Binary.extract_byte(data, 0x52)
        red = Binary.extract_byte(data, 0x53)
        if None in (alpha, blue, green, red):
            return None
        argb = (alpha << 24) | (red << 16) | (green << 8) | blue

        minTemp = Binary.extract_uint16_le(data, 0x60)
        maxTemp = Binary.extract_uint16_le(data, 0x62)
        bedMaxTemp = Binary.extract_uint16_le(data, 0x76)
        rawDiameter = Binary.extract_uint16_le(data, 0x78)
        lengthM = Binary.extract_uint16_le(data, 0x7A)
        if None in (minTemp, maxTemp, rawDiameter, lengthM):
            return None

        # The tag stores length, not weight - resolve it via the spool sizes Anycubic ships.
        weightGrams = Constants.ANYCUBIC_LENGTH_M_TO_WEIGHT_G.get(
            lengthM, Constants.ANYCUBIC_DEFAULT_WEIGHT_G
        )

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "Anycubic", sku, brand, filamentType, argb, lengthM
            ),
            manufacturer=brand or "Anycubic",
            type=typeParts[0] if typeParts else "PLA",
            modifiers=typeParts[1:],
            colors=[argb],
            diameter_mm=rawDiameter / 100.0,
            weight_grams=weightGrams,
            hotend_min_temp_c=minTemp,
            hotend_max_temp_c=maxTemp,
            bed_temp_c=bedMaxTemp if bedMaxTemp is not None else 0,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


class ElegooTagParser(object):
    """Elegoo's binary layout, offset by a fixed 0x40 header.

    Ported from src/tag/elegoo/processor.py.
    """

    id = "elegoo"
    label = "Elegoo"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    DATA_OFFSET = 0x40
    DATA_LENGTH = 0x29
    MAGIC_OFFSET = 0x01
    MAGIC = b"\xEE\xEE\xEE\xEE"

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
            return None

        filamentData = Binary.extract_slice(data, self.DATA_OFFSET, self.DATA_LENGTH)
        if filamentData is None:
            return None
        if Binary.extract_slice(filamentData, self.MAGIC_OFFSET, 4) != self.MAGIC:
            return None

        materialId = Binary.extract_uint16_be(filamentData, 0x0C)
        if materialId is None:
            return None
        material = Constants.elegooMaterial(materialId >> 8, materialId & 0xFF)
        if material is None:
            # A material pair we have no name for - better to reject than to invent one.
            return None
        typeName, modifiers = material

        red = Binary.extract_byte(filamentData, 0x10)
        green = Binary.extract_byte(filamentData, 0x11)
        blue = Binary.extract_byte(filamentData, 0x12)
        alpha = Binary.extract_byte(filamentData, 0x13)
        if None in (red, green, blue, alpha):
            return None
        argb = (alpha << 24) | (red << 16) | (green << 8) | blue

        minTemp = Binary.extract_uint16_be(filamentData, 0x14)
        maxTemp = Binary.extract_uint16_be(filamentData, 0x16)
        rawDiameter = Binary.extract_uint16_be(filamentData, 0x1C)
        weightGrams = Binary.extract_uint16_be(filamentData, 0x1E)
        if None in (minTemp, maxTemp, rawDiameter, weightGrams):
            return None

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "Elegoo", typeName, modifiers, argb, rawDiameter, weightGrams
            ),
            manufacturer="Elegoo",
            type=typeName,
            modifiers=modifiers,
            colors=[argb],
            diameter_mm=rawDiameter / 100.0,
            weight_grams=weightGrams,
            hotend_min_temp_c=minTemp,
            hotend_max_temp_c=maxTemp,
            # Not on the tag; upstream leaves it at 0 too rather than guessing.
            bed_temp_c=0,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


# Mirrors TagFormats.TAG_FORMATS: id -> descriptor.
#
# Two fields exist purely to keep a read fast, both measured on real hardware:
#
#  "sectors"  - the Mifare Classic sectors a parser actually reads, so the firmware can be
#               asked for those alone. A full 1K dump costs ~2150 ms on a hit and ~765 ms
#               on a miss; a single sector ~54 ms on a miss. None means "read everything",
#               which is also the right answer for the NTAG parsers - those get a keyless
#               page walk where the question does not arise.
#  "needsKeyB" - whether key B has to be sent at all. Sending it is NOT free: after a
#               failed key A auth the card drops out of the selected state, so the firmware
#               must re-select before every key B attempt. Measured: 765 ms for key A alone
#               across 16 sectors, 2547 ms with key B added - 3.3x, not 2x. Sending key B
#               "just in case" triples the cost of every rejection for nothing. Bambu, for
#               one, has key B all zeros and never needs it.
class QidiTagParser(object):
    """Qidi's Mifare Classic layout, read from sector 1.

    Ported from src/tag/qidi/processor.py, itself adapted from
    https://github.com/TinkerBarn/BoxRFID.

    Unlike every other parser here this one runs on tags protected by the *factory* key
    (FFFFFFFFFFFF), which is not a secret and which every blank Classic tag also carries.
    Authentication therefore proves nothing, and recognition has to come entirely from the
    content being plausible. That is why the checks below are stricter than the upstream
    parser's and why this parser is registered last: a genuinely blank tag is all zeroes
    and must be rejected, not read as "PLA, black, 0 g".
    """

    id = "qidi"
    label = "Qidi"
    tagClass = TagType.MIFARE_CLASSIC_1K
    requiresKey = False

    # Sector 1 starts at byte 64; block 4 holds the descriptor, block 5 the colour.
    SECTOR_1_OFFSET = 0x40
    MATERIAL_OFFSET = 0x40
    COLOR_OFFSET = 0x44
    TEMP_OFFSET = 0x48

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_CLASSIC_1K:
            return None
        # Sector 1 has to be present; a partial dump that stopped earlier cannot be judged.
        if data is None or len(data) < 0x50:
            return None

        materialId = Binary.extract_byte(data, self.MATERIAL_OFFSET)
        if materialId is None:
            return None
        typeName = Constants.QIDI_MATERIALS.get(materialId)
        if typeName is None:
            # An unknown code is the main line of defence against blank tags: 0x00 is not a
            # material, so an all-zero sector never gets this far.
            return None

        red = Binary.extract_byte(data, self.COLOR_OFFSET)
        green = Binary.extract_byte(data, self.COLOR_OFFSET + 1)
        blue = Binary.extract_byte(data, self.COLOR_OFFSET + 2)
        if None in (red, green, blue):
            return None
        argb = (0xFF << 24) | (red << 16) | (green << 8) | blue

        minTemp = Binary.extract_byte(data, self.TEMP_OFFSET)
        maxTemp = Binary.extract_byte(data, self.TEMP_OFFSET + 1)
        bedTemp = Binary.extract_byte(data, self.TEMP_OFFSET + 2)
        if None in (minTemp, maxTemp):
            return None

        # Plausibility, not decoration: these ranges are what separates a real Qidi tag
        # from arbitrary bytes that happen to start with a valid material code. A hotend
        # below 150 C or above 450 C, or a range running backwards, is not filament data.
        if minTemp < 150 or maxTemp > 450 or maxTemp < minTemp:
            return None
        if bedTemp is not None and bedTemp > 200:
            return None

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "Qidi", typeName, argb, minTemp, maxTemp
            ),
            manufacturer="Qidi",
            type=typeName,
            modifiers=[],
            colors=[argb],
            diameter_mm=1.75,
            weight_grams=Constants.QIDI_DEFAULT_WEIGHT_G,
            hotend_min_temp_c=minTemp,
            hotend_max_temp_c=maxTemp,
            bed_temp_c=bedTemp if bedTemp is not None else 0,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


class KeyedClassicTagParser(object):
    """Shared base for Mifare Classic parsers whose sectors are not on the factory key.

    Each subclass names the settings entry holding its key. Without a valid entry the
    parser disables itself and authenticationKeys() returns None, which the dispatch reads
    as "skip me" - the same self-disabling behaviour OpenRFID has, kept deliberately so the
    registry needs no filtering and the dispatch no special case.

    NO KEY MATERIAL IS SHIPPED. See FilamentTagKeys for the reasoning.
    """

    tagClass = TagType.MIFARE_CLASSIC_1K
    requiresKey = True
    keyName = None

    def __init__(self, keyStore=None):
        self._keyStore = keyStore
        self.enabled = bool(keyStore is not None and keyStore.has(self.keyName))
        if not self.enabled:
            _logger.info(
                "%s: no valid key configured, parser stays disabled", self.label
            )

    def authenticationKeys(self, scanResult):
        """Per-sector key A values (16 x 12 hex chars), or None when unavailable."""
        raise NotImplementedError


class TigerTagTagParser(object):
    """TigerTag's raw page layout (no NDEF).

    Ported from TigerTag-Project/TigerTag-SDK-Python (`tigertag/tag.py`), Apache-2.0,
    Copyright TigerTag Corp. 2025-2026. The specification carries an explicit, irrevocable
    permission to implement it; see THIRD_PARTY_NOTICES.md and
    3rdPartySoftware/TigerTag-SDK-Python/LICENSE.

    Three things differ from every other parser here and are easy to get wrong:
      - all multi-byte values are BIG-endian, where the other formats are little-endian,
      - offsets are relative to the start of user memory (page 4), so the page-0 dump the
        reader returns has to be advanced by 16 bytes first,
      - the weight is a bare number whose unit lives in a separate id field: reading it as
        grams is wrong by a factor of 1000 whenever the tag says kilograms.
    """

    id = "tigerTag"
    label = "TigerTag"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    # User memory starts at page 4; the reader hands us the tag from page 0.
    USER_MEMORY_OFFSET = 16

    # id_tigertag, u32 BE at offset 0. Four bytes of magic - a foreign tag matching one of
    # these by chance is not a practical concern.
    MAGIC_TIGERTAG = 0x5BF59264
    MAGIC_TIGERTAG_PLUS = 0xBC0FCB97
    MAGIC_TIGERTAG_INIT = 0x6C41A2E1

    # An initialised-but-unprogrammed tag carries no filament data worth offering.
    INIT_PRODUCT_ID = 0x00000000

    # The SDK accepts exactly these two lengths: 80 bytes without the ECDSA signature
    # (NTAG213), 144 with it (NTAG215/216).
    MIN_DATA_LEN = 80

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
            return None
        if data is None:
            return None

        # Advance past pages 0-3 (UID, lock bytes, capability container).
        if len(data) < self.USER_MEMORY_OFFSET + self.MIN_DATA_LEN:
            return None
        body = data[self.USER_MEMORY_OFFSET :]

        magic = Binary.extract_uint32_be(body, 0)
        if magic not in (
            self.MAGIC_TIGERTAG,
            self.MAGIC_TIGERTAG_PLUS,
            self.MAGIC_TIGERTAG_INIT,
        ):
            return None

        productId = Binary.extract_uint32_be(body, 4)
        if magic == self.MAGIC_TIGERTAG_INIT or productId == self.INIT_PRODUCT_ID:
            # A blank TigerTag: correctly recognized, but there is nothing to fill a spool
            # with. Rejecting keeps it out of the "create a spool from this" flow.
            return None

        materialId = Binary.extract_uint16_be(body, 8)
        typeId = Binary.extract_byte(body, 12)
        diameterId = Binary.extract_byte(body, 13)
        brandId = Binary.extract_uint16_be(body, 14)
        if None in (materialId, diameterId):
            return None

        red = Binary.extract_byte(body, 16)
        green = Binary.extract_byte(body, 17)
        blue = Binary.extract_byte(body, 18)
        alpha = Binary.extract_byte(body, 19)
        if None in (red, green, blue):
            return None
        argb = ((alpha if alpha is not None else 0xFF) << 24) | (
            (red << 16) | (green << 8) | blue
        )

        measure = Binary.extract_uint24_be(body, 20)
        unitId = Binary.extract_byte(body, 23)
        weightGrams = Constants.tigerTagWeightGrams(measure, unitId)

        nozzleMin = Binary.extract_uint16_be(body, 24)
        nozzleMax = Binary.extract_uint16_be(body, 26)
        dryTemp = Binary.extract_byte(body, 28)
        dryTime = Binary.extract_byte(body, 29)
        bedMin = Binary.extract_byte(body, 30)
        bedMax = Binary.extract_byte(body, 31)
        if None in (nozzleMin, nozzleMax):
            return None
        if nozzleMax < nozzleMin:
            return None

        # The tag's own bed temperature, never the material table's recommendation: a table
        # lookup shadowing a real value is exactly the silent error this project already hit
        # once with OpenSpool's bed temperature.
        bedTempC = bedMax if bedMax else bedMin
        # TigerTag carries bedMin/bedMax as two separate bytes - unlike most other formats
        # here, which only ever have one bed value. Passing both through (instead of only
        # the single bed_temp_c collapse above) is what lets a round trip (write, then
        # read back) preserve the original min/target/max instead of flattening all three
        # SpoolModel fields to the same number - see FilamentTagToSpool.genericFilamentToSpoolFields.

        typeName = Constants.tigerTagLabel("id_material", materialId)
        diameterMm = Constants.tigerTagDiameterMm(diameterId)
        brandName = Constants.tigerTagLabel("id_brand", brandId)

        modifiers = []
        aspectName = Constants.tigerTagLabel("id_aspect", Binary.extract_byte(body, 10))
        if aspectName:
            modifiers.append(aspectName)
        typeLabel = Constants.tigerTagLabel("id_type", typeId)
        if typeLabel:
            modifiers.append(typeLabel)

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "TigerTag", productId, materialId, argb, measure
            ),
            manufacturer=brandName or "TigerTag",
            type=typeName or ("Unknown(%s)" % materialId),
            modifiers=modifiers,
            colors=[argb],
            diameter_mm=diameterMm,
            weight_grams=weightGrams,
            hotend_min_temp_c=nozzleMin,
            hotend_max_temp_c=nozzleMax,
            bed_temp_c=bedTempC if bedTempC is not None else 0,
            bed_min_temp_c=bedMin if bedMin else None,
            bed_max_temp_c=bedMax if bedMax else None,
            drying_temp_c=dryTemp if dryTemp is not None else 0,
            drying_time_hours=dryTime if dryTime is not None else 0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


class BambuTagParser(object):
    """Bambu Lab's Mifare Classic layout.

    Written against the format description in Bambu-Research-Group/RFID-Tag-Guide
    (BambuLabRfid.md). That repository carries no licence, so nothing is copied from it -
    this is an independent implementation from the documented field offsets, which are
    factual interoperability information about someone else's data format.

    Needs a salt the user supplies; none ships with this plugin, and without one the parser
    disables itself and never claims a tag.

    Little-endian throughout (unlike TigerTag). Two details bite otherwise: the diameter is
    an 8-byte double, not a float, and the drying time is already in hours.
    """

    id = "bambu"
    label = "Bambu Lab"
    tagClass = TagType.MIFARE_CLASSIC_1K
    requiresKey = True
    keyName = Keys.KEY_BAMBU_SALT

    # Block n starts at n * 16 in the full 1K image (trailers included).
    TRAY_INFO_BLOCK = 1 * 16
    FILAMENT_TYPE_BLOCK = 2 * 16
    DETAILED_TYPE_BLOCK = 4 * 16
    COLOR_BLOCK = 5 * 16
    TEMPERATURE_BLOCK = 6 * 16

    def __init__(self, keyStore=None):
        self._keyStore = keyStore
        self.enabled = bool(keyStore is not None and keyStore.has(self.keyName))
        if not self.enabled:
            _logger.info(
                "%s: no valid key configured, parser stays disabled", self.label
            )

    def authenticationKeys(self, scanResult):
        """Per-sector key A values, or None when no salt is configured.

        Key B is not derived: on these tags it is all zeroes and carries nothing, so asking
        for it would only cost a re-selection per sector after every key A failure.
        """
        if not self.enabled:
            return None
        return Keys.deriveBambuKeys(scanResult.uid, self._keyStore.get(self.keyName))

    def parseTag(self, scanResult, data):
        if not self.enabled:
            return None
        if scanResult.tag_type != TagType.MIFARE_CLASSIC_1K:
            return None
        if data is None or len(data) < self.TEMPERATURE_BLOCK + 12:
            return None

        # Authenticating with the derived key is itself the proof that this is a Bambu tag -
        # no other tag would accept it. The material id check below is a plausibility guard
        # against a successful read of something unexpected, not the primary criterion.
        materialId = Binary.extract_string(data, self.TRAY_INFO_BLOCK + 8, 8)
        filamentType = Binary.extract_string(data, self.FILAMENT_TYPE_BLOCK, 16)
        detailedType = Binary.extract_string(data, self.DETAILED_TYPE_BLOCK, 16)
        if not filamentType and not detailedType:
            return None
        if materialId and not materialId.startswith("GF"):
            # Every material id seen so far starts with "GF"; anything else means the bytes
            # are not what this parser thinks they are.
            return None

        red = Binary.extract_byte(data, self.COLOR_BLOCK)
        green = Binary.extract_byte(data, self.COLOR_BLOCK + 1)
        blue = Binary.extract_byte(data, self.COLOR_BLOCK + 2)
        alpha = Binary.extract_byte(data, self.COLOR_BLOCK + 3)
        if None in (red, green, blue):
            return None
        argb = ((alpha if alpha is not None else 0xFF) << 24) | (
            (red << 16) | (green << 8) | blue
        )

        weightGrams = Binary.extract_uint16_le(data, self.COLOR_BLOCK + 4)
        # 8 bytes: a double, not a float. The 4-byte helper would return a number here too,
        # just not the right one.
        diameterMm = Binary.extract_double_le(data, self.COLOR_BLOCK + 8)

        dryTemp = Binary.extract_uint16_le(data, self.TEMPERATURE_BLOCK)
        # Already hours on the tag, like Snapmaker and TigerTag - no conversion.
        dryTimeHours = Binary.extract_uint16_le(data, self.TEMPERATURE_BLOCK + 2)
        bedTemp = Binary.extract_uint16_le(data, self.TEMPERATURE_BLOCK + 6)
        hotendMax = Binary.extract_uint16_le(data, self.TEMPERATURE_BLOCK + 8)
        hotendMin = Binary.extract_uint16_le(data, self.TEMPERATURE_BLOCK + 10)
        if None in (hotendMin, hotendMax):
            return None
        if hotendMin < 150 or hotendMax > 450 or hotendMax < hotendMin:
            return None

        # "PLA" plus "PLA Basic" - keep the base type and carry the variant separately,
        # rather than letting "Basic" become part of the material name.
        baseType = filamentType or detailedType
        modifiers = []
        if detailedType and detailedType != baseType:
            variant = detailedType
            if baseType and variant.startswith(baseType):
                variant = variant[len(baseType) :].strip()
            if variant:
                modifiers.append(variant)

        if diameterMm is None or not (0.5 <= diameterMm <= 5.0):
            diameterMm = 1.75

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "Bambu", materialId or "", baseType, detailedType or "", argb
            ),
            manufacturer="Bambu Lab",
            type=baseType,
            modifiers=modifiers,
            colors=[argb],
            diameter_mm=diameterMm,
            weight_grams=weightGrams if weightGrams else 1000,
            hotend_min_temp_c=hotendMin,
            hotend_max_temp_c=hotendMax,
            bed_temp_c=bedTemp if bedTemp is not None else 0,
            drying_temp_c=dryTemp if dryTemp is not None else 0,
            drying_time_hours=dryTimeHours if dryTimeHours is not None else 0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )


class SnapmakerTagParser(object):
    """Snapmaker's Mifare Classic layout.

    Ported from paxx12-snapmaker-u1/spool-link-apps (GPL-3.0), file
    android-app/app/src/main/java/dev/pages/paxx12/spoollink/formats/SnapmakerFormat.kt
    (repository state 2026-07-30). Combined into this AGPLv3 work; see
    THIRD_PARTY_NOTICES.md and 3rdPartySoftware/spool-link-apps/LICENSE.

    The sector keys are derived from the tag's own UID (see
    FilamentTagKeys.deriveSnapmakerKeys) - there is no shared secret, so this parser needs
    no configuration and is always available.

    Offsets below are absolute into the full 1K image with sector trailers left in place,
    which is what the reader returns.
    """

    id = "snapmaker"
    label = "Snapmaker"
    tagClass = TagType.MIFARE_CLASSIC_1K
    requiresKey = False

    VENDOR_OFFSET = 16
    MANUFACTURER_OFFSET = 32
    VERSION_OFFSET = 64
    MAIN_TYPE_OFFSET = 66
    SUB_TYPE_OFFSET = 68
    COLOR_NUMS_OFFSET = 72
    ALPHA_OFFSET = 73
    COLOR_BASE_OFFSET = 80
    DIAMETER_OFFSET = 128
    WEIGHT_OFFSET = 130
    DRY_TEMP_OFFSET = 144
    DRY_TIME_OFFSET = 146
    HOTEND_MAX_OFFSET = 148
    HOTEND_MIN_OFFSET = 150
    BED_TEMP_OFFSET = 154
    MFG_DATE_OFFSET = 160

    def authenticationKeys(self, scanResult):
        """Per-sector key A values for this tag, as 16 hex strings."""
        return Keys.deriveSnapmakerKeys(scanResult.uid, "a")

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_CLASSIC_1K:
            return None
        # Everything through the bed temperature has to be present.
        if data is None or len(data) < self.BED_TEMP_OFFSET + 2:
            return None

        mainTypeId = Binary.extract_uint16_le(data, self.MAIN_TYPE_OFFSET)
        if mainTypeId is None:
            return None
        typeName = Constants.SNAPMAKER_MAIN_TYPES.get(mainTypeId)
        if typeName is None:
            # Also the blank-tag guard: 0 is not a material id.
            return None

        subTypeId = Binary.extract_uint16_le(data, self.SUB_TYPE_OFFSET)
        subTypeName = Constants.SNAPMAKER_SUB_TYPES.get(subTypeId)

        # Snapmaker sells rebadged filament, so the tag names both: "Snapmaker" as the
        # vendor and the actual producer separately (verified on a real tag: Snapmaker /
        # Polymaker). The vendor is what belongs on the spool; the producer is kept as a
        # modifier so the information is not silently dropped.
        vendor = Binary.extract_string(data, self.VENDOR_OFFSET, 16) or "Snapmaker"
        producer = Binary.extract_string(data, self.MANUFACTURER_OFFSET, 16)

        colorNums = Binary.extract_byte(data, self.COLOR_NUMS_OFFSET)
        alphaByte = Binary.extract_byte(data, self.ALPHA_OFFSET)
        if colorNums is None or alphaByte is None:
            return None
        # The tag stores transparency, not opacity - upstream inverts it.
        alpha = 0xFF - alphaByte

        colors = []
        for index in range(max(1, min(colorNums or 1, 5))):
            offset = self.COLOR_BASE_OFFSET + index * 3
            red = Binary.extract_byte(data, offset)
            green = Binary.extract_byte(data, offset + 1)
            blue = Binary.extract_byte(data, offset + 2)
            if None in (red, green, blue):
                break
            colors.append((alpha << 24) | (red << 16) | (green << 8) | blue)
        if not colors:
            return None

        rawDiameter = Binary.extract_uint16_le(data, self.DIAMETER_OFFSET)
        weightGrams = Binary.extract_uint16_le(data, self.WEIGHT_OFFSET)
        hotendMax = Binary.extract_uint16_le(data, self.HOTEND_MAX_OFFSET)
        hotendMin = Binary.extract_uint16_le(data, self.HOTEND_MIN_OFFSET)
        bedTemp = Binary.extract_uint16_le(data, self.BED_TEMP_OFFSET)
        dryTemp = Binary.extract_uint16_le(data, self.DRY_TEMP_OFFSET)
        # Already hours on the tag, which is also what GenericFilament expects - no
        # conversion, unlike the minute-based fields elsewhere in this plugin.
        dryTimeHours = Binary.extract_uint16_le(data, self.DRY_TIME_OFFSET)

        if None in (hotendMin, hotendMax):
            return None
        if hotendMin < 150 or hotendMax > 450 or hotendMax < hotendMin:
            return None

        # Both names go into the vendor, because SpoolManager has one vendor field and
        # materialCharacteristic means the variant (Silk, Matte) - a producer name there
        # would read as if the filament were a "SnapSpeed Polymaker" type.
        vendorName = vendor
        if producer and producer != vendor:
            vendorName = "%s (%s)" % (vendor, producer)

        return GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "Snapmaker", typeName, subTypeName or "", colors[0], weightGrams
            ),
            manufacturer=vendorName,
            type=typeName,
            modifiers=[subTypeName] if subTypeName else [],
            colors=colors,
            diameter_mm=(rawDiameter / 100.0) if rawDiameter else 1.75,
            weight_grams=weightGrams if weightGrams else 1000,
            hotend_min_temp_c=hotendMin,
            hotend_max_temp_c=hotendMax,
            bed_temp_c=bedTemp if bedTemp is not None else 0,
            drying_temp_c=dryTemp if dryTemp is not None else 0,
            drying_time_hours=dryTimeHours if dryTimeHours is not None else 0,
            manufacturing_date=self._manufacturingDate(data),
        )

    def _manufacturingDate(self, data):
        """The tag's "YYYYMMDD" string as ISO 8601, or the sentinel when absent."""
        raw = Binary.extract_string(data, self.MFG_DATE_OFFSET, 8)
        if not raw or len(raw) != 8 or not raw.isdigit():
            return Constants.NO_MANUFACTURING_DATE
        return "%s-%s-%s" % (raw[0:4], raw[4:6], raw[6:8])


# ---------------------------------------------------------------------------------------
# OctoScale's own "extended" tag format - one Python-side reader per carrier (Mifare
# Classic, NTAG/Ultralight, NFC-V). The firmware builds the on-tag bytes (see
# TagFormats.py's module docstring); this is the read side that was missing entirely -
# the plugin could write these tags but never parse one back. Layout reverse-engineered
# together with the firmware's own author against real hardware dumps (Mifare Classic
# tag 37, NTAG215 UID 045330AC3A0289, an ICODE NFC-V tag) - not from the (nonexistent)
# on-tag documentation, since the firmware is this format's only other implementation.
#
# All three carriers share the same *field semantics* (scalings, sentinels, epoch-day and
# minute-of-day date encoding, [len][utf-8] strings, plain RGB colour) but each has its own
# frame, magic position and field table - they must never be computed from one another.
# ---------------------------------------------------------------------------------------

# Sentinel values the firmware uses for "this field was never written". Distinct per width
# because the wire format is fixed-width integers with no separate null bit.
_OCTOSCALE_SENTINEL_U8 = 0xFF
_OCTOSCALE_SENTINEL_U16 = 0xFFFF
_OCTOSCALE_SENTINEL_U24 = 0xFFFFFF
# Minute-of-day is 0..1439; anything at or above that (not just the 0xFFFF sentinel) means
# "not set" - a v3 Classic tag written before Block 17 existed has that block all-zero,
# which must read as "not set", not as midnight... except zero IS midnight and therefore
# valid. Only values >1439 are rejected.
_OCTOSCALE_MINUTE_OF_DAY_MAX = 1439


def _octoscaleU8OrNone(value):
    if value is None or value == _OCTOSCALE_SENTINEL_U8:
        return None
    return value


def _octoscaleU16OrNone(value):
    if value is None or value == _OCTOSCALE_SENTINEL_U16:
        return None
    return value


def _octoscaleU24OrNone(value):
    if value is None or value == _OCTOSCALE_SENTINEL_U24:
        return None
    return value


def _octoscaleMinuteOfDayOrNone(value):
    if value is None or value > _OCTOSCALE_MINUTE_OF_DAY_MAX:
        return None
    return value


def _octoscaleEpochDaysToIso(days):
    """Epoch-days (days since 1970-01-01) to an ISO 8601 date string, or the "not set"
    sentinel GenericFilament otherwise uses. Mirrors TagFormats._epochDaysOrNone() in
    reverse: that function goes date -> epoch-days for the write side, this goes back."""
    days = _octoscaleU16OrNone(days)
    if days is None:
        return Constants.NO_MANUFACTURING_DATE
    import datetime

    try:
        return (datetime.date(1970, 1, 1) + datetime.timedelta(days=days)).isoformat()
    except (OverflowError, ValueError):
        return Constants.NO_MANUFACTURING_DATE


def _octoscaleColorArgb(red, green, blue):
    """Plain R,G,B (no alpha) to the 0xAARRGGBB GenericFilament expects, or None.

    0,0,0 is not black on this format - it is the firmware's own "unparseable/mixed
    colour" marker (verified: pn5180ReadNtagExtended leaves the color field unset rather
    than emitting "#000000" for it). Importing it as black would silently overwrite a
    spool's real colour with one the tag never actually claimed.
    """
    if red is None or green is None or blue is None:
        return None
    if red == 0 and green == 0 and blue == 0:
        return None
    return (0xFF << 24) | (red << 16) | (green << 8) | blue


# Bit layout of the v4/v3/v2 multi-color flags byte, identical across all three carriers
# (Mifare block10[8], NFC-V block24[3], NTAG page19[3]) - RED FALCON/octoscale-46, verified
# on real hardware. 0x00 means "no color information", which is also what an older tag's
# zeroed reserve bytes already read as - no separate presence bit was needed.
_OCTOSCALE_COLOR_FLAG_TRANSPARENT = 0x01
_OCTOSCALE_COLOR_FLAG_COUNT_MASK = 0x06  # bits 1-2, count 0-3
_OCTOSCALE_COLOR_FLAG_COUNT_SHIFT = 1
_OCTOSCALE_COLOR_FLAG_RAINBOW = 0x08


def _octoscaleParseColorFlags(flagsByte):
    """Flags byte -> (isTransparent, colorCount, isRainbow), or the all-unset defaults.

    A None input (field not present on this read, e.g. a pre-v4/v3/v2 tag) is treated the
    same as 0x00 - both mean "no multi-color information", matching the firmware's own
    "zeroed reserve reads as absent" convention documented above.
    """
    flags = flagsByte or 0
    isTransparent = bool(flags & _OCTOSCALE_COLOR_FLAG_TRANSPARENT)
    colorCount = (flags & _OCTOSCALE_COLOR_FLAG_COUNT_MASK) >> _OCTOSCALE_COLOR_FLAG_COUNT_SHIFT
    isRainbow = bool(flags & _OCTOSCALE_COLOR_FLAG_RAINBOW)
    return isTransparent, colorCount, isRainbow


def _octoscaleComposeColorString(primaryArgb, extraColors, isTransparent, isRainbow):
    """Rebuilds SpoolManager's own "color" grammar (see SPOOLMANAGER_UTILS.composeSpoolColor
    in utils.js) from what the tag's flags byte and RGB slots carry, so a round trip through
    an Extended tag reproduces the same string the spool was written with - not just the
    primary color.

    Returns None when there is nothing to compose (no primary color and not rainbow), so the
    caller's "absent key, not a guessed value" convention still applies.
    """
    if isRainbow:
        return "rainbow"

    colors = []
    if primaryArgb is not None:
        colors.append("#%06X" % (primaryArgb & 0xFFFFFF))
    for argb in extraColors:
        if argb is not None:
            colors.append("#%06X" % (argb & 0xFFFFFF))

    if not colors:
        return "transparent" if isTransparent else None

    composed = ";".join(colors)
    return ("transparent:" + composed) if isTransparent else composed


def _octoscaleReadStrings(data, offset, fieldNames, blockSkip=None):
    """Read a flat sequence of [len_uint8][utf-8] fields starting at `offset`.

    Every declared slot is read regardless of length - a len=0 slot is a placeholder, not
    a terminator, and the fields the format actually carries can sit behind one (verified
    against the NTAG215 dump: purchasedFrom and displayName follow two empty slots).
    Stopping at the first len=0 silently drops every field after it.

    `blockSkip(absoluteOffset) -> newOffset` optionally jumps over bytes that are not part
    of the string buffer (Mifare Classic's sector trailers, interleaved every 4 blocks).
    Returns {fieldName: str} for slots with a non-empty value; empty/absent slots are
    simply not in the returned dict, same "absent means not on the tag" convention as
    every sentinel above.
    """
    result = {}
    pos = offset
    for name in fieldNames:
        if blockSkip is not None:
            pos = blockSkip(pos)
        length = Binary.extract_byte(data, pos)
        if length is None:
            break
        pos += 1
        if length == 0:
            continue
        raw = Binary.extract_slice(data, pos, length)
        if raw is None:
            break
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        if text:
            result[name] = text
        pos += length
    return result


class OctoScaleExtendedTagParser(object):
    """OctoScale's own extended format on Mifare Classic 1K.

    Layout confirmed against a real tag (databaseId 37, written and read back by the
    firmware author, CRC and every field independently recomputed here). Versions 1-3 are
    purely additive - no offset moves and no field changes meaning between them - except
    for the CRC coverage, which grows with each version and is the one place a v2 reader
    would break on a v3 tag if it used the wrong length.

    Like Qidi, this format sits on the Mifare factory key (FFFFFFFFFFFF), which every
    blank Classic tag also accepts - authentication proves nothing here. Recognition has
    to come entirely from the magic bytes and the CRC, not from having authenticated.
    """

    id = "octoscaleExtended"
    label = "OctoScale Extended"
    tagClass = TagType.MIFARE_CLASSIC_1K
    requiresKey = False

    # Block n starts at n * 16 in the full 1K image (sector trailers included, never
    # written by this format and simply skipped over when walking string buffers).
    BLOCK_4_LEGACY_ID = 4 * 16
    BLOCK_8_HEADER = 8 * 16
    BLOCK_9_PHYSICAL = 9 * 16
    BLOCK_10_BED_RANGE = 10 * 16
    BLOCK_16_V3_NUMERIC = 16 * 16
    BLOCK_17_V3_MINUTES = 17 * 16
    BLOCK_36_V3_COMMIT = 36 * 16

    MAGIC = b"OS"
    V3_SUB_MAGIC = b"O3"

    # String buffer 1 (block 8's flags bit0 gates it): vendor/material/colorName, 48 bytes
    # across blocks 12-14.
    BUFFER_1_OFFSET = 12 * 16
    BUFFER_1_FIELDS = ("vendor", "material", "colorName")

    # String buffer 2 (block 36's flags bit0 gates it): the five bookkeeping fields, 192
    # bytes spread over blocks 20,21,22,24,25,26,28,29,30,32,33,34 - sector trailers
    # 23,27,31,35 sit in between and are skipped, never part of the buffer.
    BUFFER_2_TRAILER_BLOCKS = frozenset([23, 27, 31, 35])
    BUFFER_2_START_BLOCK = 20
    BUFFER_2_FIELDS = ("code", "batchNumber", "purchasedFrom", "finish", "displayName")

    def _buffer2Skip(self, absoluteOffset):
        block = absoluteOffset // 16
        while block in self.BUFFER_2_TRAILER_BLOCKS:
            block += 1
            absoluteOffset = block * 16
        return absoluteOffset

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_CLASSIC_1K:
            return None
        if data is None or len(data) < self.BLOCK_10_BED_RANGE + 16:
            return None

        if Binary.extract_slice(data, self.BLOCK_8_HEADER, 2) != self.MAGIC:
            return None

        version = Binary.extract_byte(data, self.BLOCK_8_HEADER + 2)
        if version is None or version < 1:
            return None
        # Unknown/future versions must be rejected, not optimistically parsed as v4 - a v5
        # tag would otherwise be misread as v4 with garbage in the fields v5 repurposed.
        if version > 4:
            return None
        isV2 = version >= 2
        isV3 = version >= 3
        isV4 = version >= 4

        flags1 = Binary.extract_byte(data, self.BLOCK_8_HEADER + 3) or 0
        buffer1Present = bool(flags1 & 0x01)

        if isV3 and len(data) < self.BLOCK_16_V3_NUMERIC + 16:
            return None

        # Coverage is non-contiguous ranges, not one span: block8[0..15] + block9[0..14]
        # (the CRC byte itself at block9[15] is excluded), extended per version:
        #   v1: stops after block9                                          (31 B)
        #   v2: + block10[0..1] (bed min/max)                                (33 B)
        #   v3: + block16[0..15] (v3 numeric fields)                         (49 B)
        #   v4: block10 grows to [0..8] (bed range + 2 extra colors + flags),
        #       block16 shifts to offset 40 in the CRC buffer instead of 33   (56 B)
        # v4's block10 covers 9 bytes (bed min/max at [0..1], unchanged; colors 2/3 and
        # flags at [2..8], new) where v2/v3 only covered the first 2 - RED FALCON
        # confirmed the CRC buffer position of block16 moves accordingly on v4 tags.
        block8Part = Binary.extract_slice(data, self.BLOCK_8_HEADER, 16)
        block9Part = Binary.extract_slice(data, self.BLOCK_9_PHYSICAL, 15)
        if block8Part is None or block9Part is None:
            return None
        crcCovered = block8Part + block9Part
        if isV4:
            block10Part = Binary.extract_slice(data, self.BLOCK_10_BED_RANGE, 9)
            if block10Part is None:
                return None
            crcCovered += block10Part
        elif isV2:
            block10Part = Binary.extract_slice(data, self.BLOCK_10_BED_RANGE, 2)
            if block10Part is None:
                return None
            crcCovered += block10Part
        if isV3:
            block16Part = Binary.extract_slice(data, self.BLOCK_16_V3_NUMERIC, 16)
            if block16Part is None:
                return None
            crcCovered += block16Part

        storedCrc = Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 15)
        if storedCrc is None or Binary.crc8(crcCovered) != storedCrc:
            # A CRC mismatch means the tag is not a valid Extended write - possibly a
            # partial write, possibly unrelated bytes that happened to start with 'O','S'.
            # Treating it as "not our format" mirrors what the firmware itself does
            # (pn5180ReadMifareExtended).
            return None

        databaseId = Binary.extract_uint32_le(data, self.BLOCK_8_HEADER + 4)
        totalWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_8_HEADER + 8)
        )
        spoolWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_8_HEADER + 10)
        )
        usedWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_8_HEADER + 12)
        )
        density = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_8_HEADER + 14)
        )

        diameter = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_9_PHYSICAL + 0)
        )
        diameterTolerance = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_9_PHYSICAL + 2)
        )
        hotendTemp = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 4))
        bedTemp = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 5))
        enclosureTemp = _octoscaleU8OrNone(
            Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 6)
        )
        offsetTemp = Binary.extract_int8(data, self.BLOCK_9_PHYSICAL + 7)
        offsetBedTemp = Binary.extract_int8(data, self.BLOCK_9_PHYSICAL + 8)
        offsetEnclosureTemp = Binary.extract_int8(data, self.BLOCK_9_PHYSICAL + 9)

        red = Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 10)
        green = Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 11)
        blue = Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 12)
        argb = _octoscaleColorArgb(red, green, blue)

        hotendMin = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 13))
        hotendMax = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_9_PHYSICAL + 14))

        bedMin = None
        bedMax = None
        if isV2 and len(data) >= self.BLOCK_10_BED_RANGE + 2:
            bedMin = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_10_BED_RANGE + 0))
            bedMax = _octoscaleU8OrNone(Binary.extract_byte(data, self.BLOCK_10_BED_RANGE + 1))

        # v4: colors 2/3 (block10[2..4], [5..7]) and the shared flags byte (block10[8]).
        # Color 1 stays at block9[10..12], unmoved since v1 - see the class docstring.
        extraColors = []
        isTransparent = False
        isRainbow = False
        if isV4 and len(data) >= self.BLOCK_10_BED_RANGE + 9:
            color2Rgb = Binary.extract_slice(data, self.BLOCK_10_BED_RANGE + 2, 3)
            color3Rgb = Binary.extract_slice(data, self.BLOCK_10_BED_RANGE + 5, 3)
            colorFlags = Binary.extract_byte(data, self.BLOCK_10_BED_RANGE + 8)
            isTransparent, colorCount, isRainbow = _octoscaleParseColorFlags(colorFlags)
            if colorCount >= 2 and color2Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color2Rgb[0], color2Rgb[1], color2Rgb[2])
                )
            if colorCount >= 3 and color3Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color3Rgb[0], color3Rgb[1], color3Rgb[2])
                )

        remainingWeight = None
        totalLength = None
        usedLength = None
        cost = None
        firstUse = None
        lastUse = None
        purchasedOn = None
        firstUseMinute = None
        lastUseMinute = None
        purchasedOnMinute = None
        if isV3:
            remainingWeight = _octoscaleU16OrNone(
                Binary.extract_uint16_le(data, self.BLOCK_16_V3_NUMERIC + 0)
            )
            totalLength = _octoscaleU24OrNone(
                Binary.extract_uint24_le(data, self.BLOCK_16_V3_NUMERIC + 2)
            )
            usedLength = _octoscaleU24OrNone(
                Binary.extract_uint24_le(data, self.BLOCK_16_V3_NUMERIC + 5)
            )
            firstUse = _octoscaleU16OrNone(
                Binary.extract_uint16_le(data, self.BLOCK_16_V3_NUMERIC + 8)
            )
            lastUse = _octoscaleU16OrNone(
                Binary.extract_uint16_le(data, self.BLOCK_16_V3_NUMERIC + 10)
            )
            purchasedOn = _octoscaleU16OrNone(
                Binary.extract_uint16_le(data, self.BLOCK_16_V3_NUMERIC + 12)
            )
            rawCost = Binary.extract_uint16_le(data, self.BLOCK_16_V3_NUMERIC + 14)
            rawCost = _octoscaleU16OrNone(rawCost)
            cost = (rawCost / 100.0) if rawCost is not None else None

            if len(data) >= self.BLOCK_17_V3_MINUTES + 6:
                firstUseMinute = _octoscaleMinuteOfDayOrNone(
                    Binary.extract_uint16_le(data, self.BLOCK_17_V3_MINUTES + 0)
                )
                lastUseMinute = _octoscaleMinuteOfDayOrNone(
                    Binary.extract_uint16_le(data, self.BLOCK_17_V3_MINUTES + 2)
                )
                purchasedOnMinute = _octoscaleMinuteOfDayOrNone(
                    Binary.extract_uint16_le(data, self.BLOCK_17_V3_MINUTES + 4)
                )

        strings1 = {}
        if buffer1Present and len(data) >= self.BUFFER_1_OFFSET + 48:
            strings1 = _octoscaleReadStrings(
                data, self.BUFFER_1_OFFSET, self.BUFFER_1_FIELDS
            )

        strings2 = {}
        # Block 36's own flags bit0 is the only integrity statement about buffer 2 - it is
        # not CRC-covered, unlike everything in blocks 8-16. A missing marker means "do not
        # trust whatever bytes are sitting there", even if their length bytes look
        # plausible - a partial write can leave exactly that behind.
        if isV3 and len(data) >= self.BLOCK_36_V3_COMMIT + 3:
            v3Marker = Binary.extract_slice(data, self.BLOCK_36_V3_COMMIT, 2)
            flags36 = Binary.extract_byte(data, self.BLOCK_36_V3_COMMIT + 2) or 0
            buffer2Present = v3Marker == self.V3_SUB_MAGIC and bool(flags36 & 0x01)
            if buffer2Present:
                strings2 = _octoscaleReadStrings(
                    data,
                    self.BUFFER_2_START_BLOCK * 16,
                    self.BUFFER_2_FIELDS,
                    blockSkip=self._buffer2Skip,
                )

        manufacturingDate = _octoscaleEpochDaysToIso(firstUse)

        filament = GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "OctoScaleExtended", databaseId, strings1.get("material"), argb
            ),
            manufacturer=strings1.get("vendor"),
            type=strings1.get("material"),
            modifiers=[],
            colors=([argb] if argb is not None else []) + [c for c in extraColors if c is not None],
            diameter_mm=(diameter / 1000.0) if diameter is not None else None,
            weight_grams=totalWeight,
            hotend_min_temp_c=hotendMin if hotendMin is not None else hotendTemp,
            hotend_max_temp_c=hotendMax if hotendMax is not None else hotendTemp,
            bed_temp_c=bedTemp if bedTemp is not None else 0,
            bed_min_temp_c=bedMin,
            bed_max_temp_c=bedMax,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=manufacturingDate,
        )

        # Fields no vendor tag carries but this one does - kept off GenericFilament's
        # standard fields (which genericFilamentToSpoolFields() maps conservatively for
        # every vendor format) and surfaced separately so the API layer can round-trip
        # them without touching that shared, deliberately conservative mapping.
        #
        # "color" here deliberately overrides the generic multi-color join
        # genericFilamentToSpoolFields() already produces from filament.colors (plain
        # "#hex;#hex;#hex", no grammar prefix) - it is applied second in
        # SpoolManagerAPI.py's _buildReadTagResponse, and only this composed form can
        # express the "transparent:"/"rainbow" prefixes SpoolManager's own color field
        # grammar defines (SPOOLMANAGER_UTILS.composeSpoolColor in utils.js). Absent when
        # a v1-v3 tag has no flags byte to read (see _octoscaleComposeColorString) - the
        # generic join then stays untouched, same as before v4 existed.
        composedColor = _octoscaleComposeColorString(
            argb, extraColors, isTransparent, isRainbow
        )

        filament.octoscaleExtendedFields = _buildOctoscaleExtendedFields(
            databaseId=databaseId,
            spoolWeight=spoolWeight,
            usedWeight=usedWeight,
            density=(density / 1000.0) if density is not None else None,
            diameterTolerance=(
                diameterTolerance / 1000.0 if diameterTolerance is not None else None
            ),
            colorName=strings1.get("colorName"),
            color=composedColor,
            remainingWeight=remainingWeight,
            totalLength=totalLength,
            usedLength=usedLength,
            cost=cost,
            firstUseDays=firstUse,
            lastUseDays=lastUse,
            purchasedOnDays=purchasedOn,
            firstUseMinuteOfDay=firstUseMinute,
            lastUseMinuteOfDay=lastUseMinute,
            purchasedOnMinuteOfDay=purchasedOnMinute,
            code=strings2.get("code"),
            batchNumber=strings2.get("batchNumber"),
            purchasedFrom=strings2.get("purchasedFrom"),
            finish=strings2.get("finish"),
            displayName=strings2.get("displayName"),
            enclosureTemperature=enclosureTemp,
            offsetTemperature=offsetTemp,
            offsetBedTemperature=offsetBedTemp,
            offsetEnclosureTemperature=offsetEnclosureTemp,
        )
        return filament


class OctoScaleExtendedNtagTagParser(object):
    """OctoScale's own extended format on NTAG/Ultralight.

    A distinct layout from the Mifare Classic version, not merely a re-offset of it: all
    eight string fields sit in one buffer instead of two, the version stays at 1 (no v2/v3
    split - remainingWeight/cost/lengths/dates are present from the start), and the CRC
    covers a fixed range regardless of version. Verified byte-for-byte against a real
    NTAG215 dump (UID 045330AC3A0289, spool 37) including its leftover openSpool NDEF JSON
    past the commit marker - proof that a write here does not erase the tag first.

    Registered ahead of every NDEF parser (see FILAMENT_TAG_PARSERS below): unlike Mifare
    Classic, nothing here is sector-authenticated, so whichever parser runs first wins -
    and this format's own leftover-NDEF fixture demonstrates the openSpool parser would
    otherwise happily misclaim it.
    """

    id = "ntagExtended"
    label = "OctoScale Extended (NTAG)"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    PAGE_4_HEADER = 4 * 4
    PAGE_5_DB_ID = 5 * 4
    PAGE_6_WEIGHTS = 6 * 4
    PAGE_7_WEIGHTS2 = 7 * 4
    PAGE_8_PHYSICAL = 8 * 4
    PAGE_9_PHYSICAL2 = 9 * 4
    PAGE_10_TEMPS = 10 * 4
    PAGE_11_COLOR = 11 * 4
    PAGE_12_TEMPS2 = 12 * 4
    PAGE_13_CRC = 13 * 4
    PAGE_14_LENGTHS = 14 * 4
    PAGE_15_LENGTHS2 = 15 * 4
    PAGE_16_DATES = 16 * 4
    PAGE_17_DATES2 = 17 * 4
    PAGE_18_MINUTES = 18 * 4
    # v1 strings start at page 19. v2 inserts colors 2/3 + flags at pages 19-20 (8 bytes),
    # pushing strings to page 21 - RED FALCON/octoscale-46, verified on real hardware. The
    # marker scan below still finds the commit marker regardless (it searches, never
    # computes), but the string BUFFER start must match the version or the scan begins
    # mid-color-data on a v2 tag.
    STRINGS_START_PAGE_V1 = 19
    STRINGS_START_PAGE_V2 = 21
    PAGE_19_COLOR2 = 19 * 4
    PAGE_20_COLOR3 = 20 * 4

    MAGIC = b"OX"
    CRC_COVERAGE_LENGTH = 36  # fixed, pages 4..12 - unchanged by v2, unlike Classic

    STRING_FIELDS = (
        "vendor", "material", "colorName", "code",
        "batchNumber", "purchasedFrom", "finish", "displayName",
    )

    # Firmware scans, rather than computes, the marker position - and so must this parser:
    # relying on a formula would be one string-length edge case away from missing it (or
    # worse, reading garbage past the tag's actual data as if it were the marker).
    MARKER = b"NX"
    MAX_MARKER_SCAN_PAGES = 222  # NTAG216's full user area, generous upper bound

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
            return None
        if data is None or len(data) < self.PAGE_13_CRC + 4:
            return None

        if Binary.extract_slice(data, self.PAGE_4_HEADER, 2) != self.MAGIC:
            return None

        version = Binary.extract_byte(data, self.PAGE_4_HEADER + 2)
        if version is None or version < 1:
            return None
        if version > 2:
            # An unknown future version must be rejected, not parsed against today's
            # field table.
            return None
        isV2 = version >= 2
        # Page 4 byte 3 is reserved and always 0 on NTAG - unlike Classic's block8[3],
        # which is a real "buffer 1 present" flag. Reading it the same way here would be
        # wrong; it carries no meaning on this carrier and is intentionally ignored.

        crcCovered = Binary.extract_slice(
            data, self.PAGE_4_HEADER, self.CRC_COVERAGE_LENGTH
        )
        if crcCovered is None:
            return None
        storedCrc = Binary.extract_byte(data, self.PAGE_13_CRC)
        if storedCrc is None or Binary.crc8(crcCovered) != storedCrc:
            return None

        databaseId = Binary.extract_uint32_le(data, self.PAGE_5_DB_ID)
        totalWeight = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.PAGE_6_WEIGHTS))
        spoolWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_6_WEIGHTS + 2)
        )
        usedWeight = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.PAGE_7_WEIGHTS2))
        remainingWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_7_WEIGHTS2 + 2)
        )
        density = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.PAGE_8_PHYSICAL))
        diameter = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_8_PHYSICAL + 2)
        )
        diameterTolerance = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_9_PHYSICAL2)
        )
        hotendTemp = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_9_PHYSICAL2 + 2))
        bedTemp = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_9_PHYSICAL2 + 3))
        enclosureTemp = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_10_TEMPS))
        offsetTemp = Binary.extract_int8(data, self.PAGE_10_TEMPS + 1)
        offsetBedTemp = Binary.extract_int8(data, self.PAGE_10_TEMPS + 2)
        offsetEnclosureTemp = Binary.extract_int8(data, self.PAGE_10_TEMPS + 3)

        red = Binary.extract_byte(data, self.PAGE_11_COLOR)
        green = Binary.extract_byte(data, self.PAGE_11_COLOR + 1)
        blue = Binary.extract_byte(data, self.PAGE_11_COLOR + 2)
        argb = _octoscaleColorArgb(red, green, blue)

        hotendMax = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_11_COLOR + 3))
        hotendMin = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_12_TEMPS2))
        bedMin = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_12_TEMPS2 + 1))
        bedMax = _octoscaleU8OrNone(Binary.extract_byte(data, self.PAGE_12_TEMPS2 + 2))

        # v2: colors 2/3 (page19[0..2], page20[0..2]) and the shared flags byte
        # (page19[3]). Color 1 stays at page11[0..2], unmoved since v1.
        extraColors = []
        isTransparent = False
        isRainbow = False
        stringsStartPage = self.STRINGS_START_PAGE_V1
        if isV2 and len(data) >= self.PAGE_20_COLOR3 + 4:
            stringsStartPage = self.STRINGS_START_PAGE_V2
            color2Rgb = Binary.extract_slice(data, self.PAGE_19_COLOR2, 3)
            colorFlags = Binary.extract_byte(data, self.PAGE_19_COLOR2 + 3)
            color3Rgb = Binary.extract_slice(data, self.PAGE_20_COLOR3, 3)
            isTransparent, colorCount, isRainbow = _octoscaleParseColorFlags(colorFlags)
            if colorCount >= 2 and color2Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color2Rgb[0], color2Rgb[1], color2Rgb[2])
                )
            if colorCount >= 3 and color3Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color3Rgb[0], color3Rgb[1], color3Rgb[2])
                )

        totalLength = _octoscaleU24OrNone(
            Binary.extract_uint24_le(data, self.PAGE_14_LENGTHS)
        )
        # usedLength is the one field that straddles a page boundary: byte 3 of page 14 is
        # its low byte, bytes 0-1 of page 15 are the middle/high bytes.
        usedLengthByte0 = Binary.extract_byte(data, self.PAGE_14_LENGTHS + 3)
        usedLengthBytes12 = Binary.extract_uint16_le(data, self.PAGE_15_LENGTHS2)
        usedLength = None
        if usedLengthByte0 is not None and usedLengthBytes12 is not None:
            usedLength = _octoscaleU24OrNone(
                usedLengthByte0 | (usedLengthBytes12 << 8)
            )

        rawCost = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_15_LENGTHS2 + 2)
        )
        cost = (rawCost / 100.0) if rawCost is not None else None

        firstUse = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.PAGE_16_DATES))
        firstUseMinute = _octoscaleMinuteOfDayOrNone(
            Binary.extract_uint16_le(data, self.PAGE_16_DATES + 2)
        )
        lastUse = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.PAGE_17_DATES2))
        purchasedOn = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.PAGE_17_DATES2 + 2)
        )
        lastUseMinute = _octoscaleMinuteOfDayOrNone(
            Binary.extract_uint16_le(data, self.PAGE_18_MINUTES)
        )
        purchasedOnMinute = _octoscaleMinuteOfDayOrNone(
            Binary.extract_uint16_le(data, self.PAGE_18_MINUTES + 2)
        )

        # The firmware scans for the commit marker rather than computing its page, so a
        # reader has to as well - a formula would be one string-length edge case away from
        # missing it. This also bounds the string read: without it, a tag whose strings are
        # shorter than the fixed offsets below assume would keep reading into whatever
        # bytes follow (verified: a real dump has leftover openSpool NDEF JSON exactly
        # there). Only accepted when found within the actual data length and before the
        # scan's own generous upper bound.
        markerPage = None
        maxScanPage = min(
            self.MAX_MARKER_SCAN_PAGES, (len(data) // 4)
        )
        for page in range(stringsStartPage, maxScanPage):
            if Binary.extract_slice(data, page * 4, 2) == self.MARKER:
                markerPage = page
                break
        if markerPage is None:
            return None

        strings = _octoscaleReadStrings(
            data, stringsStartPage * 4, self.STRING_FIELDS
        )

        manufacturingDate = _octoscaleEpochDaysToIso(firstUse)

        filament = GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "OctoScaleExtendedNtag", databaseId, strings.get("material"), argb
            ),
            manufacturer=strings.get("vendor"),
            type=strings.get("material"),
            modifiers=[],
            colors=([argb] if argb is not None else []) + [c for c in extraColors if c is not None],
            diameter_mm=(diameter / 1000.0) if diameter is not None else None,
            weight_grams=totalWeight,
            hotend_min_temp_c=hotendMin if hotendMin is not None else hotendTemp,
            hotend_max_temp_c=hotendMax if hotendMax is not None else hotendTemp,
            bed_temp_c=bedTemp if bedTemp is not None else 0,
            bed_min_temp_c=bedMin,
            bed_max_temp_c=bedMax,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=manufacturingDate,
        )
        # See OctoScaleExtendedTagParser's parseTag() for why "color" is overridden here
        # rather than left to the generic multi-color join.
        composedColor = _octoscaleComposeColorString(
            argb, extraColors, isTransparent, isRainbow
        )
        filament.octoscaleExtendedFields = _buildOctoscaleExtendedFields(
            databaseId=databaseId,
            spoolWeight=spoolWeight,
            usedWeight=usedWeight,
            density=(density / 1000.0) if density is not None else None,
            diameterTolerance=(
                diameterTolerance / 1000.0 if diameterTolerance is not None else None
            ),
            colorName=strings.get("colorName"),
            color=composedColor,
            remainingWeight=remainingWeight,
            totalLength=totalLength,
            usedLength=usedLength,
            cost=cost,
            firstUseDays=firstUse,
            lastUseDays=lastUse,
            purchasedOnDays=purchasedOn,
            firstUseMinuteOfDay=firstUseMinute,
            lastUseMinuteOfDay=lastUseMinute,
            purchasedOnMinuteOfDay=purchasedOnMinute,
            code=strings.get("code"),
            batchNumber=strings.get("batchNumber"),
            purchasedFrom=strings.get("purchasedFrom"),
            finish=strings.get("finish"),
            displayName=strings.get("displayName"),
            enclosureTemperature=enclosureTemp,
            offsetTemperature=offsetTemp,
            offsetBedTemperature=offsetBedTemp,
            offsetEnclosureTemperature=offsetEnclosureTemp,
        )
        return filament


class OctoScaleExtendedNfcvTagParser(object):
    """OctoScale's own extended format on NFC-V (ISO15693).

    The smallest of the three carriers, not just a differently-arranged one: only three
    string fields exist (vendor/material/colorName), and remainingWeight, cost, code, the
    two length fields and every date field are simply absent from this layout - they are
    never on an NFC-V extended tag, not merely unset. Verified against a real ICODE tag
    written with spool 37's data and dumped back (databaseId, weights and all three
    strings matched the write exactly).

    Unlike those, a primary RGB color DOES exist on this carrier (physBuf[10..12],
    straddling the block9/block10 boundary at absolute offset 38-40) - it was simply never
    read here before a fix confirmed against the firmware source (RED FALCON/
    octoscale-46), which affected every version, not just the v3 multi-color extension.

    Same magic bytes as Mifare Classic ('O','S'), but at a different absolute position
    (block 3, not block 8) - the position identifies the carrier, not the magic.

    Capacity is unreliable here: /nfcprobe's capacityBytes for NFC-V is a compile-time
    format-budget constant (not a tag attribute, unlike NTAG's), so the read length this
    parser gets handed already reflects the reader's actual block walk - it must not be
    second-guessed against capacityBytes.
    """

    id = "nfcvExtended"
    label = "OctoScale Extended (NFC-V)"
    tagClass = TagType.NFCV
    requiresKey = False

    BLOCK_3_HEADER = 3 * 4
    BLOCK_4_DB_ID = 4 * 4
    BLOCK_5_WEIGHTS = 5 * 4
    BLOCK_6_WEIGHTS2 = 6 * 4
    BLOCK_7_PHYSICAL = 7 * 4
    BLOCK_8_PHYSICAL2 = 8 * 4
    BLOCK_9_PHYSICAL3 = 9 * 4
    BLOCK_10_PHYSICAL4 = 10 * 4
    STRINGS_START_BLOCK = 11

    MAGIC = b"OS"
    # v2 is the only version that predates this parser's primary-color fix and the v3
    # multi-color extension. v3 adds colors 2/3 + flags in blocks 24-25 - see parseTag().
    VERSION_V2 = 2
    VERSION_V3 = 3

    STRING_FIELDS = ("vendor", "material", "colorName")

    # physBuf is 16 bytes across four 4-byte blocks starting at block 7 (RED FALCON/
    # octoscale-46, src/pn5180nfc.h): block7=physBuf[0..3], block8=[4..7], block9=[8..11],
    # block10=[12..15]. The primary color at physBuf[10..12] therefore straddles the
    # block9/block10 boundary (byte 2-3 of block9, byte 0 of block10) - reading only
    # blocks 7-9 (as this parser did before this fix) silently missed it, returning
    # colors=[] on every NFC-V extended tag regardless of version. physBuf[13] (the CRC
    # byte) sits at block10 byte 1, absolute offset 41.
    PHYSBUF_COLOR_OFFSET = BLOCK_9_PHYSICAL3 + 2  # = 38, spans into block 10

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.NFCV:
            return None
        if data is None or len(data) < self.BLOCK_10_PHYSICAL4 + 4:
            return None

        if Binary.extract_slice(data, self.BLOCK_3_HEADER, 2) != self.MAGIC:
            return None

        version = Binary.extract_byte(data, self.BLOCK_3_HEADER + 2)
        if version is None or version < self.VERSION_V2:
            return None
        # Unknown/future versions must be rejected, not optimistically parsed as v3 - a v4
        # tag would otherwise be misread with garbage in the fields v4 repurposed.
        if version > self.VERSION_V3:
            return None
        isV3 = version >= self.VERSION_V3

        flags = Binary.extract_byte(data, self.BLOCK_3_HEADER + 3) or 0
        stringsPresent = bool(flags & 0x01)

        databaseId = Binary.extract_uint32_le(data, self.BLOCK_4_DB_ID)
        totalWeight = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.BLOCK_5_WEIGHTS))
        spoolWeight = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_5_WEIGHTS + 2)
        )
        usedWeight = _octoscaleU16OrNone(Binary.extract_uint16_le(data, self.BLOCK_6_WEIGHTS2))
        density = _octoscaleU16OrNone(
            Binary.extract_uint16_le(data, self.BLOCK_6_WEIGHTS2 + 2)
        )

        # Primary color at physBuf[10..12], absolute offset 38-40 - straddles the
        # block9/block10 boundary (see PHYSBUF_COLOR_OFFSET's comment above). Existed
        # since v1/v2 but was never read here before this fix.
        primaryRgb = Binary.extract_slice(data, self.PHYSBUF_COLOR_OFFSET, 3)
        argb = None
        if primaryRgb is not None:
            argb = _octoscaleColorArgb(primaryRgb[0], primaryRgb[1], primaryRgb[2])

        # v3: colors 2/3 (block24[0..2], block25[0..2]) and the shared flags byte
        # (block24[3]). Block 25 byte 3 is reserved.
        BLOCK_24 = 24 * 4
        BLOCK_25 = 25 * 4
        extraColors = []
        isTransparent = False
        isRainbow = False
        if isV3 and len(data) >= BLOCK_25 + 4:
            color2Rgb = Binary.extract_slice(data, BLOCK_24, 3)
            colorFlags = Binary.extract_byte(data, BLOCK_24 + 3)
            color3Rgb = Binary.extract_slice(data, BLOCK_25, 3)
            isTransparent, colorCount, isRainbow = _octoscaleParseColorFlags(colorFlags)
            if colorCount >= 2 and color2Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color2Rgb[0], color2Rgb[1], color2Rgb[2])
                )
            if colorCount >= 3 and color3Rgb is not None:
                extraColors.append(
                    _octoscaleColorArgb(color3Rgb[0], color3Rgb[1], color3Rgb[2])
                )

        strings = {}
        if stringsPresent and len(data) >= self.STRINGS_START_BLOCK * 4:
            strings = _octoscaleReadStrings(
                data, self.STRINGS_START_BLOCK * 4, self.STRING_FIELDS
            )

        # This format identifies a spool by databaseId alone - no manufacturer, no
        # colour/temperature fields exist at all on this carrier, so a valid tag with
        # every optional field empty must still be accepted rather than rejected for
        # "nothing to show".
        if databaseId is None:
            return None

        filament = GenericFilament(
            source_processor=self.id,
            unique_id=GenericFilament.generate_unique_id(
                "OctoScaleExtendedNfcv", databaseId, strings.get("material")
            ),
            manufacturer=strings.get("vendor"),
            type=strings.get("material"),
            modifiers=[],
            colors=([argb] if argb is not None else []) + [c for c in extraColors if c is not None],
            diameter_mm=None,
            weight_grams=totalWeight,
            hotend_min_temp_c=None,
            hotend_max_temp_c=None,
            bed_temp_c=0,
            drying_temp_c=0,
            drying_time_hours=0,
            manufacturing_date=Constants.NO_MANUFACTURING_DATE,
        )
        # See OctoScaleExtendedTagParser's parseTag() for why "color" is overridden here
        # rather than left to the generic multi-color join.
        composedColor = _octoscaleComposeColorString(
            argb, extraColors, isTransparent, isRainbow
        )
        filament.octoscaleExtendedFields = _buildOctoscaleExtendedFields(
            databaseId=databaseId,
            spoolWeight=spoolWeight,
            usedWeight=usedWeight,
            density=(density / 1000.0) if density is not None else None,
            diameterTolerance=None,
            colorName=strings.get("colorName"),
            color=composedColor,
            remainingWeight=None,
            totalLength=None,
            usedLength=None,
            cost=None,
            firstUseDays=None,
            lastUseDays=None,
            purchasedOnDays=None,
            firstUseMinuteOfDay=None,
            lastUseMinuteOfDay=None,
            purchasedOnMinuteOfDay=None,
            code=None,
            batchNumber=None,
            purchasedFrom=None,
            finish=None,
            displayName=None,
        )
        return filament


def _buildOctoscaleExtendedFields(**kwargs):
    """Fields our own extended format carries beyond what GenericFilament/
    genericFilamentToSpoolFields() knows how to map for vendor tags - see the module docs
    above. A key is present only when the tag actually carries that field: absence (not an
    empty value) is what lets the API layer's "never clear an existing value" semantics
    work correctly for a carrier that structurally lacks a field (e.g. NFC-V has no cost).
    """
    fields = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        fields[key] = value
    return fields


FILAMENT_TAG_PARSERS = {
    OctoScaleExtendedNtagTagParser.id: {
        "id": OctoScaleExtendedNtagTagParser.id,
        "label": OctoScaleExtendedNtagTagParser.label,
        "tagClass": OctoScaleExtendedNtagTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": OctoScaleExtendedNtagTagParser,
        # Must run before every NDEF parser: unlike Mifare Classic, nothing on NTAG is
        # sector-authenticated, so whichever parser runs first wins. A write here does not
        # erase the tag first, so leftover foreign NDEF content can sit right behind this
        # format's own commit marker - see the class docstring and
        # TestParsersRejectEachOthersTags for the fixture that demonstrates it.
        "parser_order": -1,
        "description": "OctoScale's own extended format on NTAG/Ultralight.",
    },
    OpenSpoolTagParser.id: {
        "id": OpenSpoolTagParser.id,
        "label": OpenSpoolTagParser.label,
        "tagClass": OpenSpoolTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": OpenSpoolTagParser,
        "description": "Open NDEF/JSON format, also written by this plugin.",
    },
    SpoolEaseTagParser.id: {
        "id": SpoolEaseTagParser.id,
        "label": SpoolEaseTagParser.label,
        "tagClass": SpoolEaseTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": SpoolEaseTagParser,
        "description": "SpoolEase tags, encoded as a tag.spoolease.io URL.",
    },
    AnycubicTagParser.id: {
        "id": AnycubicTagParser.id,
        "label": AnycubicTagParser.label,
        "tagClass": AnycubicTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": AnycubicTagParser,
        "description": "Anycubic vendor tags (binary page layout).",
    },
    ElegooTagParser.id: {
        "id": ElegooTagParser.id,
        "label": ElegooTagParser.label,
        "tagClass": ElegooTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": ElegooTagParser,
        "description": "Elegoo vendor tags (binary layout at a fixed 0x40 offset).",
    },
    TigerTagTagParser.id: {
        "id": TigerTagTagParser.id,
        "label": TigerTagTagParser.label,
        "tagClass": TigerTagTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": TigerTagTagParser,
        "description": "TigerTag tags (raw NTAG page layout, big-endian, 4-byte magic).",
    },
    BambuTagParser.id: {
        "id": BambuTagParser.id,
        "label": BambuTagParser.label,
        "tagClass": BambuTagParser.tagClass,
        # Needs a salt the user supplies; disabled until then.
        "requiresKey": True,
        "keyName": BambuTagParser.keyName,
        # Blocks 1-6, so sectors 0 and 1 - the rest of the tag is signature and spool data
        # this plugin has no use for.
        "sectors": [0, 1],
        # Key B is all zeroes on these tags: asking for it would only cost a re-selection
        # per sector after each key A failure.
        "needsKeyB": False,
        "parser": BambuTagParser,
        "description": "Bambu Lab vendor tags (Mifare Classic, needs a user-supplied salt).",
    },
    SnapmakerTagParser.id: {
        "id": SnapmakerTagParser.id,
        "label": SnapmakerTagParser.label,
        "tagClass": SnapmakerTagParser.tagClass,
        # No shared secret: the keys come from the tag's own UID, so nothing has to be
        # configured and the parser is always available.
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": SnapmakerTagParser,
        "description": "Snapmaker vendor tags (Mifare Classic, keys derived from the UID).",
    },
    OctoScaleExtendedTagParser.id: {
        "id": OctoScaleExtendedTagParser.id,
        "label": OctoScaleExtendedTagParser.label,
        "tagClass": OctoScaleExtendedTagParser.tagClass,
        "requiresKey": False,
        # Sector 0 MUST be included even though this format writes nothing there: the
        # firmware's /nfcreadstart returns only the requested sectors, back-to-back, with
        # no padding for the ones skipped - a sector list starting at 1 makes the response
        # begin at block 4, not block 0, silently shifting every absolute offset this
        # parser uses by 64 bytes. Caught live on hardware (spool 110): the read itself
        # succeeded (all nine sectors authenticated, 576 bytes back, legacy id "110"
        # readable at byte 0 of the response) but parseTag() rejected the shifted data as
        # not matching the magic - the *reported* "authentication failed" was a red
        # herring from Qidi, which runs after this parser in the same dispatch loop and
        # overwrites the shared lastError/lastRetryable on its own unrelated rejection.
        #
        # The *sectors* (not block numbers - 4 blocks per sector) that cover every block
        # this format's fields and CRC actually live in:
        #   sector 1 = blocks  4- 7 (legacy id anchor, block 4)
        #   sector 2 = blocks  8-11 (header/numeric, block 8-9, CRC)
        #   sector 3 = blocks 12-15 (buffer 1: vendor/material/colorName)
        #   sector 4 = blocks 16-19 (v3 numeric + minutes, block 16-17)
        #   sectors 5-9 = blocks 20-39 (buffer 2 + its trailers, v3 commit marker block 36)
        "sectors": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "needsKeyB": False,
        "parser": OctoScaleExtendedTagParser,
        # Must run after the keyed vendor parsers (which can reject a foreign tag outright)
        # but before Qidi (order 100), whose recognition is only heuristic and which must
        # not get a shot at a tag this format actually owns. Like Qidi, this format sits on
        # the Mifare factory key, so authentication alone proves nothing here either - see
        # the class docstring.
        "parser_order": 50,
        "description": "OctoScale's own extended format on Mifare Classic 1K.",
    },
    QidiTagParser.id: {
        "id": QidiTagParser.id,
        "label": QidiTagParser.label,
        "tagClass": QidiTagParser.tagClass,
        "requiresKey": False,
        # Only sector 1 carries filament data. Reading just that sector instead of all
        # sixteen is the single biggest saving on this path (~54 ms versus ~765 ms on a
        # rejection), and block reads - not authentication - are the expensive part.
        "sectors": [1],
        "needsKeyB": False,
        "parser": QidiTagParser,
        # Listed last on purpose: it authenticates with the factory key, which every blank
        # Classic tag also accepts, so keyed parsers must get their unambiguous rejection in
        # first. See the class docstring.
        "parser_order": 100,
        "description": "Qidi vendor tags (Mifare Classic, factory key - no secret needed).",
    },
    OctoScaleExtendedNfcvTagParser.id: {
        "id": OctoScaleExtendedNfcvTagParser.id,
        "label": OctoScaleExtendedNfcvTagParser.label,
        "tagClass": OctoScaleExtendedNfcvTagParser.tagClass,
        "requiresKey": False,
        # NFC-V has no sector concept; the reader does a plain block walk.
        "sectors": None,
        "needsKeyB": False,
        "parser": OctoScaleExtendedNfcvTagParser,
        "description": "OctoScale's own extended format on NFC-V (ISO15693).",
    },
    OpenSpoolNfcvTagParser.id: {
        "id": OpenSpoolNfcvTagParser.id,
        "label": OpenSpoolNfcvTagParser.label,
        "tagClass": OpenSpoolNfcvTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": OpenSpoolNfcvTagParser,
        # After the extended parser: extended's magic+CRC check rejects unambiguously,
        # OpenSpool's NDEF/JSON has no comparable guard against misreading foreign data.
        "parser_order": 10,
        "description": "Open NDEF/JSON format on NFC-V (ISO15693), also written by this plugin.",
    },
    OpenPrintTagParser.id: {
        "id": OpenPrintTagParser.id,
        "label": OpenPrintTagParser.label,
        "tagClass": OpenPrintTagParser.tagClass,
        "requiresKey": False,
        "sectors": None,
        "needsKeyB": False,
        "parser": OpenPrintTagParser,
        "parser_order": 20,
        "description": "OpenPrintTag CBOR/NDEF format on NFC-V (ISO15693).",
    },
}


def getParser(parserId):
    return FILAMENT_TAG_PARSERS.get(parserId)


def parsersForTagClass(tagClass):
    """Parser descriptors for one protocol class, in the order they should be tried.

    Order is significant, not cosmetic: a parser whose recognition is only heuristic (Qidi,
    which authenticates with the factory key every blank tag accepts) must run after the
    ones that can reject a foreign format outright, or it would claim tags that are not
    its own. Descriptors without an explicit parser_order keep their registry position.
    """
    matching = [
        descriptor
        for descriptor in FILAMENT_TAG_PARSERS.values()
        if descriptor["tagClass"] == tagClass
    ]
    return sorted(matching, key=lambda descriptor: descriptor.get("parser_order", 0))


def instantiateParser(descriptor, keyStore=None):
    """Build a parser from its registry entry.

    Parsers that need a user-supplied key take the store in their constructor and disable
    themselves when it holds nothing valid; the keyless ones take no arguments at all. This
    keeps the "a parser switches itself off" behaviour in the parser rather than turning the
    dispatch into a series of special cases.
    """
    parserClass = descriptor["parser"]
    if descriptor.get("requiresKey"):
        return parserClass(keyStore)
    return parserClass()


def parseTagData(scanResult, data, parserIds=None, keyStore=None):
    """First parser that recognizes the data wins. Returns (filament, diagnostics)."""
    attempted = []
    candidates = parsersForTagClass(scanResult.tag_type)
    if parserIds is not None:
        candidates = [d for d in candidates if d["id"] in parserIds]

    for descriptor in candidates:
        attempted.append(descriptor["id"])
        try:
            parser = instantiateParser(descriptor, keyStore)
            filament = parser.parseTag(scanResult, data)
        except Exception:
            # A malformed tag must not take down the request. Ported parser bodies index
            # fixed offsets in places; a guard here keeps one bad tag from becoming a 500.
            _logger.exception(
                "Parser '%s' raised while reading a tag - treating as not recognized",
                descriptor["id"],
            )
            continue
        if filament is not None:
            return filament, {"attemptedParsers": attempted, "parserId": descriptor["id"]}

    return None, {"attemptedParsers": attempted, "parserId": None}
