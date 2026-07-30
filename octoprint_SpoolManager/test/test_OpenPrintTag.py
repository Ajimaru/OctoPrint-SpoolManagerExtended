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
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManager/__init__.py pulls in flask
# and OctoPrint, which are not available in a bare test environment. Both modules under test are
# dependency-free on purpose, so this keeps them runnable with plain pytest.
_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)


def _loadModule(moduleName):
    modulePath = os.path.join(_COMMON_DIR, moduleName + ".py")
    spec = importlib.util.spec_from_file_location(
        "spoolmanager_test_" + moduleName, modulePath
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OpenPrintTag = _loadModule("OpenPrintTag")
TagFormats = _loadModule("TagFormats")


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
            "bedTemperature": 80,
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

    def test_floats_use_double_precision(self):
        self.assertEqual(
            OpenPrintTag.encodeCBOR(1.5), b"\xfb\x3f\xf8\x00\x00\x00\x00\x00\x00"
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


class TestSpoolMapping(unittest.TestCase):

    def test_maps_the_expected_sections(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        self.assertEqual(set(fields.keys()), {"meta", "main", "aux"})

    def test_main_section_carries_the_product_description(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        main = fields["main"]
        self.assertEqual(main["materialName"], "PETG")
        self.assertEqual(main["colorName"], "Silver")
        self.assertEqual(main["colorHex"], "#c0c0c0")
        self.assertEqual(main["diameter"], 1.75)
        self.assertEqual(main["netWeight"], 1000.0)
        self.assertEqual(main["spoolWeight"], 200.0)

    def test_tag_id_is_the_database_id(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub(databaseId=7))
        self.assertEqual(fields["meta"]["tagId"], 7)

    def test_remaining_weight_is_derived_when_not_stored(self):
        fields = OpenPrintTag.spoolModelToFields(
            SpoolStub(remainingWeight=None, totalWeight=1000.0, usedWeight=250.0)
        )
        self.assertEqual(fields["aux"]["remainingWeight"], 750.0)

    def test_stored_remaining_weight_wins(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub(remainingWeight=123.0))
        self.assertEqual(fields["aux"]["remainingWeight"], 123.0)

    def test_dates_are_formatted_iso(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        self.assertEqual(fields["aux"]["purchasedOn"], "2026-01-15")

    def test_empty_values_are_dropped(self):
        # a field without a value must not end up on the tag claiming "0"
        fields = OpenPrintTag.spoolModelToFields(
            SpoolStub(batchNumber=None, enclosureTemperature=None)
        )
        self.assertNotIn("batchNumber", fields["aux"])
        self.assertNotIn("enclosureTemperature", fields["aux"])


class TestFieldKeyResolution(unittest.TestCase):
    # The integer keys are not transcribed from the specification yet - these tests pin down that
    # the code says so instead of inventing keys, and will start failing (usefully) once the map
    # is filled in, prompting the encoding tests below to be enabled.

    def test_unresolved_fields_are_reported(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        unresolved = OpenPrintTag.getUnresolvedFieldNames(fields)
        self.assertIn("main.materialName", unresolved)
        self.assertFalse(OpenPrintTag.isEncodingComplete(fields))

    def test_encoding_refuses_unresolved_keys(self):
        fields = OpenPrintTag.spoolModelToFields(SpoolStub())
        with self.assertRaises(OpenPrintTag.UnresolvedFieldKeyError):
            OpenPrintTag.encodeFields(fields)

    def test_encoding_works_once_keys_are_known(self):
        # simulates a filled-in key map without touching the module-level one for other tests
        originalKeyMap = OpenPrintTag.FIELD_KEY_MAP
        try:
            OpenPrintTag.FIELD_KEY_MAP = {
                "meta": {"version": 0, "tagId": 1},
                "main": {"materialName": 0, "colorName": 1},
                "aux": {},
            }
            fields = {
                "meta": {"version": 1, "tagId": 42},
                "main": {"materialName": "PETG", "colorName": "Silver"},
            }
            self.assertTrue(OpenPrintTag.isEncodingComplete(fields))
            encoded = OpenPrintTag.encodeFields(fields)
            self.assertIsInstance(encoded, bytes)
            self.assertGreater(len(encoded), 0)
        finally:
            OpenPrintTag.FIELD_KEY_MAP = originalKeyMap

    def test_section_size_limit_is_enforced(self):
        originalKeyMap = OpenPrintTag.FIELD_KEY_MAP
        try:
            OpenPrintTag.FIELD_KEY_MAP = {"main": {"colorName": 0}}
            oversizedSection = {
                "colorName": "x" * (OpenPrintTag.MAX_SECTION_SIZE_BYTES + 10)
            }
            with self.assertRaises(ValueError):
                OpenPrintTag.encodeSection("main", oversizedSection)
        finally:
            OpenPrintTag.FIELD_KEY_MAP = originalKeyMap


class TestTagFormats(unittest.TestCase):

    def test_spool_id_format_is_supported(self):
        self.assertTrue(TagFormats.isSupported(TagFormats.TAG_FORMAT_SPOOL_ID_NTAG))

    def test_openprinttag_is_registered_but_not_writable_yet(self):
        # the hardware cannot write NFC-V tags, so the registry must keep this one disabled
        tagFormat = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_OPENPRINTTAG)
        self.assertIsNotNone(tagFormat)
        self.assertFalse(TagFormats.isSupported(TagFormats.TAG_FORMAT_OPENPRINTTAG))

    def test_unknown_format_is_not_supported(self):
        self.assertIsNone(TagFormats.getTagFormat("somethingElse"))
        self.assertFalse(TagFormats.isSupported("somethingElse"))

    def test_spool_id_payload_contains_the_database_id(self):
        tagFormat = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_SPOOL_ID_NTAG)
        payload = tagFormat["buildPayload"](SpoolStub(databaseId=99))
        self.assertEqual(payload["databaseId"], 99)


if __name__ == "__main__":
    unittest.main()
