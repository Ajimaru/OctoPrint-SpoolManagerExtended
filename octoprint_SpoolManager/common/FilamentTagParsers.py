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

try:
    from urllib import parse as urlparse
except ImportError:  # pragma: no cover - Python 2 never applies here
    urlparse = None

from . import FilamentTagBinary as Binary
from . import FilamentTagConstants as Constants
from . import FilamentTagKeys as Keys
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
