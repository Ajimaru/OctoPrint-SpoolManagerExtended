import logging
import os
import sys
import types
import unittest

# register a lightweight package module so the absolute imports inside DatabaseManager.py resolve
# without executing the plugin __init__.py (which requires a full OctoPrint installation)
_packageDir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_repoDir = os.path.dirname(_packageDir)
if _repoDir not in sys.path:
    sys.path.insert(0, _repoDir)
if "octoprint_SpoolManager" not in sys.modules:
    _package = types.ModuleType("octoprint_SpoolManager")
    _package.__path__ = [_packageDir]
    sys.modules["octoprint_SpoolManager"] = _package

from octoprint_SpoolManager.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManager.models.PluginMetaDataModel import PluginMetaDataModel
from octoprint_SpoolManager.models.SpoolModel import SpoolModel

SPOOL_TABLE = SpoolModel._meta.table_name
META_TABLE = PluginMetaDataModel._meta.table_name


class TestDumpImportValidation(unittest.TestCase):

    def setUp(self):
        self.databaseManager = DatabaseManager(logging.getLogger("test"), False)
        self.allowedModels = {model._meta.table_name: model for model in MODELS}

    def _parse(self, statement):
        return self.databaseManager._parseInsertDumpStatement(
            statement, self.allowedModels
        )

    ################################################################################## statement splitting

    def test_splitRealisticDump(self):
        dumpText = "\n".join(
            [
                "-- SpoolManager MySQL dump",
                "-- schemeVersion: 9",
                "",
                "SET NAMES utf8mb4;",
                "",
                "DROP TABLE IF EXISTS `" + SPOOL_TABLE + "`;",
                "CREATE TABLE `" + SPOOL_TABLE + "` (",
                "  `databaseId` int NOT NULL AUTO_INCREMENT",
                ") ENGINE=InnoDB;",
                "",
                "INSERT INTO `"
                + SPOOL_TABLE
                + "` (`databaseId`, `displayName`) VALUES (1, 'PLA');",
            ]
        )
        statements = self.databaseManager._splitSQLDumpStatements(dumpText)
        self.assertEqual(4, len(statements))
        self.assertEqual("SET NAMES utf8mb4;", statements[0])
        self.assertTrue(
            statements[2].startswith("CREATE TABLE `" + SPOOL_TABLE + "` (")
        )
        self.assertTrue(statements[3].startswith("INSERT INTO"))

    def test_ignoredStatements(self):
        self.assertTrue(
            self.databaseManager._isIgnoredDumpStatement(
                "SET NAMES utf8mb4;", self.allowedModels
            )
        )
        self.assertTrue(
            self.databaseManager._isIgnoredDumpStatement(
                "DROP TABLE IF EXISTS `" + SPOOL_TABLE + "`;", self.allowedModels
            )
        )
        self.assertTrue(
            self.databaseManager._isIgnoredDumpStatement(
                "CREATE TABLE `" + META_TABLE + "` (\n  `databaseId` int\n);",
                self.allowedModels,
            )
        )
        # unknown table or foreign statements are NOT ignored (and will then fail the INSERT parser)
        self.assertFalse(
            self.databaseManager._isIgnoredDumpStatement(
                "DROP TABLE IF EXISTS `mysql`.`user`;", self.allowedModels
            )
        )
        self.assertFalse(
            self.databaseManager._isIgnoredDumpStatement(
                "GRANT ALL ON *.* TO 'evil'@'%';", self.allowedModels
            )
        )

    ################################################################################## accepted INSERTs

    def test_parseSimpleInsert(self):
        parsed = self._parse(
            "INSERT INTO `"
            + SPOOL_TABLE
            + "` (`databaseId`, `displayName`, `totalWeight`) "
            "VALUES (1, 'PLA Galaxy Black', 1000.0);"
        )
        self.assertIsNotNone(parsed)
        model, columnNames, values = parsed
        self.assertEqual(SpoolModel, model)
        self.assertEqual(["databaseId", "displayName", "totalWeight"], columnNames)
        self.assertEqual([1, "PLA Galaxy Black", 1000.0], values)

    def test_parseNullAndNumbers(self):
        parsed = self._parse(
            "INSERT INTO `"
            + SPOOL_TABLE
            + "` (`databaseId`, `vendor`, `offsetTemperature`, `density`) "
            "VALUES (7, NULL, -15, 1e-05);"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual([7, None, -15, 1e-05], parsed[2])

    def test_parseEscapedQuotes(self):
        parsed = self._parse(
            "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`, `vendor`) "
            "VALUES (1, 'O\\'Reilly', 'It''s');"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual([1, "O'Reilly", "It's"], parsed[2])

    def test_parseBackslashSequences(self):
        parsed = self._parse(
            "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `noteText`) "
            "VALUES (1, 'line1\\nline2\\\\end\\Z');"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual("line1\nline2\\end\x1a", parsed[2][1])

    def test_parseDatetimeAndEmoji(self):
        parsed = self._parse(
            "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `created`, `colorName`) "
            "VALUES (1, '2026-07-16 12:00:00', 'Grün 🌈');"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(["2026-07-16 12:00:00", "Grün 🌈"], parsed[2][1:])

    def test_injectionPayloadInsideLiteralStaysData(self):
        parsed = self._parse(
            "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
            "VALUES (1, '\\'); DROP TABLE " + SPOOL_TABLE + ";--');"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual("'); DROP TABLE " + SPOOL_TABLE + ";--", parsed[2][1])

    def test_parseMetaTableInsert(self):
        parsed = self._parse(
            "INSERT INTO `" + META_TABLE + "` (`databaseId`, `key`, `value`) "
            "VALUES (1, 'databaseSchemeVersion', '9');"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(PluginMetaDataModel, parsed[0])

    ################################################################################## rejected INSERTs

    def test_rejectSubquery(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, (SELECT password FROM users));"
            )
        )

    def test_rejectFunctionCall(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `created`) "
                "VALUES (1, NOW());"
            )
        )

    def test_rejectOnDuplicateKeyTail(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'PLA') ON DUPLICATE KEY UPDATE displayName='x';"
            )
        )

    def test_rejectStackedStatement(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'PLA'); DROP TABLE `" + SPOOL_TABLE + "`;"
            )
        )

    def test_rejectComments(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'PLA' -- comment\n);"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, /*x*/ 'PLA');"
            )
        )

    def test_rejectUnknownTable(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `mysql_user` (`databaseId`, `displayName`) VALUES (1, 'PLA');"
            )
        )

    def test_rejectUnknownColumn(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `"
                + SPOOL_TABLE
                + "` (`databaseId`, `evilColumn`) VALUES (1, 'x');"
            )
        )

    def test_rejectDuplicateColumn(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `"
                + SPOOL_TABLE
                + "` (`databaseId`, `displayName`, `displayName`) "
                "VALUES (1, 'a', 'b');"
            )
        )

    def test_rejectColumnValueCountMismatch(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `"
                + SPOOL_TABLE
                + "` (`databaseId`, `displayName`) VALUES (1);"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`) VALUES (1, 'x');"
            )
        )

    def test_rejectMissingLeadingDatabaseId(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`displayName`, `databaseId`) "
                "VALUES ('PLA', 1);"
            )
        )

    def test_rejectMalformedValues(self):
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'unterminated);"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, `displayName`);"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'a' 'b');"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, 'a',);"
            )
        )
        self.assertIsNone(
            self._parse(
                "INSERT INTO `" + SPOOL_TABLE + "` (`databaseId`, `displayName`) "
                "VALUES (1, VERSION);"
            )
        )

    ################################################################################## identifier guard

    def test_safeIdentifierAccepted(self):
        self.assertEqual(
            SPOOL_TABLE, self.databaseManager._assertSafeSQLIdentifier(SPOOL_TABLE)
        )
        self.assertEqual(
            "databaseId", self.databaseManager._assertSafeSQLIdentifier("databaseId")
        )

    def test_unsafeIdentifierRejected(self):
        for badIdentifier in [
            "spo`ol",
            "a b",
            "a;b",
            "a-b",
            "a.b",
            "",
            "col`); DROP TABLE x;--",
        ]:
            with self.assertRaises(ValueError):
                self.databaseManager._assertSafeSQLIdentifier(badIdentifier)

    ################################################################################## unescaping

    def test_unescapeRoundtrip(self):
        self.assertEqual(
            'It\'s a "test"\n\t\\',
            self.databaseManager._unescapeMySQLStringLiteral(
                'It\\\'s a \\"test\\"\\n\\t\\\\'
            ),
        )
        self.assertEqual(
            "It's", self.databaseManager._unescapeMySQLStringLiteral("It''s")
        )
        self.assertEqual(
            "\0\x1a", self.databaseManager._unescapeMySQLStringLiteral("\\0\\Z")
        )


if __name__ == "__main__":
    unittest.main()
