# coding=utf-8

import unittest

from octoprint_SpoolManagerExtended.common import RfidKeyCollision


class TestIsAmbiguous(unittest.TestCase):
    def test_a_single_matching_spool_is_not_ambiguous(self):
        self.assertFalse(RfidKeyCollision.isAmbiguous(1))

    def test_two_matching_spools_are_ambiguous(self):
        # loadSpoolByRfidTagKey() picks the newest of these silently; without the flag the
        # caller cannot tell that pick from a unique hit.
        self.assertTrue(RfidKeyCollision.isAmbiguous(2))

    def test_no_matching_spool_is_not_ambiguous(self):
        self.assertFalse(RfidKeyCollision.isAmbiguous(0))

    def test_an_unavailable_count_is_not_reported_as_ambiguous(self):
        # A failed count must not manufacture a warning out of nothing - the caller turns
        # None into "no answer" instead.
        self.assertFalse(RfidKeyCollision.isAmbiguous(None))

    def test_a_non_numeric_count_is_not_reported_as_ambiguous(self):
        self.assertFalse(RfidKeyCollision.isAmbiguous("2"))


class TestIsForeignTagFormat(unittest.TestCase):
    def test_tigertag_is_foreign(self):
        # The reported case: a foreign tag resolved to an unrelated spool via a shared suffix.
        self.assertTrue(RfidKeyCollision.isForeignTagFormat("ntagTigerTag"))

    def test_openprinttag_is_foreign(self):
        self.assertTrue(RfidKeyCollision.isForeignTagFormat("nfcvOpenPrintTag"))

    def test_our_own_extended_format_is_not_foreign(self):
        # Carries a databaseId, so a spool found for it was identified, not guessed.
        self.assertFalse(RfidKeyCollision.isForeignTagFormat("octoscaleExtended"))

    def test_our_own_ntag_extended_format_is_not_foreign(self):
        self.assertFalse(RfidKeyCollision.isForeignTagFormat("ntagExtended"))

    def test_legacy_spool_id_format_is_not_foreign(self):
        # Header-less but ours: it holds a SpoolManager id and nothing else's.
        self.assertFalse(RfidKeyCollision.isForeignTagFormat("spoolIdNtag"))

    def test_openspool_is_not_treated_as_foreign(self):
        # openSpool is a shared community format we both read and write, not another tool's
        # exclusive tag. Distinct from TigerTag/OpenPrintTag on purpose.
        self.assertFalse(RfidKeyCollision.isForeignTagFormat("openSpool"))

    def test_an_unknown_format_is_not_reported_as_foreign(self):
        self.assertFalse(RfidKeyCollision.isForeignTagFormat("somethingNew"))

    def test_a_missing_format_is_not_reported_as_foreign(self):
        self.assertFalse(RfidKeyCollision.isForeignTagFormat(None))


class TestIsForeignTagPayload(unittest.TestCase):
    def test_extended_without_id_is_foreign(self):
        self.assertTrue(RfidKeyCollision.isForeignTagPayload("extendedNoId"))

    def test_extended_with_id_is_not_foreign(self):
        self.assertFalse(RfidKeyCollision.isForeignTagPayload("extended"))

    def test_legacy_is_not_foreign(self):
        self.assertFalse(RfidKeyCollision.isForeignTagPayload("legacy"))

    def test_an_unfinished_read_is_not_reported_as_foreign(self):
        # The firmware sends "" while a read is still in flight, and older firmware never
        # sends the field at all. Neither is evidence of anything.
        self.assertFalse(RfidKeyCollision.isForeignTagPayload(""))
        self.assertFalse(RfidKeyCollision.isForeignTagPayload(None))


class TestDescribeMatch(unittest.TestCase):
    def test_the_reported_case_is_flagged_as_a_foreign_tag(self):
        # Observed on real hardware: a foreign tag resolved to the only spool carrying that
        # key. Unique, and still the wrong spool - which is why ambiguity alone is not enough.
        result = RfidKeyCollision.describeMatch(
            matchingSpoolCount=1, tagFormat="ntagTigerTag"
        )
        self.assertEqual({"foreignTag": True, "ambiguous": False}, result)

    def test_a_snapmaker_spool_read_from_either_side_gets_no_caveat(self):
        # Observed on a two-tag spool: its two sides report UIDs that differ in their leading
        # bytes but share the trailing ones, so both resolve to the same spool. This is the
        # case the truncation exists for and the one a warning must stay silent on - ordinary
        # spools all look like this.
        for tagFormat in ("octoscaleExtended", "spoolIdNtag"):
            result = RfidKeyCollision.describeMatch(
                matchingSpoolCount=1, tagFormat=tagFormat
            )
            self.assertEqual({"foreignTag": False, "ambiguous": False}, result)

    def test_two_spools_sharing_a_key_are_flagged_as_ambiguous(self):
        result = RfidKeyCollision.describeMatch(matchingSpoolCount=2)
        self.assertEqual({"foreignTag": False, "ambiguous": True}, result)

    def test_a_foreign_tag_that_is_also_ambiguous_reports_both(self):
        result = RfidKeyCollision.describeMatch(
            matchingSpoolCount=3, tagFormat="ntagTigerTag"
        )
        self.assertEqual({"foreignTag": True, "ambiguous": True}, result)

    def test_the_firmware_id_source_alone_is_enough(self):
        # The write path has no parsed format, only what the firmware reported.
        result = RfidKeyCollision.describeMatch(
            matchingSpoolCount=1, idSource="extendedNoId"
        )
        self.assertEqual({"foreignTag": True, "ambiguous": False}, result)

    def test_a_caller_with_no_tag_evidence_reports_nothing(self):
        # /spool/byCode is handed a string and never sees the reader. It can answer the
        # ambiguity half and must not guess at the other.
        result = RfidKeyCollision.describeMatch(matchingSpoolCount=1)
        self.assertEqual({"foreignTag": False, "ambiguous": False}, result)

    def test_format_and_id_source_agree_for_our_own_tag(self):
        result = RfidKeyCollision.describeMatch(
            matchingSpoolCount=1, tagFormat="octoscaleExtended", idSource="extended"
        )
        self.assertEqual({"foreignTag": False, "ambiguous": False}, result)

    def test_both_flags_are_always_present(self):
        # The frontend reads the keys directly; a missing key would read as undefined and
        # silently skip the warning.
        result = RfidKeyCollision.describeMatch(matchingSpoolCount=1)
        self.assertEqual({"foreignTag", "ambiguous"}, set(result.keys()))


if __name__ == "__main__":
    unittest.main()
