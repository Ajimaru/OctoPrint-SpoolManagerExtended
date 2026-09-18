# coding=utf-8

# Tests for common/UnverifiableSpoolId.py - the pure decision logic behind the
# "spoolIdIsUnverifiable" flag on GET /octoscale/nfc. Dependency-free by design, loaded by path
# like test_RfidTeachIn.py so it runs without flask/OctoPrint/peewee.
#
# The cases below are built from real hardware measurements taken on 2026-09-18 against
# OctoScale firmware v0.0.3 (reader at the U1 test bench), not from invented values - see the
# per-test comments for the tag each one stands for.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_UnverifiableSpoolId.py

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


UnverifiableSpoolId = _loadModule("UnverifiableSpoolId")


class TestIsSpoolIdUnverifiable(unittest.TestCase):
    def test_legacy_id_pointing_at_no_spool_is_unverifiable(self):
        # The reported tag: NTAG215, UID 04D64B55CB2A81, pages 4-6 holding ASCII "81" padded
        # with spaces, no magic and no NDEF. Firmware reports idParsed 81 / occupancy "foreign"
        # / hasExtendedData false, and spool 81 does not exist in the database.
        self.assertTrue(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=81,
                occupancy="foreign",
                hasExtendedData=False,
                spoolExistsInDatabase=False,
            )
        )

    def test_legacy_id_of_a_known_spool_stays_normal(self):
        # The regression that matters most: a tag written by the firmware's own legacy
        # /nfcwriteid path is byte-identical to the one above, so it also reports "foreign".
        # As long as its id resolves to a real spool the user can reconcile it, and the existing
        # overwrite flow must stay untouched.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=110,
                occupancy="foreign",
                hasExtendedData=False,
                spoolExistsInDatabase=True,
            )
        )

    def test_extended_tag_is_trusted_even_without_an_occupancy_verdict(self):
        # Measured on the ntagExtended reference tag (UID 045330AC3A0289, databaseId 120): the
        # firmware skips the occupancy computation entirely for tags carrying extended data, so
        # the field arrives as "" rather than any verdict. The magic+CRC-checked payload is what
        # makes the id trustworthy here.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=120,
                occupancy="",
                hasExtendedData=True,
                spoolExistsInDatabase=True,
            )
        )

    def test_extended_tag_whose_id_is_unknown_is_still_trusted(self):
        # Same as above but for a spool that has since been deleted. The id came out of a
        # verified payload, so it is not "unverifiable" - it is simply stale, which is the
        # existing overwrite-confirmation case, not this one.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=999,
                occupancy="",
                hasExtendedData=True,
                spoolExistsInDatabase=False,
            )
        )

    def test_extended_payload_outranks_a_foreign_verdict(self):
        # The case that isolates the hasExtendedData guard: everything else here matches the
        # unverifiable pattern (id present, occupancy "foreign", id unknown to this database),
        # so only the extended payload can rule it out. Kept as its own test because the two
        # extended cases above pass occupancy "" and would still hold if the guard were dropped.
        #
        # Current firmware never produces this combination - it skips occupancy entirely when
        # extended data is present - but the guard must not depend on that: firmware that starts
        # reporting both would otherwise flip verified tags to "unverifiable".
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=999,
                occupancy="foreign",
                hasExtendedData=True,
                spoolExistsInDatabase=False,
            )
        )

    def test_genuine_vendor_tag_without_an_id_is_not_affected(self):
        # A Bambu/Creality tag carries no SpoolManager id at all. It reports "foreign" too, but
        # with no id there is nothing to doubt - the untouched vendor-tag protection handles it.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=None,
                occupancy="foreign",
                hasExtendedData=False,
                spoolExistsInDatabase=False,
            )
        )

    def test_blank_tag_confirmed_empty_is_not_affected(self):
        # occupancy "empty" is the firmware positively confirming a blank tag.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=None,
                occupancy="empty",
                hasExtendedData=False,
                spoolExistsInDatabase=False,
            )
        )

    def test_older_firmware_without_an_occupancy_field_stays_normal(self):
        # Firmware too old to report occupancy sends "". Absent a verdict there is no reason to
        # doubt an id, so behaviour is unchanged for those devices.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=81,
                occupancy="",
                hasExtendedData=False,
                spoolExistsInDatabase=False,
            )
        )

    def test_occupancy_empty_with_an_id_stays_normal(self):
        # Defensive: an id plus a positive "this tag is blank" verdict is contradictory, but the
        # blank verdict is not grounds for the unverifiable path.
        self.assertFalse(
            UnverifiableSpoolId.isSpoolIdUnverifiable(
                spoolId=81,
                occupancy="empty",
                hasExtendedData=False,
                spoolExistsInDatabase=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
