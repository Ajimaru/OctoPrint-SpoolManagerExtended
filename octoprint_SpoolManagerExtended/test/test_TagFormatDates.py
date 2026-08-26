# coding=utf-8

# Tests for the date encoding used when writing a tag.
#
# The tag carries a date as two separate fields: the day (days since 1970-01-01) and,
# optionally, the time of day in minutes. This split is not an aesthetic choice - the
# firmware stores these as uint16 on the wire, so a single "minutes since the epoch" value
# (~29.8 million today) would exceed 65535 and be written as the 0xFFFF "not set" sentinel.
# Every timestamp would then read back as no date at all, silently. The tests below pin
# both the range and the round trip so that encoding cannot be reintroduced by accident.
#
# Run with:  python3 octoprint_SpoolManagerExtended/test/test_TagFormatDates.py
# (or `pytest --import-mode=importlib`)

import datetime
import importlib.util
import os
import sys
import types
import unittest

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


TagFormats = _loadModule("TagFormats")

epochDays = TagFormats._epochDaysOrNone
minuteOfDay = TagFormats._minuteOfDayOrNone

# The firmware writes these fields with pn5180ScaleU16; anything above this is the "not set"
# sentinel rather than a value.
UINT16_MAX = 65535


def _reconstruct(days, minutes):
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(
        days=days, minutes=(minutes or 0)
    )


class TestDateEncodingRoundTrip(unittest.TestCase):
    def test_timestamp_survives_day_plus_minute_of_day(self):
        for moment in (
            datetime.datetime(2025, 1, 3, 23, 14),
            datetime.datetime(2025, 4, 23, 17, 53),
            datetime.datetime(2025, 6, 1, 0, 0),
            datetime.datetime(2025, 6, 1, 23, 59),
        ):
            self.assertEqual(
                moment.replace(second=0, microsecond=0),
                _reconstruct(epochDays(moment), minuteOfDay(moment)),
            )

    def test_plain_date_has_no_time_of_day(self):
        # purchasedOn is a date, not a timestamp: the day field alone already says midnight,
        # so writing a 0 here would claim a precision the value does not have.
        purchased = datetime.date(2024, 2, 27)
        self.assertIsNone(minuteOfDay(purchased))
        self.assertEqual(
            datetime.datetime(2024, 2, 27, 0, 0),
            _reconstruct(epochDays(purchased), minuteOfDay(purchased)),
        )

    def test_unset_stays_unset(self):
        self.assertIsNone(epochDays(None))
        self.assertIsNone(minuteOfDay(None))


class TestValuesFitTheWireFormat(unittest.TestCase):
    def test_minute_of_day_never_reaches_the_uint16_sentinel(self):
        # Highest possible value is 23:59 -> 1439, orders of magnitude below the sentinel.
        latest = minuteOfDay(datetime.datetime(2025, 6, 1, 23, 59))
        self.assertEqual(1439, latest)
        self.assertLess(latest, UINT16_MAX)

    def test_minutes_since_epoch_would_have_overflowed(self):
        # Guards the reasoning, not the code: this is the encoding that was almost used, and
        # the number below is why it cannot be. If a future change starts sending minutes
        # since the epoch, this test explains what breaks.
        moment = datetime.datetime(2025, 1, 3, 23, 14)
        minutesSinceEpoch = int(
            (moment - datetime.datetime(1970, 1, 1)).total_seconds() // 60
        )
        self.assertGreater(minutesSinceEpoch, UINT16_MAX)

    def test_day_field_still_fits_for_a_long_time(self):
        self.assertLess(epochDays(datetime.date(2100, 1, 1)), UINT16_MAX)


if __name__ == "__main__":
    unittest.main()
