# coding=utf-8

# Tests for the TigerTag write-format registry entry: payload building, id resolution
# fallback behaviour, and the settings-guard that must never let an unsupported format
# reach the firmware (see TagFormats.py's module comment on main.cpp:1698's silent
# fallback-to-openSpool trap).
#
# Run with:  python3 -m pytest octoprint_SpoolManager/test/test_TagFormats.py
# (needs flask/peewee/requests - use the repo .venv, not the bare system python3)

import struct
import unittest

from octoprint_SpoolManager.common import FilamentTagConstants, TagFormats
from octoprint_SpoolManager.common.FilamentTagParsers import TigerTagTagParser
from octoprint_SpoolManager.common.FilamentTagModel import ScanResult, TagType
from octoprint_SpoolManager.models.SpoolModel import SpoolModel


def _spool(**kwargs):
    spool = SpoolModel()
    defaults = {
        "material": None,
        "vendor": None,
        "finish": None,
        "diameter": None,
        "color": None,
        "colorName": None,
        "temperature": None,
        "minTemperature": None,
        "maxTemperature": None,
        "bedTemperature": None,
        "minBedTemperature": None,
        "maxBedTemperature": None,
        "dryingTemperature": None,
        "dryingTime": None,
        "td": None,
        "totalWeight": None,
        "spoolWeight": None,
        "usedWeight": None,
        "remainingWeight": None,
        "totalLength": None,
        "usedLength": None,
        "code": None,
        "batchNumber": None,
        "purchasedFrom": None,
        "displayName": None,
        "firstUse": None,
        "lastUse": None,
        "purchasedOn": None,
        "cost": None,
        "density": None,
        "databaseId": 42,
    }
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(spool, key, value)
    return spool


class TestRegistry(unittest.TestCase):
    def test_tigertag_entry_exists_and_starts_unsupported(self):
        entry = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)
        self.assertIsNotNone(entry)
        self.assertEqual("ntagTigerTag", entry["id"])
        # Verified against real hardware (NTAG215 write-then-read round trip, confirmed
        # by the OctoScale firmware team) - see the module comment in TagFormats.py for
        # the details. Flipping this without such a round trip would have risked silently
        # writing OpenSpool instead (main.cpp:1698 used to coerce any unrecognized
        # preferredNtagFormat to "openSpool") - that risk is why this was False for a
        # while and is now gone.
        self.assertTrue(entry["supported"])
        self.assertTrue(TagFormats.isSupported(TagFormats.TAG_FORMAT_NTAG_TIGERTAG))

    def test_tigertag_is_a_recognized_ntag_setting_value(self):
        self.assertEqual(
            TagFormats.TAG_FORMAT_NTAG_TIGERTAG,
            TagFormats.NTAG_FORMAT_SETTING_TO_TAG_FORMAT["tigerTag"],
        )


class TestFormatForTagTypeNeverReturnsUnsupported(unittest.TestCase):
    def test_an_unsupported_ntag_setting_falls_back_to_openspool(self):
        # formatForTagType is what feeds both the write-preview UI and (indirectly, via
        # the same guard duplicated in SpoolManagerAPI) the actual write - it must never
        # claim an unsupported format will be written. TigerTag itself is supported now
        # (see TestRegistry above), so this test exercises the guard itself via a
        # temporarily-unsupported registry entry rather than relying on a real format
        # happening to be gated at the time this runs.
        formatId = "zzTestOnlyUnsupportedFormat"
        TagFormats.TAG_FORMATS[formatId] = {
            "id": formatId,
            "label": "test-only",
            "supported": False,
            "buildPayload": lambda spoolModel: {},
            "description": "",
        }
        TagFormats.NTAG_FORMAT_SETTING_TO_TAG_FORMAT["zzTestOnly"] = formatId
        try:
            resolved = TagFormats.formatForTagType("ntag", ntagFormatSetting="zzTestOnly")
            self.assertEqual(TagFormats.TAG_FORMAT_OPENSPOOL, resolved)
        finally:
            del TagFormats.TAG_FORMATS[formatId]
            del TagFormats.NTAG_FORMAT_SETTING_TO_TAG_FORMAT["zzTestOnly"]

    def test_unset_ntag_setting_defaults_to_openspool(self):
        resolved = TagFormats.formatForTagType(
            "ntag", ntagFormatSetting=None, nfcvFormatSetting=None
        )
        self.assertEqual(TagFormats.TAG_FORMAT_OPENSPOOL, resolved)

    def test_known_supported_ntag_settings_still_resolve_normally(self):
        self.assertEqual(
            TagFormats.TAG_FORMAT_NTAG_EXTENDED,
            TagFormats.formatForTagType("ntag", ntagFormatSetting="extended"),
        )
        self.assertEqual(
            TagFormats.TAG_FORMAT_OPENSPOOL,
            TagFormats.formatForTagType("ntag", ntagFormatSetting="openSpool"),
        )
        self.assertEqual(
            TagFormats.TAG_FORMAT_NTAG_TIGERTAG,
            TagFormats.formatForTagType("ntag", ntagFormatSetting="tigerTag"),
        )


class TestBuildTigerTagPayload(unittest.TestCase):
    def setUp(self):
        # A tiny in-memory id table, independent of the live service/shipped snapshot,
        # so this test does not depend on network access or the current state of
        # tigertag_ids.json.
        self._service = _FakeIdService(
            {
                "id_material": {"18775": "PE-CF"},
                "id_brand": {"1": "Atome3D"},
                "id_aspect": {"21": "Clear"},
                "id_type": {"142": "Filament"},
                "id_diameter": {"56": "1.75", "221": "2.85"},
                "id_measure_unit": {"21": "g", "35": "kg"},
            }
        )
        FilamentTagConstants.setTigerTagIdService(self._service)
        self.addCleanup(FilamentTagConstants.setTigerTagIdService, None)

    # The 6 keys the firmware's TigerTag encoder requires - all 6, or it rejects the
    # whole write (confirmed against a real incident, see PAYLOAD_ID_KEYS' first user
    # below). A payload missing even one of these must fail this check, which is the
    # opposite of what happened before this constant existed: tigerTagTypeId was left out
    # of _buildTigerTagPayload entirely, and no test caught it because every other test
    # only asserted on the fields it happened to check rather than the full required set.
    PAYLOAD_ID_KEYS = (
        "tigerTagMaterialId",
        "tigerTagBrandId",
        "tigerTagAspectId",
        "tigerTagTypeId",
        "tigerTagDiameterId",
        "tigerTagMeasureUnitId",
    )

    def test_all_six_required_id_keys_are_present_in_the_payload(self):
        # Presence, not resolvability: even a spool the id tables can't resolve anything
        # for must still send all 6 keys (as None where unresolved) - the firmware needs
        # to see the full key set to accept the write at all (see the incident note on
        # PAYLOAD_ID_KEYS above), independent of whether every value could be filled in.
        spool = _spool(material=None, vendor=None, finish=None, diameter=None)
        payload = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)[
            "buildPayload"
        ](spool)
        for key in self.PAYLOAD_ID_KEYS:
            self.assertIn(key, payload, "missing required TigerTag id key: " + key)

    def test_resolvable_fields_produce_expected_ids(self):
        spool = _spool(
            material="PE-CF", vendor="Atome3D", finish="Clear", diameter=1.75
        )
        payload = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)[
            "buildPayload"
        ](spool)

        self.assertEqual(18775, payload["tigerTagMaterialId"])
        self.assertEqual(1, payload["tigerTagBrandId"])
        self.assertEqual(21, payload["tigerTagAspectId"])
        self.assertEqual(142, payload["tigerTagTypeId"])  # always "Filament"
        self.assertEqual(56, payload["tigerTagDiameterId"])
        self.assertEqual(21, payload["tigerTagMeasureUnitId"])  # always grams

    def test_unresolvable_fields_are_omitted_not_guessed(self):
        # "PLA" and "Prusament" are not in the fake table above - a real install's
        # tigertag_ids.json won't cover every string a user has typed either.
        spool = _spool(material="PLA", vendor="Prusament", finish=None, diameter=2.85)
        payload = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)[
            "buildPayload"
        ](spool)

        self.assertIsNone(payload["tigerTagMaterialId"])
        self.assertIsNone(payload["tigerTagBrandId"])
        self.assertIsNone(payload["tigerTagAspectId"])
        self.assertEqual(221, payload["tigerTagDiameterId"])

    def test_payload_still_carries_the_common_field_set(self):
        # TigerTag's payload is additive on top of the shared extended payload, not a
        # replacement - the firmware ignores keys it doesn't need for TigerTag Standard,
        # but other formats reuse the same base fields.
        spool = _spool(material="PE-CF", totalWeight=1000.0)
        payload = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)[
            "buildPayload"
        ](spool)
        self.assertEqual(1000.0, payload["totalWeight"])
        self.assertEqual(42, payload["databaseId"])


class TestRoundTripAgainstTheReadParser(unittest.TestCase):
    """The write-side id resolution and the read-side TigerTagTagParser must agree on
    the same byte layout - this builds a tag from the write payload's resolved ids using
    the exact same layout _tigerTagImage() in test_FilamentTagParsers.py uses, then reads
    it back and checks the spool comes out the same. Catches any drift between what this
    plugin writes and what it (or a real TigerTag reader) reads back."""

    def setUp(self):
        self._service = _FakeIdService(
            {
                "id_material": {"18775": "PE-CF"},
                "id_brand": {},
                "id_aspect": {},
                "id_type": {"142": "Filament"},
                "id_diameter": {"56": "1.75"},
                "id_measure_unit": {"21": "g"},
            }
        )
        FilamentTagConstants.setTigerTagIdService(self._service)
        self.addCleanup(FilamentTagConstants.setTigerTagIdService, None)

    def _buildImage(self, payload):
        body = bytearray(80)
        struct.pack_into(">I", body, 0, 0x5BF59264)  # TigerTag Standard magic
        struct.pack_into(">I", body, 4, 0xFFFFFFFF)  # offline/generic product id
        struct.pack_into(">H", body, 8, payload["tigerTagMaterialId"] or 0)
        body[13] = payload["tigerTagDiameterId"] or 0
        body[16], body[17], body[18], body[19] = (0xFF, 0x00, 0x00, 0xFF)
        weight = int(payload["totalWeight"] or 0)
        body[20] = (weight >> 16) & 0xFF
        body[21] = (weight >> 8) & 0xFF
        body[22] = weight & 0xFF
        body[23] = payload["tigerTagMeasureUnitId"] or 0
        struct.pack_into(">H", body, 24, int(payload["minTemperature"] or 0))
        struct.pack_into(">H", body, 26, int(payload["maxTemperature"] or 0))
        return bytes(16) + bytes(body)  # 16-byte prefix stands in for pages 0-3

    def test_write_then_read_round_trips_material_and_diameter(self):
        spool = _spool(
            material="PE-CF",
            diameter=1.75,
            totalWeight=1000.0,
            temperature=210,
            minTemperature=190,
            maxTemperature=230,
        )
        payload = TagFormats.getTagFormat(TagFormats.TAG_FORMAT_NTAG_TIGERTAG)[
            "buildPayload"
        ](spool)
        image = self._buildImage(payload)

        parser = TigerTagTagParser()
        scan = ScanResult(TagType.MIFARE_ULTRALIGHT, bytes([0x04, 0x11, 0x22, 0x33]))
        filament = parser.parseTag(scan, image)

        self.assertIsNotNone(filament)
        self.assertEqual("PE-CF", filament.type)
        self.assertEqual(1.75, filament.diameter_mm)
        self.assertEqual(1000, filament.weight_grams)
        self.assertEqual(190, filament.hotend_min_temp_c)
        self.assertEqual(230, filament.hotend_max_temp_c)


class _FakeIdService:
    """Minimal stand-in for TigerTagIdService: same label()/id_for_label() surface,
    backed by a fixed in-memory table instead of a cache file or HTTP fetch."""

    def __init__(self, sections):
        self._sections = sections

    def label(self, section, identifier, ttl_days=7):
        if identifier is None:
            return None
        return (self._sections.get(section) or {}).get(str(identifier))

    def id_for_label(self, section, label, ttl_days=7):
        if not isinstance(label, str) or not label.strip():
            return None
        needle = label.strip().casefold()
        for identifier, candidate in (self._sections.get(section) or {}).items():
            if candidate.casefold() == needle:
                return int(identifier)
        return None


if __name__ == "__main__":
    unittest.main()
