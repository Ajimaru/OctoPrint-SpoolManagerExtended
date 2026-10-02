# coding=utf-8

# Tests for the material/vendor/colour filters of the spool table query
# (DatabaseManager._applyTableQueryFilters, used by loadAllSpoolsByQuery and
# countSpoolsByQuery).
#
# The spool table sends each filter as "all", as a comma-separated selection, or as "" when
# nothing is selected (TableItemHelper._evalFilter). The material filter used to decide on
# "" by looking at the colour filter: with materials picked and every colour deselected, it
# returned the spools without a material instead of the picked materials.
#
# Run with:  .venv/bin/python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_TableQueryFilters.py -v

import logging
import unittest

import peewee

from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel


class TestMaterialFilter(unittest.TestCase):
    def setUp(self):
        self.database = peewee.SqliteDatabase(":memory:")
        self.database.bind(MODELS)
        self.database.create_tables(MODELS)

        self.databaseManager = DatabaseManager(
            logging.getLogger("test.dbmanager"), False
        )
        self.databaseManager._database = self.database
        self.databaseManager._isConnected = True
        self.databaseManager.connectoToDatabase = lambda *a, **k: None
        self.databaseManager.closeDatabase = lambda *a, **k: None
        # a failing query would only show up here, as the browser popup
        self.clientMessages = []
        self.databaseManager._passMessageToClient = (
            lambda *args, **kwargs: self.clientMessages.append(args)
        )

        self._create("Red PLA", "PLA", "#ff0000", "red")
        self._create("Blue PETG", "PETG", "#0000ff", "blue")
        self._create("Red ABS", "ABS", "#ff0000", "red")
        # the spools an empty material filter stands for
        self._create("No material", "", "#00ff00", "green")

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _create(self, displayName, material, color, colorName):
        SpoolModel.create(
            displayName=displayName,
            material=material,
            color=color,
            colorName=colorName,
            isActive=True,
        )

    def _query(self, materialFilter, colorFilter):
        tableQuery = {
            "from": "0",
            "to": "25",
            "sortColumn": "displayName",
            "sortOrder": "asc",
            "filterName": "",
            "materialFilter": materialFilter,
            "vendorFilter": "all",
            "colorFilter": colorFilter,
        }
        names = [
            spool.displayName
            for spool in self.databaseManager.loadAllSpoolsByQuery(tableQuery)
        ]
        count = self.databaseManager.countSpoolsByQuery(tableQuery)
        self.assertEqual(self.clientMessages, [])
        return names, count

    def test_pickedMaterialsWithEveryColourDeselected(self):
        # the reported case: the picked materials, not the spools without one
        names, count = self._query("PLA,PETG", "")

        self.assertEqual(names, ["Blue PETG", "Red PLA"])
        self.assertEqual(count, 2)

    def test_pickedMaterialsWithAllColours(self):
        names, count = self._query("PLA,PETG", "all")

        self.assertEqual(names, ["Blue PETG", "Red PLA"])
        self.assertEqual(count, 2)

    def test_pickedMaterialsAndColoursNarrowEachOther(self):
        names, count = self._query("PLA,PETG", "#ff0000;red")

        self.assertEqual(names, ["Red PLA"])
        self.assertEqual(count, 1)

    def test_noMaterialSelectedLeavesTheSpoolsWithoutMaterial(self):
        # whatever the colour filter says
        for colorFilter in ("all", ""):
            with self.subTest(colorFilter=colorFilter):
                names, count = self._query("", colorFilter)

                self.assertEqual(names, ["No material"])
                self.assertEqual(count, 1)

    def test_allMaterialsDoNotFilter(self):
        names, count = self._query("all", "all")

        self.assertEqual(names, ["Blue PETG", "No material", "Red ABS", "Red PLA"])
        self.assertEqual(count, 4)


if __name__ == "__main__":
    unittest.main()
