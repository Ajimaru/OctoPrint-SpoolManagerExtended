# coding=utf-8

# Vendor filament tag parsers and the dispatch that runs them.
#
# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0:
#   OpenSpoolTagParser   <- src/tag/openspool/processor.py  (itself adapted from
#                           https://github.com/paxx12/SnapmakerU1-Extended-Firmware)
#   SpoolEaseTagParser   <- src/tag/spoolease/processor.py
#   readFilamentFromTag  <- src/runtime.py's process_mifare_* dispatch
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
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

try:
    from urllib import parse as urlparse
except ImportError:  # pragma: no cover - Python 2 never applies here
    urlparse = None

from . import FilamentTagBinary as Binary
from . import FilamentTagConstants as Constants
from . import FilamentTagNdef as Ndef
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
    try:
        hexString = str(value).strip()
        if hexString.startswith("#"):
            hexString = hexString[1:]
        return int(hexString, 16)
    except (ValueError, TypeError):
        return default


class OpenSpoolTagParser(object):
    """OpenSpool's NDEF/JSON record - the open format this plugin also writes."""

    id = "openSpool"
    label = "OpenSpool"
    tagClass = TagType.MIFARE_ULTRALIGHT
    requiresKey = False

    def parseTag(self, scanResult, data):
        if scanResult.tag_type != TagType.MIFARE_ULTRALIGHT:
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
FILAMENT_TAG_PARSERS = {
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
}


def getParser(parserId):
    return FILAMENT_TAG_PARSERS.get(parserId)


def parsersForTagClass(tagClass):
    """Parser descriptors for one protocol class, in the order they should be tried."""
    return [
        descriptor
        for descriptor in FILAMENT_TAG_PARSERS.values()
        if descriptor["tagClass"] == tagClass
    ]


def parseTagData(scanResult, data, parserIds=None):
    """First parser that recognizes the data wins. Returns (filament, diagnostics)."""
    attempted = []
    candidates = parsersForTagClass(scanResult.tag_type)
    if parserIds is not None:
        candidates = [d for d in candidates if d["id"] in parserIds]

    for descriptor in candidates:
        attempted.append(descriptor["id"])
        try:
            filament = descriptor["parser"]().parseTag(scanResult, data)
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
