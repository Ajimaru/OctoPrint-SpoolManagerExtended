# coding=utf-8

# The note logged when a job ends too soon after its start for the sliced fallback (or,
# on a Moonraker printer, Klipper's own count) to be booked. It used to say "finished"
# for every job end, including a cancel, where nothing changes because of the duration.
#
# Run with:  python3 -m pytest octoprint_SpoolManagerExtended/test/test_ShortJobEndLog.py
# (needs the repo .venv)

import unittest
from unittest import mock

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin


class FakeConnector(object):
    def __init__(self, moonraker):
        self.moonraker = moonraker

    def _getConnectorParams(self):
        return {"host": "printer.invalid", "port": 7125} if self.moonraker else None


class FakePlugin(object):
    _logShortJobEnd = SpoolmanagerPlugin._logShortJobEnd
    _isMoonrakerConnection = SpoolmanagerPlugin._isMoonrakerConnection

    def __init__(self, moonraker=False):
        self._logger = mock.Mock()
        self._u1RfidManager = FakeConnector(moonraker)

    def logged(self):
        return [call.args[0] for call in self._logger.info.call_args_list]


class TestShortJobEndLog(unittest.TestCase):
    def test_a_short_finished_job_still_says_why_nothing_is_booked(self):
        for moonraker in (False, True):
            with self.subTest(moonraker=moonraker):
                plugin = FakePlugin(moonraker)
                plugin._logShortJobEnd("success", 12.0)
                self.assertEqual(
                    [
                        "print 'finished' after only 12.0s - sliced filament usage"
                        " will not be booked (spurious event?)"
                    ],
                    plugin.logged(),
                )

    def test_a_cancel_is_not_called_finished(self):
        for status in ("canceled", "failed"):
            with self.subTest(status=status):
                plugin = FakePlugin(moonraker=False)
                plugin._logShortJobEnd(status, 12.0)
                # nothing depends on the duration here, so there is nothing to say
                self.assertEqual([], plugin.logged())

    def test_a_short_cancel_on_moonraker_names_what_is_skipped(self):
        for status in ("canceled", "failed"):
            with self.subTest(status=status):
                plugin = FakePlugin(moonraker=True)
                plugin._logShortJobEnd(status, 12.0)
                self.assertEqual(
                    [
                        "print %s after only 12.0s - Klipper's filament count will"
                        " not be booked" % status
                    ],
                    plugin.logged(),
                )

    def test_a_pause_logs_nothing(self):
        plugin = FakePlugin(moonraker=True)
        plugin._logShortJobEnd("paused", 3.0)
        self.assertEqual([], plugin.logged())

    def test_without_the_u1_manager_there_is_no_moonraker_connection(self):
        plugin = FakePlugin()
        del plugin._u1RfidManager
        self.assertFalse(plugin._isMoonrakerConnection())


if __name__ == "__main__":
    unittest.main()
