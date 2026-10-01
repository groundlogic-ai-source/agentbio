"""The reported affinity is a median, and the dossier must say so.

Post-benchmark correction of 2026-09-29. `chembl.py` aggregates a molecule's
qualifying target-matched activities with `statistics.median`, which is the
right choice -- a median does not move when one unusually potent assay lands in
the set. The dossier then printed that number under the label "Best
target-qualified pChEMBL-equivalent affinity", and its methodology note stated
the value was "the best persisted pChEMBL-equivalent value, not a median".
Both were the exact opposite of what the code computed.

That is falsifiable in one query. Resistance to thyroid hormone beta is the
worked example: resmetirom (CHEMBL3261331) against THRB (CHEMBL1947) holds 7
qualifying EC50 records -- 5.62, 6.00, 6.68, 6.68, 6.70, 6.78, 7.14, every one
human and every one assay confidence 9. The median is 6.68 and the dossier
printed 6.68 while calling it the best value. A reviewer pulling the same
records sees a maximum of 7.14 and concludes the traceability claim does not
hold, which is the one claim the pipeline cannot afford to lose.

The second defect is provenance. The evidence ledger cited the median value
against `source_activity_ids[0]` -- an arbitrary first record, not the record
carrying the median. For RTHb those coincided by luck (activity 14658935 really
does read 6.68). When they do not coincide, the ledger asserts that a named
activity record holds a number it does not hold.
"""

import unittest

from agents import writer
from data_sources.chembl import _median_with_anchor

# The real resmetirom x THRB ledger, value paired with its activity id.
RESMETIROM_THRB = [
    (6.68, 14658935),
    (6.78, 22001001),
    (6.00, 22001002),
    (7.14, 22001003),
    (5.62, 22001004),
    (6.70, 22001005),
    (6.68, 22001006),
]


class MedianWithAnchorTest(unittest.TestCase):

    def test_odd_count_anchors_to_the_record_carrying_the_median(self):
        """The shape that shipped: 7 records, median 6.68, real provenance."""
        median, anchor = _median_with_anchor(RESMETIROM_THRB)
        self.assertAlmostEqual(median, 6.68)
        carried = [act for value, act in RESMETIROM_THRB if value == median]
        self.assertIn(anchor, carried,
                      "anchor must name a record that holds the median")

    def test_the_median_is_not_the_maximum(self):
        """Guards the mislabel itself: these are different numbers."""
        median, _ = _median_with_anchor(RESMETIROM_THRB)
        self.assertLess(median, max(v for v, _ in RESMETIROM_THRB))

    def test_even_count_refuses_to_name_a_single_record(self):
        """An even-count median may match no record at all."""
        pairs = [(5.0, "a"), (6.0, "b"), (7.0, "c"), (8.0, "d")]
        median, anchor = _median_with_anchor(pairs)
        self.assertAlmostEqual(median, 6.5)
        self.assertIsNone(
            anchor, "no record holds 6.5, so none may be cited as its source")

    def test_single_record_anchors_to_itself(self):
        median, anchor = _median_with_anchor([(7.2, "only")])
        self.assertAlmostEqual(median, 7.2)
        self.assertEqual(anchor, "only")

    def test_empty_input_yields_no_value_and_no_anchor(self):
        self.assertEqual(_median_with_anchor([]), (None, None))

    def test_anchor_survives_unordered_input(self):
        """Input arrives in API order, not sorted order."""
        shuffled = list(reversed(RESMETIROM_THRB))
        median, anchor = _median_with_anchor(shuffled)
        self.assertAlmostEqual(median, 6.68)
        carried = [act for value, act in shuffled if value == median]
        self.assertIn(anchor, carried)

    def test_a_missing_activity_id_does_not_shift_the_value(self):
        """Pairing must not be recovered by index; ids can be absent.

        `pchembls` and `activity_ids` were accumulated as two independent
        lists, each skipping on a different condition, so their indices drifted
        apart. Pairing at accumulation time is what makes the anchor sound.
        """
        pairs = [(6.0, None), (6.5, "x"), (9.0, "y")]
        median, anchor = _median_with_anchor(pairs)
        self.assertAlmostEqual(median, 6.5)
        self.assertEqual(anchor, "x")


class AffinityLabelTest(unittest.TestCase):

    def test_label_names_the_statistic_and_the_record_count(self):
        label = writer._affinity_statistic_label(
            {"pchembl_aggregation": "median", "pchembl_n": 7})
        self.assertIn("median", label.lower())
        self.assertIn("7", label)

    def test_label_never_claims_best(self):
        for candidate in (
            {"pchembl_aggregation": "median", "pchembl_n": 7},
            {"pchembl_aggregation": "median"},
            {},
        ):
            self.assertNotIn("best",
                             writer._affinity_statistic_label(candidate).lower())

    def test_singular_record_is_not_pluralised(self):
        label = writer._affinity_statistic_label(
            {"pchembl_aggregation": "median", "pchembl_n": 1})
        self.assertIn("1 qualified record", label)
        self.assertNotIn("records", label)

    def test_unknown_aggregation_does_not_invent_a_statistic(self):
        """Absent metadata must not be narrated as a median."""
        label = writer._affinity_statistic_label({})
        self.assertNotIn("median", label.lower())
        self.assertIn("Target-qualified pChEMBL-equivalent affinity", label)


class LedgerAnchorFieldTest(unittest.TestCase):
    """The qualifying fields must not be borrowed from a sibling row.

    A duplicate row for the same active moiety carries its own pchembl_value
    with its own record count and anchor. Back-filling those onto this row
    would caption one number with another number's provenance.
    """

    def test_aggregation_fields_are_ledger_authoritative(self):
        from data_sources.multisource_candidates import (
            _LEDGER_AUTHORITATIVE_FIELDS,
        )
        for field in ("pchembl_aggregation", "pchembl_n",
                      "pchembl_anchor_activity_id"):
            self.assertIn(field, _LEDGER_AUTHORITATIVE_FIELDS)


if __name__ == "__main__":
    unittest.main()
