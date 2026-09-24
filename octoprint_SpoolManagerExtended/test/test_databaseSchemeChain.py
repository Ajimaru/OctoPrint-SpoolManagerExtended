# coding=utf-8

# Tests for the chain of scheme migrations as a whole.
#
# _upgradeDatabase() walks a hand-maintained list of _upgradeFromNToM methods by index, and
# CURRENT_DATABASE_SCHEME_VERSION decides where the walk ends. All three have to change
# together with every new scheme version: a method missing from the list, or a constant that
# was not raised, means an installation silently never receives that migration. This
# replaces a test that pinned the constant to one fixed number and went stale with the next
# scheme version.
#
# The migrations themselves are replaced by recorders: what is under test here is which
# steps run and in which order. What each step does to a database is covered by the
# per-version test files.
#
# Run with:  python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_databaseSchemeChain.py

import logging
import re
import unittest

from octoprint_SpoolManagerExtended.DatabaseManager import (
    CURRENT_DATABASE_SCHEME_VERSION,
    DatabaseManager,
)

# the trailing "$" keeps helpers like _upgradeFrom4To5_HACK out
_MIGRATION_METHOD_NAME = re.compile(r"^_upgradeFrom(\d+)To(\d+)$")


def _definedMigrationSteps():
    steps = []
    for name in dir(DatabaseManager):
        match = _MIGRATION_METHOD_NAME.match(name)
        if match:
            steps.append((int(match.group(1)), int(match.group(2))))
    return sorted(steps)


def _expectedMigrationSteps():
    return [
        (version, version + 1) for version in range(1, CURRENT_DATABASE_SCHEME_VERSION)
    ]


class TestSchemeMigrationChain(unittest.TestCase):
    def setUp(self):
        self.databaseManager = DatabaseManager(
            logging.getLogger("test.schemeChain"), False
        )
        self.executedSteps = []
        for fromVersion, toVersion in _definedMigrationSteps():
            setattr(
                self.databaseManager,
                "_upgradeFrom%dTo%d" % (fromVersion, toVersion),
                self._recorder(fromVersion, toVersion),
            )

    def _recorder(self, fromVersion, toVersion):
        def record():
            self.executedSteps.append((fromVersion, toVersion))

        return record

    def test_fullUpgradeRunsEveryStepUpToTheCurrentVersion(self):
        self.databaseManager._upgradeDatabase(1, CURRENT_DATABASE_SCHEME_VERSION)

        self.assertEqual(self.executedSteps, _expectedMigrationSteps())

    def test_everyMigrationMethodIsPartOfTheChain(self):
        # a new _upgradeFromNToM without raising CURRENT_DATABASE_SCHEME_VERSION never runs
        self.assertEqual(_definedMigrationSteps(), _expectedMigrationSteps())


if __name__ == "__main__":
    unittest.main()
