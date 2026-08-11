import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import requests

from octoprint_SpoolManager.common.FilamentDatabaseService import FilamentDatabaseService


class FakeResponse:
    def __init__(self, records, status_code=200, etag='"etag"'):
        self.status_code = status_code
        self.headers = {"ETag": etag}
        self._content = records

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def iter_content(self, chunk_size):
        yield self._content


class FakeSession:
    def __init__(self, responses):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls = []
        self._index = 0

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        return item


class FilamentDatabaseServiceTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = FilamentDatabaseService(
            self.folder.name,
            logging.getLogger(__name__),
            "test",
            now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.folder.cleanup()

    def test_status_uses_requested_ttl_for_next_refresh(self):
        response = FakeResponse(
            b'[{"manufacturer":"Maker","material":"PLA","name":"Blue","extruder_temp":210,"bed_temp":60}]'
        )
        session = FakeSession(response)
        self.service._http_session = session
        self.service._get_installation_id = lambda: "0123456789abcdef"

        _, status = self.service.vendors(ttl_days=5)

        self.assertEqual("fresh", status["status"])
        next_refresh = datetime.fromisoformat(status["next_refresh_at"])
        last_fetch = datetime.fromisoformat(status["last_fetch"])
        self.assertGreaterEqual(next_refresh, last_fetch + timedelta(days=5))
        self.assertLess(next_refresh, last_fetch + timedelta(days=10))

    def test_retry_on_transient_network_error_then_success(self):
        response = FakeResponse(
            b'[{"manufacturer":"Maker","material":"PLA","name":"Blue","extruder_temp":210,"bed_temp":60}]'
        )
        session = FakeSession([requests.ConnectionError("network"), response])
        sleeps = []
        service = FilamentDatabaseService(
            self.folder.name,
            logging.getLogger(__name__),
            "test",
            now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            http_session=session,
            sleep=lambda seconds: sleeps.append(seconds),
        )

        vendors, status = service.vendors()

        self.assertEqual(["Maker"], vendors)
        self.assertEqual("fresh", status["status"])
        self.assertEqual(2, len(session.calls))
        self.assertEqual(1, len(sleeps))
        self.assertGreater(sleeps[0], 0)

    def test_build_index_deduplicates_ranges_and_marks_conflicts_ambiguous(self):
        index = self.service.build_index(
            [
                {"manufacturer": "Maker", "material": "PLA", "name": "Blue", "extruder_temp": 210, "bed_temp": None, "color_hex": "0000FF"},
                {"manufacturer": "Maker", "material": "PLA", "name": "Blue", "extruder_temp": 210, "bed_temp": None, "color_hex": "#0000ff"},
                {"manufacturer": "Maker", "material": "PLA", "name": "Multi-color", "extruder_temp": 210, "bed_temp": 60, "color_hex": "ff0000"},
                {"manufacturer": "Maker", "material": "PLA", "name": "Multi-color", "extruder_temp": 210, "bed_temp": 60, "color_hex": "00ff00"},
                {"manufacturer": "Maker", "material": "PETG", "name": "Range", "extruder_temp": None, "extruder_temp_range": [220, 240], "bed_temp": None, "bed_temp_range": [70, 80]},
                {"manufacturer": "Maker", "material": "ABS", "name": "Conflict", "extruder_temp": 230, "bed_temp": 90},
                {"manufacturer": "Maker", "material": "ABS", "name": "Conflict", "extruder_temp": 240, "bed_temp": 90},
            ]
        )

        blue = index["Maker"]["PLA"][0]
        multi_color = index["Maker"]["PLA"][1]
        ranged = index["Maker"]["PETG"][0]
        conflict = index["Maker"]["ABS"][0]
        self.assertFalse(blue["ambiguous"])
        self.assertEqual(210, blue["extruder_temp"])
        self.assertEqual("#0000ff", blue["color_hex"])
        self.assertFalse(multi_color["ambiguous"])
        self.assertEqual(210, multi_color["extruder_temp"])
        self.assertIsNone(multi_color["color_hex"])
        self.assertEqual(230, ranged["extruder_temp"])
        self.assertEqual([220, 240], ranged["extruder_temp_range"])
        self.assertTrue(conflict["ambiguous"])
        self.assertIsNone(conflict["extruder_temp"])

    def test_fetch_writes_cache_and_reuses_it_before_refresh_slot(self):
        response = FakeResponse(b'[{"manufacturer":"Maker","material":"PLA","name":"Blue","extruder_temp":210,"bed_temp":60}]')
        session = FakeSession(response)
        self.service._http_session = session

        vendors, status = self.service.vendors()
        cached_vendors, cached_status = self.service.vendors()

        self.assertEqual(["Maker"], vendors)
        self.assertEqual("fresh", status["status"])
        self.assertEqual(vendors, cached_vendors)
        self.assertEqual("fresh", cached_status["status"])
        self.assertEqual(1, len(session.calls))

    def test_build_index_keeps_unique_document_links_and_discards_conflicts(self):
        index = self.service.build_index(
            [
                {
                    "manufacturer": "Maker",
                    "material": "PLA",
                    "name": "Documented",
                    "tds_url": "https://example.com/tds.pdf",
                    "sds_url": "https://example.com/sds.pdf",
                },
                {
                    "manufacturer": "Maker",
                    "material": "PLA",
                    "name": "Documented",
                    "tds_url": "https://example.com/tds.pdf",
                    "sds_url": "javascript:alert(1)",
                },
                {
                    "manufacturer": "Maker",
                    "material": "PLA",
                    "name": "Conflicting documents",
                    "tds_url": "https://example.com/first.pdf",
                },
                {
                    "manufacturer": "Maker",
                    "material": "PLA",
                    "name": "Conflicting documents",
                    "tds_url": "https://example.com/second.pdf",
                },
            ]
        )

        products = {product["name"]: product for product in index["Maker"]["PLA"]}
        documented = products["Documented"]
        conflicting = products["Conflicting documents"]
        self.assertEqual("https://example.com/tds.pdf", documented["tds_url"])
        self.assertEqual("https://example.com/sds.pdf", documented["sds_url"])
        self.assertIsNone(conflicting["tds_url"])
        self.assertIsNone(conflicting["sds_url"])

    def test_build_index_infers_color_name_and_finish_from_product_name(self):
        index = self.service.build_index(
            [
                {"manufacturer": "Maker", "material": "PLA", "name": "PLA Silk Ocean Blue", "color_hex": "145DA0"},
                {"manufacturer": "Maker", "material": "PETG", "name": "PETG Matte Black", "color_hex": "000000", "finish": "glossy"},
                {"manufacturer": "Maker", "material": "ABS", "name": "Brand Metallic Red", "color_hex": "FF0000"},
                {"manufacturer": "Maker", "material": "PLA", "name": "Chameleon Green - Blue", "color_hex": "38a64d"},
                {"manufacturer": "Maker", "material": "PLA", "name": "American Yellow", "color_hex": "ffaa1d"},
            ]
        )

        pla = {product["name"]: product for product in index["Maker"]["PLA"]}
        silk = pla["PLA Silk Ocean Blue"]
        matte = index["Maker"]["PETG"][0]
        branded = index["Maker"]["ABS"][0]
        chameleon = pla["Chameleon Green - Blue"]
        american_yellow = pla["American Yellow"]
        self.assertEqual("Ocean Blue", silk["color_name"])
        self.assertEqual("silk", silk["finish"])
        self.assertEqual("Black", matte["color_name"])
        self.assertEqual("glossy", matte["finish"])
        self.assertEqual("Brand Red", branded["color_name"])
        self.assertEqual("metal", branded["finish"])
        self.assertEqual("Green - Blue", chameleon["color_name"])
        self.assertEqual("American Yellow", american_yellow["color_name"])

    def test_build_index_preserves_up_to_three_structured_colors(self):
        index = self.service.build_index(
            [
                {
                    "manufacturer": "Maker",
                    "material": "PLA",
                    "name": "PLA Red Yellow Blue",
                    "color_hexes": ["ff0000", "ffff00", "0000ff"],
                }
            ]
        )

        product = index["Maker"]["PLA"][0]
        self.assertIsNone(product["color_hex"])
        self.assertEqual(["#ff0000", "#ffff00", "#0000ff"], product["color_hexes"])
        self.assertEqual("Red Yellow Blue", product["color_name"])

    def test_build_index_identifies_tinted_and_untinted_transparent_products(self):
        index = self.service.build_index(
            [
                {"manufacturer": "Maker", "material": "PETG", "name": "Transparent Black", "color_hex": "000000"},
                {"manufacturer": "Maker", "material": "PETG", "name": "Translucent Red", "color_hex": "ff0000"},
                {"manufacturer": "Maker", "material": "PETG", "name": "PETG Transparent", "color_hex": "ffffff"},
                {"manufacturer": "Maker", "material": "PCTG", "name": "PCTG Clear", "color_hex": "ffffff"},
            ]
        )

        petg = {product["name"]: product for product in index["Maker"]["PETG"]}
        clear = index["Maker"]["PCTG"][0]
        self.assertTrue(petg["Transparent Black"]["is_transparent"])
        self.assertFalse(petg["Transparent Black"]["is_untinted_transparent"])
        self.assertTrue(petg["Translucent Red"]["is_transparent"])
        self.assertFalse(petg["Translucent Red"]["is_untinted_transparent"])
        self.assertTrue(petg["PETG Transparent"]["is_transparent"])
        self.assertTrue(petg["PETG Transparent"]["is_untinted_transparent"])
        self.assertTrue(clear["is_transparent"])
        self.assertTrue(clear["is_untinted_transparent"])


if __name__ == "__main__":
    unittest.main()
