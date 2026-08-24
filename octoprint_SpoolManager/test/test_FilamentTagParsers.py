# coding=utf-8

# Tests for the ported vendor tag parsers. No hardware and no network: every fixture is
# built in-test from the format's own constants, so the byte layout under test is spelled
# out rather than captured from a real tag (whose contents would be manufacturer data of
# unclear redistribution status).
#
# Run with:  python3 octoprint_SpoolManager/test/test_FilamentTagParsers.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import json
import os
import sys
import types
import unittest

_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)

_PACKAGE_NAME = "spoolmanager_test_common_pkg"
if _PACKAGE_NAME not in sys.modules:
    _package = types.ModuleType(_PACKAGE_NAME)
    _package.__path__ = [_COMMON_DIR]
    sys.modules[_PACKAGE_NAME] = _package


def _loadModule(moduleName):
    modulePath = os.path.join(_COMMON_DIR, moduleName + ".py")
    qualifiedName = _PACKAGE_NAME + "." + moduleName
    spec = importlib.util.spec_from_file_location(qualifiedName, modulePath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualifiedName] = module
    spec.loader.exec_module(module)
    return module


FilamentTagBinary = _loadModule("FilamentTagBinary")
FilamentTagModel = _loadModule("FilamentTagModel")
FilamentTagNdef = _loadModule("FilamentTagNdef")
FilamentTagConstants = _loadModule("FilamentTagConstants")
FilamentTagParsers = _loadModule("FilamentTagParsers")

TagType = FilamentTagModel.TagType
ScanResult = FilamentTagModel.ScanResult


def buildNtagDump(ndefMessage):
    """A page-0 NTAG image carrying one NDEF message, as the firmware returns it."""
    # pages 0-3: UID / internal / lock bytes / capability container. Only the CC (0xE1)
    # matters to the parser, the rest just has to be there so absolute offsets line up.
    image = bytearray()
    image += bytes([0x04, 0x11, 0x22, 0x33])  # page 0 - UID part
    image += bytes([0x44, 0x55, 0x66, 0x77])  # page 1 - UID part
    image += bytes([0x88, 0x00, 0x00, 0x00])  # page 2 - lock bytes
    image += bytes([0xE1, 0x10, 0x6D, 0x00])  # page 3 - capability container
    # TLV: 0x03 = NDEF message, then length, then the message, then the terminator.
    image += bytes([0x03, len(ndefMessage)])
    image += ndefMessage
    image += bytes([0xFE])
    while len(image) % 4 != 0:
        image += b"\x00"
    return bytes(image)


def buildMimeRecord(mimeType, payload):
    """Single short MIME record with MB+ME+SR set."""
    typeBytes = mimeType.encode("ascii")
    header = 0xD2  # MB=1, ME=1, SR=1, TNF=0x02 (MIME)
    return bytes([header, len(typeBytes), len(payload)]) + typeBytes + payload


def buildUriRecord(prefixCode, rest):
    """Single short well-known URI record."""
    restBytes = rest.encode("utf-8")
    payload = bytes([prefixCode]) + restBytes
    header = 0xD1  # MB=1, ME=1, SR=1, TNF=0x01 (well-known)
    return bytes([header, 1, len(payload)]) + b"U" + payload


def ntagScan():
    return ScanResult(TagType.MIFARE_ULTRALIGHT, bytes([0x04, 0x11, 0x22, 0x33]))


class TestBinaryHelpers(unittest.TestCase):
    def test_reads_within_range(self):
        data = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        self.assertEqual(0x0201, FilamentTagBinary.extract_uint16_le(data, 0))
        self.assertEqual(0x0102, FilamentTagBinary.extract_uint16_be(data, 0))
        self.assertEqual(0x04030201, FilamentTagBinary.extract_uint32_le(data, 0))

    def test_out_of_range_returns_none_instead_of_raising(self):
        # The whole point of the divergence from upstream: a short tag must be rejected,
        # not crash the request with an IndexError.
        data = bytes([0x01, 0x02])
        self.assertIsNone(FilamentTagBinary.extract_uint16_le(data, 1))
        self.assertIsNone(FilamentTagBinary.extract_uint32_le(data, 0))
        self.assertIsNone(FilamentTagBinary.extract_string(data, 0, 10))
        self.assertIsNone(FilamentTagBinary.extract_byte(data, 5))

    def test_string_stops_at_null(self):
        self.assertEqual(
            "PLA", FilamentTagBinary.extract_string(b"PLA\x00\x00\x00", 0, 6)
        )


class TestGenericFilament(unittest.TestCase):
    def test_cf_modifier_folds_into_type(self):
        filament = FilamentTagModel.GenericFilament(
            "test", "id", "Vendor", "PLA", ["CF"], [0xFF112233],
            1.75, 1000, 190, 230, 60, 55, 8, "2024-01-01",
        )
        self.assertEqual("PLA-CF", filament.type)
        self.assertEqual([], filament.modifiers)
        self.assertTrue(filament.typeRecognized)

    def test_unknown_material_is_kept_not_rejected(self):
        # Upstream raises ValueError here. A Bambu "PLA Basic" would lose its color and
        # weight over a name we don't happen to know - so it is flagged, not dropped.
        filament = FilamentTagModel.GenericFilament(
            "test", "id", "Bambu", "PLA Basic", [], [0xFF112233],
            1.75, 1000, 190, 230, 60, 55, 8, "2024-01-01",
        )
        self.assertEqual("PLA Basic", filament.type)
        self.assertFalse(filament.typeRecognized)

    def test_rgba_conversion_moves_alpha_to_the_end(self):
        filament = FilamentTagModel.GenericFilament(
            "test", "id", "Vendor", "PLA", [], [0xFF112233],
            1.75, 1000, 190, 230, 60, 55, 8, "2024-01-01",
        )
        self.assertEqual(0x112233FF, filament.rgba)

    def test_unique_id_is_stable_and_ignores_argument_types(self):
        first = FilamentTagModel.GenericFilament.generate_unique_id("A", 1, None)
        second = FilamentTagModel.GenericFilament.generate_unique_id("A", "1", "None")
        self.assertEqual(first, second)


class TestTagTypeMapping(unittest.TestCase):
    def test_octoscale_strings(self):
        self.assertEqual(
            TagType.MIFARE_CLASSIC_1K,
            FilamentTagModel.tagTypeFromOctoScale("mifareClassic1k"),
        )
        self.assertEqual(
            TagType.MIFARE_ULTRALIGHT, FilamentTagModel.tagTypeFromOctoScale("ntag")
        )
        # NFC-V has no parser here, and an unknown string must not masquerade as a type
        self.assertEqual(TagType.UNKNOWN, FilamentTagModel.tagTypeFromOctoScale("nfcv"))
        self.assertEqual(TagType.UNKNOWN, FilamentTagModel.tagTypeFromOctoScale(None))


class TestNdefParsing(unittest.TestCase):
    def test_finds_mime_record_after_page_zero_header(self):
        message = buildMimeRecord("application/json", b'{"a":1}')
        errorCode, records = FilamentTagNdef.parseNdefRecords(buildNtagDump(message))
        self.assertEqual(FilamentTagNdef.NDEF_OK, errorCode)
        self.assertEqual(1, len(records))
        self.assertEqual("application/json", records[0].mime_type)
        self.assertEqual(b'{"a":1}', records[0].payload)

    def test_uri_record_prefix_is_expanded(self):
        message = buildUriRecord(0x04, "tag.spoolease.io/S1?M=PLA")
        errorCode, records = FilamentTagNdef.parseNdefRecords(buildNtagDump(message))
        self.assertEqual(FilamentTagNdef.NDEF_OK, errorCode)
        self.assertEqual(
            "https://tag.spoolease.io/S1?M=PLA", records[0].uriText()
        )

    def test_tag_without_capability_container_is_rejected(self):
        self.assertEqual(
            FilamentTagNdef.NDEF_PARAMETER_ERR,
            FilamentTagNdef.parseNdefRecords(bytes(64))[0],
        )

    def test_empty_and_garbage_input_do_not_raise(self):
        for payload in (None, b"", b"\x00\x01\x02", "not bytes"):
            errorCode, records = FilamentTagNdef.parseNdefRecords(payload)
            self.assertNotEqual(FilamentTagNdef.NDEF_OK, errorCode)
            self.assertEqual([], records)


class TestOpenSpoolParser(unittest.TestCase):
    def buildTag(self, **overrides):
        payload = {
            "protocol": "openspool",
            "version": "1.0",
            "brand": "Polymaker",
            "type": "PETG",
            "color_hex": "1A2B3C",
            "min_temp": "230",
            "max_temp": "250",
        }
        payload.update(overrides)
        message = buildMimeRecord(
            "application/json", json.dumps(payload).encode("utf-8")
        )
        return buildNtagDump(message)

    def test_parses_a_well_formed_tag(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(ntagScan(), self.buildTag())
        self.assertIsNotNone(filament)
        self.assertEqual("Polymaker", filament.manufacturer)
        self.assertEqual("PETG", filament.type)
        self.assertEqual([0xFF1A2B3C], filament.colors)
        self.assertEqual(230, filament.hotend_min_temp_c)
        self.assertEqual(250, filament.hotend_max_temp_c)
        # not on the tag - filled in from the material defaults
        self.assertEqual(70.0, filament.bed_temp_c)

    def test_defaults_apply_when_fields_are_absent(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(ntagScan(), self.buildTag())
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(1000, filament.weight_grams)

    def test_bed_temperature_from_the_tag_wins_over_the_material_table(self):
        # The spec defines bed_min_temp/bed_max_temp and OctoScale writes them. A table
        # lookup must never shadow a real value, or a tag would read back with a bed
        # temperature it does not actually contain.
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag(bed_min_temp="75", bed_max_temp="85")
        )
        self.assertEqual(85, filament.bed_temp_c)

    def test_bed_min_temp_alone_is_used(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(ntagScan(), self.buildTag(bed_min_temp="75"))
        self.assertEqual(75, filament.bed_temp_c)

    def test_material_table_fills_in_when_the_tag_carries_no_bed_temperature(self):
        # PETG default from OPENSPOOL_TYPE_DEFAULTS - a suggestion, not tag data.
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(ntagScan(), self.buildTag())
        self.assertEqual(70.0, filament.bed_temp_c)

    def test_sentinel_bed_temperature_falls_back_to_the_table(self):
        # -1 is the firmware's "not set" marker and must not be read as a temperature.
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag(bed_min_temp="-1", bed_max_temp="-1")
        )
        self.assertEqual(70.0, filament.bed_temp_c)

    def test_foreign_protocol_is_rejected(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        self.assertIsNone(
            parser.parseTag(ntagScan(), self.buildTag(protocol="somethingelse"))
        )

    def test_inverted_temperature_range_is_rejected(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        self.assertIsNone(
            parser.parseTag(ntagScan(), self.buildTag(min_temp="250", max_temp="230"))
        )

    def test_wrong_tag_type_returns_none_rather_than_raising(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        classicScan = ScanResult(TagType.MIFARE_CLASSIC_1K, b"\x01\x02\x03\x04")
        self.assertIsNone(parser.parseTag(classicScan, self.buildTag()))

    def test_truncated_tag_does_not_raise(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        full = self.buildTag()
        for length in (0, 4, 12, 20, len(full) // 2):
            self.assertIsNone(parser.parseTag(ntagScan(), full[:length]))


class TestSpoolEaseParser(unittest.TestCase):
    def buildTag(self, query="M=PLA&B=SpoolEase&CC=FF0000&NN=200&NX=220&WL=750"):
        message = buildUriRecord(0x04, "tag.spoolease.io/S1?" + query)
        return buildNtagDump(message)

    def test_parses_a_well_formed_tag(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        filament = parser.parseTag(ntagScan(), self.buildTag())
        self.assertIsNotNone(filament)
        self.assertEqual("SpoolEase", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual([0xFFFF0000], filament.colors)
        self.assertEqual(200, filament.hotend_min_temp_c)
        self.assertEqual(750, filament.weight_grams)

    def test_material_alias_is_resolved(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag("M=PLA-S&NN=200&NX=220")
        )
        self.assertEqual("PLA", filament.type)

    def test_multi_color_field_is_split(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag("M=PLA&CC=FF0000;00FF00&NN=200&NX=220")
        )
        self.assertEqual([0xFFFF0000, 0xFF00FF00], filament.colors)

    def test_rgba_alpha_is_taken_from_an_eight_digit_value(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag("M=PLA&CC=FF000080&NN=200&NX=220")
        )
        self.assertEqual([0x80FF0000], filament.colors)

    def test_missing_required_temperature_is_rejected(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        self.assertIsNone(parser.parseTag(ntagScan(), self.buildTag("M=PLA&B=X")))

    def test_foreign_host_is_rejected(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        message = buildUriRecord(0x04, "example.com/S1?M=PLA&NN=200&NX=220")
        self.assertIsNone(parser.parseTag(ntagScan(), buildNtagDump(message)))

    def test_truncated_tag_does_not_raise(self):
        parser = FilamentTagParsers.SpoolEaseTagParser()
        full = self.buildTag()
        for length in (0, 4, 12, 20, len(full) // 2):
            self.assertIsNone(parser.parseTag(ntagScan(), full[:length]))


def buildAnycubicTag(
    sku="AC-PLA-001",
    brand="Anycubic",
    filamentType="PLA+ Silk",
    argb=(0xFF, 0x11, 0x22, 0x33),  # a, r, g, b
    minTemp=200,
    maxTemp=230,
    bedMaxTemp=60,
    diameterHundredths=175,
    lengthM=330,
):
    """Anycubic's binary layout, built from the offsets the parser reads."""
    image = bytearray(b"\x00" * 0x80)
    image[0x10:0x14] = b"\x7B\x00\x65\x00"  # format marker
    image[0x14 : 0x14 + len(sku)] = sku.encode("ascii")
    image[0x28 : 0x28 + len(brand)] = brand.encode("ascii")
    image[0x3C : 0x3C + len(filamentType)] = filamentType.encode("ascii")
    alpha, red, green, blue = argb
    # stored a,b,g,r - deliberately not the order the value is assembled in
    image[0x50] = alpha
    image[0x51] = blue
    image[0x52] = green
    image[0x53] = red
    image[0x60:0x62] = minTemp.to_bytes(2, "little")
    image[0x62:0x64] = maxTemp.to_bytes(2, "little")
    image[0x76:0x78] = bedMaxTemp.to_bytes(2, "little")
    image[0x78:0x7A] = diameterHundredths.to_bytes(2, "little")
    image[0x7A:0x7C] = lengthM.to_bytes(2, "little")
    return bytes(image)


def buildElegooTag(
    materialId=0x00,  # PLA
    modifierId=0x03,  # Silk
    rgba=(0x11, 0x22, 0x33, 0xFF),
    minTemp=190,
    maxTemp=220,
    diameterHundredths=175,
    weightGrams=1000,
):
    """Elegoo's binary layout, which lives at a fixed 0x40 offset into the page image."""
    image = bytearray(b"\x00" * 0x80)
    block = bytearray(b"\x00" * 0x29)
    block[0x01:0x05] = b"\xEE\xEE\xEE\xEE"  # format marker
    block[0x0C] = materialId
    block[0x0D] = modifierId
    red, green, blue, alpha = rgba
    block[0x10] = red
    block[0x11] = green
    block[0x12] = blue
    block[0x13] = alpha
    block[0x14:0x16] = minTemp.to_bytes(2, "big")
    block[0x16:0x18] = maxTemp.to_bytes(2, "big")
    block[0x1C:0x1E] = diameterHundredths.to_bytes(2, "big")
    block[0x1E:0x20] = weightGrams.to_bytes(2, "big")
    image[0x40 : 0x40 + len(block)] = block
    return bytes(image)


class TestAnycubicParser(unittest.TestCase):
    def test_parses_a_well_formed_tag(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        filament = parser.parseTag(ntagScan(), buildAnycubicTag())
        self.assertIsNotNone(filament)
        self.assertEqual("Anycubic", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(["+", "Silk"], sorted(filament.modifiers))
        self.assertEqual([0xFF112233], filament.colors)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(200, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)

    def test_length_resolves_to_a_spool_weight(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        self.assertEqual(
            750, parser.parseTag(ntagScan(), buildAnycubicTag(lengthM=247)).weight_grams
        )
        self.assertEqual(
            250, parser.parseTag(ntagScan(), buildAnycubicTag(lengthM=82)).weight_grams
        )

    def test_unknown_length_falls_back_to_one_kilo(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        filament = parser.parseTag(ntagScan(), buildAnycubicTag(lengthM=999))
        self.assertEqual(1000, filament.weight_grams)

    def test_cf_suffix_folds_into_the_type_name(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        filament = parser.parseTag(
            ntagScan(), buildAnycubicTag(filamentType="PETG-CF")
        )
        self.assertEqual("PETG-CF", filament.type)
        self.assertTrue(filament.typeRecognized)

    def test_missing_magic_is_rejected(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        data = bytearray(buildAnycubicTag())
        data[0x10] = 0x00
        self.assertIsNone(parser.parseTag(ntagScan(), bytes(data)))

    def test_truncated_tag_does_not_raise(self):
        parser = FilamentTagParsers.AnycubicTagParser()
        full = buildAnycubicTag()
        for length in (0, 16, 0x40, 0x7B):
            self.assertIsNone(parser.parseTag(ntagScan(), full[:length]))


class TestElegooParser(unittest.TestCase):
    def test_parses_a_well_formed_tag(self):
        parser = FilamentTagParsers.ElegooTagParser()
        filament = parser.parseTag(ntagScan(), buildElegooTag())
        self.assertIsNotNone(filament)
        self.assertEqual("Elegoo", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(["Silk"], filament.modifiers)
        self.assertEqual([0xFF112233], filament.colors)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(1000, filament.weight_grams)

    def test_digit_modifier_folds_into_the_type_name(self):
        # PA with modifier 0x04 ("6") is really PA6 - the digit belongs to the name.
        parser = FilamentTagParsers.ElegooTagParser()
        filament = parser.parseTag(
            ntagScan(), buildElegooTag(materialId=0x04, modifierId=0x04)
        )
        self.assertEqual("PA6", filament.type)
        self.assertEqual([], filament.modifiers)

    def test_cf_modifier_folds_via_generic_filament(self):
        parser = FilamentTagParsers.ElegooTagParser()
        filament = parser.parseTag(
            ntagScan(), buildElegooTag(materialId=0x01, modifierId=0x01)
        )
        self.assertEqual("PETG-CF", filament.type)

    def test_unknown_material_pair_is_rejected(self):
        parser = FilamentTagParsers.ElegooTagParser()
        self.assertIsNone(
            parser.parseTag(ntagScan(), buildElegooTag(materialId=0xFE, modifierId=0xFE))
        )

    def test_missing_magic_is_rejected(self):
        parser = FilamentTagParsers.ElegooTagParser()
        data = bytearray(buildElegooTag())
        data[0x41] = 0x00
        self.assertIsNone(parser.parseTag(ntagScan(), bytes(data)))

    def test_truncated_tag_does_not_raise(self):
        parser = FilamentTagParsers.ElegooTagParser()
        full = buildElegooTag()
        for length in (0, 16, 0x40, 0x50):
            self.assertIsNone(parser.parseTag(ntagScan(), full[:length]))


class TestParsersRejectEachOthersTags(unittest.TestCase):
    """Every parser must reject every other vendor's tag - that is what makes the
    first-match-wins dispatch safe."""

    def buildOpenSpoolTag(self):
        payload = {
            "protocol": "openspool",
            "brand": "Generic",
            "type": "PLA",
            "color_hex": "FFFFFF",
            "min_temp": "190",
            "max_temp": "220",
        }
        return buildNtagDump(
            buildMimeRecord("application/json", json.dumps(payload).encode("utf-8"))
        )

    def test_each_tag_is_claimed_by_exactly_one_parser(self):
        tags = {
            "openSpool": self.buildOpenSpoolTag(),
            "spoolEase": buildNtagDump(
                buildUriRecord(0x04, "tag.spoolease.io/S1?M=PLA&NN=200&NX=220")
            ),
            "anycubic": buildAnycubicTag(),
            "elegoo": buildElegooTag(),
        }
        for expectedParser, data in tags.items():
            filament, diagnostics = FilamentTagParsers.parseTagData(ntagScan(), data)
            self.assertIsNotNone(
                filament, "no parser recognized the " + expectedParser + " tag"
            )
            self.assertEqual(
                expectedParser,
                diagnostics["parserId"],
                "the " + expectedParser + " tag was claimed by the wrong parser",
            )


class TestDispatch(unittest.TestCase):
    def test_picks_the_parser_that_recognizes_the_tag(self):
        payload = {
            "protocol": "openspool",
            "brand": "Generic",
            "type": "PLA",
            "color_hex": "FFFFFF",
            "min_temp": "190",
            "max_temp": "220",
        }
        data = buildNtagDump(
            buildMimeRecord("application/json", json.dumps(payload).encode("utf-8"))
        )
        filament, diagnostics = FilamentTagParsers.parseTagData(ntagScan(), data)
        self.assertIsNotNone(filament)
        self.assertEqual("openSpool", diagnostics["parserId"])

    def test_unrecognized_tag_reports_which_parsers_were_tried(self):
        filament, diagnostics = FilamentTagParsers.parseTagData(
            ntagScan(), buildNtagDump(buildMimeRecord("text/plain", b"hello"))
        )
        self.assertIsNone(filament)
        self.assertIsNone(diagnostics["parserId"])
        self.assertIn("openSpool", diagnostics["attemptedParsers"])
        self.assertIn("spoolEase", diagnostics["attemptedParsers"])

    def test_a_raising_parser_does_not_abort_the_dispatch(self):
        class ExplodingParser(object):
            id = "exploding"
            label = "Exploding"
            tagClass = TagType.MIFARE_ULTRALIGHT
            requiresKey = False

            def parseTag(self, scanResult, data):
                raise RuntimeError("boom")

        original = dict(FilamentTagParsers.FILAMENT_TAG_PARSERS)
        try:
            FilamentTagParsers.FILAMENT_TAG_PARSERS.clear()
            FilamentTagParsers.FILAMENT_TAG_PARSERS["exploding"] = {
                "id": "exploding",
                "label": "Exploding",
                "tagClass": TagType.MIFARE_ULTRALIGHT,
                "requiresKey": False,
                "sectors": None,
                "parser": ExplodingParser,
                "description": "",
            }
            filament, diagnostics = FilamentTagParsers.parseTagData(
                ntagScan(), buildNtagDump(buildMimeRecord("text/plain", b"x"))
            )
            self.assertIsNone(filament)
            self.assertEqual(["exploding"], diagnostics["attemptedParsers"])
        finally:
            FilamentTagParsers.FILAMENT_TAG_PARSERS.clear()
            FilamentTagParsers.FILAMENT_TAG_PARSERS.update(original)

    def test_classic_tag_finds_no_ultralight_parser(self):
        filament, diagnostics = FilamentTagParsers.parseTagData(
            ScanResult(TagType.MIFARE_CLASSIC_1K, b"\x01\x02\x03\x04"), bytes(1024)
        )
        self.assertIsNone(filament)
        self.assertEqual([], diagnostics["attemptedParsers"])


class TestParserRegistry(unittest.TestCase):
    def test_every_entry_is_complete(self):
        for parserId, descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.items():
            self.assertEqual(parserId, descriptor["id"])
            self.assertTrue(descriptor["label"])
            self.assertIn("tagClass", descriptor)
            self.assertIn("requiresKey", descriptor)
            self.assertIn("sectors", descriptor)
            self.assertIsNotNone(descriptor["parser"])

    def test_lookup_of_unknown_id(self):
        self.assertIsNone(FilamentTagParsers.getParser("doesNotExist"))

    def test_no_shipped_parser_needs_a_key_yet(self):
        # Phase 1 is deliberately keyless - the keyed vendors come with the key store.
        for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values():
            self.assertFalse(descriptor["requiresKey"])

    def test_no_parser_asks_for_key_b_without_needing_it(self):
        # Sending key B costs 3.3x on a rejection (measured: 765 ms -> 2547 ms across 16
        # sectors), because the card has to be re-selected before every attempt. A parser
        # that sets this without needing it slows down every miss for nothing.
        for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values():
            if descriptor["needsKeyB"]:
                self.assertTrue(
                    descriptor["requiresKey"],
                    descriptor["id"] + " asks for key B but uses no keys at all",
                )

    def test_ultralight_parsers_do_not_request_sectors(self):
        # Sector masks are a Mifare Classic concept; an NTAG gets a page walk.
        for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values():
            if descriptor["tagClass"] == TagType.MIFARE_ULTRALIGHT:
                self.assertIsNone(descriptor["sectors"])


if __name__ == "__main__":
    unittest.main()
