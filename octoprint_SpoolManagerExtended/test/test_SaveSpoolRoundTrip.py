# coding=utf-8

# saveSpool fed with what GET /spool/<id> answers. That answer carries labels and
# noteDeltaFormat as the stored JSON text and the three dates only in display form, so a
# client saving it back used to wrap labels/notes in one more JSON layer per round trip
# and wipe every date.
#
# Run with:  python3 -m pytest octoprint_SpoolManagerExtended/test/test_SaveSpoolRoundTrip.py
# (needs flask/peewee - use the repo .venv)

import datetime
import json
import logging
import unittest

from octoprint_SpoolManagerExtended.api import Transformer
from octoprint_SpoolManagerExtended.api.SpoolManagerAPI import SpoolManagerAPI
from octoprint_SpoolManagerExtended.models.SpoolModel import SpoolModel


class _Api(SpoolManagerAPI):
    """The JSON mapping only; SpoolManagerAPI.__init__ is deliberately not called."""

    def __init__(self):
        self._logger = logging.getLogger("test")


# The mapping also checks the mandatory fields; these keep its error list about the
# fields under test.
BASE = {
    "displayName": "White",
    "vendor": "Snapmaker",
    "material": "PLA",
    "color": "#FFFFFF",
    "colorName": "White",
    "diameter": 1.75,
}


def _apply(payload):
    spoolModel = SpoolModel()
    errors = _Api()._updateSpoolModelFromJSONData(spoolModel, dict(BASE, **payload))
    return spoolModel, errors


class TestLabels(unittest.TestCase):
    def test_a_list_is_stored_as_before(self):
        spoolModel, errors = _apply({"labels": ["weight-estimated"]})
        self.assertEqual('["weight-estimated"]', spoolModel.labels)
        self.assertEqual([], errors)

    def test_json_text_is_not_wrapped_again(self):
        cases = {
            "[]": "[]",
            '["a", "b"]': '["a", "b"]',
            # What earlier round trips left behind: one and two extra layers.
            '"[]"': "[]",
            '"\\"[]\\""': "[]",
        }
        for sent, stored in cases.items():
            with self.subTest(sent=sent):
                spoolModel, _ = _apply({"labels": sent})
                self.assertEqual(stored, spoolModel.labels)

    def test_plain_text_is_one_label(self):
        # json.dumps("PLA") used to store '"PLA"', which the dialog splits into P, L, A.
        spoolModel, _ = _apply({"labels": "PLA"})
        self.assertEqual('["PLA"]', spoolModel.labels)

    def test_missing_empty_or_odd_values_store_no_labels(self):
        # A stored number or object would break loadCatalogLabels(), which iterates every
        # stored value.
        for payload in (
            {},
            {"labels": None},
            {"labels": ""},
            {"labels": "   "},
            {"labels": "123"},
            {"labels": {"a": 1}},
            {"labels": "true"},
        ):
            with self.subTest(payload=payload):
                spoolModel, _ = _apply(payload)
                self.assertEqual("[]", spoolModel.labels)


class TestNoteDeltaFormat(unittest.TestCase):
    DELTA = {"ops": [{"insert": "note\n"}]}

    def test_an_object_is_stored_as_before(self):
        spoolModel, _ = _apply({"noteDeltaFormat": self.DELTA})
        self.assertEqual(json.dumps(self.DELTA), spoolModel.noteDeltaFormat)

    def test_json_text_is_not_wrapped_again(self):
        text = json.dumps(self.DELTA)
        for sent in (text, json.dumps(text), "null"):
            with self.subTest(sent=sent):
                spoolModel, _ = _apply({"noteDeltaFormat": sent})
                expected = "null" if sent == "null" else text
                self.assertEqual(expected, spoolModel.noteDeltaFormat)

    def test_missing_stays_null(self):
        spoolModel, _ = _apply({})
        self.assertEqual("null", spoolModel.noteDeltaFormat)


class TestDates(unittest.TestCase):
    def test_the_iso_keys_win(self):
        spoolModel, errors = _apply(
            {"purchasedOnKO": "2026-02-25", "purchasedOn": "01.01.2000"}
        )
        self.assertEqual(datetime.datetime(2026, 2, 25), spoolModel.purchasedOn)
        self.assertEqual([], errors)

    def test_a_cleared_iso_key_stays_cleared(self):
        # The dialog sends the *KO key as null when the user removed the date.
        spoolModel, _ = _apply({"purchasedOnKO": None, "purchasedOn": "25.02.2026"})
        self.assertIsNone(spoolModel.purchasedOn)

    def test_the_display_form_is_used_when_the_iso_key_is_absent(self):
        spoolModel, errors = _apply(
            {
                "purchasedOn": "25.02.2026",
                "firstUse": "24.08.2026 03:58",
                "lastUse": "25.08.2026 23:58",
            }
        )
        self.assertEqual(datetime.datetime(2026, 2, 25), spoolModel.purchasedOn)
        self.assertEqual(datetime.datetime(2026, 8, 24, 3, 58), spoolModel.firstUse)
        self.assertEqual(datetime.datetime(2026, 8, 25, 23, 58), spoolModel.lastUse)
        self.assertEqual([], errors)

    def test_an_unreadable_display_date_is_reported(self):
        spoolModel, errors = _apply({"purchasedOn": "Feb 25"})
        self.assertIsNone(spoolModel.purchasedOn)
        self.assertEqual(1, len(errors))


class TestGetAnswerRoundTrip(unittest.TestCase):
    def test_saving_the_get_answer_back_changes_nothing(self):
        original = SpoolModel()
        original.displayName = "White"
        original.vendor = "Snapmaker"
        original.material = "PLA"
        original.color = "#FFFFFF"
        original.colorName = "White"
        original.diameter = 1.75
        original.labels = json.dumps(["weight-estimated"])
        original.noteDeltaFormat = json.dumps({"ops": [{"insert": "note\n"}]})
        original.purchasedOn = datetime.datetime(2026, 2, 25)
        original.firstUse = datetime.datetime(2026, 8, 24, 3, 58)
        original.lastUse = datetime.datetime(2026, 8, 25, 23, 58)

        answer = Transformer.transformSpoolModelToDict(original)
        for _ in range(3):  # a script that keeps saving what it reads
            saved = SpoolModel()
            errors = _Api()._updateSpoolModelFromJSONData(saved, answer)
            self.assertEqual([], errors)
            answer = Transformer.transformSpoolModelToDict(saved)

        self.assertEqual(original.labels, saved.labels)
        self.assertEqual(original.noteDeltaFormat, saved.noteDeltaFormat)
        self.assertEqual(original.purchasedOn, saved.purchasedOn)
        self.assertEqual(original.firstUse, saved.firstUse)
        self.assertEqual(original.lastUse, saved.lastUse)


if __name__ == "__main__":
    unittest.main()
