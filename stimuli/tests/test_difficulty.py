"""Tests for the two difficulty measures, on cases you can check by hand.

The interior used here is 4 cells wide and 3 tall, the configured size:

    . . . .
    . . . .
    . . . .
"""

import unittest

from stimuli import difficulty, fit, shapes
from stimuli.config import DEFAULT

W, H = 4, 3
REGION = fit.rectangle_region(W, H)


class TestBoundingBoxSlack(unittest.TestCase):
    """Slack = spare width + spare height at the snuggest orientation."""

    def test_a_shape_exactly_filling_the_interior_has_no_slack(self):
        exact = [(c, r) for c in range(4) for r in range(3)]
        self.assertEqual(difficulty.bbox_slack(exact, W, H), 0)

    def test_a_single_cell_has_the_most_slack(self):
        # 4 - 1 spare width, 3 - 1 spare height.
        self.assertEqual(difficulty.bbox_slack([(0, 0)], W, H), 5)

    def test_slack_uses_the_snuggest_orientation_not_the_drawn_one(self):
        # A 1x4 standing bar does not fit at all as drawn (4 > 3 tall), but
        # turned it is 4x1, which exactly spans the width: slack 0 + 2 = 2.
        standing = [(0, 0), (0, 1), (0, 2), (0, 3)]
        self.assertEqual(difficulty.bbox_slack(standing, W, H), 2)

    def test_a_shape_that_never_fits_has_no_slack_value(self):
        huge = [(c, r) for c in range(5) for r in range(5)]
        self.assertIsNone(difficulty.bbox_slack(huge, W, H))

    def test_slack_cannot_be_negative(self):
        for shape in shapes.full_library(5, 4):
            slack = difficulty.bbox_slack(shape.cells, W, H)
            if slack is not None:
                self.assertGreaterEqual(slack, 0)


class TestNearMissDistance(unittest.TestCase):
    """Distance = fewest cells to remove before it would fit."""

    def test_one_cell_over_is_a_near_miss(self):
        # A 5-in-a-row bar: take one cell off either end and it is a 4-bar,
        # which fits the 4-wide interior exactly.
        bar5 = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        self.assertFalse(fit.fits_in_region(bar5, REGION))
        self.assertEqual(difficulty.near_miss_distance(bar5, REGION), 1)

    def test_two_cells_over_is_distance_two(self):
        bar6 = [(c, 0) for c in range(6)]
        self.assertEqual(difficulty.near_miss_distance(bar6, REGION), 2)

    def test_the_remaining_piece_has_to_stay_in_one_lump(self):
        # Removing the middle cell of a 5-bar would leave two separate pieces,
        # which is not the same object any more, so that does not count.
        bar5 = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        middle_removed = [(0, 0), (1, 0), (3, 0), (4, 0)]
        self.assertFalse(difficulty._is_connected(middle_removed))
        # The answer is still 1, found by taking an END cell off.
        self.assertEqual(difficulty.near_miss_distance(bar5, REGION), 1)

    def test_a_shape_far_past_fitting_returns_nothing(self):
        bar9 = [(c, 0) for c in range(9)]
        self.assertIsNone(difficulty.near_miss_distance(bar9, REGION, cap=2))


class TestCircleNearMiss(unittest.TestCase):
    def test_a_circle_one_too_wide_is_a_near_miss(self):
        # The interior is 3 tall, so a 4-wide circle is 1 over.
        self.assertEqual(difficulty.circle_near_miss(4, W, H), 1)

    def test_a_circle_two_too_wide_is_distance_two(self):
        self.assertEqual(difficulty.circle_near_miss(5, W, H), 2)

    def test_a_circle_that_fits_has_no_near_miss_value(self):
        self.assertIsNone(difficulty.circle_near_miss(3, W, H))

    def test_the_short_side_is_what_limits_a_circle(self):
        # A circle needs a square space, so the 3-tall side binds, not the
        # 4-wide one.
        self.assertEqual(difficulty.circle_near_miss(4, 10, 3), 1)


class TestScoring(unittest.TestCase):
    def test_fitting_shapes_are_scored_by_slack(self):
        square = shapes.Shape("t", "poly",
                              fit.normalize([(0, 0), (1, 0), (0, 1), (1, 1)]))
        label, measure = difficulty.score(square, W, H)
        self.assertEqual(label, fit.FITS_AS_DRAWN)
        self.assertEqual(measure, (4 - 2) + (3 - 2))

    def test_too_big_shapes_are_scored_by_how_near_they_came(self):
        bar5 = shapes.Shape("t", "poly",
                            fit.normalize([(c, 0) for c in range(5)]))
        label, measure = difficulty.score(bar5, W, H)
        self.assertEqual(label, fit.TOO_BIG)
        self.assertEqual(measure, 1)

    def test_circles_are_scored_the_circle_way(self):
        circle4 = shapes.Shape("c", "circle", fit.rectangle_region(4, 4))
        label, measure = difficulty.score(circle4, W, H)
        self.assertEqual(label, fit.TOO_BIG)
        self.assertEqual(measure, 1)


class TestTheBand(unittest.TestCase):
    def test_a_shape_with_no_measure_is_always_out_of_band(self):
        self.assertFalse(difficulty.in_band(fit.TOO_BIG, None, DEFAULT))

    def test_the_band_actually_removes_something(self):
        # If the band let everything through it would not be doing any work.
        before, after = shapes.library_summary(DEFAULT)
        self.assertLess(sum(after.values()), sum(before.values()))

    def test_every_category_still_has_shapes_in_it(self):
        after = shapes.library_summary(DEFAULT)[1]
        for label, count in after.items():
            with self.subTest(category=label):
                self.assertGreater(count, 0)

    def test_every_shape_used_is_inside_the_band(self):
        for label, items in shapes.banded_library(DEFAULT).items():
            for shape, measure in items:
                with self.subTest(shape=shape.name):
                    self.assertTrue(
                        difficulty.in_band(label, measure, DEFAULT)
                    )

    def test_the_trivially_easy_shapes_are_excluded(self):
        # A single cell in a 4x3 interior has slack 5, outside the (0, 3)
        # band, so it should not be in the usable library.
        used = {shape.name
                for items in shapes.banded_library(DEFAULT).values()
                for shape, _ in items}
        self.assertNotIn("poly1_000", used)


class TestTheLibraryItself(unittest.TestCase):
    def test_the_polyomino_counts_are_the_known_ones(self):
        # The number of distinct fixed polyominoes of each size is a known
        # sequence. Matching it is a good sign nothing is missing or doubled.
        counts = {}
        for shape in shapes.polyominoes(6):
            counts[shape.n_cells] = counts.get(shape.n_cells, 0) + 1
        self.assertEqual(counts, {1: 1, 2: 2, 3: 6, 4: 19, 5: 63, 6: 216})

    def test_every_generated_shape_is_one_connected_piece(self):
        for shape in shapes.polyominoes(6):
            with self.subTest(shape=shape.name):
                self.assertTrue(difficulty._is_connected(shape.cells))

    def test_no_two_library_shapes_are_the_same(self):
        all_cells = [s.cells for s in shapes.polyominoes(6)]
        self.assertEqual(len(all_cells), len(set(all_cells)))

    def test_names_are_stable_between_runs(self):
        first = [s.name for s in shapes.polyominoes(5)]
        shapes.polyominoes.cache_clear()
        second = [s.name for s in shapes.polyominoes(5)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
