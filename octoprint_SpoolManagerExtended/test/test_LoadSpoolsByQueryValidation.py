# coding=utf-8

# Tests for the parameter check of GET /loadSpoolsByQuery.
#
# A request without the table parameters used to reach DatabaseManager.loadAllSpoolsByQuery(),
# which raised a KeyError ('sortColumn') inside the database layer: an ERROR with traceback in
# octoprint.log and a "Database call error" popup in every open browser tab, although nothing
# was wrong with the database. Such a request is now answered with a 400 before the database
# is touched.
#
# loadAllSpoolsByQuery() is bound from the real SpoolManagerAPI class, and requests that pass
# the check run against the real DatabaseManager, so the check is held to what the database
# layer actually accepts.
#
# Run with:  .venv/bin/python -m pytest --import-mode=importlib \
#                octoprint_SpoolManagerExtended/test/test_LoadSpoolsByQueryValidation.py -v

import logging
import unittest

import flask
import peewee

from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.DatabaseManager import MODELS, DatabaseManager
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel

# what the sidebar's select-spool dialog sends (SpoolManager.js loadSpoolSelectorData)
SIDEBAR_QUERY = {
    "filterName": "all",
    "from": "0",
    "to": "3333",
    "sortColumn": "lastUse",
    "sortOrder": "desc",
}

# what the spool table sends (TableItemHelper.js), with no filter selected
TABLE_QUERY = {
    "selectedPageSize": "25",
    "from": "0",
    "to": "25",
    "sortColumn": "displayName",
    "sortOrder": "asc",
    "filterName": "",
    "materialFilter": "all",
    "vendorFilter": "all",
    "colorFilter": "all",
    "textFilter": "",
}

################################################################################################ fakes


class FakePlugin(object):
    # @no_firstrun_access needs OctoPrint's global settings singleton, which does not exist
    # outside a running server - unwrap to the plain implementation, that is what we test
    loadAllSpoolsByQuery = SpoolManagerAPI.loadAllSpoolsByQuery.__wrapped__
    # getattr, so this module still loads against code that predates the check
    _tableQueryValidationErrors = getattr(
        SpoolManagerAPI, "_tableQueryValidationErrors", None
    )

    def __init__(self, databaseManager):
        self._databaseManager = databaseManager
        self._logger = logging.getLogger("test.loadspoolsbyquery")
        self.queriedTables = []

    def _loadAllSpoolsByQueryResponse(self, tableQuery):
        # the two database calls that read tableQuery; the catalogs around them do not
        self.queriedTables.append(dict(tableQuery))
        allSpools = self._databaseManager.loadAllSpoolsByQuery(tableQuery)
        totalItemCount = self._databaseManager.countSpoolsByQuery(tableQuery)
        return flask.jsonify(
            {
                "allSpools": [spool.displayName for spool in allSpools or []],
                "totalItemCount": totalItemCount,
            }
        )


################################################################################################ tests


class TestLoadSpoolsByQueryValidation(unittest.TestCase):
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
        # what would pop up in the browser
        self.clientMessages = []
        self.databaseManager._passMessageToClient = (
            lambda *args, **kwargs: self.clientMessages.append(args)
        )

        SpoolModel.create(displayName="Some spool", isActive=True)
        self.plugin = FakePlugin(self.databaseManager)
        self.app = flask.Flask(__name__)

    def tearDown(self):
        self.database.drop_tables(MODELS)
        self.database.close()

    def _request(self, queryString):
        with self.app.test_request_context(query_string=queryString):
            response = flask.make_response(self.plugin.loadAllSpoolsByQuery())
        return response.status_code, flask.json.loads(response.get_data())

    def _assertRejected(self, queryString, expectedErrors):
        statusCode, body = self._request(queryString)

        self.assertEqual(statusCode, 400)
        self.assertEqual(body["validationErrors"], expectedErrors)
        # rejected before the database: no query, no popup
        self.assertEqual(self.plugin.queriedTables, [])
        self.assertEqual(self.clientMessages, [])

    def test_requestWithoutTableParametersIsRejected(self):
        # the health check that caused the ERROR: paging only, nothing else
        self._assertRejected(
            {"from": "0", "to": "1"},
            [
                "parameter 'sortColumn' is missing",
                "parameter 'sortOrder' is missing",
                "parameter 'filterName' is missing",
            ],
        )

    def test_sameRequestFailsInsideTheDatabaseLayer(self):
        # the other side of the test above: unchecked, the same request is exactly what
        # raised in loadAllSpoolsByQuery and reached the browser as a database error
        allSpools = self.databaseManager.loadAllSpoolsByQuery({"from": "0", "to": "1"})

        self.assertIsNone(allSpools)
        self.assertEqual(len(self.clientMessages), 1)
        self.assertIn("loadAllSpoolsByQuery", self.clientMessages[0][2])

    def test_emptyRequestNamesEveryMissingParameter(self):
        self._assertRejected(
            {},
            [
                "parameter 'sortColumn' is missing",
                "parameter 'sortOrder' is missing",
                "parameter 'filterName' is missing",
                "parameter 'from' is missing",
                "parameter 'to' is missing",
            ],
        )

    def test_pagingMustBeANonNegativeInteger(self):
        query = dict(SIDEBAR_QUERY, **{"from": "abc", "to": "-1"})
        self._assertRejected(
            query,
            [
                "parameter 'from' must be a non-negative integer",
                "parameter 'to' must be a non-negative integer",
            ],
        )

    def test_materialFilterNeedsTheOtherFilters(self):
        query = dict(SIDEBAR_QUERY, materialFilter="all")
        self._assertRejected(
            query,
            [
                "parameter 'vendorFilter' is missing",
                "parameter 'colorFilter' is missing",
            ],
        )

    def test_sidebarQueryLoadsSpools(self):
        statusCode, body = self._request(SIDEBAR_QUERY)

        self.assertEqual(statusCode, 200)
        self.assertEqual(body["allSpools"], ["Some spool"])
        self.assertEqual(self.clientMessages, [])

    def test_tableQueryLoadsSpools(self):
        statusCode, body = self._request(TABLE_QUERY)

        self.assertEqual(statusCode, 200)
        self.assertEqual(body["allSpools"], ["Some spool"])
        self.assertEqual(self.clientMessages, [])

    def test_pageSizeAllNeedsNoPaging(self):
        query = dict(TABLE_QUERY, selectedPageSize="all")
        del query["from"]
        del query["to"]

        statusCode, body = self._request(query)

        self.assertEqual(statusCode, 200)
        self.assertEqual(body["allSpools"], ["Some spool"])
        self.assertEqual(self.clientMessages, [])


if __name__ == "__main__":
    unittest.main()
