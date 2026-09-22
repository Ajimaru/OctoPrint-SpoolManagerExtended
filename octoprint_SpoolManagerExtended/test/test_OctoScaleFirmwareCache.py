# coding=utf-8

# Tests for the firmware verdict CACHE, not the parser (that is test_OctoScaleFirmware.py).
# The cache is what decides how often the device is asked and when a block lifts again, so
# the cases that matter are the recovery ones: a device that was unreachable must be asked
# again, and a verdict must never be reused for a different address.
#
# The cache methods live on the SpoolManagerAPI mixin, which cannot be imported without
# flask/OctoPrint. They only touch self, so they are rebound here onto a plain object
# together with a fake _callOctoScale - the logic under test is unchanged, only its
# transport and its host class are stand-ins.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_OctoScaleFirmwareCache.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import os
import sys
import threading
import types
import unittest

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMMON_DIR = os.path.join(_PLUGIN_DIR, "common")

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


class _FakeResponse(object):
    def __init__(self, text):
        self.text = text


class _FakeLogger(object):
    def warning(self, message):
        pass


class FakeOctoScaleHost(object):
    """Stands in for the plugin: same cache methods, a scripted _callOctoScale."""

    _octoScaleFirmwareLock = threading.Lock()

    def __init__(self, answers):
        # answers: list of (bodyOrNone, errorMessageOrNone), consumed one per probe
        self._answers = list(answers)
        self.callCount = 0
        self._logger = _FakeLogger()

    def _callOctoScale(self, baseUrl, path):
        self.callCount += 1
        if not self._answers:
            raise AssertionError("probed more often than the test scripted")
        body, errorMessage = self._answers.pop(0)
        if errorMessage is not None:
            return (None, errorMessage)
        return (_FakeResponse(body), None)

    # --- verbatim copies of the methods under test (see module docstring) ---

    def _getOctoScaleFirmwareCache(self):
        return getattr(self, "_octoScaleFirmwareState", None)

    def _setOctoScaleFirmwareCache(self, baseUrl, verdict):
        entry = dict(verdict)
        entry["checkedUrl"] = baseUrl
        with self._octoScaleFirmwareLock:
            self._octoScaleFirmwareState = entry
        return entry

    def invalidateOctoScaleFirmwareCache(self):
        with self._octoScaleFirmwareLock:
            self._octoScaleFirmwareState = None

    def _probeOctoScaleFirmware(self, baseUrl):
        response, errorMessage = self._callOctoScale(baseUrl, "/version")
        if errorMessage is not None:
            verdict = OctoScaleFirmware.evaluateUnreachable(errorMessage)
        else:
            verdict = OctoScaleFirmware.evaluateVersionBody(response.text)
        return self._setOctoScaleFirmwareCache(baseUrl, verdict)

    def getOctoScaleFirmwareVerdict(self, baseUrl, forceRecheck=False):
        cached = self._getOctoScaleFirmwareCache()
        if (
            forceRecheck
            or cached is None
            or cached.get("checkedUrl") != baseUrl
            or cached.get("status") == OctoScaleFirmware.STATUS_UNKNOWN
        ):
            return self._probeOctoScaleFirmware(baseUrl)
        return cached


URL = "http://192.0.2.20"
OTHER_URL = "http://192.0.2.99"

OK_BODY = ("OctoScale v0.0.4-dev1 (Build Sep 20 2026 08:00:00)", None)
OLD_BODY = ("OctoScale v0.0.3-dev32 (Build Sep 19 2026 12:52:24)", None)
UNREACHABLE = (None, "Could not reach OctoScale: connection refused")


class TestFakeMatchesProduction(unittest.TestCase):
    """The methods above are copies, so they can silently drift from the real ones.

    This compares them against the source of SpoolManagerAPI.py textually - crude, but it
    is the only thing standing between "these tests pass" and "these tests still describe
    the shipped code". If it fails, the copies above need updating, not deleting.
    """

    def _productionSource(self):
        path = os.path.join(_PLUGIN_DIR, "api", "SpoolManagerAPI.py")
        with open(path, "r") as handle:
            return handle.read()

    def test_copied_methods_still_exist_in_the_api(self):
        source = self._productionSource()
        for name in [
            "_getOctoScaleFirmwareCache",
            "_setOctoScaleFirmwareCache",
            "invalidateOctoScaleFirmwareCache",
            "_probeOctoScaleFirmware",
            "getOctoScaleFirmwareVerdict",
        ]:
            self.assertIn(
                "def " + name + "(",
                source,
                name
                + " is gone from SpoolManagerAPI.py - update the copy in this test",
            )

    def test_transport_errors_are_not_echoed_to_the_client(self):
        """CodeQL alerts 45/46: str(e) from requests carried the urllib3 chain - host,
        port, OS error - into JSON the browser renders. The detail belongs in the log.

        Checked against the source rather than by calling it, because the leak was in a
        branch that only a dead socket reaches. If this fails, look at whether a new
        `"... " + str(e)` was handed to a caller that returns it, not at this test.
        """
        source = self._productionSource()
        start = source.index("def _callOctoScale(")
        end = source.index("def _octoScaleWeightOrError(")
        body = source[start:end]

        self.assertNotIn(
            'return (None, "Could not reach OctoScale: " + str(e))',
            body,
            "the raw exception text is being returned to the caller again",
        )
        self.assertIn(
            "self._logger.warning(",
            body,
            "the exception detail must still be logged, only not returned",
        )

    def test_reprobe_condition_still_matches(self):
        # The single most important line: drop the "unknown" clause and the recovery path
        # from a powered-off device disappears, with every test here still passing.
        source = self._productionSource()
        self.assertIn(
            'cached.get("status") == OctoScaleFirmware.STATUS_UNKNOWN',
            source,
            "the re-probe-on-unknown condition changed - update the copy in this test",
        )


class TestVerdictCaching(unittest.TestCase):
    def test_first_call_probes(self):
        host = FakeOctoScaleHost([OK_BODY])
        verdict = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(OctoScaleFirmware.STATUS_OK, verdict["status"])
        self.assertEqual(1, host.callCount)

    def test_ok_verdict_is_reused(self):
        # /weight polls once a second - re-probing per call would double the device traffic
        host = FakeOctoScaleHost([OK_BODY])
        host.getOctoScaleFirmwareVerdict(URL)
        for _ in range(5):
            host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(1, host.callCount)

    def test_too_old_verdict_is_reused(self):
        # a device does not get newer by asking again, and each ask can cost 8s
        host = FakeOctoScaleHost([OLD_BODY])
        first = host.getOctoScaleFirmwareVerdict(URL)
        second = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(OctoScaleFirmware.STATUS_TOO_OLD, first["status"])
        self.assertEqual(OctoScaleFirmware.STATUS_TOO_OLD, second["status"])
        self.assertEqual(1, host.callCount)

    def test_unknown_verdict_is_reprobed(self):
        # THE recovery path: OctoPrint started while the device was off, so the first probe
        # failed. Without this the user would stay stuck on that verdict until a restart.
        host = FakeOctoScaleHost([UNREACHABLE, OK_BODY])
        first = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(OctoScaleFirmware.STATUS_UNKNOWN, first["status"])
        second = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(OctoScaleFirmware.STATUS_OK, second["status"])
        self.assertEqual(2, host.callCount)

    def test_unreachable_never_reports_too_old(self):
        # fail open: a device nobody could reach must not be blocked
        host = FakeOctoScaleHost([UNREACHABLE])
        verdict = host.getOctoScaleFirmwareVerdict(URL)
        self.assertNotEqual(OctoScaleFirmware.STATUS_TOO_OLD, verdict["status"])

    def test_verdict_is_not_reused_for_a_different_address(self):
        # pointing at another device must not inherit the first one's verdict
        host = FakeOctoScaleHost([OLD_BODY, OK_BODY])
        self.assertEqual(
            OctoScaleFirmware.STATUS_TOO_OLD,
            host.getOctoScaleFirmwareVerdict(URL)["status"],
        )
        self.assertEqual(
            OctoScaleFirmware.STATUS_OK,
            host.getOctoScaleFirmwareVerdict(OTHER_URL)["status"],
        )
        self.assertEqual(2, host.callCount)

    def test_force_recheck_probes_again(self):
        # the "Re-check firmware" button after flashing the device
        host = FakeOctoScaleHost([OLD_BODY, OK_BODY])
        host.getOctoScaleFirmwareVerdict(URL)
        verdict = host.getOctoScaleFirmwareVerdict(URL, forceRecheck=True)
        self.assertEqual(OctoScaleFirmware.STATUS_OK, verdict["status"])
        self.assertEqual(2, host.callCount)

    def test_invalidate_forces_a_fresh_probe(self):
        # what on_settings_save does when the address or the enabled flag changed
        host = FakeOctoScaleHost([OLD_BODY, OK_BODY])
        host.getOctoScaleFirmwareVerdict(URL)
        host.invalidateOctoScaleFirmwareCache()
        verdict = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(OctoScaleFirmware.STATUS_OK, verdict["status"])
        self.assertEqual(2, host.callCount)

    def test_cache_entry_records_the_address(self):
        host = FakeOctoScaleHost([OK_BODY])
        verdict = host.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(URL, verdict["checkedUrl"])

    def test_cache_is_per_instance(self):
        # the lock is a class attribute; the state must not be
        first = FakeOctoScaleHost([OLD_BODY])
        second = FakeOctoScaleHost([OK_BODY])
        first.getOctoScaleFirmwareVerdict(URL)
        self.assertEqual(
            OctoScaleFirmware.STATUS_OK,
            second.getOctoScaleFirmwareVerdict(URL)["status"],
        )


if __name__ == "__main__":
    unittest.main()
