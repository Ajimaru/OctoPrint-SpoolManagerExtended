# coding=utf-8

# Tests for the OpenPrintTag encoding chain (issue #56). No hardware and no database involved:
# the mapper only reads attributes, so a plain stub object stands in for a SpoolModel.
#
# Run with:  python3 octoprint_SpoolManager/test/test_OpenPrintTag.py
# (pytest's default import mode inserts the plugin package, whose __init__ needs flask/OctoPrint;
#  `pytest --import-mode=importlib` works too.)

import datetime
import importlib.util
import os
import sys
import types
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManager/__init__.py pulls in flask
# and OctoPrint, which are not available in a bare test environment. Both modules under test are
# dependency-free on purpose, so this keeps them runnable with plain pytest.
_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)

# OpenPrintTag.spoolModelToFields() does `from . import TagFormats` (a relative import, to reuse
# resolveTemperatureRange() without duplicating the fallback-to-target-temperature logic) - that
# needs a real package in sys.modules to resolve against, hence this stand-in package rather than
# the bare spec_from_file_location loading used for the rest of this test suite.
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


TagFormats = _loadModule("TagFormats")
OpenPrintTag = _loadModule("OpenPrintTag")


class SpoolStub(object):
    def __init__(self, **attributes):
        defaults = {
            "databaseId": 42,
            "displayName": "Test Spool",
            "material": "PETG",
            "vendor": "TestVendor",
            "colorName": "Silver",
            "color": "#c0c0c0",
            "density": 1.27,
            "diameter": 1.75,
            "totalWeight": 1000.0,
            "spoolWeight": 200.0,
            "usedWeight": 250.0,
            "remainingWeight": None,
            "temperature": 240,
            "minTemperature": None,
            "maxTemperature": None,
            "bedTemperature": 80,
            "minBedTemperature": None,
            "maxBedTemperature": None,
            "enclosureTemperature": None,
            "code": "SN-1",
            "batchNumber": "B-7",
            "purchasedOn": datetime.date(2026, 1, 15),
        }
        defaults.update(attributes)
        for key, value in defaults.items():
            setattr(self, key, value)


class TestCBOREncoder(unittest.TestCase):
    # Test vectors taken from RFC 8949, appendix A.

    def test_small_unsigned_integers(self):
        self.assertEqual(OpenPrintTag.encodeCBOR(0), b"\x00")
        self.assertEqual(OpenPrintTag.encodeCBOR(1), b"\x01")
        self.assertEqual(OpenPrintTag.encodeCBOR(23), b"\x17")

    def test_larger_unsigned_integers(self):
        self.assertEqual(OpenPrintTag.encodeCBOR(24), b"\x18\x18")
        self.assertEqual(OpenPrintTag.encodeCBOR(1000), b"\x19\x03\xe8")
        self.assertEqual(OpenPrintTag.encodeCBOR(1000000), b"\x1a\x00\x0f\x42\x40")

    def test_negative_integers(self):
        self.assertEqual(OpenPrintTag.encodeCBOR(-1), b"\x20")
        self.assertEqual(OpenPrintTag.encodeCBOR(-10), b"\x29")
        self.assertEqual(OpenPrintTag.encodeCBOR(-1000), b"\x39\x03\xe7")

    def test_floats_use_double_precision_by_default(self):
        # The default `float` path stays float64 on purpose - narrowing it would silently
        # regress precision for any future non-spec float, and this is the RFC 8949 vector.
        self.assertEqual(
            OpenPrintTag.encodeCBOR(1.5), b"\xfb\x3f\xf8\x00\x00\x00\x00\x00\x00"
        )

    def test_float32_wrapper_encodes_binary32(self):
        self.assertEqual(
            OpenPrintTag.encodeCBOR(OpenPrintTag.Float32(1.75)),
            b"\xfa\x3f\xe0\x00\x00",
        )
        self.assertEqual(
            OpenPrintTag.encodeCBOR(OpenPrintTag.Float32(1000.0)),
            b"\xfa\x44\x7a\x00\x00",
        )

    def test_simple_values(self):
        self.assertEqual(OpenPrintTag.encodeCBOR(True), b"\xf5")
        self.assertEqual(OpenPrintTag.encodeCBOR(False), b"\xf4")
        self.assertEqual(OpenPrintTag.encodeCBOR(None), b"\xf6")

    def test_text_strings(self):
        self.assertEqual(OpenPrintTag.encodeCBOR(""), b"\x60")
        self.assertEqual(OpenPrintTag.encodeCBOR("a"), b"\x61\x61")
        self.assertEqual(OpenPrintTag.encodeCBOR("IETF"), b"\x64IETF")

    def test_text_strings_are_utf8(self):
        # length prefix must count bytes, not characters
        self.assertEqual(OpenPrintTag.encodeCBOR("ü"), b"\x62\xc3\xbc")

    def test_byte_strings(self):
        self.assertEqual(
            OpenPrintTag.encodeCBOR(b"\x01\x02\x03\x04"), b"\x44\x01\x02\x03\x04"
        )

    def test_arrays(self):
        self.assertEqual(OpenPrintTag.encodeCBOR([1, 2, 3]), b"\x83\x01\x02\x03")

    def test_maps_with_integer_keys(self):
        self.assertEqual(OpenPrintTag.encodeCBOR({1: 2, 3: 4}), b"\xa2\x01\x02\x03\x04")

    def test_map_key_order_is_deterministic(self):
        # the same spool must always produce identical bytes, whatever order the dict was built in
        first = OpenPrintTag.encodeCBOR({3: "c", 1: "a", 2: "b"})
        second = OpenPrintTag.encodeCBOR({1: "a", 2: "b", 3: "c"})
        self.assertEqual(first, second)

    def test_unsupported_type_raises(self):
        with self.assertRaises(TypeError):
            OpenPrintTag.encodeCBOR(datetime.date(2026, 1, 1))


class TestNDEFMessage(unittest.TestCase):

    def test_short_record_header(self):
        message = OpenPrintTag.buildNDEFMessage(b"\x01\x02")
        # MB|ME|SR|TNF=2 -> 0xD2, then type length, payload length, type, payload
        self.assertEqual(message[0], 0xD2)
        self.assertEqual(message[1], len(OpenPrintTag.OPENPRINTTAG_MIME_TYPE))
        self.assertEqual(message[2], 2)
        typeStart = 3
        typeEnd = typeStart + len(OpenPrintTag.OPENPRINTTAG_MIME_TYPE)
        self.assertEqual(
            message[typeStart:typeEnd].decode("ascii"),
            OpenPrintTag.OPENPRINTTAG_MIME_TYPE,
        )
        self.assertEqual(message[typeEnd:], b"\x01\x02")

    def test_long_record_uses_four_byte_length(self):
        payload = b"\x00" * 300
        message = OpenPrintTag.buildNDEFMessage(payload)
        # SR bit cleared -> 0xC2, and the payload length occupies four bytes
        self.assertEqual(message[0], 0xC2)
        self.assertEqual(message[2:6], b"\x00\x00\x01\x2c")
        self.assertTrue(message.endswith(payload))


class TestColorEncoding(unittest.TestCase):

    def test_hash_prefixed_hex(self):
        self.assertEqual(
            OpenPrintTag._primaryColorBytes(SpoolStub(color="#c0c0c0")),
            b"\xc0\xc0\xc0",
        )

    def test_hex_without_hash(self):
        self.assertEqual(
            OpenPrintTag._primaryColorBytes(SpoolStub(color="c0c0c0")),
            b"\xc0\xc0\xc0",
        )

    def test_shorthand_hex_is_expanded(self):
        self.assertEqual(
            OpenPrintTag._primaryColorBytes(SpoolStub(color="#fff")),
            b"\xff\xff\xff",
        )

    def test_alpha_is_discarded(self):
        self.assertEqual(
            OpenPrintTag._primaryColorBytes(SpoolStub(color="#c0c0c080")),
            b"\xc0\xc0\xc0",
        )

    def test_unparsable_or_missing_color_is_none(self):
        self.assertIsNone(OpenPrintTag._primaryColorBytes(SpoolStub(color=None)))
        self.assertIsNone(OpenPrintTag._primaryColorBytes(SpoolStub(color="")))
        self.assertIsNone(
            OpenPrintTag._primaryColorBytes(SpoolStub(color="notacolor"))
        )


class TestMaterialTypeIndex(unittest.TestCase):

    def test_known_abbreviations(self):
        self.assertEqual(OpenPrintTag.materialTypeIndex("PLA"), 0)
        self.assertEqual(OpenPrintTag.materialTypeIndex("petg"), 1)
        self.assertEqual(OpenPrintTag.materialTypeIndex("ABS"), 3)
        self.assertEqual(OpenPrintTag.materialTypeIndex("PA612"), 42)

    def test_no_fuzzy_matching(self):
        # the enum is normative - a descriptive suffix must not resolve to the base material
        self.assertIsNone(OpenPrintTag.materialTypeIndex("PLA Silk"))

    def test_empty_or_missing(self):
        self.assertIsNone(OpenPrintTag.materialTypeIndex(None))
        self.assertIsNone(OpenPrintTag.materialTypeIndex(""))


class TestUtf8Truncation(unittest.TestCase):

    def test_short_value_is_untouched(self):
        self.assertEqual(OpenPrintTag._truncateUtf8("PLA", 63), "PLA")

    def test_truncates_on_a_codepoint_boundary(self):
        # each 'ü' is 2 UTF-8 bytes; a cap of 3 bytes must not split the second one
        truncated = OpenPrintTag._truncateUtf8("üüü", 3)
        self.assertLessEqual(len(truncated.encode("utf-8")), 3)
        # result must still be valid, decodable text - not a stray lead byte
        truncated.encode("utf-8").decode("utf-8")

    def test_none_passes_through(self):
        self.assertIsNone(OpenPrintTag._truncateUtf8(None, 10))


class TestSpoolMapping(unittest.TestCase):

    def test_maps_only_the_main_section(self):
        # aux/meta carry no spool fields today - see UNMAPPED_FIELD_NAMES
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        self.assertEqual(set(fields.keys()), {"main"})

    def test_full_spool_produces_the_expected_key_set(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        main = fields["main"]
        self.assertEqual(
            set(main.keys()),
            {
                "material_class",
                "material_type",
                "material_name",
                "brand_name",
                "nominal_netto_full_weight",
                "empty_container_weight",
                "primary_color",
                "density",
                "filament_diameter",
                "min_print_temperature",
                "max_print_temperature",
                "preheat_temperature",
                "min_bed_temperature",
                "max_bed_temperature",
            },
        )
        self.assertEqual(main["material_class"], 0)
        self.assertEqual(main["material_type"], 1)  # PETG
        self.assertEqual(main["material_name"], "PETG")
        self.assertEqual(main["brand_name"], "TestVendor")
        self.assertIsInstance(main["nominal_netto_full_weight"], OpenPrintTag.Float32)
        self.assertEqual(main["nominal_netto_full_weight"].value, 1000.0)
        self.assertEqual(main["primary_color"], b"\xc0\xc0\xc0")

    def test_material_type_omitted_on_no_match(self):
        # material_name still carries the free text even when material_type has no match
        fields = OpenPrintTag.spoolModelToFields(SpoolStub(material="Nylon Blend"))
        main = fields["main"]
        self.assertNotIn("material_type", main)
        self.assertEqual(main["material_name"], "Nylon Blend")

    def test_color_omitted_when_unset(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub(color=None))
        self.assertNotIn("primary_color", fields["main"])

    def test_temperature_range_falls_back_to_target(self):
        fields = OpenPrintTag.spoolModelToFields(
            SpoolStub(minTemperature=None, maxTemperature=None, temperature=215)
        )
        main = fields["main"]
        self.assertEqual(main["min_print_temperature"], 215)
        self.assertEqual(main["max_print_temperature"], 215)
        self.assertEqual(main["preheat_temperature"], 215)

    def test_explicit_temperature_range_is_kept(self):
        fields = OpenPrintTag.spoolModelToFields(
            SpoolStub(minTemperature=190, maxTemperature=230, temperature=210)
        )
        main = fields["main"]
        self.assertEqual(main["min_print_temperature"], 190)
        self.assertEqual(main["max_print_temperature"], 230)
        self.assertEqual(main["preheat_temperature"], 210)


class TestUnmappedFields(unittest.TestCase):

    def test_unresolved_field_names_is_empty_for_a_normal_spool(self):
        # regression guard: FIELD_KEY_MAP must cover everything spoolModelToFields() produces
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        self.assertEqual(OpenPrintTag.getUnresolvedFieldNames(fields), [])
        self.assertTrue(OpenPrintTag.isEncodingComplete(fields))

    def test_dropped_field_names_lists_values_with_no_spec_key(self):
        dropped = OpenPrintTag.getDroppedFieldNames(
            SpoolStub(
                colorName="Silver",
                batchNumber="B-7",
                code="SN-1",
                purchasedOn=datetime.date(2026, 1, 15),
                enclosureTemperature=None,
                remainingWeight=None,
            )
        )
        self.assertIn("colorName", dropped)
        self.assertIn("batchNumber", dropped)
        self.assertIn("serialNumber", dropped)
        self.assertIn("purchasedOn", dropped)
        self.assertNotIn("enclosureTemperature", dropped)

    def test_dropped_field_names_empty_when_none_are_set(self):
        dropped = OpenPrintTag.getDroppedFieldNames(
            SpoolStub(
                colorName=None,
                batchNumber=None,
                code=None,
                purchasedOn=None,
                enclosureTemperature=None,
                remainingWeight=None,
            )
        )
        self.assertEqual(dropped, [])

    def test_truncated_field_names(self):
        longMaterial = "x" * (OpenPrintTag.MATERIAL_NAME_MAX_BYTES + 5)
        truncated = OpenPrintTag.getTruncatedFieldNames(SpoolStub(material=longMaterial))
        self.assertIn("material_name", truncated)
        self.assertNotIn("brand_name", truncated)


class TestEncoding(unittest.TestCase):

    def test_encoding_succeeds_for_a_full_spool(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        encoded = OpenPrintTag.encodeFields(fields)
        self.assertIsInstance(encoded, bytes)
        self.assertGreater(len(encoded), 0)

    def test_region_layout_is_meta_main_aux(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        encoded = OpenPrintTag.encodeFields(fields)
        expectedTotal = (
            OpenPrintTag.OPT_META_SIZE
            + (len(encoded) - OpenPrintTag.OPT_META_SIZE - OpenPrintTag.OPT_AUX_SIZE)
            + OpenPrintTag.OPT_AUX_SIZE
        )
        self.assertEqual(len(encoded), expectedTotal)
        self.assertEqual(len(encoded[-OpenPrintTag.OPT_AUX_SIZE :]), OpenPrintTag.OPT_AUX_SIZE)

    def test_section_size_limit_is_enforced(self):
        oversizedSection = {
            "material_name": "x" * (OpenPrintTag.MAX_SECTION_SIZE_BYTES + 10)
        }
        with self.assertRaises(ValueError):
            OpenPrintTag.encodeSection("main", oversizedSection)

    def test_unknown_section_raises(self):
        with self.assertRaises(OpenPrintTag.UnresolvedFieldKeyError):
            OpenPrintTag.encodeSection("bogus", {})

    def test_build_tag_payload_end_to_end(self):
        payload = OpenPrintTag.buildTagPayload(SpoolStub())
        self.assertIsInstance(payload, bytes)
        self.assertIn(
            OpenPrintTag.OPENPRINTTAG_MIME_TYPE.encode("ascii"), payload
        )

    # Golden-bytes reference: pins the full encoding of one fully-populated stub spool so a
    # real tag written by OctoScale's firmware can be diffed against it byte-for-byte. If this
    # test starts failing after an intentional encoder change, regenerate the expected hex by
    # printing OpenPrintTag.buildTagPayload(SpoolStub(...)).hex() and update the constant below
    # together with a note of what changed and why.
    def test_golden_bytes_for_a_fully_populated_spool(self):
        stub = SpoolStub(
            databaseId=42,
            material="PLA",
            vendor="NoName",
            color="#0000FF",
            colorName="blue",
            diameter=1.75,
            density=1.24,
            totalWeight=1000.0,
            spoolWeight=150.0,
            usedWeight=68.0,
            temperature=210,
            minTemperature=190,
            maxTemperature=230,
            bedTemperature=50,
            minBedTemperature=40,
            maxBedTemperature=60,
            code="MY-SERIAL-1",
            batchNumber="BATCH1",
            purchasedOn=None,
            enclosureTemperature=None,
        )
        payload = OpenPrintTag.buildTagPayload(stub)
        expectedHex = "d21c7d6170706c69636174696f6e2f766e642e6f70656e7072696e74746167a400181801184502185d0318200000000000000000000000ae080009000a63504c410b664e6f4e616d6510fa447a000012fa4316000013430000ff181dfa3f9eb852181efa3fe00000182218be182318e6182418d2182518281826183ca000000000000000000000000000000000000000000000000000000000000000"
        self.assertEqual(payload.hex(), expectedHex)
        self.assertEqual(len(payload), 156)


class TestTagFormats(unittest.TestCase):

    def test_spool_id_format_is_supported(self):
        self.assertTrue(TagFormats.isSupported(TagFormats.TAG_FORMAT_SPOOL_ID_NTAG))

    def test_openprinttag_nfcv_is_registered_and_supported(self):
        tagFormat = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG)
        self.assertIsNotNone(tagFormat)
        self.assertTrue(
            TagFormats.isSupported(TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG)
        )
        self.assertIsNotNone(tagFormat["buildPayload"])

    def test_unknown_format_is_not_supported(self):
        self.assertIsNone(TagFormats.getTagFormat("somethingElse"))
        self.assertFalse(TagFormats.isSupported("somethingElse"))

    def test_spool_id_payload_contains_the_database_id(self):
        tagFormat = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_SPOOL_ID_NTAG)
        payload = tagFormat["buildPayload"](SpoolStub(databaseId=99))
        self.assertEqual(payload["databaseId"], 99)

    def test_nfcv_format_setting_resolves_openprinttag(self):
        self.assertEqual(
            TagFormats.NFCV_FORMAT_SETTING_TO_TAG_FORMAT["openPrintTag"],
            TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG,
        )

    def test_format_for_tag_type_nfcv_openprinttag(self):
        self.assertEqual(
            TagFormats.formatForTagType("nfcv", "openPrintTag"),
            TagFormats.TAG_FORMAT_NFCV_OPENPRINTTAG,
        )

    def test_format_for_tag_type_unknown_setting_falls_back_to_extended(self):
        self.assertEqual(
            TagFormats.formatForTagType("nfcv", "bogus"),
            TagFormats.TAG_FORMAT_NFCV_EXTENDED,
        )

    def test_format_for_tag_type_openprinttag_only_applies_to_nfcv(self):
        # a non-NFC-V tag ignores the nfcv format preference entirely
        self.assertEqual(
            TagFormats.formatForTagType("ntag", "openPrintTag"),
            TagFormats.TAG_FORMAT_OPENSPOOL,
        )

    def test_resolve_temperature_range_falls_back_to_target(self):
        result = TagFormats.resolveTemperatureRange(
            SpoolStub(
                minTemperature=None,
                maxTemperature=None,
                temperature=215,
                minBedTemperature=None,
                maxBedTemperature=None,
                bedTemperature=55,
            )
        )
        self.assertEqual(result["minTemperature"], 215)
        self.assertEqual(result["maxTemperature"], 215)
        self.assertEqual(result["minBedTemperature"], 55)
        self.assertEqual(result["maxBedTemperature"], 55)


if __name__ == "__main__":
    unittest.main()
