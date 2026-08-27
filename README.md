# OctoPrint-SpoolManagerExtended

[![License][badge-license]](LICENSE.txt)
[![Python][badge-python]](https://python.org)
[![OctoPrint][badge-octoprint]](https://octoprint.org)
[![Latest Release][badge-release]](https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended/releases/latest)
[![Latest Prerelease][badge-prerelease]](https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended/releases)
[![Downloads][badge-downloads]](https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended/releases)
[![Made with Love][badge-love]](https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended)

[badge-license]: https://img.shields.io/github/license/Ajimaru/OctoPrint-SpoolManagerExtended?style=flat-square
[badge-python]: https://img.shields.io/badge/python-3.11%2B-blue.svg?style=flat-square
[badge-octoprint]: https://img.shields.io/badge/OctoPrint-2.0.0%2B-blue.svg?style=flat-square
[badge-release]: https://img.shields.io/github/v/release/Ajimaru/OctoPrint-SpoolManagerExtended?style=flat-square
[badge-prerelease]: https://img.shields.io/github/v/release/Ajimaru/OctoPrint-SpoolManagerExtended?include_prereleases&label=prerelease&style=flat-square
[badge-downloads]: https://img.shields.io/github/downloads/Ajimaru/OctoPrint-SpoolManagerExtended/total.svg?style=flat-square
[badge-love]: https://img.shields.io/badge/made_with-%E2%9D%A4%EF%B8%8F-ff69b4?style=flat-square

An OctoPrint plugin that manages spool information (filament type, color, remaining weight, RFID/QR tagging, and more) and stores it in a database (SQLite by default; MySQL/PostgreSQL supported experimentally).

## Disclaimer

> [!CAUTION]
> **About the codebase.** This is a personal project, in an early development stage,
> and I make no guarantees about its functionality or safety.
> It has not been fully tested and should not be used in production environments.
> **Use at your own risk.**

> [!NOTE]
> **About this project.** I built this for my own printer setup with AI,
> and if it helps others, even better.
> I have tested it to the best of my knowledge and
> ability, and every change is backed by an automated test suite, CI, and
> security scans (Bandit, CodeQL). Disclosed here per the OctoPrint plugin guidelines.
> Issues and PRs are welcome.

## Origin

This plugin is a fork descending from:

- [OllisGit/OctoPrint-SpoolManager](https://github.com/OllisGit/OctoPrint-SpoolManager) — original project
- [WildRikku/OctoPrint-SpoolManager](https://github.com/WildRikku/OctoPrint-SpoolManager) — fork this repository is based on

With contributions merged in from:

- [dojohnso/OctoPrint-SpoolManager](https://github.com/dojohnso/OctoPrint-SpoolManager)
- [mdziekon/OctoPrint-SpoolManager](https://github.com/mdziekon/OctoPrint-SpoolManager)

Original READMEs from these projects are archived in [docs/original-readmes/](docs/original-readmes/).

## Features

- Spool tracking with vendor/material/color catalogs, sourced from [SpoolmanDB](https://github.com/Donkie/SpoolmanDB)
- Automatic filament consumption tracking per print, with fallbacks for connectors that don't report it directly
- RFID and QR tagging: scan a spool to select it, or write vendor tags for supported readers
- [OctoScale](https://github.com/Ajimaru/OctoScale) support: weigh spools and read/write NFC tags on a connected scale
- MQTT / Home Assistant discovery for exposing spool data to your smart home
- Moonraker/Klipper and Bambu connector support (Snapmaker U1, A1 mini, and similar)
- Inventory reports exportable as PDF or XLSX
- Migration tools for moving spools and settings over from the original SpoolManager plugin

> [!TIP]
> **Get the full experience with [OctoScale](https://github.com/Ajimaru/OctoScale).**
> This plugin's weighing, NFC read/write, and scale-triggered spool workflows are
> built around it — without a companion scale, those features stay unused.

## Requirements

- **OctoPrint 2.0.0 or newer** — at the time of writing only available as a release
  candidate. The OctoPrint 1.x branch is **not supported**: installation is refused by pip,
  and the plugin will not load on a 1.x core.
- **Python 3.11 or newer** (up to 3.14).

## Installation

> [!WARNING]
> **About this project.** Create a backup of your OctoPrint instance before installing this
> plugin, and read the [migrating from SpoolManager](#migrating-from-spoolmanager) section
> below if you are already using that plugin.

Install via OctoPrint's Plugin Manager using this repository's release archive URL, or manually via pip:

```bash
pip install https://github.com/Ajimaru/OctoPrint-SpoolManagerExtended/archive/main.zip
```

## Migrating from SpoolManager

Because this plugin has its own identifier, OctoPrint treats it as a separate plugin: it
starts with an empty database and default settings, and an existing SpoolManager install
is left completely untouched. Nothing is migrated automatically — you decide what to take
over, and when.

Both migrations are found in the plugin settings, each behind a dialog that shows what
would happen before anything is written:

- **Spools** — *Storage* → *Migrate database from SpoolManager*. Previews the spools in
  the old database, then lets you pick which files to copy.
- **Settings** — *General* → *Migrate settings*. Compares every setting side by side and
  preselects the ones that differ, so you tick off exactly what to adopt.

Files are **copied**, never moved, so the old installation stays intact and usable as a
fallback. Each migration can be reversed with its own *Undo* button.

Two things worth knowing:

- Afterwards, **disable the old SpoolManager plugin** — with both active they work on the
  same data.
- The migrated database uses an older scheme, so *Storage* will ask for a scheme upgrade.
  That is expected, and a separate step with its own backup.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

AGPLv3 — see [LICENSE.txt](LICENSE.txt).

Some vendor tag parsers are ported or derived from third-party projects under their own licenses — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
