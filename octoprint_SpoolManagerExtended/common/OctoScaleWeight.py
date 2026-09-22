# coding=utf-8

# Parsing the body of the device's GET /weight. Kept pure (no flask/OctoPrint) so it can be
# unit-tested, same reasoning as OctoScaleUrl.py next to it.
#
# Two body shapes are in the field, told apart by shape alone - never by version string:
#
#   "1068.1"            firmware up to and including the v0.0.3 release
#   "1068.1|ok|0.0"     firmware carrying OctoScale commit 205780b (2026-09-19) and later
#
# Both report 0.0.3 in /version, so the version cannot distinguish them (the firmware
# changed inside an unreleased 0.0.3). Splitting on "|" and taking field 0 covers both, and
# also survives further fields being appended - the firmware side states it would append
# rather than reorder.
#
# "Never by version string" is about THIS decision - which shape a body has - and still
# holds: shape detection is free here and survives firmware that misreports itself. It is
# not a rule about whether a device may be used at all. That separate question is decided
# by version, in OctoScaleFirmware.py, which requires v0.0.4 or newer; both 0.0.3 bodies
# above are therefore excluded from the supported path anyway. The bare-float branch below
# stays regardless - it costs nothing and is the more robust of the two checks.
#
# Fields 2 and 3 describe the scale's stored ZERO POINT, not the quality of this reading:
# the weight in field 1 is a usable number in all four zero states, so a bad zero point is
# a warning to show, never a reason to reject the reading.

# The four values scaleZeroStateName() can emit. "unverified" is also the firmware's own
# fallback for a state it does not recognize.
ZERO_STATE_OK = "ok"
ZERO_STATE_UNVERIFIED = "unverified"
ZERO_STATE_SUSPECT = "suspect"
ZERO_STATE_UNSTABLE = "unstable"

KNOWN_ZERO_STATES = (
    ZERO_STATE_OK,
    ZERO_STATE_UNVERIFIED,
    ZERO_STATE_SUSPECT,
    ZERO_STATE_UNSTABLE,
)


def parseWeightBody(body):
    # Returns (reading, errorMessage). reading is a dict on success, None on failure:
    #   grams           float, may be negative (the device reports below-zero readings)
    #   zeroState       one of KNOWN_ZERO_STATES, or None from a pre-205780b body
    #   zeroDeltaGrams  float or None - display only, see below
    #
    # zeroDeltaGrams is deliberately not trusted for decisions: the firmware writes the
    # state and the deviation non-atomically, so a poll can pick up a fresh state next to a
    # stale delta. It is also 0.0 on an uncalibrated device regardless of the real
    # deviation, so 0.0 does not mean "no deviation". Branch on zeroState only.
    if body is None:
        return (None, "OctoScale sent an empty answer")

    fields = str(body).strip().split("|")

    try:
        grams = float(fields[0].strip())
    except ValueError:
        return (
            None,
            "OctoScale sent an unreadable value: '" + str(body)[:80] + "'",
        )

    zeroState = None
    if len(fields) > 1:
        zeroState = fields[1].strip().lower()
        if zeroState not in KNOWN_ZERO_STATES:
            # An unknown state must not cost us the reading - the firmware treats anything
            # it cannot name as "unverified", so this side does the same.
            zeroState = ZERO_STATE_UNVERIFIED

    zeroDeltaGrams = None
    if len(fields) > 2:
        try:
            zeroDeltaGrams = float(fields[2].strip())
        except ValueError:
            # display-only field; a malformed one is dropped rather than failing the read
            zeroDeltaGrams = None

    return (
        {
            "grams": grams,
            "zeroState": zeroState,
            "zeroDeltaGrams": zeroDeltaGrams,
        },
        None,
    )
