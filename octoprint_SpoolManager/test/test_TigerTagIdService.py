# coding=utf-8

# Tests for TigerTagIdService: the runtime auto-updater for TigerTag's id lookup tables,
# mirroring FilamentDatabaseService's SpoolmanDB-Community mechanism (see
# test_FilamentDatabaseService.py for the pattern this follows).
#
# Run with:  python3 -m pytest octoprint_SpoolManager/test/test_TigerTagIdService.py

import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import requests

from octoprint_SpoolManager.common.TigerTagIdService import TigerTagIdService


class FakeResponse:
    def __init__(self, records, status_code=200, etag='"etag"'):
        self.status_code = status_code
        self.headers = {"ETag": etag}
        self._content = json.dumps(records).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def iter_content(self, chunk_size):
        yield self._content


class FakeSession:
    def __init__(self, responses_by_filename):
        # {filename: FakeResponse or Exception or list of either, consumed in order}
        self._responses = {
            name: (value if isinstance(value, list) else [value])
            for name, value in responses_by_filename.items()
        }
        self._index = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        filename = url.rsplit("/", 1)[-1]
        queue = self._responses.get(filename, [FakeResponse([])])
        index = self._index.get(filename, 0)
        item = queue[min(index, len(queue) - 1)]
        self._index[filename] = index + 1
        if isinstance(item, Exception):
            raise item
        return item


def _allSectionResponses(overrides=None):
    base = {
        "id_material.json": FakeResponse([{"id": 18775, "label": "PE-CF"}]),
        "id_brand.json": FakeResponse([{"id": 1, "name": "Atome3D"}]),
        "id_aspect.json": FakeResponse([{"id": 21, "label": "Clear"}]),
        "id_type.json": FakeResponse([{"id": 142, "label": "Filament"}]),
        "id_diameter.json": FakeResponse([{"id": 56, "label": "1.75"}]),
        "id_measure_unit.json": FakeResponse([{"id": 21, "label": "g"}]),
    }
    if overrides:
        base.update(overrides)
    return base


class TigerTagIdServiceTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.folder.cleanup()

    def _service(self, session=None, **kwargs):
        return TigerTagIdService(
            self.folder.name,
            logging.getLogger(__name__),
            "test",
            http_session=session or FakeSession(_allSectionResponses()),
            now=kwargs.pop("now", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)),
            **kwargs,
        )

    def test_fetches_all_sections_and_resolves_label_and_reverse(self):
        service = self._service()

        self.assertEqual("PE-CF", service.label("id_material", 18775))
        self.assertEqual(18775, service.id_for_label("id_material", "PE-CF"))
        self.assertEqual(18775, service.id_for_label("id_material", "pe-cf"))  # case-insensitive
        self.assertEqual("Atome3D", service.label("id_brand", 1))
        self.assertIsNone(service.label("id_brand", 999))
        self.assertIsNone(service.id_for_label("id_material", "Unobtainium"))

    def test_second_call_within_ttl_does_not_refetch(self):
        session = FakeSession(_allSectionResponses())
        service = self._service(session=session)

        service.ensure_index()
        callsAfterFirst = len(session.calls)
        service.ensure_index()

        self.assertEqual(callsAfterFirst, len(session.calls))
        self.assertEqual(6, callsAfterFirst)  # one per section, not one per lookup

    def test_disabled_never_fetches_and_uses_fallback(self):
        session = FakeSession(_allSectionResponses())
        service = self._service(session=session, is_enabled=lambda: False)

        data, status = service.ensure_index()

        self.assertEqual(0, len(session.calls))
        self.assertEqual("disabled", status["status"])
        # Falls back to the shipped snapshot rather than returning nothing.
        self.assertIn("id_diameter", data)

    def test_all_sections_failing_on_first_ever_fetch_falls_back_to_shipped_snapshot(self):
        allFailing = {
            name: requests.ConnectionError("network")
            for name in _allSectionResponses()
        }
        session = FakeSession(allFailing)
        service = self._service(session=session, sleep=lambda seconds: None)

        data, status = service.ensure_index()

        self.assertEqual("error", status["status"])
        # The fallback snapshot's own (very sparse) id_material table, not empty.
        self.assertIsInstance(data.get("id_material"), dict)

    def test_one_section_failing_keeps_the_others_and_reports_stale(self):
        # Only id_material fails - the other five sections still fetch successfully, so
        # this is a partial success, not a total loss: the fallback snapshot must not be
        # used when good data for most sections was just fetched.
        session = FakeSession(
            _allSectionResponses(
                {"id_material.json": requests.ConnectionError("network")}
            )
        )
        service = self._service(session=session, sleep=lambda seconds: None)

        data, status = service.ensure_index()

        self.assertEqual("stale", status["status"])
        self.assertEqual("Atome3D", data["id_brand"]["1"])

    def test_partial_failure_keeps_previously_fetched_sections(self):
        # First fetch succeeds fully.
        session = FakeSession(_allSectionResponses())
        service = self._service(session=session)
        service.ensure_index()

        # Second fetch (forced): brand fails, everything else would succeed again.
        session2 = FakeSession(
            _allSectionResponses(
                {"id_brand.json": requests.ConnectionError("network")}
            )
        )
        service._http_session = session2
        service._sleep = lambda seconds: None

        data, status = service.ensure_index(force=True)

        # Material section still present from the first successful fetch even though
        # brand failed this time - a transient failure on one section must not blank out
        # sections that were already known good.
        self.assertEqual("PE-CF", (data.get("id_material") or {}).get("18775"))
        self.assertIn(status["status"], ("stale", "fresh"))

    def test_etag_304_keeps_previous_section_data(self):
        session = FakeSession(_allSectionResponses())
        service = self._service(session=session)
        service.ensure_index()

        notModified = _allSectionResponses(
            {"id_material.json": FakeResponse([], status_code=304)}
        )
        session2 = FakeSession(notModified)
        service._http_session = session2

        data, _status = service.ensure_index(force=True)

        self.assertEqual("PE-CF", data["id_material"]["18775"])

    def test_status_reports_section_counts(self):
        service = self._service()
        _data, status = service.ensure_index()

        self.assertEqual(1, status["section_counts"]["id_material"])
        self.assertEqual(1, status["section_counts"]["id_brand"])

    def test_next_refresh_is_jittered_within_ttl_window(self):
        service = self._service()
        service._get_installation_id = lambda: "0123456789abcdef"

        _data, status = service.ensure_index(ttl_days=5)

        next_refresh = datetime.fromisoformat(status["next_refresh_at"])
        last_fetch = datetime.fromisoformat(status["last_fetch"])
        self.assertGreaterEqual(next_refresh, last_fetch + timedelta(days=5))
        self.assertLess(next_refresh, last_fetch + timedelta(days=10))


if __name__ == "__main__":
    unittest.main()
