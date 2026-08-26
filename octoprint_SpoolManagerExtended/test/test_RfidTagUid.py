# coding=utf-8

# Tests for tag UID plausibility and key derivation.
#
# These exist because of a concrete failure mode: the OctoScale firmware's NFC-A
# anticollision runs in two cascade levels, and an abort in the second one leaves a 3-byte
# fragment of a 7-byte UID that the reader still reports as a successful read. The fragment
# derives a *different* rfidTagKey than the same tag's full UID, and that key would be
# stored on a spool - binding it to a value the tag never presents again.
#
# The important property proven below is why deriveRfidTagKey() cannot guard against this
# on its own: every truncated UID still yields a perfectly well-formed 4-character key.
# Nothing about the key itself looks wrong, so the length has to be checked before it.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_RfidTagUid.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import os
import sys
import types
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManagerExtended/__init__.py pulls in
# flask and OctoPrint, which are not available in a bare test environment. U1RfidManager
# itself only imports from common/, so it loads cleanly this way. Same harness as
# test_OpenPrintTag.py, with one extra step: the common/ package has to exist under the
# real package name before U1RfidManager's own import of it can resolve.
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMMON_DIR = os.path.join(_PLUGIN_DIR, "common")


def _ensurePackage(name, path):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [path]
        package.__package__ = name
        sys.modules[name] = package


_ensurePackage("octoprint_SpoolManagerExtended", _PLUGIN_DIR)
_ensurePackage("octoprint_SpoolManagerExtended.common", _COMMON_DIR)


def _loadModule(moduleName, modulePath):
    spec = importlib.util.spec_from_file_location(moduleName, modulePath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[moduleName] = module
    spec.loader.exec_module(module)
    return module


_loadModule(
    "octoprint_SpoolManagerExtended.common.SettingsKeys",
    os.path.join(_COMMON_DIR, "SettingsKeys.py"),
)
U1RfidManager = _loadModule(
    "octoprint_SpoolManagerExtended.U1RfidManager",
    os.path.join(_PLUGIN_DIR, "U1RfidManager.py"),
)

normalizeCardUid = U1RfidManager.normalizeCardUid
isPlausibleTagUid = U1RfidManager.isPlausibleTagUid
deriveRfidTagKey = U1RfidManager.deriveRfidTagKey


class TestIsPlausibleTagUid(unittest.TestCase):
    def test_acceptsRealUidLengths(self):
        # 4 and 7 bytes are the two NFC-A cascade outcomes, 8 bytes is NFC-V.
        self.assertTrue(isPlausibleTagUid("A1B2C3D4"))
        self.assertTrue(isPlausibleTagUid("04AC6F56CB2A81"))
        self.assertTrue(isPlausibleTagUid("E00401502F1A2B3C"))

    def test_rejectsTruncatedAnticollisionResult(self):
        # The observed real-world case: cascade level 2 aborted, leaving CL1's 3 bytes.
        self.assertFalse(isPlausibleTagUid("04AC6F"))

    def test_rejectsOtherImpossibleLengths(self):
        self.assertFalse(isPlausibleTagUid("AC6F"))
        self.assertFalse(isPlausibleTagUid("04AC6F56CB"))

    def test_rejectsEmptyAndNone(self):
        self.assertFalse(isPlausibleTagUid(""))
        self.assertFalse(isPlausibleTagUid(None))


class TestTruncatedUidProducesWrongKey(unittest.TestCase):
    # The reason the length check has to exist at all.

    def test_fragmentDerivesDifferentKeyThanFullUid(self):
        fullUid = normalizeCardUid("04AC6F56CB2A81")
        fragment = normalizeCardUid("04AC6F")

        self.assertEqual("2A81", deriveRfidTagKey(fullUid))
        self.assertEqual("AC6F", deriveRfidTagKey(fragment))
        self.assertNotEqual(deriveRfidTagKey(fullUid), deriveRfidTagKey(fragment))

    def test_deriveRfidTagKeyAloneCannotDetectTruncation(self):
        # Every fragment long enough to reach deriveRfidTagKey yields a well-formed key,
        # so the derived value carries no evidence that the read was incomplete. This is
        # what makes the failure silent, and why callers must check the UID first.
        for fragment in ("04AC6F", "AC6F", "04AC6F56CB"):
            key = deriveRfidTagKey(normalizeCardUid(fragment))
            self.assertIsNotNone(key)
            self.assertEqual(4, len(key))
            self.assertFalse(isPlausibleTagUid(normalizeCardUid(fragment)))


if __name__ == "__main__":
    unittest.main()
