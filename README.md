# OctoPrint-SpoolManagerExtended

An OctoPrint plugin that manages spool information (filament type, color, remaining weight, RFID/QR tagging, and more) and stores it in a database (SQLite by default; MySQL/PostgreSQL supported experimentally).

## Requirements

- **OctoPrint 2.0.0 or newer** — at the time of writing only available as a release
  candidate. The OctoPrint 1.x branch is **not supported**: installation is refused by pip,
  and the plugin will not load on a 1.x core.
- **Python 3.11 or newer** (up to 3.14).

## Origin

This plugin is a fork descending from:

- [OllisGit/OctoPrint-SpoolManager](https://github.com/OllisGit/OctoPrint-SpoolManager) — original project
- [WildRikku/OctoPrint-SpoolManager](https://github.com/WildRikku/OctoPrint-SpoolManager) — fork this repository is based on

With contributions merged in from:

- [dojohnso/OctoPrint-SpoolManager](https://github.com/dojohnso/OctoPrint-SpoolManager)
- [mdziekon/OctoPrint-SpoolManager](https://github.com/mdziekon/OctoPrint-SpoolManager)

Original READMEs from these projects are archived in [docs/original-readmes/](docs/original-readmes/).

## Installation

Install via OctoPrint's Plugin Manager using this repository's release archive URL, or manually via pip:

```
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

## License

AGPLv3 — see [LICENSE.txt](LICENSE.txt).
