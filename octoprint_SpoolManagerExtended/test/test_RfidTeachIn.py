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

    def test_legacy_suffix_key_is_upgraded_without_force(self):
        # Taught in while 7-byte UIDs were still keyed on their last 4 hex chars, the
        # spool carries "E5F6"; the tag now derives its whole UID. Blocking this as
        # "existing key differs" would ask the user to confirm replacing a key the tag no
        # longer presents.
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="04A1B2C3D4E5F6",
            existingKeyOnTargetSpool="E5F6",
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=False,
        )
        self.assertTrue(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_TAUGHT)

    def test_unrelated_short_key_still_blocks_a_full_uid(self):
        # Only the suffix of the new UID counts as its legacy form - any other short key
        # is a different tag's key and stays protected.
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="04A1B2C3D4E5F6",
            existingKeyOnTargetSpool="1040",
            conflictingSpoolId=None,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_EXISTING_KEY_DIFFERS)

    def test_legacy_upgrade_still_respects_a_collision(self):
        shouldSave, reason = RfidTeachIn.evaluateTeachIn(
            newKey="04A1B2C3D4E5F6",
            existingKeyOnTargetSpool="E5F6",
            conflictingSpoolId=7,
            targetSpoolId=42,
            force=False,
        )
        self.assertFalse(shouldSave)
        self.assertEqual(reason, RfidTeachIn.REASON_COLLISION)


class TestIsLegacySuffixKeyOf(unittest.TestCase):
    def test_suffix_of_a_full_uid_is_legacy(self):
        self.assertTrue(RfidTeachIn.isLegacySuffixKeyOf("E5F6", "04A1B2C3D4E5F6"))

    def test_other_short_key_is_not_legacy(self):
        self.assertFalse(RfidTeachIn.isLegacySuffixKeyOf("1040", "04A1B2C3D4E5F6"))

    def test_equal_short_keys_are_not_legacy(self):
        # two 4-byte UIDs keyed on the same suffix - that is a collision, not an upgrade
        self.assertFalse(RfidTeachIn.isLegacySuffixKeyOf("E5F6", "E5F6"))

    def test_full_uid_is_never_the_legacy_form_of_a_short_key(self):
        self.assertFalse(RfidTeachIn.isLegacySuffixKeyOf("04A1B2C3D4E5F6", "E5F6"))

    def test_missing_keys_are_not_legacy(self):
        self.assertFalse(RfidTeachIn.isLegacySuffixKeyOf(None, "04A1B2C3D4E5F6"))
        self.assertFalse(RfidTeachIn.isLegacySuffixKeyOf("E5F6", None))


if __name__ == "__main__":
    unittest.main()
