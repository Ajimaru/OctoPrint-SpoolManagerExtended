# Third-Party Notices

## OpenRFID

The vendor filament tag reading feature ports parser logic from [OpenRFID](https://github.com/suchmememanyskill/OpenRFID), published under the [GNU General Public License v3.0](https://github.com/suchmememanyskill/OpenRFID/blob/main/LICENSE) (full text in `3rdPartySoftware/OpenRFID/LICENSE`). GPLv3-licensed code may be combined into this AGPLv3-licensed work; the combined work remains available under the AGPLv3.

Ported files carry a provenance header naming the upstream file they derive from, along with the attributions those files themselves carry: OpenRFID's OpenSpool and NDEF processors are adapted from [SnapmakerU1-Extended-Firmware](https://github.com/paxx12/SnapmakerU1-Extended-Firmware) by paxx12, its Anycubic processor from [ACE-RFID](https://github.com/DnG-Crafts/ACE-RFID) by DnG-Crafts, and its Qidi processor references [BoxRFID](https://github.com/TinkerBarn/BoxRFID) by TinkerBarn.

**No cryptographic key material from any manufacturer is included in this plugin.** Tags whose data is protected by manufacturer keys can only be read if the user supplies those keys themselves; the plugin validates a supplied key against a published checksum and otherwise leaves the affected parser disabled. Reading is read-only — this plugin never writes a proprietary vendor tag format.

## SpoolmanDB-Community

The optional SpoolmanDB temperature suggestion feature retrieves filament data at runtime from [SpoolmanDB-Community](https://github.com/Icezaza2543/SpoolmanDB-Community), published under the [MIT License](https://github.com/Icezaza2543/SpoolmanDB-Community/blob/main/LICENSE).

The data is fetched only when the user enables the feature, stored locally as a compact derived cache, and is not included in this plugin's package. Filament values are suggestions only; manufacturer documentation, material-specific testing, and safe printer operation remain the user's responsibility.
