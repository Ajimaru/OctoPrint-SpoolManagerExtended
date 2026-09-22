# coding=utf-8

# Tests for the OctoScale firmware version gate. The decision this module makes is a hard
# block on a user's hardware, so the unreadable/unreachable cases matter as much as the
# version arithmetic: a parser that turns a surprise into "too old" would lock people out.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_OctoScaleFirmware.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import os
import sys
import time
import types
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManagerExtended/__init__.py pulls in flask
# and OctoPrint, which are not available in a bare test environment. Same harness as
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


OctoScaleFirmware = _loadModule("OctoScaleFirmware")


def _status(body):
    return OctoScaleFirmware.evaluateVersionBody(body)["status"]


class TestParseVersionBody(unittest.TestCase):
    def test_real_device_body(self):
        # verbatim from a device running the firmware this gate was written against
        version, errorMessage = OctoScaleFirmware.parseVersionBody(
            "OctoScale v0.0.3-dev32 (Build Sep 19 2026 12:52:24)"
        )
        self.assertIsNone(errorMessage)
        self.assertEqual((0, 0, 3), version["numbers"])
        self.assertEqual("dev32", version["suffix"])
        self.assertEqual("v0.0.3-dev32", version["display"])

    def test_release_body_without_suffix(self):
        version, errorMessage = OctoScaleFirmware.parseVersionBody(
            "OctoScale v0.0.4 (Build Sep 20 2026 08:00:00)"
        )
        self.assertIsNone(errorMessage)
        self.assertEqual((0, 0, 4), version["numbers"])
        self.assertIsNone(version["suffix"])
        self.assertEqual("v0.0.4", version["display"])

    def test_bare_version_without_prefix(self):
        # the parser must not depend on the "OctoScale " prefix - see _VERSION_PATTERN
        version, errorMessage = OctoScaleFirmware.parseVersionBody("0.0.4")
        self.assertIsNone(errorMessage)
        self.assertEqual((0, 0, 4), version["numbers"])

    def test_surrounding_whitespace_and_newlines(self):
        version, errorMessage = OctoScaleFirmware.parseVersionBody(
            "  OctoScale v0.0.4-rc1 \r\n"
        )
        self.assertIsNone(errorMessage)
        self.assertEqual("v0.0.4-rc1", version["display"])
        self.assertEqual("OctoScale v0.0.4-rc1", version["raw"])

    def test_empty_body_is_an_error(self):
        version, errorMessage = OctoScaleFirmware.parseVersionBody("")
        self.assertIsNone(version)
        self.assertIsNotNone(errorMessage)

    def test_none_body_is_an_error(self):
        version, errorMessage = OctoScaleFirmware.parseVersionBody(None)
        self.assertIsNone(version)
        self.assertIsNotNone(errorMessage)

    def test_unreadable_body_is_an_error(self):
        version, errorMessage = OctoScaleFirmware.parseVersionBody("OK")
        self.assertIsNone(version)
        self.assertIsNotNone(errorMessage)

    def test_error_message_is_truncated(self):
        # an HTTP error page instead of a device answer must not end up in the UI whole
        version, errorMessage = OctoScaleFirmware.parseVersionBody("x" * 500)
        self.assertIsNone(version)
        self.assertLess(len(errorMessage), 200)


class TestCompareToMinimum(unittest.TestCase):
    def test_exact_minimum_is_ok(self):
        self.assertEqual(
            OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.0.4 (Build ...)")
        )

    def test_minimum_with_dev_suffix_is_ok(self):
        # the whole point of "all suffixes": a dev build of the required version qualifies
        self.assertEqual(
            OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.0.4-dev1 (Build ...)")
        )

    def test_minimum_with_rc_suffix_is_ok(self):
        self.assertEqual(OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.0.4-rc1"))

    def test_newer_patch_is_ok(self):
        self.assertEqual(
            OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.0.5-dev1 (Build ...)")
        )

    def test_newer_minor_is_ok(self):
        self.assertEqual(OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.1.0"))

    def test_newer_major_is_ok(self):
        self.assertEqual(OctoScaleFirmware.STATUS_OK, _status("OctoScale v1.0.0"))

    def test_double_digit_patch_compares_numerically(self):
        # a string compare would put "10" before "4" and wrongly reject this
        self.assertEqual(OctoScaleFirmware.STATUS_OK, _status("OctoScale v0.0.10"))

    def test_older_patch_is_too_old(self):
        self.assertEqual(
            OctoScaleFirmware.STATUS_TOO_OLD,
            _status("OctoScale v0.0.3-dev32 (Build Sep 19 2026 12:52:24)"),
        )

    def test_older_release_is_too_old(self):
        self.assertEqual(OctoScaleFirmware.STATUS_TOO_OLD, _status("OctoScale v0.0.3"))

    def test_much_older_is_too_old(self):
        self.assertEqual(OctoScaleFirmware.STATUS_TOO_OLD, _status("OctoScale v0.0.1"))

    def test_unparseable_version_is_unknown_not_too_old(self):
        # the distinction the whole fail-open behaviour rests on
        for body in [None, "", "OK", "<html>404 Not Found</html>"]:
            self.assertEqual(
                OctoScaleFirmware.STATUS_UNKNOWN,
                _status(body),
                "body %r should be unknown, never tooOld" % (body,),
            )

    def test_compare_tolerates_none(self):
        self.assertEqual(
            OctoScaleFirmware.STATUS_UNKNOWN, OctoScaleFirmware.compareToMinimum(None)
        )


class TestEvaluateVersionBody(unittest.TestCase):
    def test_ok_carries_no_message(self):
        result = OctoScaleFirmware.evaluateVersionBody("OctoScale v0.0.4-dev1")
        self.assertEqual(OctoScaleFirmware.STATUS_OK, result["status"])
        self.assertEqual("v0.0.4-dev1", result["firmwareVersion"])
        self.assertEqual("v0.0.4", result["requiredVersion"])
        self.assertIsNone(result["message"])

    def test_too_old_names_both_versions(self):
        result = OctoScaleFirmware.evaluateVersionBody("OctoScale v0.0.3-dev32")
        self.assertEqual(OctoScaleFirmware.STATUS_TOO_OLD, result["status"])
        self.assertEqual("v0.0.3-dev32", result["firmwareVersion"])
        # the user has to learn what they have and what they need, in one sentence
        self.assertIn("v0.0.3-dev32", result["message"])
        self.assertIn("v0.0.4", result["message"])

    def test_unknown_has_no_version_but_keeps_required(self):
        result = OctoScaleFirmware.evaluateVersionBody("OK")
        self.assertEqual(OctoScaleFirmware.STATUS_UNKNOWN, result["status"])
        self.assertIsNone(result["firmwareVersion"])
        self.assertEqual("v0.0.4", result["requiredVersion"])
        self.assertIsNotNone(result["message"])

    def test_unreachable_matches_the_parsed_shape(self):
        # both cache paths must store the same keys, see _probeOctoScaleFirmware
        reachable = OctoScaleFirmware.evaluateVersionBody("OctoScale v0.0.4")
        unreachable = OctoScaleFirmware.evaluateUnreachable("Could not reach OctoScale")
        self.assertEqual(sorted(reachable.keys()), sorted(unreachable.keys()))
        self.assertEqual(OctoScaleFirmware.STATUS_UNKNOWN, unreachable["status"])
        self.assertEqual("Could not reach OctoScale", unreachable["message"])


class TestHostileInput(unittest.TestCase):
    """The body comes off the network, so a malformed one must stay cheap and harmless.

    CodeQL alert 44: the original pattern used search(), which retries at every position.
    A body of N digits with no match cost O(N^2) - 20k digits measured at 1.8s, 60k at
    15.8s, blocking an OctoPrint request thread the whole time.
    """

    def test_long_digit_run_is_fast(self):
        # The exact shape CodeQL flagged. The budget is generous on purpose: it is there to
        # catch a return to quadratic behaviour, not to measure the machine.
        body = "9" * 100000
        start = time.time()
        result = OctoScaleFirmware.evaluateVersionBody(body)
        elapsed = time.time() - start
        self.assertEqual(OctoScaleFirmware.STATUS_UNKNOWN, result["status"])
        self.assertLess(
            elapsed,
            1.0,
            "parsing %d digits took %.2fs - quadratic again?" % (len(body), elapsed),
        )

    def test_long_suffix_is_fast(self):
        body = "0.0.4-" + ("a" * 100000)
        start = time.time()
        OctoScaleFirmware.evaluateVersionBody(body)
        self.assertLess(time.time() - start, 1.0)

    def test_oversized_body_is_truncated_before_matching(self):
        # A version hidden past the limit must not be found: that is the limit working, not
        # a parsing bug. Anything that far into the body is not a version answer.
        body = ("x" * OctoScaleFirmware.MAX_VERSION_BODY_LENGTH) + "v0.0.4"
        self.assertEqual(
            OctoScaleFirmware.STATUS_UNKNOWN,
            OctoScaleFirmware.evaluateVersionBody(body)["status"],
        )

    def test_error_message_never_echoes_the_whole_body(self):
        # the message is shown in the UI; a megabyte of it must not be
        result = OctoScaleFirmware.evaluateVersionBody("q" * 100000)
        self.assertLess(len(result["message"]), 200)

    def test_absurd_version_numbers_do_not_crash(self):
        # int() on an unbounded digit run is its own denial of service
        result = OctoScaleFirmware.evaluateVersionBody(("9" * 5000) + ".0.4")
        self.assertIn(
            result["status"],
            (OctoScaleFirmware.STATUS_OK, OctoScaleFirmware.STATUS_UNKNOWN),
        )


class TestFormatVersion(unittest.TestCase):
    def test_minimum_display_matches_the_constant(self):
        # guards the constant and its display string against drifting apart
        self.assertEqual(
            OctoScaleFirmware.formatVersion(OctoScaleFirmware.MINIMUM_FIRMWARE),
            OctoScaleFirmware.MINIMUM_FIRMWARE_DISPLAY,
        )


if __name__ == "__main__":
    unittest.main()
