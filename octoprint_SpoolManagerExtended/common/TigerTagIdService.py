# coding=utf-8

# Runtime auto-update for TigerTag's id->label lookup tables, mirroring
# FilamentDatabaseService's SpoolmanDB-Community mechanism: fetch, cache to disk with an
# ETag, refresh on a jittered TTL so many installs don't all hit the source at once, and
# fall back to the last good cache (or the shipped common/tagdata/tigertag_ids.json
# fallback) on any fetch failure. Kept as a separate class rather than folded into
# FilamentDatabaseService because the shape is different: six small flat id->label
# sections, no vendor/material/product tree, and multiple source files instead of one.
#
# Source: TigerTag-Project/TigerTag-SDK-Python (tigertag/database/*.json), Apache-2.0,
# Copyright TigerTag Corp. 2025-2026 - see THIRD_PARTY_NOTICES.md. Raw GitHub content is
# used directly (no SDK code is executed or vendored beyond its LICENSE).

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import requests


class TigerTagIdService:
    SOURCE_BASE_URL = (
        "https://raw.githubusercontent.com/TigerTag-Project/TigerTag-SDK-Python/"
        "main/tigertag/database/"
    )
    # section name -> (source file, field carrying the label; id_brand's SDK records use
    # "name" instead of "label" like every other section)
    SECTIONS = {
        "id_material": ("id_material.json", "label"),
        "id_brand": ("id_brand.json", "name"),
        "id_aspect": ("id_aspect.json", "label"),
        "id_type": ("id_type.json", "label"),
        "id_diameter": ("id_diameter.json", "label"),
        "id_measure_unit": ("id_measure_unit.json", "label"),
    }
    CACHE_FORMAT_VERSION = 1
    CACHE_FILE_NAME = "tigertag_ids_index.json"
    INSTALLATION_ID_FILE_NAME = "tigertag_ids_installation_id"
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024
    TIMEOUT_SECONDS = (10, 60)
    MAX_FETCH_ATTEMPTS = 3

    def __init__(
        self,
        data_folder,
        logger,
        plugin_version,
        fallback_path=None,
        http_session=None,
        now=None,
        sleep=None,
        is_enabled=None,
    ):
        self._data_folder = data_folder
        self._logger = logger
        self._plugin_version = plugin_version
        self._fallback_path = fallback_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "tagdata", "tigertag_ids.json"
        )
        self._http_session = http_session or requests.Session()
        self._now = now or self._utcnow
        self._sleep = sleep or time.sleep
        # Checked before every fetch attempt; defaults to always-enabled so tests and
        # direct instantiation don't need a settings object. When it returns False, the
        # cache (if any) or the shipped fallback is used and no HTTP request is ever
        # made - the auto-update setting is a hard gate, not just a default TTL.
        self._is_enabled = is_enabled or (lambda: True)

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc)

    @property
    def _cache_path(self):
        return os.path.join(self._data_folder, self.CACHE_FILE_NAME)

    @property
    def _installation_id_path(self):
        return os.path.join(self._data_folder, self.INSTALLATION_ID_FILE_NAME)

    def _ensure_data_folder(self):
        if not os.path.isdir(self._data_folder):
            os.makedirs(self._data_folder)

    def _atomic_write(self, path, content):
        self._ensure_data_folder()
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=".tigertag-", dir=self._data_folder
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _get_installation_id(self):
        try:
            with open(self._installation_id_path, "r", encoding="utf-8") as source:
                value = source.read().strip()
                if value:
                    return value
        except IOError:
            pass

        import secrets

        value = secrets.token_hex(32)
        self._atomic_write(self._installation_id_path, value)
        return value

    def _read_cache(self):
        try:
            with open(self._cache_path, "r", encoding="utf-8") as source:
                cache = json.load(source)
        except (IOError, TypeError, ValueError):
            return None

        if (
            not isinstance(cache, dict)
            or cache.get("format_version") != self.CACHE_FORMAT_VERSION
            or not isinstance(cache.get("data"), dict)
            or not cache.get("fetched_at")
        ):
            return None
        return cache

    def _read_fallback(self):
        # The shipped snapshot under common/tagdata/ - used until the first successful
        # fetch, and again if every fetch attempt ever since has failed and no cache
        # exists yet. Never overwritten; the live cache lives entirely under
        # self._cache_path in the plugin's data folder.
        try:
            with open(self._fallback_path, "rb") as handle:
                data = json.loads(handle.read().decode("utf-8"))
        except (IOError, OSError, ValueError):
            return {}
        return {
            section: (data.get(section) or {})
            for section in self.SECTIONS
        }

    def _write_cache(self, index, etags):
        cache = {
            "format_version": self.CACHE_FORMAT_VERSION,
            "fetched_at": self._now().isoformat(),
            "source_base_url": self.SOURCE_BASE_URL,
            "etags": etags,
            "data": index,
        }
        self._atomic_write(self._cache_path, json.dumps(cache, separators=(",", ":")))
        return cache

    @staticmethod
    def _parse_timestamp(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return None

    def _next_refresh_at(self, cache, ttl_days):
        fetched_at = self._parse_timestamp(cache["fetched_at"])
        if fetched_at is None:
            return self._now()
        ttl = timedelta(days=max(1, int(ttl_days)))
        digest = hashlib.sha256(self._get_installation_id().encode("ascii")).digest()
        fraction = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        return fetched_at + ttl + timedelta(seconds=ttl.total_seconds() * fraction)

    def _retry_delay_seconds(self, attempt):
        base_seconds = min(60.0, float(2 ** max(0, attempt - 1)))
        digest = hashlib.sha256(
            (self._get_installation_id() + ":" + str(attempt)).encode("ascii")
        ).digest()
        fraction = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        jitter_factor = 0.5 + fraction
        return base_seconds * jitter_factor

    @staticmethod
    def _is_retryable_error(error):
        if isinstance(error, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(error, requests.HTTPError):
            status = error.response.status_code if error.response is not None else None
            return status in (408, 429) or (status is not None and status >= 500)
        return False

    def _fetch_one(self, section, filename, label_field, previous_etag):
        headers = {"User-Agent": "OctoPrint-SpoolManager/" + self._plugin_version}
        if previous_etag:
            headers["If-None-Match"] = previous_etag
        response = self._http_session.get(
            self.SOURCE_BASE_URL + filename,
            headers=headers,
            stream=True,
            timeout=self.TIMEOUT_SECONDS,
        )
        if response.status_code == 304:
            return None  # unchanged - caller keeps the previous section data
        response.raise_for_status()
        chunks = []
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=65536):
            total_bytes += len(chunk)
            if total_bytes > self.MAX_RESPONSE_BYTES:
                raise ValueError(
                    "TigerTag %s response exceeds the configured size limit" % section
                )
            chunks.append(chunk)
        records = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(records, list):
            raise ValueError("TigerTag %s response must be a JSON array" % section)

        section_data = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            identifier = record.get("id")
            label = record.get(label_field)
            if identifier is None or not isinstance(label, str) or not label.strip():
                continue
            section_data[str(identifier)] = label.strip()
        return (section_data, response.headers.get("ETag"))

    def _status(self, cache, state, ttl_days=7, error=None):
        data = cache.get("data", {}) if cache else {}
        result = {
            "status": state,
            "last_fetch": cache.get("fetched_at") if cache else None,
            "section_counts": {
                section: len(data.get(section, {})) for section in self.SECTIONS
            },
            "next_refresh_at": self._next_refresh_at(cache, ttl_days).isoformat()
            if cache
            else None,
        }
        if error:
            result["error"] = error
        return result

    def ensure_index(self, ttl_days=7, force=False):
        """Returns (data-dict-by-section, status-dict). data is never empty on IOError -
        it falls back to the shipped snapshot so tag reading/writing degrades to the same
        "Unknown(<id>)"/unresolved behaviour as before this service existed, rather than
        losing lookups entirely because of a network hiccup."""
        cache = self._read_cache()
        if cache and not force and self._now() < self._next_refresh_at(cache, ttl_days):
            return (cache["data"], self._status(cache, "fresh", ttl_days=ttl_days))

        if not self._is_enabled():
            # Auto-update disabled in settings: never fetch, just serve whatever is
            # already on disk (cache if one exists from before disabling, else fallback).
            if cache:
                return (cache["data"], self._status(cache, "disabled", ttl_days=ttl_days))
            fallback = self._read_fallback()
            return (fallback, self._status(None, "disabled", ttl_days=ttl_days))

        previous_data = cache["data"] if cache else {}
        previous_etags = cache.get("etags", {}) if cache else {}
        new_data = dict(previous_data)
        new_etags = dict(previous_etags)
        last_error = None
        any_success = False

        for section, (filename, label_field) in self.SECTIONS.items():
            for attempt in range(1, self.MAX_FETCH_ATTEMPTS + 1):
                try:
                    result = self._fetch_one(
                        section, filename, label_field, previous_etags.get(section)
                    )
                    if result is not None:
                        new_data[section], new_etags[section] = result
                    any_success = True
                    break
                except (requests.RequestException, ValueError, UnicodeDecodeError) as error:
                    last_error = error
                    if attempt >= self.MAX_FETCH_ATTEMPTS or not self._is_retryable_error(
                        error
                    ):
                        self._logger.warning(
                            "TigerTag id table refresh failed for %s: %s", section, error
                        )
                        break
                    delay_seconds = self._retry_delay_seconds(attempt)
                    self._sleep(delay_seconds)

        if not any_success and not previous_data:
            # Never fetched anything before, and this attempt got nothing either - fall
            # back to the shipped snapshot rather than returning empty sections.
            fallback = self._read_fallback()
            return (
                fallback,
                self._status(None, "error", ttl_days=ttl_days, error=str(last_error)),
            )

        cache = self._write_cache(new_data, new_etags)
        state = "fresh" if any_success and last_error is None else "stale"
        return (
            cache["data"],
            self._status(cache, state, ttl_days=ttl_days, error=str(last_error) if last_error else None),
        )

    def label(self, section, identifier, ttl_days=7):
        if identifier is None:
            return None
        data, _status = self.ensure_index(ttl_days)
        return (data.get(section) or {}).get(str(identifier))

    def id_for_label(self, section, label, ttl_days=7):
        """Reverse lookup: label -> id, case-insensitive exact match. None when no entry
        matches - callers must treat that as "field omitted", not an error."""
        if not isinstance(label, str) or not label.strip():
            return None
        data, _status = self.ensure_index(ttl_days)
        needle = label.strip().casefold()
        for identifier, candidate in (data.get(section) or {}).items():
            if candidate.casefold() == needle:
                try:
                    return int(identifier)
                except (TypeError, ValueError):
                    return None
        return None
