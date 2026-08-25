# coding=utf-8

# Tests for the tag -> spool field mapping and the OctoScale raw-read adapter. No network:
# the reader takes a callable, so a hand-written fake stands in for the HTTP layer.
#
# Run with:  python3 octoprint_SpoolManager/test/test_FilamentTagToSpool.py
# (or `pytest --import-mode=importlib`)

import importlib.util
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


FilamentTagModel = _loadModule("FilamentTagModel")
FilamentTagToSpool = _loadModule("FilamentTagToSpool")
FilamentTagReader = _loadModule("FilamentTagReader")

GenericFilament = FilamentTagModel.GenericFilament


def filament(**overrides):
    values = {
        "source_processor": "openSpool",
        "unique_id": "abc123",
        "manufacturer": "Polymaker",
        "type": "PETG",
        "modifiers": [],
        "colors": [0xFF1A2B3C],
        "diameter_mm": 1.75,
        "weight_grams": 1000,
        "hotend_min_temp_c": 230,
        "hotend_max_temp_c": 250,
        "bed_temp_c": 70,
        "drying_temp_c": 65,
        "drying_time_hours": 8,
        "manufacturing_date": "2024-05-01",
    }
    values.update(overrides)
    return GenericFilament(**values)


class FakeResponse(object):
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeOctoScale(object):
    """Stands in for SpoolManagerAPI._callOctoScale - records calls, replays answers."""

    def __init__(self, answers):
        # answers: path -> (response, errorMessage) or a list of them, consumed in order
        self.answers = answers
        self.calls = []

    def __call__(self, baseUrl, path, timeout=None, method="GET", json=None):
        self.calls.append({"path": path, "method": method, "json": json})
        answer = self.answers.get(path)
        if isinstance(answer, list):
            return answer.pop(0) if answer else (None, "no more answers")
        return answer if answer is not None else (None, "unexpected path " + path)


class TestFieldMapping(unittest.TestCase):
    def test_maps_the_straightforward_fields(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertEqual("Polymaker", fields["vendor"])
        self.assertEqual("PETG", fields["material"])
        self.assertEqual(1.75, fields["diameter"])
        self.assertEqual(1000, fields["totalWeight"])

    def test_color_drops_alpha(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertEqual("#1A2B3C", fields["color"])

    def test_multi_color_joins_and_caps_at_three(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(
            filament(colors=[0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0xFF123456])
        )
        self.assertEqual("#FF0000;#00FF00;#0000FF", fields["color"])

    def test_color_name_is_never_set(self):
        # The frontend derives it; two writers for this field caused a real bug before.
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertNotIn("colorName", fields)

    def test_remaining_weight_is_never_set(self):
        # A tag states the nominal amount, it knows nothing about consumption.
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertNotIn("remainingWeight", fields)

    def test_temperature_prefers_max_then_min(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertEqual(250, fields["temperature"])

        onlyMin = FilamentTagToSpool.genericFilamentToSpoolFields(
            filament(hotend_max_temp_c=0)
        )
        self.assertEqual(230, onlyMin["temperature"])

    def test_zero_temperatures_are_dropped_not_stored(self):
        # Qidi hardcodes 0 for every temperature - storing that would claim "print at 0 C".
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(
            filament(
                hotend_min_temp_c=0,
                hotend_max_temp_c=0,
                bed_temp_c=0,
                drying_temp_c=0,
                drying_time_hours=0,
            )
        )
        for key in (
            "temperature",
            "minTemperature",
            "maxTemperature",
            "bedTemperature",
            "minBedTemperature",
            "maxBedTemperature",
            "dryingTemperature",
            "dryingTime",
        ):
            self.assertNotIn(key, fields, key + " must not be set from a zero value")

    def test_bed_temperature_mirrors_into_the_range(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertEqual(70, fields["bedTemperature"])
        self.assertEqual(70, fields["minBedTemperature"])
        self.assertEqual(70, fields["maxBedTemperature"])

    def test_drying_values_map_to_the_v12_fields(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        self.assertEqual(65, fields["dryingTemperature"])
        self.assertEqual(8, fields["dryingTime"])

    def test_modifiers_go_to_material_characteristic_not_material(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(
            filament(type="PLA", modifiers=["Silk", "Matte"])
        )
        self.assertEqual("PLA", fields["material"])
        self.assertEqual("Silk Matte", fields["materialCharacteristic"])

    def test_absent_values_produce_absent_keys(self):
        # "not on the tag" and "empty" must stay distinguishable: the frontend only writes
        # fields that are present, and never clears an existing value.
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(
            filament(manufacturer=None, colors=[], weight_grams=0, diameter_mm=0)
        )
        self.assertNotIn("vendor", fields)
        self.assertNotIn("color", fields)
        self.assertNotIn("totalWeight", fields)
        self.assertNotIn("diameter", fields)

    def test_none_filament_maps_to_empty(self):
        self.assertEqual({}, FilamentTagToSpool.genericFilamentToSpoolFields(None))

    def test_diagnostics_keep_the_unique_id_out_of_the_spool_fields(self):
        fields = FilamentTagToSpool.genericFilamentToSpoolFields(filament())
        diagnostics = FilamentTagToSpool.diagnosticsFor(filament(), uid="04A1B2C3")
        self.assertNotIn("uniqueId", fields)
        self.assertEqual("abc123", diagnostics["uniqueId"])
        self.assertEqual("04A1B2C3", diagnostics["uid"])


class TestOctoScaleTagReader(unittest.TestCase):
    def buildReader(self, answers):
        fake = FakeOctoScale(answers)
        reader = FilamentTagReader.OctoScaleTagReader(
            fake, "http://device", logger=None, sleep=lambda _seconds: None
        )
        return reader, fake

    def test_successful_read_returns_the_bytes(self):
        reader, fake = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True, "pending": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {
                            "done": True,
                            "ok": True,
                            "bytes": "0a0b0c0d",
                            "byteCount": 4,
                            "startPage": 0,
                            "uid": "04A1B2C3",
                            "tagType": "ntag",
                            "ntagVariant": "ntag215",
                        }
                    ),
                    None,
                ),
            }
        )
        result = reader.readRaw()
        self.assertTrue(result.ok)
        self.assertEqual(b"\x0a\x0b\x0c\x0d", result.data)
        self.assertEqual("ntag215", result.ntagVariant)

    def test_byte_count_mismatch_is_refused(self):
        # Silently parsing a short dump is worse than not parsing at all.
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {"done": True, "ok": True, "bytes": "0a0b", "byteCount": 99}
                    ),
                    None,
                ),
            }
        )
        result = reader.readRaw()
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertIn("byte", result.error.lower())

    def test_non_zero_start_page_is_refused(self):
        # Every ultralight parser indexes absolute offsets - a shifted dump would parse
        # plausible nonsense rather than fail.
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {
                            "done": True,
                            "ok": True,
                            "bytes": "0a0b0c0d",
                            "byteCount": 4,
                            "startPage": 4,
                        }
                    ),
                    None,
                ),
            }
        )
        result = reader.readRaw()
        self.assertFalse(result.ok)
        self.assertIn("page 0", result.error)

    def test_authentication_failure_is_not_retryable(self):
        # This is the "wrong format, try the next parser" signal.
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {
                            "done": True,
                            "ok": False,
                            "error": "authentication failed",
                            "retryable": False,
                            "authFailedSectors": list(range(16)),
                        }
                    ),
                    None,
                ),
            }
        )
        result = reader.readRaw(keyA=["ff" * 6] * 16)
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(list(range(16)), result.authFailedSectors)

    def test_tag_removed_is_retryable(self):
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {
                            "done": True,
                            "ok": False,
                            "error": "tag removed during read",
                            "retryable": True,
                        }
                    ),
                    None,
                ),
            }
        )
        result = reader.readRaw()
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)

    def test_busy_device_is_retryable(self):
        # 409 "read already in progress" / "write in progress" - transient, not a rejection.
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (
                    FakeResponse(
                        {
                            "error": "write in progress",
                            "retryable": True,
                            "message": "a write is already in progress",
                        }
                    ),
                    "OctoScale answered with HTTP 409",
                ),
            }
        )
        result = reader.readRaw()
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual("a write is already in progress", result.error)

    def test_key_b_and_sectors_are_only_sent_when_given(self):
        reader, fake = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {"done": True, "ok": True, "bytes": "00", "byteCount": 1}
                    ),
                    None,
                ),
            }
        )
        reader.readRaw(keyA=["ff" * 6] * 16)
        body = fake.calls[0]["json"]
        # Sending key B when it is not needed triples the cost of a rejection.
        self.assertNotIn("keyB", body)
        self.assertNotIn("sectors", body)
        self.assertIn("keyA", body)

    def test_sector_mask_is_forwarded(self):
        reader, fake = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (
                    FakeResponse(
                        {"done": True, "ok": True, "bytes": "00", "byteCount": 1}
                    ),
                    None,
                ),
            }
        )
        reader.readRaw(keyA=["ff" * 6] * 16, sectors=[1])
        self.assertEqual([1], fake.calls[0]["json"]["sectors"])

    def test_timeout_reports_retryable(self):
        reader, _ = self.buildReader(
            {
                "/nfcreadstart": (FakeResponse({"ok": True}), None),
                "/nfcreadstatus": (FakeResponse({"done": False}), None),
            }
        )
        result = reader.readRaw()
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertIn("in time", result.error)

    def test_probe_maps_the_tag_type(self):
        reader, _ = self.buildReader(
            {
                "/nfcprobe": (
                    FakeResponse(
                        {"present": True, "uid": "04A1B2C3", "tagType": "ntag"}
                    ),
                    None,
                ),
            }
        )
        scanResult = reader.probe()
        self.assertIsNotNone(scanResult)
        self.assertEqual(
            FilamentTagModel.TagType.MIFARE_ULTRALIGHT, scanResult.tag_type
        )
        self.assertEqual("04A1B2C3", scanResult.uidHex)

    def test_probe_without_a_tag_returns_none(self):
        reader, _ = self.buildReader(
            {"/nfcprobe": (FakeResponse({"present": False}), None)}
        )
        self.assertIsNone(reader.probe())


if __name__ == "__main__":
    unittest.main()
