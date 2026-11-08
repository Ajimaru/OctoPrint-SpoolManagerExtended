Lookup tables for the TigerTag parser.

Derived from TigerTag-Project/TigerTag-SDK-Python (`tigertag/database/`), Apache-2.0,
Copyright TigerTag Corp. 2025-2026. See THIRD_PARTY_NOTICES.md and
3rdPartySoftware/TigerTag-SDK-Python/LICENSE.

Only the id -> label mappings the parser actually needs are kept here; the upstream files
carry additional per-material metadata (density, recommended temperatures) that this plugin
deliberately does not use - a tag's own values must never be shadowed by a table lookup.

These tables age: an unknown id degrades to "Unknown(<id>)" rather than failing, which is
the intended behaviour.

At runtime, `common/TigerTagIdService.py` downloads the current tables from
TigerTag-SDK-Python and caches them in the plugin data folder (`tigertag_ids_index.json`),
refreshing them about every 7 days. The file here is only the offline fallback: it is used
when there is no cache yet and the download fails, or when the setting
`tigerTagIdsAutoUpdateEnabled` (on by default) has been switched off before any cache existed.
