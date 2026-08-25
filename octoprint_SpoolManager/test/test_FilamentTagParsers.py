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
import struct
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

    def test_classic_tag_does_not_reach_ultralight_parsers(self):
        # A blank Classic image must be rejected by every Classic parser, and the NTAG ones
        # must not even be offered it. The Qidi parser is the reason this matters: it
        # authenticates with the factory key that a blank tag also carries, so "all zeroes"
        # is exactly the input it could wrongly claim.
        filament, diagnostics = FilamentTagParsers.parseTagData(
            ScanResult(TagType.MIFARE_CLASSIC_1K, b"\x01\x02\x03\x04"), bytes(1024)
        )
        self.assertIsNone(filament)

        attempted = diagnostics["attemptedParsers"]
        for ultralightParser in ("openSpool", "spoolEase", "anycubic", "elegoo"):
            self.assertNotIn(ultralightParser, attempted)

    def test_qidi_runs_after_the_keyed_classic_parsers(self):
        # Order is a correctness property here, not presentation: Qidi's recognition is only
        # heuristic, so parsers that can reject a foreign tag outright have to go first.
        order = [
            descriptor["id"]
            for descriptor in FilamentTagParsers.parsersForTagClass(
                TagType.MIFARE_CLASSIC_1K
            )
        ]
        self.assertIn("qidi", order)
        self.assertEqual("qidi", order[-1])


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

    def test_every_keyed_parser_names_its_key_and_stays_off_without_it(self):
        # A parser that needs a user-supplied secret must say which one, and must disable
        # itself when it is absent - that is what keeps the dispatch free of special cases
        # and what guarantees the plugin ships usable without any key material at all.
        for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values():
            if not descriptor["requiresKey"]:
                continue
            self.assertIsNotNone(
                descriptor.get("keyName"),
                descriptor["id"] + " requires a key but does not name it",
            )
            parser = FilamentTagParsers.instantiateParser(descriptor, None)
            self.assertFalse(
                parser.enabled,
                descriptor["id"] + " is enabled without a key",
            )

    def test_keyless_parsers_take_no_constructor_argument(self):
        # instantiateParser() branches on requiresKey; a keyless parser that started needing
        # one would break the dispatch rather than just itself.
        for descriptor in FilamentTagParsers.FILAMENT_TAG_PARSERS.values():
            if descriptor["requiresKey"]:
                continue
            self.assertIsNotNone(FilamentTagParsers.instantiateParser(descriptor, None))

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


def _snapmakerImage(
    mainType=1, subType=3, alphaByte=0x00, colorNums=1, diameter=175,
    weight=1000, dryTemp=55, dryHours=10, hotendMax=230, hotendMin=200, bedTemp=60,
    vendor="Snapmaker", producer="Polymaker", mfgDate="20251214"
):
    """A synthetic Snapmaker 1K image, built from named offsets rather than a captured dump.

    Layout per paxx12-snapmaker-u1/spool-link-apps, SnapmakerFormat.kt.
    """
    def le16(value):
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    image = bytearray(1024)
    image[66:68] = le16(mainType)
    image[68:70] = le16(subType)
    image[72] = colorNums
    image[73] = alphaByte
    image[80:83] = bytes([0xF4, 0xC0, 0x32])
    image[83:86] = bytes([0x11, 0x22, 0x33])
    image[128:130] = le16(diameter)
    image[130:132] = le16(weight)
    image[144:146] = le16(dryTemp)
    image[146:148] = le16(dryHours)
    image[148:150] = le16(hotendMax)
    image[150:152] = le16(hotendMin)
    image[154:156] = le16(bedTemp)
    if vendor:
        image[16:16 + len(vendor)] = vendor.encode("ascii")
    if producer:
        image[32:32 + len(producer)] = producer.encode("ascii")
    if mfgDate:
        image[160:168] = mfgDate.encode("ascii")
    return bytes(image)


class TestSnapmakerTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.SnapmakerTagParser()
        self.scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("30FB3002"))

    def test_parses_a_valid_tag(self):
        filament = self.parser.parseTag(self.scan, _snapmakerImage())
        self.assertIsNotNone(filament)
        self.assertEqual("Snapmaker (Polymaker)", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(["SnapSpeed"], filament.modifiers)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(1000, filament.weight_grams)
        self.assertEqual(200, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)
        self.assertEqual(60, filament.bed_temp_c)

    def test_drying_time_is_hours_not_minutes(self):
        # The tag stores hours and GenericFilament expects hours, so this value must pass
        # through untouched - the minute conversion elsewhere in this plugin does not apply.
        filament = self.parser.parseTag(self.scan, _snapmakerImage(dryHours=10))
        self.assertEqual(10, filament.drying_time_hours)
        self.assertEqual(55, filament.drying_temp_c)

    def test_alpha_is_inverted(self):
        # The tag stores transparency, not opacity: 0x00 means fully opaque.
        opaque = self.parser.parseTag(self.scan, _snapmakerImage(alphaByte=0x00))
        self.assertEqual(0xFF, (opaque.colors[0] >> 24) & 0xFF)

        clear = self.parser.parseTag(self.scan, _snapmakerImage(alphaByte=0xFF))
        self.assertEqual(0x00, (clear.colors[0] >> 24) & 0xFF)

    def test_reads_multiple_colours(self):
        filament = self.parser.parseTag(self.scan, _snapmakerImage(colorNums=2))
        self.assertEqual(2, len(filament.colors))
        self.assertEqual(0xFFF4C032, filament.colors[0])
        self.assertEqual(0xFF112233, filament.colors[1])

    def test_rejects_blank_and_implausible_tags(self):
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(1024)))
        self.assertIsNone(self.parser.parseTag(self.scan, _snapmakerImage(mainType=99)))
        self.assertIsNone(
            self.parser.parseTag(self.scan, _snapmakerImage(hotendMin=20, hotendMax=30))
        )
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(100)))

    def test_rejects_wrong_tag_class(self):
        ntagScan = ScanResult(TagType.MIFARE_ULTRALIGHT, b"\x04\x11\x22\x33")
        self.assertIsNone(self.parser.parseTag(ntagScan, _snapmakerImage()))

    def test_derives_sixteen_distinct_per_sector_keys(self):
        keys = self.parser.authenticationKeys(self.scan)
        self.assertEqual(16, len(keys))
        for key in keys:
            self.assertEqual(12, len(key))
        # Per-sector derivation: if these collapsed to one value the whole point is lost.
        self.assertEqual(16, len(set(keys)))

    def test_keys_depend_on_the_tag_uid(self):
        other = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("04AC6F56CB2A81"))
        self.assertNotEqual(
            self.parser.authenticationKeys(self.scan),
            self.parser.authenticationKeys(other),
        )


def _qidiImage(material=0x01, rgb=(0xF4, 0xC0, 0x32), temps=(210, 230, 60)):
    image = bytearray(1024)
    image[0x40] = material
    image[0x44], image[0x45], image[0x46] = rgb
    image[0x48], image[0x49], image[0x4A] = temps
    return bytes(image)


class TestQidiTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.QidiTagParser()
        self.scan = ScanResult(TagType.MIFARE_CLASSIC_1K, b"\x01\x02\x03\x04")

    def test_parses_a_valid_tag(self):
        filament = self.parser.parseTag(self.scan, _qidiImage())
        self.assertIsNotNone(filament)
        self.assertEqual("Qidi", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(210, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)

    def test_rejects_a_blank_tag(self):
        # The important one: Qidi authenticates with the factory key, so a blank Classic tag
        # reaches this parser. Material id 0x00 is what keeps it from being read as filament.
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(1024)))

    def test_rejects_implausible_temperatures(self):
        self.assertIsNone(self.parser.parseTag(self.scan, _qidiImage(temps=(20, 30, 60))))
        self.assertIsNone(self.parser.parseTag(self.scan, _qidiImage(temps=(250, 200, 60))))
        self.assertIsNone(self.parser.parseTag(self.scan, _qidiImage(temps=(210, 230, 250))))

    def test_rejects_truncated_input(self):
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(32)))


class TestSnapmakerRealTagFields(unittest.TestCase):
    """Fields verified against a physical Snapmaker spool (UID 30FB3002)."""

    def setUp(self):
        self.parser = FilamentTagParsers.SnapmakerTagParser()
        self.scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("30FB3002"))

    def test_keeps_the_rebadged_producer(self):
        # Snapmaker sells filament made by others and the tag names both. Dropping the
        # producer would lose the only hint about what the material actually is.
        filament = self.parser.parseTag(self.scan, _snapmakerImage())
        self.assertEqual("Snapmaker (Polymaker)", filament.manufacturer)
        # The variant field must stay the variant - a producer name there would read as if
        # the material itself were called "SnapSpeed Polymaker".
        self.assertEqual(["SnapSpeed"], filament.modifiers)

    def test_producer_equal_to_vendor_is_not_duplicated(self):
        filament = self.parser.parseTag(
            self.scan, _snapmakerImage(producer="Snapmaker")
        )
        self.assertEqual("Snapmaker", filament.manufacturer)

    def test_manufacturing_date_becomes_iso(self):
        filament = self.parser.parseTag(self.scan, _snapmakerImage(mfgDate="20251214"))
        self.assertEqual("2025-12-14", filament.manufacturing_date)

    def test_missing_date_falls_back_to_the_sentinel(self):
        filament = self.parser.parseTag(self.scan, _snapmakerImage(mfgDate=None))
        self.assertEqual(
            FilamentTagConstants.NO_MANUFACTURING_DATE, filament.manufacturing_date
        )


def _tigerTagImage(
    magic=0x5BF59264, product=0xFFFFFFFF, material=18775, diameter=56,
    measure=1000, unit=21, nozzleMin=190, nozzleMax=230, bedMin=50, bedMax=60,
    dryTemp=55, dryTime=6, rgba=(0xE7, 0x2F, 0x1D, 0xFF), pagePrefix=True
):
    """A synthetic TigerTag, built from the layout in TigerTag-SDK-Python's tag.py.

    All multi-byte fields are big-endian, and the layout starts at user memory (page 4) -
    the 16-byte prefix stands in for pages 0-3, which is what the reader actually returns.
    """
    body = bytearray(80)
    struct.pack_into(">I", body, 0, magic)
    struct.pack_into(">I", body, 4, product)
    struct.pack_into(">H", body, 8, material)
    body[13] = diameter
    body[16], body[17], body[18], body[19] = rgba
    body[20] = (measure >> 16) & 0xFF
    body[21] = (measure >> 8) & 0xFF
    body[22] = measure & 0xFF
    body[23] = unit
    struct.pack_into(">H", body, 24, nozzleMin)
    struct.pack_into(">H", body, 26, nozzleMax)
    body[28], body[29] = dryTemp, dryTime
    body[30], body[31] = bedMin, bedMax
    return (bytes(16) if pagePrefix else b"") + bytes(body)


class TestTigerTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.TigerTagTagParser()
        self.scan = ntagScan()

    def test_parses_a_valid_tag(self):
        filament = self.parser.parseTag(self.scan, _tigerTagImage())
        self.assertIsNotNone(filament)
        self.assertEqual("PE-CF", filament.type)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(1000, filament.weight_grams)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)
        self.assertEqual(60, filament.bed_temp_c)
        self.assertEqual(0xFFE72F1D, filament.colors[0])

    def test_bed_min_and_max_are_kept_separate_not_collapsed(self):
        # Regression guard: TigerTag carries bedMin and bedMax as two distinct bytes on
        # the tag (unlike most other formats, which only have one bed value). Before
        # bed_min_temp_c/bed_max_temp_c existed on GenericFilament, both were collapsed
        # into the single bed_temp_c, and a write-then-read round trip silently flattened
        # a spool's minBedTemperature/maxBedTemperature/bedTemperature to the same number
        # (reported by a user comparing dev271's write against the read-back diff).
        filament = self.parser.parseTag(
            self.scan, _tigerTagImage(bedMin=50, bedMax=70)
        )
        self.assertEqual(70, filament.bed_temp_c)  # unchanged: max wins as the "target"
        self.assertEqual(50, filament.bed_min_temp_c)
        self.assertEqual(70, filament.bed_max_temp_c)

    def test_bed_min_equal_to_max_still_reports_both(self):
        filament = self.parser.parseTag(
            self.scan, _tigerTagImage(bedMin=55, bedMax=55)
        )
        self.assertEqual(55, filament.bed_min_temp_c)
        self.assertEqual(55, filament.bed_max_temp_c)

    def test_offsets_are_relative_to_user_memory(self):
        # The reader returns the tag from page 0; the layout starts at page 4. Handing the
        # parser a dump without those 16 bytes must not accidentally parse - that is the
        # mistake this format invites, and it would misread every single field.
        self.assertIsNone(
            self.parser.parseTag(self.scan, _tigerTagImage(pagePrefix=False))
        )

    def test_weight_unit_is_honoured(self):
        # measure is a bare number; the unit lives in a separate id. Reading it as grams is
        # wrong by a factor of 1000 for a tag that states kilograms.
        inGrams = self.parser.parseTag(self.scan, _tigerTagImage(measure=1, unit=21))
        inKilos = self.parser.parseTag(self.scan, _tigerTagImage(measure=1, unit=35))
        self.assertEqual(1, inGrams.weight_grams)
        self.assertEqual(1000, inKilos.weight_grams)

    def test_length_unit_leaves_the_weight_unset(self):
        # A length cannot be converted to a weight without a density, so it must stay unset
        # rather than be reported as if it were grams.
        inMetres = self.parser.parseTag(self.scan, _tigerTagImage(measure=330, unit=149))
        self.assertIsNone(inMetres.weight_grams)

    def test_rejects_foreign_and_blank_tags(self):
        self.assertIsNone(
            self.parser.parseTag(self.scan, _tigerTagImage(magic=0x11223344))
        )
        # Initialised but never programmed - recognized, but carries no filament data.
        self.assertIsNone(
            self.parser.parseTag(self.scan, _tigerTagImage(magic=0x6C41A2E1))
        )
        self.assertIsNone(self.parser.parseTag(self.scan, _tigerTagImage(product=0)))

    def test_rejects_truncated_and_implausible_input(self):
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(50)))
        self.assertIsNone(
            self.parser.parseTag(
                self.scan, _tigerTagImage(nozzleMin=250, nozzleMax=200)
            )
        )

    def test_unknown_material_id_degrades_instead_of_failing(self):
        # The shipped tables are a snapshot and will age; an unknown id must cost a label,
        # not the whole tag.
        filament = self.parser.parseTag(self.scan, _tigerTagImage(material=9999))
        self.assertIsNotNone(filament)
        self.assertEqual("Unknown(9999)", filament.type)


FilamentTagKeys = _loadModule("FilamentTagKeys")


def _bambuImage(
    materialId=b"GFA50   ", filamentType=b"PLA", detailedType=b"PLA Basic",
    rgba=(0xF4, 0xC0, 0x32, 0xFF), weight=1000, diameter=1.75,
    dryTemp=55, dryHours=8, bedTemp=60, hotendMax=230, hotendMin=190
):
    """A synthetic Bambu 1K image, per the layout in Bambu-Research-Group/RFID-Tag-Guide."""
    image = bytearray(1024)
    image[24:32] = materialId[:8].ljust(8, b"\x00")
    image[32 : 32 + len(filamentType)] = filamentType
    image[64 : 64 + len(detailedType)] = detailedType
    image[80], image[81], image[82], image[83] = rgba
    struct.pack_into("<H", image, 84, weight)
    # Eight bytes: a double. Writing a float32 here is the mistake the parser guards against.
    struct.pack_into("<d", image, 88, diameter)
    struct.pack_into("<H", image, 96, dryTemp)
    struct.pack_into("<H", image, 98, dryHours)
    struct.pack_into("<H", image, 102, bedTemp)
    struct.pack_into("<H", image, 104, hotendMax)
    struct.pack_into("<H", image, 106, hotendMin)
    return bytes(image)


class TestBambuTagParser(unittest.TestCase):
    def setUp(self):
        self.scan = ScanResult(
            TagType.MIFARE_CLASSIC_1K, bytes.fromhex("04AABBCCDDEE80")
        )
        # Not a real Bambu salt - any value works here, since no reference checksum is
        # configured and the derivation only has to be exercised, not be correct.
        self.keyStore = FilamentTagKeys.FilamentTagKeyStore(
            {FilamentTagKeys.KEY_BAMBU_SALT: "9a759cf2c4f7caff222cb9769b41bc96"}
        )
        self.parser = FilamentTagParsers.BambuTagParser(self.keyStore)

    def test_disables_itself_without_a_key(self):
        # The behaviour upstream relies on: no key means the parser never claims a tag, and
        # the dispatch skips it without needing a special case.
        parser = FilamentTagParsers.BambuTagParser(None)
        self.assertFalse(parser.enabled)
        self.assertIsNone(parser.parseTag(self.scan, _bambuImage()))
        self.assertIsNone(parser.authenticationKeys(self.scan))

    def test_parses_a_valid_tag(self):
        filament = self.parser.parseTag(self.scan, _bambuImage())
        self.assertIsNotNone(filament)
        self.assertEqual("Bambu Lab", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(["Basic"], filament.modifiers)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)

    def test_diameter_is_read_as_a_double(self):
        # Reading these eight bytes with a float32 helper yields a number, just not this one.
        filament = self.parser.parseTag(self.scan, _bambuImage(diameter=2.85))
        self.assertAlmostEqual(2.85, filament.diameter_mm, places=6)

    def test_drying_time_stays_in_hours(self):
        filament = self.parser.parseTag(self.scan, _bambuImage(dryHours=8))
        self.assertEqual(8, filament.drying_time_hours)
        self.assertEqual(55, filament.drying_temp_c)

    def test_derives_sixteen_per_sector_keys(self):
        keys = self.parser.authenticationKeys(self.scan)
        self.assertEqual(16, len(keys))
        self.assertEqual(16, len(set(keys)))

    def test_rejects_implausible_content(self):
        self.assertIsNone(
            self.parser.parseTag(self.scan, _bambuImage(materialId=b"XX999   "))
        )
        self.assertIsNone(
            self.parser.parseTag(
                self.scan, _bambuImage(filamentType=b"", detailedType=b"")
            )
        )
        self.assertIsNone(
            self.parser.parseTag(self.scan, _bambuImage(hotendMin=20, hotendMax=30))
        )
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(50)))


if __name__ == "__main__":
    unittest.main()
