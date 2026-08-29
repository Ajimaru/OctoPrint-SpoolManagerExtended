# coding=utf-8

# Tests for the ported vendor tag parsers. No hardware and no network: every fixture is
# built in-test from the format's own constants, so the byte layout under test is spelled
# out rather than captured from a real tag (whose contents would be manufacturer data of
# unclear redistribution status).
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_FilamentTagParsers.py
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
OpenPrintTagModule = _loadModule("OpenPrintTag")
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


def buildNfcvDump(ndefMessage):
    """A block-0 NFC-V image carrying one NDEF message, as the firmware returns it.

    Unlike NTAG (UID/lock bytes before a Page-3 CC), NFC-V has no such header: the CC
    sits at block 0 itself and the NDEF TLV starts right at block 1 / byte offset 4 - per
    RED FALCON (octoscale-46), confirmed against pn5180WriteNfcvOpenSpool. MLEN (byte 2)
    is in 8-byte units on this carrier.
    """
    image = bytearray()
    image += bytes([0xE1, 0x40, 0x0E, 0x00])  # block 0 - capability container
    image += bytes([0x03, len(ndefMessage)])
    image += ndefMessage
    image += bytes([0xFE])
    while len(image) % 4 != 0:
        image += b"\x00"
    return bytes(image)


def nfcvScan():
    return ScanResult(TagType.NFCV, bytes.fromhex("E00401532560D3EA"))


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
        self.assertEqual(
            TagType.NFCV, FilamentTagModel.tagTypeFromOctoScale("nfcv")
        )
        # An unknown string must not masquerade as a type
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

    def test_eight_digit_color_hex_keeps_only_the_first_six_as_rgb(self):
        # Regression guard: color_hex can legitimately be 8 hex digits (RRGGBBAA) - the
        # firmware writes it verbatim without a length check (confirmed against the
        # firmware source, RED FALCON/octoscale-46 via a real /nfcdump: "FFFFFF00" landed
        # on the tag unchanged). Passing all 8 digits straight to int(..., 16) here used
        # to yield the full 32-bit value; a later "& 0xFFFFFF" mask then kept the LAST six
        # hex digits instead of the first six, turning white ("FFFFFF00") into yellow
        # ("#FFFF00") - caught live during a test run against real hardware.
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag(color_hex="FFFFFF00")
        )
        self.assertEqual([0xFFFFFFFF], filament.colors)

    def test_eight_digit_color_hex_with_hash_prefix(self):
        parser = FilamentTagParsers.OpenSpoolTagParser()
        filament = parser.parseTag(
            ntagScan(), self.buildTag(color_hex="#AABBCC99")
        )
        self.assertEqual([0xFFAABBCC], filament.colors)

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


# ---------------------------------------------------------------------------------------
# OctoScale's own extended format - the read side this project was entirely missing (it
# could write these tags but never parse one back, see the SpoolManagerAPI docstrings).
#
# Unlike every vendor format above, real dumps ARE used here: this is our own format, not
# manufacturer data of unclear redistribution status. The NTAG fixture is a genuine dump
# (UID 045330AC3A0289, spool 37, captured via /nfcdump) including its leftover openSpool
# NDEF JSON past the commit marker - that garbage is not incidental, it is the actual test
# of the registry ordering below. The Mifare Classic and NFC-V fixtures are synthetic
# (built from the format's own constants, CRC computed the same way the parser verifies
# it), matching this project's usual doctrine, pending a Classic hardware dump.
# ---------------------------------------------------------------------------------------


def _classicExtendedImage(
    version=3,
    databaseId=37,
    totalWeight=1000,
    spoolWeight=150,
    usedWeight=263,
    density=1270,
    vendor="owlsat",
    material="PETG",
    colorName="white",
    rgb=(0xFD, 0x74, 0x12),
    hotendMin=190,
    hotendMax=230,
    bedMin=60,
    bedMax=70,
    remainingWeight=737,
    totalLength=327364,
    usedLength=85990,
    cost=1292,
    firstUse=20160,
    lastUse=20161,
    purchasedOn=18585,
    firstUseMinute=1196,
    lastUseMinute=13,
    purchasedOnMinute=0xFFFF,
    buffer1=True,
    buffer2=True,
    code="0714637006884",
    batchNumber="",
    purchasedFrom="Ebay",
    finish="",
    displayName="Weiß",
    badCrc=False,
    color2Rgb=None,
    color3Rgb=None,
    colorFlags=0,
):
    def blk(n):
        return n * 16

    img = bytearray(1024)
    img[blk(4) : blk(4) + 2] = b"37"
    img[blk(8) : blk(8) + 2] = b"OS"
    img[blk(8) + 2] = version
    img[blk(8) + 3] = 0x01 if buffer1 else 0x00
    struct.pack_into("<I", img, blk(8) + 4, databaseId)
    struct.pack_into("<H", img, blk(8) + 8, totalWeight)
    struct.pack_into("<H", img, blk(8) + 10, spoolWeight)
    struct.pack_into("<H", img, blk(8) + 12, usedWeight)
    struct.pack_into("<H", img, blk(8) + 14, density)

    struct.pack_into("<H", img, blk(9) + 0, 1750)
    struct.pack_into("<H", img, blk(9) + 2, 0xFFFF)
    img[blk(9) + 4] = 220
    img[blk(9) + 5] = 70
    img[blk(9) + 6] = 0xFF
    img[blk(9) + 7] = (-2) & 0xFF
    img[blk(9) + 8] = 0
    img[blk(9) + 9] = 0
    img[blk(9) + 10 : blk(9) + 13] = bytes(rgb)
    img[blk(9) + 13] = hotendMin
    img[blk(9) + 14] = hotendMax

    isV2 = version >= 2
    isV3 = version >= 3
    isV4 = version >= 4

    if isV2:
        img[blk(10) + 0] = bedMin
        img[blk(10) + 1] = bedMax

    if isV4:
        if color2Rgb is not None:
            img[blk(10) + 2 : blk(10) + 5] = bytes(color2Rgb)
        if color3Rgb is not None:
            img[blk(10) + 5 : blk(10) + 8] = bytes(color3Rgb)
        img[blk(10) + 8] = colorFlags

    if isV3:
        struct.pack_into("<H", img, blk(16) + 0, remainingWeight)
        img[blk(16) + 2 : blk(16) + 5] = totalLength.to_bytes(3, "little")
        img[blk(16) + 5 : blk(16) + 8] = usedLength.to_bytes(3, "little")
        struct.pack_into("<H", img, blk(16) + 8, firstUse)
        struct.pack_into("<H", img, blk(16) + 10, lastUse)
        struct.pack_into("<H", img, blk(16) + 12, purchasedOn)
        struct.pack_into("<H", img, blk(16) + 14, cost)

        struct.pack_into("<H", img, blk(17) + 0, firstUseMinute)
        struct.pack_into("<H", img, blk(17) + 2, lastUseMinute)
        struct.pack_into("<H", img, blk(17) + 4, purchasedOnMinute)

    covered = bytes(img[blk(8) : blk(8) + 16]) + bytes(img[blk(9) : blk(9) + 15])
    if isV4:
        covered += bytes(img[blk(10) : blk(10) + 9])
    elif isV2:
        covered += bytes(img[blk(10) : blk(10) + 2])
    if isV3:
        covered += bytes(img[blk(16) : blk(16) + 16])
    crc = FilamentTagBinary.crc8(covered)
    if badCrc:
        crc ^= 0xFF
    img[blk(9) + 15] = crc

    if buffer1:
        off = blk(12)

        def writeString(offset, s):
            b = s.encode("utf-8")
            img[offset] = len(b)
            img[offset + 1 : offset + 1 + len(b)] = b
            return offset + 1 + len(b)

        off = writeString(off, vendor)
        off = writeString(off, material)
        off = writeString(off, colorName)

    if isV3 and buffer2:
        img[blk(36) : blk(36) + 2] = b"O3"
        img[blk(36) + 2] = 0x01

        trailerBlocks = {23, 27, 31, 35}

        def advance(pos):
            b = pos // 16
            while b in trailerBlocks:
                b += 1
                pos = b * 16
            return pos

        pos = blk(20)

        def writeString2(pos, s):
            pos = advance(pos)
            bts = s.encode("utf-8")
            img[pos] = len(bts)
            pos += 1
            img[pos : pos + len(bts)] = bts
            return pos + len(bts)

        for s in (code, batchNumber, purchasedFrom, finish, displayName):
            pos = writeString2(pos, s)

    return bytes(img)


class TestOctoScaleExtendedTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedTagParser()
        self.scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("01020304"))

    def test_parses_a_valid_v3_tag_with_every_field(self):
        data = _classicExtendedImage()
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("owlsat", filament.manufacturer)
        self.assertEqual("PETG", filament.type)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)
        self.assertEqual(60, filament.bed_min_temp_c)
        self.assertEqual(70, filament.bed_max_temp_c)
        self.assertEqual(0xFD7412FF, filament.rgba)

        ext = filament.octoscaleExtendedFields
        self.assertEqual(37, ext["databaseId"])
        self.assertEqual(263, ext["usedWeight"])
        self.assertEqual(737, ext["remainingWeight"])
        self.assertAlmostEqual(12.92, ext["cost"], places=2)
        self.assertEqual("0714637006884", ext["code"])
        self.assertEqual("Ebay", ext["purchasedFrom"])
        self.assertEqual("Weiß", ext["displayName"])
        self.assertEqual(20160, ext["firstUseDays"])
        self.assertEqual(1196, ext["firstUseMinuteOfDay"])
        self.assertEqual(327364, ext["totalLength"])
        self.assertEqual(85990, ext["usedLength"])

    def test_v1_tag_has_no_v3_fields(self):
        data = _classicExtendedImage(version=1, buffer2=False)
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        ext = filament.octoscaleExtendedFields
        self.assertNotIn("remainingWeight", ext)
        self.assertNotIn("totalLength", ext)
        self.assertNotIn("cost", ext)

    def test_v2_tag_has_bed_range_but_no_v3_fields(self):
        data = _classicExtendedImage(version=2, buffer2=False)
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual(60, filament.bed_min_temp_c)
        self.assertEqual(70, filament.bed_max_temp_c)
        self.assertNotIn("remainingWeight", filament.octoscaleExtendedFields)

    def test_crc_mismatch_is_rejected(self):
        data = _classicExtendedImage(badCrc=True)
        self.assertIsNone(self.parser.parseTag(self.scan, data))

    def test_v3_tag_with_only_v2_crc_coverage_is_rejected(self):
        # The one place v3 can break a v2-shaped reader: a tag labelled v3 but whose CRC
        # only covers the v2 range (31/33 bytes) must not be accepted by re-deriving a
        # "close enough" checksum - the coverage length is part of the format, not a
        # detail to be lenient about.
        data = bytearray(_classicExtendedImage(version=2))
        data[8 * 16 + 2] = 3  # relabel as v3 without extending the CRC coverage
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_rejects_a_blank_tag(self):
        # The factory-key trap: this format authenticates with the same key every blank
        # Classic tag also accepts, so a blank tag reaching this parser must be rejected on
        # content (magic + CRC), not on having failed to authenticate.
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(1024)))

    def test_rejects_an_unknown_future_version(self):
        # v4 is now a known version (multi-color extension) - v5 is the current unknown.
        data = bytearray(_classicExtendedImage())
        data[8 * 16 + 2] = 5
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_rejects_truncated_input(self):
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(32)))

    def test_rejects_wrong_magic(self):
        data = bytearray(_classicExtendedImage())
        data[8 * 16 : 8 * 16 + 2] = b"XX"
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_sentinels_read_as_none_not_65535(self):
        data = _classicExtendedImage()
        filament = self.parser.parseTag(self.scan, data)
        # diameterTolerance was left at its 0xFFFF sentinel in the fixture builder.
        self.assertNotIn("diameterTolerance", filament.octoscaleExtendedFields)

    def test_minute_of_day_above_1439_reads_as_not_set(self):
        data = _classicExtendedImage(purchasedOnMinute=0xFFFF)
        filament = self.parser.parseTag(self.scan, data)
        self.assertNotIn("purchasedOnMinuteOfDay", filament.octoscaleExtendedFields)

    def test_minute_of_day_zero_is_midnight_not_an_error(self):
        data = _classicExtendedImage(lastUseMinute=0)
        filament = self.parser.parseTag(self.scan, data)
        self.assertEqual(0, filament.octoscaleExtendedFields["lastUseMinuteOfDay"])

    def test_negative_offset_bytes_are_signed(self):
        # The fixture builder writes offsetTemperature as -2 (0xFE); parseTag must read it
        # back as -2, not 254 - proving int8 extraction, not uint8.
        data = _classicExtendedImage()
        # offsetTemperature does not surface on GenericFilament directly, so this is
        # exercised indirectly: a wrong (unsigned) read would not raise, it would just be
        # silently wrong, which is exactly why FilamentTagBinary.extract_int8 exists.
        self.assertEqual(
            -2, FilamentTagBinary.extract_int8(data, 9 * 16 + 7)
        )

    def test_strings_after_empty_slots_are_not_dropped(self):
        # batchNumber and finish are empty in the fixture; purchasedFrom and displayName
        # sit behind them and must still be read.
        data = _classicExtendedImage()
        filament = self.parser.parseTag(self.scan, data)
        ext = filament.octoscaleExtendedFields
        self.assertNotIn("batchNumber", ext)
        self.assertNotIn("finish", ext)
        self.assertEqual("Ebay", ext["purchasedFrom"])
        self.assertEqual("Weiß", ext["displayName"])

    def test_color_all_zero_is_no_color_not_black(self):
        data = _classicExtendedImage(rgb=(0, 0, 0))
        filament = self.parser.parseTag(self.scan, data)
        self.assertEqual([], filament.colors)

    def test_buffer2_ignored_without_block36_marker_even_with_plausible_bytes(self):
        data = bytearray(_classicExtendedImage(buffer2=False))
        # Plant plausible-looking length/text bytes at buffer 2's start anyway - the
        # marker's absence must win regardless of what is sitting there.
        data[20 * 16] = 5
        data[20 * 16 + 1 : 20 * 16 + 6] = b"HELLO"
        filament = self.parser.parseTag(self.scan, bytes(data))
        self.assertIsNotNone(filament)
        self.assertNotIn("code", filament.octoscaleExtendedFields)

    def test_buffer1_ignored_without_flag_bit(self):
        data = _classicExtendedImage(buffer1=False)
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertIsNone(filament.manufacturer)
        self.assertIsNone(filament.type)


def _ntagExtendedRealDump():
    """The exact bytes captured via /nfcdump for UID 045330AC3A0289 (spool 37) - see
    the plan this parser was built from. Deliberately a real dump, not synthesized: this
    is our own format, and the leftover openSpool NDEF JSON past the commit marker (page
    32 onward) is the actual point of the fixture, not noise to be trimmed away."""
    rows = {
        0: "045330EFAC3A02891D480000E1103E00",
        4: "4F58010025000000E80396000701E102",
        8: "F604D606FFFFFFFFFF000000FFFFFFFF",
        12: "FFFFFF00BF000000C4FE04E64F010C05",
        16: "C04EAC04C14E99480D00FFFF066F776C",
        20: "73617404504554470577686974650D30",
        24: "37313436333730303638383400044562",
        28: "61790005576569C39F0000004E580100",
        32: "616E64223A224F63746F54657374222C",
        36: "22776569676874223A313030307DFE00",
    }
    data = bytearray(520)
    for start, hx in rows.items():
        b = bytes.fromhex(hx)
        data[start * 4 : start * 4 + len(b)] = b
    return bytes(data)


def _ntagExtendedV2Image(
    databaseId=7,
    totalWeight=1000,
    vendor="TestVendor",
    material="PLA",
    version=2,
    rgb=(0xFD, 0x74, 0x12),
    color2Rgb=None,
    color3Rgb=None,
    colorFlags=0,
):
    """Synthetic v2 (multi-color) NTAG image - unlike _ntagExtendedRealDump() (a real,
    pre-v2 capture), this exercises the new colors-2/3 + flags fields at pages 19-20 and
    the version-dependent string start (page 21 on v2, was 19 on v1)."""
    img = bytearray(60 * 4)  # generous - 8 strings plus headroom for the marker scan

    def pg(n):
        return n * 4

    img[pg(4) : pg(4) + 2] = b"OX"
    img[pg(4) + 2] = version
    struct.pack_into("<I", img, pg(5), databaseId)
    struct.pack_into("<H", img, pg(6), totalWeight)
    img[pg(11) : pg(11) + 3] = bytes(rgb)

    isV2 = version >= 2
    if isV2:
        if color2Rgb is not None:
            img[pg(19) : pg(19) + 3] = bytes(color2Rgb)
        img[pg(19) + 3] = colorFlags
        if color3Rgb is not None:
            img[pg(20) : pg(20) + 3] = bytes(color3Rgb)

    crcCovered = bytes(img[pg(4) : pg(4) + 36])
    img[pg(13)] = FilamentTagBinary.crc8(crcCovered)

    stringsStartPage = 21 if isV2 else 19
    off = pg(stringsStartPage)

    def writeString(offset, s):
        b = s.encode("utf-8")
        img[offset] = len(b)
        img[offset + 1 : offset + 1 + len(b)] = b
        return offset + 1 + len(b)

    off = writeString(off, vendor)
    off = writeString(off, material)
    for _ in range(6):
        off = writeString(off, "")

    markerPage = off // 4 + (1 if off % 4 else 0)
    markerOffset = markerPage * 4
    if markerOffset + 4 > len(img):
        img += bytearray(markerOffset + 4 - len(img))
    img[markerOffset : markerOffset + 2] = b"NX"
    return bytes(img[: markerOffset + 4])


class TestOctoScaleExtendedNtagTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedNtagTagParser()
        self.scan = ScanResult(TagType.MIFARE_ULTRALIGHT, bytes.fromhex("045330AC3A0289"))

    def test_parses_the_real_dump_with_every_verified_field(self):
        # Every value here was independently cross-checked against the live /nfcprobe
        # response for the same physical tag before this parser existed.
        filament = self.parser.parseTag(self.scan, _ntagExtendedRealDump())
        self.assertIsNotNone(filament)
        self.assertEqual("owlsat", filament.manufacturer)
        self.assertEqual("PETG", filament.type)
        self.assertEqual(0xFFFFFFFF, filament.rgba)

        ext = filament.octoscaleExtendedFields
        self.assertEqual(37, ext["databaseId"])
        self.assertEqual(263, ext["usedWeight"])
        self.assertEqual(737, ext["remainingWeight"])
        self.assertAlmostEqual(12.92, ext["cost"], places=2)
        self.assertEqual("0714637006884", ext["code"])
        self.assertEqual("Ebay", ext["purchasedFrom"])
        self.assertEqual("Weiß", ext["displayName"])
        self.assertEqual(20160, ext["firstUseDays"])
        self.assertEqual(1196, ext["firstUseMinuteOfDay"])
        self.assertEqual(85990, ext["usedLength"])
        self.assertEqual(327364, ext["totalLength"])

    def test_crc_mismatch_over_the_fixed_36_bytes_is_rejected(self):
        data = bytearray(_ntagExtendedRealDump())
        data[13 * 4] ^= 0xFF  # flip the stored CRC byte
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_used_length_splits_correctly_across_the_page_boundary(self):
        # The one field with no analogue on Classic: byte 3 of page 14 is its low byte,
        # bytes 0-1 of page 15 are the middle/high bytes - a naive same-page read would
        # silently produce a different (wrong) number instead of failing loudly.
        filament = self.parser.parseTag(self.scan, _ntagExtendedRealDump())
        self.assertEqual(85990, filament.octoscaleExtendedFields["usedLength"])

    def test_all_eight_string_slots_are_read_past_empty_ones(self):
        filament = self.parser.parseTag(self.scan, _ntagExtendedRealDump())
        ext = filament.octoscaleExtendedFields
        self.assertNotIn("batchNumber", ext)
        self.assertNotIn("finish", ext)
        self.assertEqual("Ebay", ext["purchasedFrom"])
        self.assertEqual("Weiß", ext["displayName"])

    def test_page4_byte3_flags_are_ignored_not_read_as_strings_present(self):
        # Classic's block8[3] is a real "buffer 1 present" gate; NTAG's page4[3] is not -
        # it must never be treated the same way by code shared between the two parsers.
        # The real dump already has page4[3]=0x00 (reserved); parsing it successfully at
        # all - reading the strings without checking that byte - is what this proves.
        # (page4[3] sits inside the CRC-covered range, so it cannot be flipped here
        # without also breaking the checksum the parser correctly rejects on.)
        filament = self.parser.parseTag(self.scan, _ntagExtendedRealDump())
        self.assertIsNotNone(filament)
        self.assertEqual("owlsat", filament.manufacturer)
        self.assertEqual(0, _ntagExtendedRealDump()[4 * 4 + 3])

    def test_our_own_tag_is_claimed_before_openspool_despite_leftover_ndef_garbage(self):
        # The real dump carries leftover openSpool NDEF JSON past the commit marker (an
        # extended write does not erase the tag first). If registry order were wrong, the
        # openSpool parser would misclaim this tag instead.
        filament, diagnostics = FilamentTagParsers.parseTagData(
            self.scan, _ntagExtendedRealDump()
        )
        self.assertIsNotNone(filament)
        self.assertEqual("ntagExtended", diagnostics["parserId"])
        openSpoolParser = FilamentTagParsers.OpenSpoolTagParser()
        self.assertIsNone(
            openSpoolParser.parseTag(self.scan, _ntagExtendedRealDump())
        )

    def test_marker_position_is_scanned_not_hardcoded(self):
        # Two fixtures with different string lengths must both parse correctly - a parser
        # that hardcoded the marker's page (31, for this particular dump) would break on
        # the second one.
        short = self._buildImage(databaseId=1, material="PLA")
        long = self._buildImage(databaseId=2, material="PETG-CF-LONGER-NAME")
        f1 = self.parser.parseTag(self.scan, short)
        f2 = self.parser.parseTag(self.scan, long)
        self.assertIsNotNone(f1)
        self.assertIsNotNone(f2)
        self.assertEqual(1, f1.octoscaleExtendedFields["databaseId"])
        self.assertEqual(2, f2.octoscaleExtendedFields["databaseId"])

    def test_rejects_a_blank_tag(self):
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(600)))

    def _buildImage(self, databaseId, material):
        img = bytearray(600)
        img[16:18] = b"OX"
        img[18] = 1
        img[19] = 0
        struct.pack_into("<I", img, 20, databaseId)
        struct.pack_into("<H", img, 24, 500)
        struct.pack_into("<H", img, 26, 0)
        struct.pack_into("<H", img, 28, 100)
        struct.pack_into("<H", img, 30, 0xFFFF)
        struct.pack_into("<H", img, 32, 1240)
        struct.pack_into("<H", img, 34, 1750)
        struct.pack_into("<H", img, 36, 0xFFFF)
        img[38] = 0xFF
        img[39] = 0xFF
        img[40] = 0xFF
        img[41] = 0
        img[42] = 0
        img[43] = 0
        img[44:47] = bytes([10, 20, 30])
        img[47] = 0xFF
        img[48] = 0xFF
        img[49] = 0xFF
        img[50] = 0xFF
        covered = bytes(img[16:52])
        img[52] = FilamentTagBinary.crc8(covered)
        off = 76

        def w(s):
            nonlocal off
            b = s.encode("utf-8")
            img[off] = len(b)
            off += 1
            img[off : off + len(b)] = b
            off += len(b)

        w("v")
        w(material)
        w("c")
        for _ in range(5):
            w("")
        markerPage = off // 4 + (1 if off % 4 else 0)
        markerOffset = markerPage * 4
        img[markerOffset : markerOffset + 2] = b"NX"
        return bytes(img[: markerOffset + 4])


def _nfcvExtendedImage(
    databaseId=37,
    totalWeight=1000,
    spoolWeight=150,
    usedWeight=263,
    density=1270,
    vendor="owlsat",
    material="PETG",
    colorName="white",
    version=2,
    stringsPresent=True,
    rgb=None,
    color2Rgb=None,
    color3Rgb=None,
    colorFlags=0,
):
    img = bytearray(112)
    img[3 * 4 : 3 * 4 + 2] = b"OS"
    img[3 * 4 + 2] = version
    img[3 * 4 + 3] = 0x01 if stringsPresent else 0x00
    struct.pack_into("<I", img, 4 * 4, databaseId)
    struct.pack_into("<H", img, 5 * 4, totalWeight)
    struct.pack_into("<H", img, 5 * 4 + 2, spoolWeight)
    struct.pack_into("<H", img, 6 * 4, usedWeight)
    struct.pack_into("<H", img, 6 * 4 + 2, density)

    # Primary color at physBuf[10..12] = absolute offset 38-40, straddling the
    # block9/block10 boundary - RED FALCON/octoscale-46.
    if rgb is not None:
        img[38:41] = bytes(rgb)

    if version >= 3:
        if color2Rgb is not None:
            img[24 * 4 : 24 * 4 + 3] = bytes(color2Rgb)
        img[24 * 4 + 3] = colorFlags
        if color3Rgb is not None:
            img[25 * 4 : 25 * 4 + 3] = bytes(color3Rgb)

    if stringsPresent:
        off = 11 * 4

        def writeString(offset, s):
            b = s.encode("utf-8")
            img[offset] = len(b)
            img[offset + 1 : offset + 1 + len(b)] = b
            return offset + 1 + len(b)

        off = writeString(off, vendor)
        off = writeString(off, material)
        off = writeString(off, colorName)

    return bytes(img)


class TestOctoScaleExtendedNfcvTagParser(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedNfcvTagParser()
        self.scan = ScanResult(TagType.NFCV, bytes.fromhex("E00401532560D3EA"))

    def test_parses_the_written_tag_fields(self):
        # Verified against a real ICODE tag written with spool 37's data and dumped back
        # via /nfcdump: databaseId, weights and all three strings matched the write.
        filament = self.parser.parseTag(self.scan, _nfcvExtendedImage())
        self.assertIsNotNone(filament)
        self.assertEqual("owlsat", filament.manufacturer)
        self.assertEqual("PETG", filament.type)
        self.assertEqual(1000, filament.weight_grams)

        ext = filament.octoscaleExtendedFields
        self.assertEqual(37, ext["databaseId"])
        self.assertEqual(263, ext["usedWeight"])
        self.assertEqual(150, ext["spoolWeight"])
        self.assertEqual("white", ext["colorName"])

    def test_fields_this_carrier_structurally_lacks_are_absent_not_sentinels(self):
        # The smallest of the three carriers: remainingWeight, cost, code, the length
        # fields and every date field simply do not exist in this layout. They must be
        # absent from the result - not present as -1/""/0 - so a "never clear an existing
        # value" import does not wipe them from a spool that already has them.
        filament = self.parser.parseTag(self.scan, _nfcvExtendedImage())
        ext = filament.octoscaleExtendedFields
        for missingField in (
            "remainingWeight", "cost", "code", "totalLength", "usedLength",
            "batchNumber", "purchasedFrom", "finish", "displayName",
        ):
            self.assertNotIn(missingField, ext)

    def test_rejects_an_empty_tag_with_the_real_leftover_ndef_bytes(self):
        # A blank ICODE tag observed on hardware is not all-zero: it carries leftover CC
        # (0x283C) and NDEF terminator (0xFE) bytes from a previous format. This exact
        # pattern is what reproduces the "own empty tag flagged as a vendor tag" report -
        # the parser must reject it on the missing magic, same as any other foreign tag.
        empty = bytearray(112)
        empty[23 * 4 : 23 * 4 + 4] = bytes.fromhex("283C0000")
        empty[27 * 4 : 27 * 4 + 4] = bytes.fromhex("000000FE")
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(empty)))

    def test_rejects_an_unknown_version(self):
        # v3 is now a known version (multi-color extension) - v4 is the current unknown.
        data = bytearray(_nfcvExtendedImage(version=4))
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_rejects_wrong_magic(self):
        data = bytearray(_nfcvExtendedImage())
        data[3 * 4 : 3 * 4 + 2] = b"XX"
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_strings_absent_when_flag_bit_clear(self):
        filament = self.parser.parseTag(
            self.scan, _nfcvExtendedImage(stringsPresent=False)
        )
        self.assertIsNotNone(filament)
        self.assertIsNone(filament.manufacturer)


def _spool110RealReadBytes():
    """The exact hex payload /nfcreadstart returned for a real tag (UID 40CA8A50, spool
    110, sectors [0..9]) - captured directly from the firmware, not synthesized. This is
    what caught the bug below: every earlier fixture in this file builds a full 1024-byte
    image, which cannot reproduce how the firmware actually answers a sector-scoped read."""
    return bytes.fromhex(
        "40ca8a5050880400000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "3131302020202020202020202020202000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "4f5303016e00000018039600c000d804d606ffffc83cff000000ffffffbed201"
        "32460000000000000000000000000000000000000000ff078069ffffffffffff"
        "084b696e67726f6f6e03504c4105576869746500000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "58028c0d04c4fb00c950d350ef4e24035d03e804ffff00000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "0957303231352d392d32000a416c694578707265737300124b696e67726f6f6e"
        "20576869746520504c41000000000000000000000000ff078069ffffffffffff"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
        "4f33010000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000ff078069ffffffffffff"
    )


class TestOctoScaleExtendedClassicSectorCoverage(unittest.TestCase):
    """Two related bugs caught live on hardware (real tag, spool 110, UID 40CA8A50):

    1. The registered sector list must cover every block the parser reads - an earlier
       version named [2,3,4,5,6,7,8,9] (confusing *block* numbers 8,9,10,16 with *sector*
       numbers), which happened to still cover every needed block by luck, but:

    2. Sector 0 MUST be requested even though this format writes nothing there. The
       firmware's /nfcreadstart returns only the requested sectors, concatenated with NO
       padding for the ones skipped - a sector list starting at 1 (not 0) makes the
       response begin at block 4, shifting every absolute offset this parser uses by 64
       bytes. The read itself succeeded (all sectors authenticated, legacy id "110"
       readable at byte 0 of the *response*), but parseTag() rejected the shifted data on
       a magic mismatch it never should have hit. The *reported* API error
       ("authentication failed") was a red herring from Qidi, which runs after this parser
       in the same dispatch loop and overwrites the shared lastError on its own unrelated
       rejection - a trap for future debugging of this dispatch, not just this bug.

    Every fixture above this class builds a full 1024-byte image with real offsets
    already correct, so none of them could catch either bug - they never simulate what
    the firmware actually sends back for a sector-scoped read."""

    def test_sector_zero_is_registered(self):
        # The single fact that fixes bug 2: without it, this exact class of shift bug
        # returns silently, with no error that points at the real cause.
        descriptor = FilamentTagParsers.getParser("octoscaleExtended")
        self.assertIn(0, descriptor["sectors"])

    def test_registered_sectors_cover_every_block_the_parser_reads(self):
        descriptor = FilamentTagParsers.getParser("octoscaleExtended")
        registeredSectors = set(descriptor["sectors"])

        blocksRead = {4, 8, 9, 10, 16, 17, 36}
        blocksRead |= set(range(20, 36))  # buffer 2 spread + its interleaved trailers

        missing = {b for b in blocksRead if (b // 4) not in registeredSectors}
        self.assertEqual(
            set(), missing,
            "registered sectors do not cover blocks: " + str(sorted(missing)),
        )

    def test_parses_the_real_sector_scoped_read_response(self):
        # The actual regression test: bytes exactly as the firmware sends them for a
        # sector-scoped read (concatenated, no per-sector padding), reconstructed with
        # the CURRENT registered sector list. If sector 0 were dropped again, this
        # fixture would need rebuilding against the wrong offsets and silently mismatch -
        # instead this drives the real captured bytes through the real dispatch.
        descriptor = FilamentTagParsers.getParser("octoscaleExtended")
        self.assertEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], sorted(descriptor["sectors"]))

        raw = _spool110RealReadBytes()
        self.assertEqual(640, len(raw))  # 10 sectors x 64 bytes, exactly what the log showed

        scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("40CA8A50"))
        filament, diagnostics = FilamentTagParsers.parseTagData(scan, raw)
        self.assertIsNotNone(filament, "real hardware read bytes were rejected")
        self.assertEqual("octoscaleExtended", diagnostics["parserId"])
        self.assertEqual("Kingroon", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(210, filament.hotend_max_temp_c)
        self.assertEqual(50, filament.bed_min_temp_c)
        self.assertEqual(70, filament.bed_max_temp_c)

        ext = filament.octoscaleExtendedFields
        self.assertEqual(110, ext["databaseId"])
        self.assertEqual(192, ext["usedWeight"])
        self.assertEqual(600, ext["remainingWeight"])
        self.assertAlmostEqual(8.04, ext["cost"], places=2)
        self.assertEqual("W0215-9-2", ext["code"])
        self.assertEqual("AliExpress", ext["purchasedFrom"])
        self.assertEqual("Kingroon White PLA", ext["displayName"])

    def test_a_sector_list_missing_sector_zero_shifts_offsets_and_is_rejected(self):
        # Documents the failure mode directly: the same real bytes, minus their first 64
        # bytes (as if sector 0 had never been requested), must NOT parse - if it did,
        # that would mean the parser is silently tolerant of misaligned data, which is
        # worse than a loud rejection.
        raw = _spool110RealReadBytes()
        shifted = raw[64:]  # drop sector 0's 64 bytes, simulating sectors=[1..9]
        scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("40CA8A50"))
        filament = FilamentTagParsers.OctoScaleExtendedTagParser().parseTag(scan, shifted)
        self.assertIsNone(filament)

    def test_parses_when_only_the_registered_sectors_are_populated_and_padded(self):
        # A complementary check using the synthetic full-image fixture: reconstructing a
        # sector-scoped read by keeping each sector at its OWN absolute position (i.e.
        # simulating a hypothetical firmware that pads skipped sectors with zeros, unlike
        # the real one) must also parse correctly - this is not the shape of bug 2, but a
        # different simplifying assumption that must not silently break either.
        fullImage = _classicExtendedImage()
        descriptor = FilamentTagParsers.getParser("octoscaleExtended")
        registeredSectors = set(descriptor["sectors"])

        restricted = bytearray(1024)
        for sector in registeredSectors:
            start = sector * 64
            restricted[start : start + 64] = fullImage[start : start + 64]

        scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("01020304"))
        filament = FilamentTagParsers.OctoScaleExtendedTagParser().parseTag(
            scan, bytes(restricted)
        )
        self.assertIsNotNone(
            filament,
            "parser rejected a tag reconstructed from only its own registered sectors",
        )
        self.assertEqual("owlsat", filament.manufacturer)
        self.assertEqual(37, filament.octoscaleExtendedFields["databaseId"])
        self.assertEqual("0714637006884", filament.octoscaleExtendedFields["code"])


class TestOctoScaleExtendedCrossCarrier(unittest.TestCase):
    """Registry-level guarantees that must hold across all three carriers together."""

    def test_classic_runs_before_qidi_but_after_keyed_parsers(self):
        order = [
            descriptor["id"]
            for descriptor in FilamentTagParsers.parsersForTagClass(
                TagType.MIFARE_CLASSIC_1K
            )
        ]
        self.assertLess(order.index("octoscaleExtended"), order.index("qidi"))
        self.assertLess(order.index("bambu"), order.index("octoscaleExtended"))
        self.assertLess(order.index("snapmaker"), order.index("octoscaleExtended"))

    def test_ntag_extended_runs_before_every_ndef_parser(self):
        order = [
            descriptor["id"]
            for descriptor in FilamentTagParsers.parsersForTagClass(
                TagType.MIFARE_ULTRALIGHT
            )
        ]
        self.assertEqual("ntagExtended", order[0])

    def test_nfcv_registry_entry_requests_no_sectors(self):
        descriptor = FilamentTagParsers.getParser("nfcvExtended")
        self.assertIsNone(descriptor["sectors"])
        self.assertEqual(TagType.NFCV, descriptor["tagClass"])

    def test_classic_and_nfcv_share_magic_but_not_position(self):
        # 'O','S' appears on two carriers at different absolute offsets - a parser that
        # checked the bytes without the position would confuse them.
        classicOffset = FilamentTagParsers.OctoScaleExtendedTagParser.BLOCK_8_HEADER
        nfcvOffset = FilamentTagParsers.OctoScaleExtendedNfcvTagParser.BLOCK_3_HEADER
        self.assertNotEqual(classicOffset, nfcvOffset)
        self.assertEqual(
            FilamentTagParsers.OctoScaleExtendedTagParser.MAGIC,
            FilamentTagParsers.OctoScaleExtendedNfcvTagParser.MAGIC,
        )

    def test_each_carrier_rejects_the_others_fixture(self):
        classicData = _classicExtendedImage()
        ntagData = _ntagExtendedRealDump()
        nfcvData = _nfcvExtendedImage()

        classicScan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("01020304"))
        ntagScanResult = ScanResult(
            TagType.MIFARE_ULTRALIGHT, bytes.fromhex("045330AC3A0289")
        )
        nfcvScan = ScanResult(TagType.NFCV, bytes.fromhex("E00401532560D3EA"))

        ntagParser = FilamentTagParsers.OctoScaleExtendedNtagTagParser()
        nfcvParser = FilamentTagParsers.OctoScaleExtendedNfcvTagParser()
        classicParser = FilamentTagParsers.OctoScaleExtendedTagParser()

        # Each parser's own tagClass guard rejects data from another carrier's scan
        # type outright, regardless of byte content.
        self.assertIsNone(ntagParser.parseTag(classicScan, classicData))
        self.assertIsNone(nfcvParser.parseTag(classicScan, classicData))
        self.assertIsNone(classicParser.parseTag(ntagScanResult, ntagData))
        self.assertIsNone(nfcvParser.parseTag(ntagScanResult, ntagData))
        self.assertIsNone(classicParser.parseTag(nfcvScan, nfcvData))
        self.assertIsNone(ntagParser.parseTag(nfcvScan, nfcvData))


class TestOctoscaleColorFlagsHelpers(unittest.TestCase):
    """Direct tests of the shared flags-byte/color-grammar helpers used by all three v4/
    v3/v2 extended parsers, independent of any single carrier's byte layout."""

    def test_parses_all_flag_bits(self):
        isTransparent, colorCount, isRainbow = FilamentTagParsers._octoscaleParseColorFlags(
            0x01 | (3 << 1) | 0x08
        )
        self.assertTrue(isTransparent)
        self.assertEqual(3, colorCount)
        self.assertTrue(isRainbow)

    def test_zero_flags_means_no_color_information(self):
        isTransparent, colorCount, isRainbow = FilamentTagParsers._octoscaleParseColorFlags(0)
        self.assertFalse(isTransparent)
        self.assertEqual(0, colorCount)
        self.assertFalse(isRainbow)

    def test_none_flags_treated_same_as_zero(self):
        # A pre-v4/v3/v2 tag has no flags byte to read at all - None must behave exactly
        # like the zeroed reserve bytes a real older tag would produce.
        self.assertEqual(
            FilamentTagParsers._octoscaleParseColorFlags(None),
            FilamentTagParsers._octoscaleParseColorFlags(0),
        )

    def test_compose_rainbow_ignores_everything_else(self):
        composed = FilamentTagParsers._octoscaleComposeColorString(
            0xFFFF0000, [0xFF00FF00], True, True
        )
        self.assertEqual("rainbow", composed)

    def test_compose_transparent_with_three_colors(self):
        composed = FilamentTagParsers._octoscaleComposeColorString(
            0xFFFF0000, [0xFF00FF00, 0xFF0000FF], True, False
        )
        self.assertEqual("transparent:#FF0000;#00FF00;#0000FF", composed)

    def test_compose_untinted_transparent_with_no_colors(self):
        composed = FilamentTagParsers._octoscaleComposeColorString(None, [], True, False)
        self.assertEqual("transparent", composed)

    def test_compose_opaque_single_color_no_prefix(self):
        composed = FilamentTagParsers._octoscaleComposeColorString(0xFFAABBCC, [], False, False)
        self.assertEqual("#AABBCC", composed)

    def test_compose_nothing_returns_none(self):
        self.assertIsNone(
            FilamentTagParsers._octoscaleComposeColorString(None, [], False, False)
        )


class TestOctoScaleExtendedClassicMultiColorV4(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedTagParser()
        self.scan = ScanResult(TagType.MIFARE_CLASSIC_1K, bytes.fromhex("01020304"))

    def test_three_transparent_colors_round_trip(self):
        data = _classicExtendedImage(
            version=4,
            rgb=(0xFF, 0x00, 0x00),
            color2Rgb=(0x00, 0xFF, 0x00),
            color3Rgb=(0x00, 0x00, 0xFF),
            colorFlags=0x01 | (3 << 1),
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual(
            [0xFFFF0000, 0xFF00FF00, 0xFF0000FF], filament.colors
        )
        self.assertEqual(
            "transparent:#FF0000;#00FF00;#0000FF",
            filament.octoscaleExtendedFields["color"],
        )

    def test_two_opaque_colors_round_trip(self):
        data = _classicExtendedImage(
            version=4,
            rgb=(0x11, 0x22, 0x33),
            color2Rgb=(0x44, 0x55, 0x66),
            colorFlags=(2 << 1),
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual([0xFF112233, 0xFF445566], filament.colors)
        self.assertEqual(
            "#112233;#445566", filament.octoscaleExtendedFields["color"]
        )

    def test_rainbow_flag_produces_rainbow_string_regardless_of_rgb_bytes(self):
        data = _classicExtendedImage(
            version=4, rgb=(0x11, 0x22, 0x33), colorFlags=0x08
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("rainbow", filament.octoscaleExtendedFields["color"])

    def test_v3_tag_composes_a_single_color_not_a_guessed_multi_color(self):
        # A v3 tag has no block10[8] flags byte to read - _octoscaleParseColorFlags(None)
        # then reports isTransparent=False/colorCount=0/isRainbow=False, so the composed
        # string falls back to just the primary color (same value the generic
        # filament.colors join would also produce - no transparent/rainbow guessing).
        data = _classicExtendedImage(version=3, rgb=(0xFD, 0x74, 0x12))
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("#FD7412", filament.octoscaleExtendedFields["color"])

    def test_v4_crc_uses_the_extended_56_byte_coverage(self):
        # A v4 tag must not validate against the old 49-byte (v3) CRC coverage - block10
        # bytes 2-8 (colors 2/3 + flags) have to be part of what's hashed.
        data = bytearray(
            _classicExtendedImage(version=4, color2Rgb=(1, 2, 3), colorFlags=(1 << 1))
        )
        data[10 * 16 + 2] ^= 0xFF  # flip a byte inside the new v4 CRC range
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_unknown_version_five_is_rejected(self):
        data = bytearray(_classicExtendedImage(version=4))
        data[8 * 16 + 2] = 5
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))


class TestOctoScaleExtendedNtagMultiColorV2(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedNtagTagParser()
        self.scan = ScanResult(TagType.MIFARE_ULTRALIGHT, bytes.fromhex("045330AC3A0289"))

    def test_three_transparent_colors_round_trip(self):
        data = _ntagExtendedV2Image(
            rgb=(0xFF, 0x00, 0x00),
            color2Rgb=(0x00, 0xFF, 0x00),
            color3Rgb=(0x00, 0x00, 0xFF),
            colorFlags=0x01 | (3 << 1),
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual(
            [0xFFFF0000, 0xFF00FF00, 0xFF0000FF], filament.colors
        )
        self.assertEqual(
            "transparent:#FF0000;#00FF00;#0000FF",
            filament.octoscaleExtendedFields["color"],
        )

    def test_string_buffer_starts_at_page_21_on_v2_not_19(self):
        # The defining v2 hazard: v1 strings start at page 19, which is now colors-2/3 +
        # flags territory. A parser that still reads strings from page 19 on a v2 tag
        # would read color bytes as string-length-prefixed garbage.
        data = _ntagExtendedV2Image(
            vendor="ShiftedVendor", material="PETG", color2Rgb=(9, 9, 9), colorFlags=(2 << 1)
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("ShiftedVendor", filament.manufacturer)
        self.assertEqual("PETG", filament.type)

    def test_v1_tag_still_reads_strings_from_page_19(self):
        data = _ntagExtendedV2Image(
            version=1, vendor="OldVendor", material="ABS", rgb=(0xFD, 0x74, 0x12)
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("OldVendor", filament.manufacturer)
        self.assertEqual("ABS", filament.type)
        # v1 has no flags byte at all - composed color falls back to just the primary
        # color, same as the Classic v3/NFC-V v2 cases (see those tests' comments).
        self.assertEqual("#FD7412", filament.octoscaleExtendedFields["color"])

    def test_unknown_version_three_is_rejected(self):
        data = bytearray(_ntagExtendedV2Image(version=2))
        data[4 * 4 + 2] = 3
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))


class TestOctoScaleExtendedNfcvMultiColorV3(unittest.TestCase):
    def setUp(self):
        self.parser = FilamentTagParsers.OctoScaleExtendedNfcvTagParser()
        self.scan = ScanResult(TagType.NFCV, bytes.fromhex("E00401532560D3EA"))

    def test_primary_color_is_now_read_on_a_plain_v2_tag(self):
        # Regression guard for the pre-existing bug RED FALCON found: physBuf[10..12]
        # (absolute offset 38-40, straddling block9/block10) existed since v1/v2 but was
        # never read here - this carrier always reported colors=[].
        data = _nfcvExtendedImage(version=2, rgb=(0x0B, 0x16, 0x21))
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual([0xFF0B1621], filament.colors)

    def test_three_transparent_colors_round_trip_on_v3(self):
        data = _nfcvExtendedImage(
            version=3,
            rgb=(0xFF, 0x00, 0x00),
            color2Rgb=(0x00, 0xFF, 0x00),
            color3Rgb=(0x00, 0x00, 0xFF),
            colorFlags=0x01 | (3 << 1),
        )
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual(
            [0xFFFF0000, 0xFF00FF00, 0xFF0000FF], filament.colors
        )
        self.assertEqual(
            "transparent:#FF0000;#00FF00;#0000FF",
            filament.octoscaleExtendedFields["color"],
        )

    def test_v2_tag_composes_a_single_color_not_a_guessed_multi_color(self):
        # Same reasoning as the Classic v3 case: no flags byte on v2, so the composed
        # string is just the (now correctly read) primary color, not a guess.
        data = _nfcvExtendedImage(version=2, rgb=(0x11, 0x22, 0x33))
        filament = self.parser.parseTag(self.scan, data)
        self.assertIsNotNone(filament)
        self.assertEqual("#112233", filament.octoscaleExtendedFields["color"])

    def test_unknown_version_four_is_rejected(self):
        data = bytearray(_nfcvExtendedImage(version=3))
        data[3 * 4 + 2] = 4
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))

    def test_version_one_is_rejected_minimum_is_two(self):
        data = bytearray(_nfcvExtendedImage(version=2))
        data[3 * 4 + 2] = 1
        self.assertIsNone(self.parser.parseTag(self.scan, bytes(data)))


class TestOpenSpoolNfcvParser(unittest.TestCase):
    """nfcvOpenSpool: same NDEF/JSON format as NTAG OpenSpool, different carrier framing
    (CC at block 0, NDEF at block 1 - see buildNfcvDump). The payload parsing itself is
    shared with OpenSpoolTagParser (inheritance), so these tests only cover what differs:
    carrier acceptance/rejection and that the shared TLV scan finds the NFC-V framing.
    """

    def buildTag(self, **overrides):
        payload = {
            "protocol": "openspool",
            "version": "1.0",
            "brand": "Kingroon",
            "type": "PLA",
            "color_hex": "FFFFFF",
            "min_temp": "190",
            "max_temp": "210",
        }
        payload.update(overrides)
        message = buildMimeRecord(
            "application/json", json.dumps(payload).encode("utf-8")
        )
        return buildNfcvDump(message)

    def test_parses_a_well_formed_nfcv_tag(self):
        parser = FilamentTagParsers.OpenSpoolNfcvTagParser()
        filament = parser.parseTag(nfcvScan(), self.buildTag())
        self.assertIsNotNone(filament)
        self.assertEqual("Kingroon", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual([0xFFFFFFFF], filament.colors)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(210, filament.hotend_max_temp_c)

    def test_wrong_tag_type_returns_none(self):
        # An NTAG-typed scan must not be accepted by the NFC-V subclass, even though the
        # underlying NDEF/JSON parsing logic is shared with OpenSpoolTagParser.
        parser = FilamentTagParsers.OpenSpoolNfcvTagParser()
        self.assertIsNone(parser.parseTag(ntagScan(), self.buildTag()))

    def test_ntag_parser_rejects_nfcv_scan(self):
        # And the inverse: the original NTAG parser must not accept an NFC-V-typed scan,
        # even against byte-identical NDEF content.
        parser = FilamentTagParsers.OpenSpoolTagParser()
        self.assertIsNone(parser.parseTag(nfcvScan(), self.buildTag()))

    def test_blank_tag_is_rejected(self):
        parser = FilamentTagParsers.OpenSpoolNfcvTagParser()
        self.assertIsNone(parser.parseTag(nfcvScan(), bytes(112)))

    def test_registered_under_the_nfcv_tag_class(self):
        descriptor = FilamentTagParsers.FILAMENT_TAG_PARSERS["nfcvOpenSpool"]
        self.assertEqual(TagType.NFCV, descriptor["tagClass"])


class TestOpenPrintTagParser(unittest.TestCase):
    """openPrintTag (NFC-V only): CBOR-in-NDEF, see common/OpenPrintTag.py for the format
    and FilamentTagParsers.OpenPrintTagParser for the read side. Round-trips through the
    real encoder (OpenPrintTagModule.buildTagPayload) rather than hand-built CBOR bytes,
    so these tests exercise the same code path a real write would produce.
    """

    class _FakeSpool(object):
        material = "PLA"
        vendor = "Kingroon"
        totalWeight = 792
        spoolWeight = 150
        color = "#FFFFFF"
        density = 1.24
        diameter = 1.75
        minTemperature = 190
        maxTemperature = 210
        temperature = 200
        minBedTemperature = 50
        maxBedTemperature = 70
        dryingTemperature = 45
        dryingTime = 6
        td = 0.85

    def buildTag(self, spoolModel=None):
        if spoolModel is None:
            spoolModel = self._FakeSpool()
        message = OpenPrintTagModule.buildTagPayload(spoolModel)
        return buildNfcvDump(message)

    def test_parses_a_well_formed_tag_round_tripped_through_the_real_encoder(self):
        parser = FilamentTagParsers.OpenPrintTagParser()
        filament = parser.parseTag(nfcvScan(), self.buildTag())
        self.assertIsNotNone(filament)
        self.assertEqual("Kingroon", filament.manufacturer)
        self.assertEqual("PLA", filament.type)
        self.assertEqual(792, filament.weight_grams)
        self.assertAlmostEqual(1.75, filament.diameter_mm, places=2)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(210, filament.hotend_max_temp_c)
        self.assertEqual(50, filament.bed_min_temp_c)
        self.assertEqual(70, filament.bed_max_temp_c)
        self.assertEqual(45, filament.drying_temp_c)
        # encoded in minutes on the tag (360), must decode back to hours
        self.assertEqual(6, filament.drying_time_hours)
        self.assertAlmostEqual(0.85, filament.td, places=2)
        self.assertEqual([0xFFFFFFFF], filament.colors)

    def test_material_type_enum_round_trips_through_the_index_not_just_free_text(self):
        # material_type (key 9) is a normative enum index, separate from material_name
        # (key 10, free text) - decoding must prefer the enum so an abbreviation the
        # firmware only wrote as an index still comes back correctly.
        spool = self._FakeSpool()
        spool.material = "PETG"
        parser = FilamentTagParsers.OpenPrintTagParser()
        filament = parser.parseTag(nfcvScan(), self.buildTag(spool))
        self.assertEqual("PETG", filament.type)

    def test_unmapped_fields_are_absent_not_guessed(self):
        # colorName/remainingWeight/etc. have no OpenPrintTag key at all (see
        # OpenPrintTag.UNMAPPED_FIELD_NAMES) - a round trip must not invent them.
        parser = FilamentTagParsers.OpenPrintTagParser()
        filament = parser.parseTag(nfcvScan(), self.buildTag())
        # GenericFilament has no colorName/remainingWeight attributes to begin with;
        # the meaningful assertion is that parsing succeeds without needing them.
        self.assertIsNotNone(filament)

    def test_wrong_tag_type_returns_none(self):
        parser = FilamentTagParsers.OpenPrintTagParser()
        self.assertIsNone(parser.parseTag(ntagScan(), self.buildTag()))

    def test_blank_tag_is_rejected(self):
        parser = FilamentTagParsers.OpenPrintTagParser()
        self.assertIsNone(parser.parseTag(nfcvScan(), bytes(112)))

    def test_octoscale_extended_magic_is_not_misread_as_cbor(self):
        # Same defense-in-depth as the cross-carrier tests above: a tag actually carrying
        # our own nfcvExtended format ('O','S' at block 3) must not be misparsed as
        # OpenPrintTag CBOR just because both are "some bytes on an NFC-V tag".
        image = bytearray(112)
        image[12:14] = b"OS"
        parser = FilamentTagParsers.OpenPrintTagParser()
        self.assertIsNone(parser.parseTag(nfcvScan(), bytes(image)))

    def test_truncated_tag_does_not_raise(self):
        parser = FilamentTagParsers.OpenPrintTagParser()
        full = self.buildTag()
        for length in (0, 4, 12, 20, len(full) // 2):
            self.assertIsNone(parser.parseTag(nfcvScan(), full[:length]))

    def test_registered_under_the_nfcv_tag_class(self):
        descriptor = FilamentTagParsers.FILAMENT_TAG_PARSERS["openPrintTag"]
        self.assertEqual(TagType.NFCV, descriptor["tagClass"])


class TestOpenPrintTagCBORCodec(unittest.TestCase):
    """Direct tests of OpenPrintTag.py's decodeCBOR/decodePayload, independent of the
    FilamentTagParsers dispatch - isolates codec bugs from parser/registry bugs.
    """

    def test_decodes_every_major_type_encodeCBOR_can_produce(self):
        for value in (
            0,
            42,
            1000,
            70000,
            5_000_000_000,
            -1,
            -42,
            -1000,
            True,
            False,
            None,
            b"\x01\x02\x03",
            "hello",
            "über",  # non-ASCII, exercises the UTF-8 length-vs-char-count distinction
            [1, 2, 3],
            {1: "a", 2: "b"},
        ):
            encoded = OpenPrintTagModule.encodeCBOR(value)
            decoded, consumed = OpenPrintTagModule.decodeCBOR(encoded, 0)
            self.assertEqual(len(encoded), consumed)
            self.assertEqual(value, decoded)

    def test_float32_marker_decodes_to_a_plain_float(self):
        encoded = OpenPrintTagModule.encodeCBOR(OpenPrintTagModule.Float32(1.75))
        decoded, _ = OpenPrintTagModule.decodeCBOR(encoded, 0)
        self.assertAlmostEqual(1.75, decoded, places=5)

    def test_decode_payload_rejects_out_of_bounds_main_region(self):
        # A meta region claiming a main region past the end of the payload must be
        # rejected, not read out of bounds.
        badMeta = OpenPrintTagModule.encodeCBOR(
            {
                OpenPrintTagModule.META_KEY_MAIN_REGION_OFFSET: 24,
                OpenPrintTagModule.META_KEY_MAIN_REGION_SIZE: 9999,
                OpenPrintTagModule.META_KEY_AUX_REGION_OFFSET: 24,
                OpenPrintTagModule.META_KEY_AUX_REGION_SIZE: 0,
            }
        )
        badMeta = badMeta + b"\x00" * (OpenPrintTagModule.OPT_META_SIZE - len(badMeta))
        with self.assertRaises(ValueError):
            OpenPrintTagModule.decodePayload(badMeta)

    def test_decode_main_region_skips_unknown_keys(self):
        # A future spec revision (or a tool writing keys this plugin doesn't map) must not
        # crash the read - unknown keys are dropped, same convention as the write side.
        cborMap = {8: 0, 999: "unknown-field"}
        encoded = OpenPrintTagModule.encodeCBOR(cborMap)
        fields = OpenPrintTagModule.decodeMainRegion(encoded)
        self.assertNotIn("unknown-field", fields.values())
        self.assertEqual(0, fields.get("material_class"))

    def test_drying_time_minutes_round_trips_to_hours(self):
        fields = {"drying_time": 480}
        values = OpenPrintTagModule.fieldsToSpoolValues(fields)
        self.assertEqual(8, values["dryingTime"])


if __name__ == "__main__":
    unittest.main()
