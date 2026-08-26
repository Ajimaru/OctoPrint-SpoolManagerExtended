# coding=utf-8

# Tests for common/RfidTeachIn.py - the pure decision logic behind
# POST /octoscale/teachRfidTagKey (auto-teaching a spool's rfidTagKey from an OpenPrintTag
# write's tag UID). Dependency-free by design, loaded by path like test_OpenPrintTag.py so
# it runs without flask/OctoPrint/peewee.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_RfidTeachIn.py

import importlib.util
import os
import unittest

_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)


def _loadModule(moduleName):
    modulePath = os.path.join(_COMMON_DIR, moduleName + ".py")
    spec = importlib.util.spec_from_file_location(
        "spoolmanager_test_" + moduleName, modulePath
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RfidTeachIn = _loadModule("RfidTeachIn")


class TestEvaluateTeachIn(unittest.TestCase):

    def test_fresh_spool_is_taught(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool=None,
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=False,
        )
        self.assertTrue(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_TAUGHT)

    def test_no_uid_is_a_graceful_noop(self):
        # None (UID absent/too short) must not be reported as a failure
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey=None,
            existingKeyOnTargetSpool=None,
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_NO_UID)

    def test_unchanged_key_is_a_noop(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool="D3EA",
            conflictingSpoolId=42,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_UNCHANGED)

    def test_different_existing_key_is_blocked_without_force(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool="AAAA",
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_EXISTING_KEY_DIFFERS)

    def test_different_existing_key_is_overridden_with_force(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool="AAAA",
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=True,
        )
        self.assertTrue(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_TAUGHT)

    def test_collision_with_another_spool_is_blocked_without_force(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool=None,
            conflictingSpoolId=7,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_COLLISION)

    def test_collision_with_another_spool_is_overridden_with_force(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool=None,
            conflictingSpoolId=7,
            targetSpoolId=42,
            force=True,
        )
        self.assertTrue(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_TAUGHT)

    def test_conflict_with_the_target_spool_itself_is_not_a_collision(self):
        # loadSpoolByRfidTagKey() resolving to the same spool we're about to save onto is
        # not a collision - e.g. re-writing the same tag for the same spool.
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="D3EA",
            existingKeyOnTargetSpool=None,
            conflictingSpoolId=42,
            targetSpoolId=42,
            force=False,
        )
        self.assertTrue(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_TAUGHT)

    def test_no_uid_takes_precedence_over_everything_else(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey=None,
            existingKeyOnTargetSpool="AAAA",
            conflictingSpoolId=7,
            targetSpoolId=42,
            force=True,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_NO_UID)


if __name__ == "__main__":
    unittest.main()
