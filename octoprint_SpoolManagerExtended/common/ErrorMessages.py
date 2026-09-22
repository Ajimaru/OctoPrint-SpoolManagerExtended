# coding=utf-8

# Turning caught exceptions into messages that are safe to hand to the browser.
#
# The plugin used to build its API responses as "<what failed>: " + str(exception). That reads
# fine in a log, but str() of a database or filesystem error carries the parts of the server
# the caller has no business seeing:
#
#   - peewee / MySQL OperationalError -> host, port, user, database name, driver internals
#   - OSError                         -> absolute paths of the plugin and legacy data folders
#   - sqlite3.DatabaseError           -> path of the uploaded temp file
#
# Every SpoolManager blueprint route is reachable by any logged-in OctoPrint user, while only
# two of them check Permissions.SETTINGS - so this is a real boundary between a normal user and
# the admin, not just noise. CodeQL reports it as py/stack-trace-exposure.
#
# The rule everywhere below: the detail goes to the log (the caller of these helpers is expected
# to have called logger.exception() already), the return value goes to the user.
#
# Pure module - no flask, no OctoPrint - so it can be unit-tested on a bare interpreter, same as
# OctoScaleUrl.py.

# Connection problems, in the order they are checked. Each entry is
# (marker substrings, message) - the first entry whose markers all appear in the lower-cased
# exception text wins.
#
# The markers are matched against str(exception) because the plugin talks to MySQL through
# peewee, which wraps the driver's own exception types; catching by class would mean importing
# and branching on whichever driver happens to be installed.
_CONNECTION_PATTERNS = (
    (("access denied",), "Access denied. Check the user name and password."),
    (
        ("unknown database",),
        "The database does not exist on the server. Check the database name.",
    ),
    (
        ("can't connect", "unknown server host"),
        "The database server could not be reached. Check the host name and port.",
    ),
    (
        ("can't connect",),
        "The database server could not be reached. Check the host name and port.",
    ),
    (
        ("unknown server host",),
        "The database server could not be reached. Check the host name and port.",
    ),
    (("timed out",), "The database server did not answer in time."),
    (("connection refused",), "The database server refused the connection."),
    (
        ("ssl",),
        "The secure connection to the database server failed. Check the SSL settings.",
    ),
)

_GENERIC_CONNECTION_MESSAGE = (
    "Could not connect to the database. Check the connection settings."
)


def classifyConnectionError(exception):
    """Maps a database connection exception onto one of a fixed set of sentences.

    Used by the diagnostic endpoints (testDatabaseConnection), where a generic "it failed"
    would make the feature useless: the point of pressing "Test connection" is to learn WHY.
    The user typed the host, port and user name themselves, so they need the failure class -
    not those values read back to them together with the driver's internals.
    """
    text = str(exception).lower()
    for markers, message in _CONNECTION_PATTERNS:
        if all(marker in text for marker in markers):
            return message
    return _GENERIC_CONNECTION_MESSAGE


def userFacingError(action, hint=None):
    """The default: name the operation that failed, point at the log for the detail.

    `action` is a short noun phrase describing what was attempted, e.g. "create the database
    backup". `hint` adds one sentence the user can act on, when there is one.
    """
    message = "Could not " + action + ". See the OctoPrint log for details."
    if hint:
        message = message + " " + hint
    return message
