# coding=utf-8

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests


class FilamentDatabaseService:
    SOURCE_URL = "https://icezaza2543.github.io/SpoolmanDB-Community/filaments.json"
    CACHE_FORMAT_VERSION = 8
    CACHE_FILE_NAME = "spoolmandb_index.json"
    INSTALLATION_ID_FILE_NAME = "spoolmandb_installation_id"
    MAX_RESPONSE_BYTES = 64 * 1024 * 1024
    TIMEOUT_SECONDS = (10, 90)
    MAX_FETCH_ATTEMPTS = 3

    def __init__(
        self,
        data_folder,
        logger,
        plugin_version,
        http_session=None,
        now=None,
        sleep=None,
    ):
        self._data_folder = data_folder
        self._logger = logger
        self._plugin_version = plugin_version
        self._http_session = http_session or requests.Session()
        self._now = now or self._utcnow
        self._sleep = sleep or time.sleep

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
            prefix=".spoolmandb-", dir=self._data_folder
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

    def _write_cache(self, index, etag):
        cache = {
            "format_version": self.CACHE_FORMAT_VERSION,
            "fetched_at": self._now().isoformat(),
            "source_url": self.SOURCE_URL,
            "etag": etag,
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

    @staticmethod
    def _normalize_temperature(value, value_range):
        if value is not None:
            return (value, None)
        if (
            isinstance(value_range, (list, tuple))
            and len(value_range) == 2
            and all(isinstance(item, (int, float)) for item in value_range)
        ):
            return (int(round((value_range[0] + value_range[1]) / 2.0)), list(value_range))
        return (None, None)

    @staticmethod
    def _normalize_color(value):
        if not isinstance(value, str):
            return None
        value = value.strip().lstrip("#")
        if len(value) == 6 and all(character in "0123456789abcdefABCDEF" for character in value):
            return "#" + value.lower()
        return None

    @classmethod
    def _normalize_colors(cls, color_hex, color_hexes):
        if isinstance(color_hexes, (list, tuple)) and 2 <= len(color_hexes) <= 3:
            colors = [cls._normalize_color(value) for value in color_hexes]
            if all(colors):
                return colors
        color = cls._normalize_color(color_hex)
        return [color] if color else []

    @staticmethod
    def _normalize_url(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return value

    @staticmethod
    def _normalize_finish(value):
        finishes = {
            "matte": "matt",
            "matt": "matt",
            "silk": "silk",
            "glossy": "glossy",
            "gloss": "glossy",
            "satin": "satin",
            "metallic": "metal",
            "metal": "metal",
            "sparkle": "sparkle",
            "marble": "marble",
            "glow": "glow",
        }
        if not isinstance(value, str):
            return None
        return finishes.get(value.strip().lower())

    @classmethod
    def _infer_finish(cls, name):
        if not isinstance(name, str):
            return None
        for term in ("matte", "matt", "silk", "glossy", "gloss", "satin", "metallic", "metal", "sparkle", "marble", "glow"):
            if re.search(r"\b" + re.escape(term) + r"\b", name, re.IGNORECASE):
                return cls._normalize_finish(term)
        return None

    @classmethod
    def _infer_color_name(cls, name, material):
        if not isinstance(name, str) or not isinstance(material, str):
            return None
        candidate = name.strip()
        candidate = re.sub(r"\b" + re.escape(material) + r"\b", " ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(
            r"\b(?:matte|matt|silk|glossy|gloss|satin|metallic|metal|sparkle|marble|glow|chameleon|filament)\b",
            " ",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"\s+", " ", candidate).strip(" -_/,")
        return candidate or None

    @classmethod
    def _infer_transparency(cls, name, material):
        if not isinstance(name, str) or not isinstance(material, str):
            return (False, False)
        if not re.search(r"\b(?:transparent|translucent|clear)\b", name, re.IGNORECASE):
            return (False, False)
        remaining = re.sub(
            r"\b(?:transparent|translucent|clear|matte|matt|silk|glossy|gloss|satin|metallic|metal|sparkle|marble|glow|filament)\b",
            " ",
            name,
            flags=re.IGNORECASE,
        )
        remaining = re.sub(
            r"\b" + re.escape(material) + r"\b", " ", remaining, flags=re.IGNORECASE
        )
        return (True, not bool(re.search(r"[a-z]", remaining, re.IGNORECASE)))

    def build_index(self, records):
        if not isinstance(records, list):
            raise ValueError("SpoolmanDB response must be a JSON array")

        variants = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            manufacturer = record.get("manufacturer")
            material = record.get("material")
            name = record.get("name")
            if not all(isinstance(value, str) and value.strip() for value in (manufacturer, material, name)):
                continue
            extruder_temp, extruder_temp_range = self._normalize_temperature(
                record.get("extruder_temp"), record.get("extruder_temp_range")
            )
            bed_temp, bed_temp_range = self._normalize_temperature(
                record.get("bed_temp"), record.get("bed_temp_range")
            )
            colors = self._normalize_colors(
                record.get("color_hex"), record.get("color_hexes")
            )
            color_hex = colors[0] if len(colors) == 1 else None
            color_hexes = colors if len(colors) > 1 else None
            finish = self._normalize_finish(record.get("finish")) or self._infer_finish(name)
            color_name = self._infer_color_name(name, material) if colors else None
            is_transparent, is_untinted_transparent = self._infer_transparency(name, material)
            tds_url = self._normalize_url(record.get("tds_url"))
            sds_url = self._normalize_url(record.get("sds_url"))
            key = (manufacturer.strip(), material.strip(), name.strip())
            variants.setdefault(key, set()).add(
                json.dumps(
                    {
                        "extruder_temp": extruder_temp,
                        "extruder_temp_range": extruder_temp_range,
                        "bed_temp": bed_temp,
                        "bed_temp_range": bed_temp_range,
                        "color_hex": color_hex,
                        "color_hexes": color_hexes,
                        "color_name": color_name,
                        "is_transparent": is_transparent,
                        "is_untinted_transparent": is_untinted_transparent,
                        "finish": finish,
                        "tds_url": tds_url,
                        "sds_url": sds_url,
                    },
                    sort_keys=True,
                )
            )

        index = {}
        for (manufacturer, material, name), temperatures in variants.items():
            parsed_temperatures = [json.loads(value) for value in temperatures]
            temperature_values = {
                json.dumps(
                    {
                        "extruder_temp": entry["extruder_temp"],
                        "extruder_temp_range": entry["extruder_temp_range"],
                        "bed_temp": entry["bed_temp"],
                        "bed_temp_range": entry["bed_temp_range"],
                    },
                    sort_keys=True,
                )
                for entry in parsed_temperatures
            }
            ambiguous = len(temperature_values) > 1
            colors = {entry["color_hex"] for entry in parsed_temperatures if entry["color_hex"]}
            color_sets = {
                tuple(entry["color_hexes"])
                for entry in parsed_temperatures
                if entry["color_hexes"]
            }
            color_names = {entry["color_name"] for entry in parsed_temperatures if entry["color_name"]}
            transparencies = {entry["is_transparent"] for entry in parsed_temperatures}
            untinted_transparencies = {
                entry["is_untinted_transparent"] for entry in parsed_temperatures
            }
            finishes = {entry["finish"] for entry in parsed_temperatures if entry["finish"]}
            tds_urls = {entry["tds_url"] for entry in parsed_temperatures if entry["tds_url"]}
            sds_urls = {entry["sds_url"] for entry in parsed_temperatures if entry["sds_url"]}
            product = {
                "name": name,
                "ambiguous": ambiguous,
                "color_hex": next(iter(colors)) if len(colors) == 1 else None,
                "color_hexes": list(next(iter(color_sets))) if len(color_sets) == 1 else None,
                "color_name": next(iter(color_names)) if len(color_names) == 1 else None,
                "is_transparent": transparencies == {True},
                "is_untinted_transparent": untinted_transparencies == {True},
                "finish": next(iter(finishes)) if len(finishes) == 1 else None,
                "tds_url": next(iter(tds_urls)) if len(tds_urls) == 1 else None,
                "sds_url": next(iter(sds_urls)) if len(sds_urls) == 1 else None,
            }
            if not ambiguous:
                product.update(json.loads(next(iter(temperature_values))))
            else:
                product.update(
                    {
                        "extruder_temp": None,
                        "extruder_temp_range": None,
                        "bed_temp": None,
                        "bed_temp_range": None,
                    }
                )
            index.setdefault(manufacturer, {}).setdefault(material, []).append(product)

        for materials in index.values():
            for products in materials.values():
                products.sort(key=lambda product: product["name"].casefold())
        return index

    def _status(self, cache, state, ttl_days=1, error=None):
        data = cache.get("data", {}) if cache else {}
        material_count = sum(len(materials) for materials in data.values())
        result = {
            "status": state,
            "last_fetch": cache.get("fetched_at") if cache else None,
            "vendor_count": len(data),
            "material_count": material_count,
            "next_refresh_at": self._next_refresh_at(cache, ttl_days).isoformat()
            if cache
            else None,
        }
        if error:
            result["error"] = error
        return result

    def ensure_index(self, ttl_days=1, force=False):
        cache = self._read_cache()
        if cache and not force and self._now() < self._next_refresh_at(cache, ttl_days):
            return (cache, self._status(cache, "fresh", ttl_days=ttl_days))

        headers = {"User-Agent": "OctoPrint-SpoolManager/" + self._plugin_version}
        if cache and cache.get("etag"):
            headers["If-None-Match"] = cache["etag"]
        last_error = None
        for attempt in range(1, self.MAX_FETCH_ATTEMPTS + 1):
            try:
                response = self._http_session.get(
                    self.SOURCE_URL,
                    headers=headers,
                    stream=True,
                    timeout=self.TIMEOUT_SECONDS,
                )
                if response.status_code == 304 and cache:
                    cache = self._write_cache(cache["data"], cache.get("etag"))
                    return (cache, self._status(cache, "fresh", ttl_days=ttl_days))
                response.raise_for_status()
                chunks = []
                total_bytes = 0
                for chunk in response.iter_content(chunk_size=65536):
                    total_bytes += len(chunk)
                    if total_bytes > self.MAX_RESPONSE_BYTES:
                        raise ValueError(
                            "SpoolmanDB response exceeds the configured size limit"
                        )
                    chunks.append(chunk)
                records = json.loads(b"".join(chunks).decode("utf-8"))
                cache = self._write_cache(
                    self.build_index(records), response.headers.get("ETag")
                )
                return (cache, self._status(cache, "fresh", ttl_days=ttl_days))
            except (requests.RequestException, ValueError, UnicodeDecodeError) as error:
                last_error = error
                if attempt >= self.MAX_FETCH_ATTEMPTS or not self._is_retryable_error(error):
                    break
                delay_seconds = self._retry_delay_seconds(attempt)
                self._logger.info(
                    "SpoolmanDB refresh attempt %s failed (%s), retrying in %.2fs",
                    attempt,
                    error,
                    delay_seconds,
                )
                self._sleep(delay_seconds)

        self._logger.warning("SpoolmanDB refresh failed: %s", last_error)
        if cache:
            return (
                cache,
                self._status(cache, "stale", ttl_days=ttl_days, error=str(last_error)),
            )
        return (None, self._status(None, "error", ttl_days=ttl_days, error=str(last_error)))

    def vendors(self, ttl_days=1):
        cache, status = self.ensure_index(ttl_days)
        return (sorted(cache["data"].keys(), key=str.casefold) if cache else [], status)

    def materials(self, vendor, ttl_days=1):
        cache, status = self.ensure_index(ttl_days)
        materials = cache["data"].get(vendor, {}) if cache else {}
        return (sorted(materials.keys(), key=str.casefold), status)

    def products(self, vendor, material, ttl_days=1):
        cache, status = self.ensure_index(ttl_days)
        products = cache["data"].get(vendor, {}).get(material, []) if cache else []
        return (products, status)
