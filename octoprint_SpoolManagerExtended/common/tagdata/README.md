Lookup tables for the TigerTag parser.

Derived from TigerTag-Project/TigerTag-SDK-Python (`tigertag/database/`), Apache-2.0,
Copyright TigerTag Corp. 2025-2026. See THIRD_PARTY_NOTICES.md and
3rdPartySoftware/TigerTag-SDK-Python/LICENSE.

Only the id -> label mappings the parser actually needs are kept here; the upstream files
carry additional per-material metadata (density, recommended temperatures) that this plugin
deliberately does not use - a tag's own values must never be shadowed by a table lookup.

These tables age: an unknown id degrades to "Unknown(<id>)" rather than failing, which is
the intended behaviour. There is no runtime download.
