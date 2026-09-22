# coding=utf-8

# Parsing the body of the device's GET /version and deciding whether that firmware is new
# enough for this plugin. Kept pure (no flask/OctoPrint) so it can be unit-tested, same
# reasoning as OctoScaleUrl.py and OctoScaleWeight.py next to it.
#
# The device answers plain text, e.g.:
#
#   "OctoScale v0.0.3-dev32 (Build Sep 19 2026 12:52:24)"
#
# WHY THIS EXISTS AT ALL - it is a deliberate exception to the rule stated in
# OctoScaleWeight.py ("told apart by shape alone - never by version string"). That rule
# still holds for reading a /weight body: shape detection there is free and more robust.
# It does not hold for deciding whether the device is fit to be used, because:
#
#   - OctoScale exposes a real version over /version, unlike the U1/paxx12 whose firmware
#     version is not in its API at all (see U1RfidManager.evaluateDetectionChain).
#   - The gated operations write to physical NFC tags. Probing capability by attempting the
#     operation is exactly what must not happen when the failure mode is a half-written tag.
#
# Raising the requirement later is a one-line change to MINIMUM_FIRMWARE below.

import re

# The version this plugin requires, as (major, minor, patch). Suffixes are accepted: a
# firmware reporting v0.0.4-dev1 or v0.0.4-rc1 satisfies a (0, 0, 4) minimum. Prerelease
# ordering is deliberately NOT modelled - the device's suffixes are build counters, not
# semver prereleases, and treating "-dev1" as "older than 0.0.4" would reject the very
# builds this gate is meant to admit.
MINIMUM_FIRMWARE = (0, 0, 4)

STATUS_OK = "ok"
STATUS_TOO_OLD = "tooOld"
STATUS_UNKNOWN = "unknown"

# Deliberately tolerant: it looks for the first "<major>.<minor>.<patch>" anywhere in the
# body rather than anchoring on the "OctoScale " prefix or the "(Build ...)" tail. Both of
# those are firmware cosmetics that have changed before and carry no meaning for this
# decision, so depending on them would turn a harmless reword into a device-wide block.
_VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?")


def formatVersion(numbers, suffix=None):
    # "v0.0.4" / "v0.0.4-dev1" - the form used in every user-facing message.
    display = "v" + ".".join(str(part) for part in numbers)
    if suffix:
        display += "-" + suffix
    return display


MINIMUM_FIRMWARE_DISPLAY = formatVersion(MINIMUM_FIRMWARE)


def parseVersionBody(body):
    # Returns (version, errorMessage). version is a dict on success, None on failure:
    #   numbers   tuple (major, minor, patch) of ints
    #   suffix    str or None - "dev32", "rc1", ...
    #   display   "v0.0.3-dev32"
    #   raw       the trimmed body as the device sent it
    if body is None:
        return (None, "OctoScale sent an empty answer")

    raw = str(body).strip()
    if not raw:
        return (None, "OctoScale sent an empty answer")

    match = _VERSION_PATTERN.search(raw)
    if match is None:
        return (
            None,
            "OctoScale sent an unreadable version: '" + raw[:80] + "'",
        )

    numbers = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    suffix = match.group(4)

    return (
        {
            "numbers": numbers,
            "suffix": suffix,
            "display": formatVersion(numbers, suffix),
            "raw": raw,
        },
        None,
    )


def compareToMinimum(version):
    # Returns one of the STATUS_* constants. A version that could not be parsed is
    # STATUS_UNKNOWN, never STATUS_TOO_OLD - see evaluateVersionBody for why that
    # distinction carries the whole fail-open behaviour.
    if version is None:
        return STATUS_UNKNOWN

    numbers = version.get("numbers") if hasattr(version, "get") else None
    if numbers is None:
        return STATUS_UNKNOWN

    # Plain tuple comparison, so 0.0.10 correctly beats 0.0.4 (a string compare would not).
    # The suffix is ignored here on purpose, see MINIMUM_FIRMWARE above.
    if tuple(numbers) >= MINIMUM_FIRMWARE:
        return STATUS_OK
    return STATUS_TOO_OLD


def evaluateVersionBody(body):
    # The one entry point the API layer uses. Returns a dict that is safe to cache and to
    # hand to the frontend as-is:
    #   status           STATUS_OK | STATUS_TOO_OLD | STATUS_UNKNOWN
    #   firmwareVersion  "v0.0.3-dev32", or None when unparseable
    #   requiredVersion  "v0.0.4"
    #   raw              what the device sent, trimmed - None when there was no body
    #   message          a user-facing sentence, or None when status is STATUS_OK
    #
    # STATUS_UNKNOWN is NOT a block. Only a version that was actually read and found too old
    # blocks anything. An unreachable or unintelligible device stays usable so it fails with
    # its own honest transport error instead of a firmware verdict nobody can act on - and
    # so an OctoPrint restart while the device is powered off cannot lock the user out of
    # every OctoScale feature with no way back.
    version, errorMessage = parseVersionBody(body)
    status = compareToMinimum(version)

    message = None
    if status == STATUS_TOO_OLD:
        message = (
            "OctoScale firmware "
            + version["display"]
            + " is too old - "
            + MINIMUM_FIRMWARE_DISPLAY
            + " or newer is required."
        )
    elif status == STATUS_UNKNOWN:
        message = errorMessage

    return {
        "status": status,
        "firmwareVersion": version["display"] if version is not None else None,
        "requiredVersion": MINIMUM_FIRMWARE_DISPLAY,
        "raw": version["raw"] if version is not None else None,
        "message": message,
    }


def evaluateUnreachable(errorMessage):
    # Same shape as evaluateVersionBody, for the case where the device never answered. Kept
    # here rather than built ad hoc in the API layer so both paths cache identical dicts.
    return {
        "status": STATUS_UNKNOWN,
        "firmwareVersion": None,
        "requiredVersion": MINIMUM_FIRMWARE_DISPLAY,
        "raw": None,
        "message": errorMessage,
    }
