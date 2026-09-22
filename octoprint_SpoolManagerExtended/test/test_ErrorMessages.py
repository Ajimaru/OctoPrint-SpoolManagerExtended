# coding=utf-8

# Tests for the user-facing error message helpers.
#
# The point of these is a security property, not formatting: the strings handed to the browser
# must not contain the connection details or server paths that the driver puts into its own
# exception text. The leak tests below therefore use marker values that cannot appear in the
# expected output by chance - if "db.internal.example" shows up, it can only have come from the
# exception.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_ErrorMessages.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import os
import sys
import types
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManagerExtended/__init__.py pulls
# in flask and OctoPrint, which are not available in a bare test environment. Same harness as
# test_OctoScaleUrl.py.
_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)

_PACKAGE_NAME = "spoolmanager_test_common_pkg"
if _PACKAGE_NAME not in sys.modules:
    _package = types.ModuleType(_PACKAGE_NAME)
    _package.__path__ = [_COMMON_DIR]
    sys.modules[_PACKAGE_NAME] = _package


def _loadModule(moduleName):
    modulePath = os.path.join(_COMMON_DIR, moduleName + ".py")
    qualifiedName = _PACKAGE_NAME + "." + moduleName
    spec = importlib.util.spec_from_file_location(qualifiedName, modulePath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualifiedName] = module
    spec.loader.exec_module(module)
    return module


ErrorMessages = _loadModule("ErrorMessages")


# Realistic driver texts. Taken from the shape peewee/pymysql actually produce, with the
# identifying parts replaced by markers the expected output can never contain.
_ACCESS_DENIED = (
    "(1045, \"Access denied for user 'spooluser'@'192.0.2.50' (using password: YES)\")"
)
_UNKNOWN_DATABASE = "(1049, \"Unknown database 'spoolmanager_secret'\")"
_CANT_CONNECT = (
    "(2003, \"Can't connect to MySQL server on 'db.internal.example' "
    '([Errno 111] Connection refused)")'
)
_UNKNOWN_HOST = (
    "(2005, \"Unknown server host 'db.internal.example' ([Errno -2] Name or service "
    'not known)")'
)
_TIMEOUT = "(2013, 'Lost connection to MySQL server during query (timed out)')"

_SECRET_MARKERS = (
    "spooluser",
    "192.0.2.50",
    "db.internal.example",
    "spoolmanager_secret",
    "1045",
    "2003",
    "Errno",
)


class TestClassifyConnectionError(unittest.TestCase):
    def test_access_denied_is_reported_as_credentials(self):
        self.assertEqual(
            "Access denied. Check the user name and password.",
            ErrorMessages.classifyConnectionError(Exception(_ACCESS_DENIED)),
        )

    def test_unknown_database_is_reported_as_database_name(self):
        self.assertEqual(
            "The database does not exist on the server. Check the database name.",
            ErrorMessages.classifyConnectionError(Exception(_UNKNOWN_DATABASE)),
        )

    def test_unreachable_server_is_reported_as_host(self):
        expected = (
            "The database server could not be reached. Check the host name and port."
        )
        self.assertEqual(
            expected, ErrorMessages.classifyConnectionError(Exception(_CANT_CONNECT))
        )
        self.assertEqual(
            expected, ErrorMessages.classifyConnectionError(Exception(_UNKNOWN_HOST))
        )

    def test_timeout_is_reported_separately(self):
        # a reachable but slow/hanging server is a different thing to tell the user than a
        # wrong host name - it must not collapse into the generic message
        self.assertEqual(
            "The database server did not answer in time.",
            ErrorMessages.classifyConnectionError(Exception(_TIMEOUT)),
        )

    def test_unrecognized_error_falls_back_to_generic_message(self):
        self.assertEqual(
            "Could not connect to the database. Check the connection settings.",
            ErrorMessages.classifyConnectionError(Exception("something entirely new")),
        )

    def test_no_connection_detail_survives_classification(self):
        # the actual security property: whatever the driver put into its message, none of it
        # may reach the response
        for text in (
            _ACCESS_DENIED,
            _UNKNOWN_DATABASE,
            _CANT_CONNECT,
            _UNKNOWN_HOST,
            _TIMEOUT,
        ):
            result = ErrorMessages.classifyConnectionError(Exception(text))
            for marker in _SECRET_MARKERS:
                self.assertNotIn(marker, result, "leaked " + marker + " from: " + text)

    def test_classification_is_case_insensitive(self):
        # drivers differ in capitalization between versions
        self.assertEqual(
            "Access denied. Check the user name and password.",
            ErrorMessages.classifyConnectionError(Exception("ACCESS DENIED for user")),
        )


class TestUserFacingError(unittest.TestCase):
    def test_names_the_action_and_points_at_the_log(self):
        self.assertEqual(
            "Could not create the database backup. See the OctoPrint log for details.",
            ErrorMessages.userFacingError("create the database backup"),
        )

    def test_hint_is_appended(self):
        self.assertEqual(
            "Could not import the dump. See the OctoPrint log for details."
            " The database might be partially restored.",
            ErrorMessages.userFacingError(
                "import the dump", "The database might be partially restored."
            ),
        )

    def test_exception_text_is_never_part_of_the_message(self):
        # userFacingError deliberately does not take the exception at all - this test pins
        # that down, so the signature cannot quietly grow one back
        message = ErrorMessages.userFacingError("read the undo record")
        self.assertNotIn("/Volumes", message)
        self.assertNotIn("Errno", message)


class TestClassifyFetchError(unittest.TestCase):
    # A real urllib3 chain, of the shape requests actually produces
    _CONNECT_FAILURE = (
        "HTTPSConnectionPool(host='raw.githubusercontent.com', port=443): Max retries "
        "exceeded with url: /SpoolmanDB/filaments.json (Caused by NewConnectionError("
        "'<urllib3.connection.HTTPSConnection object at 0x7f9c1a2b3c40>: Failed to "
        "establish a new connection: [Errno 111] Connection refused'))"
    )

    def test_none_stays_none(self):
        # a successful refresh must not grow an "error" key, and must never report the
        # literal string "None" - that was the TigerTag bug
        self.assertIsNone(ErrorMessages.classifyFetchError(None))
        self.assertNotEqual("None", ErrorMessages.classifyFetchError(None))

    def test_timeout(self):
        self.assertEqual(
            "timeout",
            ErrorMessages.classifyFetchError(Exception("Read timed out after 10s")),
        )

    def test_dns_failure(self):
        self.assertEqual(
            "dns",
            ErrorMessages.classifyFetchError(
                Exception("[Errno -2] Name or service not known")
            ),
        )

    def test_connection_refused(self):
        self.assertEqual(
            "connection",
            ErrorMessages.classifyFetchError(Exception(self._CONNECT_FAILURE)),
        )

    def test_http_status_is_kept(self):
        self.assertEqual(
            "http-404",
            ErrorMessages.classifyFetchError(
                Exception(
                    "404 Client Error: Not Found for url: https://example.org/a.json"
                )
            ),
        )

    def test_parse_failure(self):
        self.assertEqual(
            "parse",
            ErrorMessages.classifyFetchError(
                Exception("Expecting value: line 1 column 1 (char 0) json decode")
            ),
        )

    def test_size_limit(self):
        self.assertEqual(
            "too-large",
            ErrorMessages.classifyFetchError(
                Exception("SpoolmanDB response exceeds the configured size limit")
            ),
        )

    def test_unknown_falls_back_to_error(self):
        self.assertEqual(
            "error", ErrorMessages.classifyFetchError(Exception("something else"))
        )

    def test_no_url_or_host_survives_classification(self):
        # the security property: the class must not carry the transport detail
        result = ErrorMessages.classifyFetchError(Exception(self._CONNECT_FAILURE))
        for marker in (
            "raw.githubusercontent.com",
            "443",
            "urllib3",
            "Errno",
            "SpoolmanDB",
            "0x7f9c1a2b3c40",
        ):
            self.assertNotIn(marker, result)


if __name__ == "__main__":
    unittest.main()
