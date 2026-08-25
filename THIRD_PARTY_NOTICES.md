# Third-Party Notices

## OpenRFID

The vendor filament tag reading feature ports parser logic from [OpenRFID](https://github.com/suchmememanyskill/OpenRFID), published under the [GNU General Public License v3.0](https://github.com/suchmememanyskill/OpenRFID/blob/main/LICENSE) (full text in `3rdPartySoftware/OpenRFID/LICENSE`). GPLv3-licensed code may be combined into this AGPLv3-licensed work; the combined work remains available under the AGPLv3.

Ported files carry a provenance header naming the upstream file they derive from, along with the attributions those files themselves carry: OpenRFID's OpenSpool and NDEF processors are adapted from [SnapmakerU1-Extended-Firmware](https://github.com/paxx12/SnapmakerU1-Extended-Firmware) by paxx12, its Anycubic processor from [ACE-RFID](https://github.com/DnG-Crafts/ACE-RFID) by DnG-Crafts, and its Qidi processor references [BoxRFID](https://github.com/TinkerBarn/BoxRFID) by TinkerBarn.

**No secret cryptographic key material from any manufacturer is included in this plugin.** Tags whose data is protected by manufacturer-specific secrets can only be read if the user supplies those secrets themselves; the plugin validates a supplied key against a published checksum where one is available, and otherwise leaves the affected parser disabled.

Snapmaker is the one case where nothing has to be supplied, and it is worth stating precisely: its per-sector keys are derived from each tag's own UID using salt strings that are published as plain literals in the public repository named below. There is no secret to withhold — what makes those keys tag-specific is the tag, not a confidential value — so the derivation is included and works without configuration.

Reading is read-only — this plugin never writes a proprietary vendor tag format. Tags recognized as belonging to a manufacturer stay protected by the overwrite safeguard even though they can now be read: being readable does not make a tag safe to write.

## Bambu Lab tag format (documentation reference only)

The Bambu Lab vendor tag parser was written against the format description published by
[Bambu-Research-Group/RFID-Tag-Guide](https://github.com/Bambu-Research-Group/RFID-Tag-Guide)
(`BambuLabRfid.md`). **No code or data from that repository is included here** - it carries
no licence, so it is used purely as a reference for byte offsets and field meanings, which
are factual interoperability information about a third party's data format. The
implementation in this plugin is independent.

Reading a Bambu tag requires a salt value the user supplies; none ships with this plugin and
the parser stays disabled until one is entered. As with every vendor format here, reading is
read-only and no proprietary format is ever written.

## TigerTag SDK (TigerTag tag format)

The TigerTag vendor tag parser and the id lookup tables under
`octoprint_SpoolManager/common/tagdata/` are derived from
[TigerTag-SDK-Python](https://github.com/TigerTag-Project/TigerTag-SDK-Python) by TigerTag
Corp., published under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
(full text in `3rdPartySoftware/TigerTag-SDK-Python/LICENSE`), specifically `tigertag/tag.py`
and `tigertag/database/*.json`.

Copyright © TigerTag Corp. 2025-2026. The TigerTag specification carries an explicit,
irrevocable, royalty-free permission to implement it in any product or software, open source
or proprietary. Apache-2.0-licensed code may be combined into this AGPLv3-licensed work; the
combined work remains available under the AGPLv3, and as with the GPLv3 components this
direction is one-way.

Changes made: only the id-to-label mappings this plugin needs were kept; the per-material
`recommended` temperature values are deliberately **not** used, because a tag's own values
must never be shadowed by a table lookup. `common/tagdata/tigertag_ids.json` ships as an
offline fallback snapshot only; at runtime `TigerTagIdService` (mirroring
`FilamentDatabaseService`'s SpoolmanDB-Community mechanism) fetches the current
`tigertag/database/*.json` files directly from the TigerTag-SDK-Python repository on a
jittered TTL, caches them in the plugin's data folder, and falls back to the shipped
snapshot on any fetch failure. An id still unrecognized after that degrades to
`Unknown(<id>)` on read, or is simply omitted from the written payload on write.

## spool-link-apps (Snapmaker tag format)

The Snapmaker vendor tag parser and its key derivation are derived from [spool-link-apps](https://github.com/paxx12-snapmaker-u1/spool-link-apps) by paxx12-snapmaker-u1 / paxx12, published under the [GNU General Public License v3.0](https://github.com/paxx12-snapmaker-u1/spool-link-apps/blob/main/LICENSE) (full text in `3rdPartySoftware/spool-link-apps/LICENSE`), specifically `android-app/app/src/main/java/dev/pages/paxx12/spoollink/formats/SnapmakerFormat.kt` (repository state 2026-07-30).

This is derived work, not merely inspiration: the salt strings, the key derivation scheme, the byte offsets and the material/sub-type tables are the result of that project's reverse engineering. GPLv3-licensed code may be combined into this AGPLv3-licensed work; the combined work remains available under the AGPLv3. Note that this direction is one-way — code from this AGPLv3 work may not be contributed back into a GPLv3-only project.

The upstream `LICENSE` file carries the unmodified GPL template with an unfilled copyright line, so no individual rights holder is named there; attribution is given to the repository owner as published.

## SpoolmanDB-Community

The optional SpoolmanDB temperature suggestion feature retrieves filament data at runtime from [SpoolmanDB-Community](https://github.com/Icezaza2543/SpoolmanDB-Community), published under the [MIT License](https://github.com/Icezaza2543/SpoolmanDB-Community/blob/main/LICENSE).

The data is fetched only when the user enables the feature, stored locally as a compact derived cache, and is not included in this plugin's package. Filament values are suggestions only; manufacturer documentation, material-specific testing, and safe printer operation remain the user's responsibility.
