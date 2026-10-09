"""Hand-checkable tests for the fit function.

Every case here is small enough to draw on squared paper and check yourself.
The interior used almost everywhere is 3 cells wide and 2 cells tall:

    . . .      <- top row of the interior (level with the tops of the arms)
    . . .      <- bottom row (sitting on the base)
"""

import unittest

from stimuli import fit, shapes

INTERIOR_3x2 = fit.rectangle_region(3, 2)


class TestGeometryHelpers(unittest.TestCase):
    def test_normalize_moves_shape_to_origin(self):
        # The same L drawn at two different spots should come out identical.
        here = fit.normalize([(5, 7), (6, 7), (5, 8)])
        there = fit.normalize([(0, 0), (1, 0), (0, 1)])
        self.assertEqual(here, there)

    def test_rotate_90_turns_a_standing_bar_into_a_lying_bar(self):
        standing = [(0, 0), (0, 1), (0, 2)]       # 1 wide, 3 tall
        lying = fit.rotate_90(standing)
        self.assertEqual(fit.size_of(lying), (3, 1))

    def test_four_rotations_return_to_the_start(self):
        start = fit.normalize([(0, 0), (0, 1), (0, 2), (1, 2)])
        turned = start
        for _ in range(4):
            turned = fit.rotate_90(turned)
        self.assertEqual(turned, start)

    def test_square_has_only_one_orientation(self):
        square = [(0, 0), (1, 0), (0, 1), (1, 1)]
        self.assertEqual(len(fit.orientations(square)), 1)

    def test_first_orientation_is_the_shape_as_drawn(self):
        drawn = fit.normalize([(0, 0), (0, 1), (0, 2)])
        self.assertEqual(fit.orientations(drawn)[0], drawn)


class TestFitsWithoutRotation(unittest.TestCase):
    def test_single_cell_fits(self):
        self.assertTrue(fit.fits_in_region([(0, 0)], INTERIOR_3x2))

    def test_lying_bar_of_three_fits_as_drawn(self):
        bar = [(0, 0), (1, 0), (2, 0)]            # 3 wide, 1 tall
        self.assertEqual(
            fit.label_shape(bar, INTERIOR_3x2), fit.FITS_AS_DRAWN
        )

    def test_two_by_two_square_fits_as_drawn(self):
        square = [(0, 0), (1, 0), (0, 1), (1, 1)]
        self.assertEqual(
            fit.label_shape(square, INTERIOR_3x2), fit.FITS_AS_DRAWN
        )


class TestTouchingTheWalls(unittest.TestCase):
    """Touching the walls and the base is allowed, not a collision."""

    def test_shape_exactly_filling_the_interior_fits(self):
        # A solid 3 x 2 block is exactly the interior: it touches the left arm,
        # the right arm, the base, and is level with the tops of the arms.
        exact = [(c, r) for c in range(3) for r in range(2)]
        self.assertTrue(fit.fits_in_region(exact, INTERIOR_3x2))
        self.assertEqual(
            fit.label_shape(exact, INTERIOR_3x2), fit.FITS_AS_DRAWN
        )

    def test_tee_spans_the_interior_in_both_directions(self):
        # The T is 3 wide and 2 tall, so it touches both arms and the base.
        tee = shapes.BY_NAME["tee_h"]
        self.assertEqual(tee.size, (3, 2))
        self.assertEqual(
            fit.label_shape(tee.cells, INTERIOR_3x2), fit.FITS_AS_DRAWN
        )

    def test_one_cell_wider_than_the_interior_does_not_fit(self):
        # 4 wide in a 3 wide interior: over the edge by exactly one cell.
        too_wide = [(0, 0), (1, 0), (2, 0), (3, 0)]
        self.assertEqual(
            fit.label_shape(too_wide, INTERIOR_3x2), fit.TOO_BIG
        )

    def test_one_cell_taller_than_the_interior_does_not_fit_unturned(self):
        # 3 tall in a 2 tall interior: over the top by exactly one cell.
        too_tall = [(0, 0), (0, 1), (0, 2)]
        self.assertFalse(fit.placement_fits(
            fit.normalize(too_tall), INTERIOR_3x2
        ))


class TestFitsOnlyWithRotation(unittest.TestCase):
    def test_standing_bar_of_three_needs_turning(self):
        standing = [(0, 0), (0, 1), (0, 2)]       # 1 wide, 3 tall
        self.assertEqual(
            fit.label_shape(standing, INTERIOR_3x2), fit.FITS_ROTATED
        )

    def test_standing_bar_does_not_fit_when_rotation_is_switched_off(self):
        standing = [(0, 0), (0, 1), (0, 2)]
        self.assertFalse(fit.fits_in_region(
            standing, INTERIOR_3x2, allow_rotation=False
        ))

    def test_every_library_rotation_shape_needs_turning(self):
        buckets = shapes.label_library(INTERIOR_3x2)
        rotation_shapes = buckets[fit.FITS_ROTATED]
        self.assertTrue(rotation_shapes, "expected some rotation-only shapes")
        for shape in rotation_shapes:
            with self.subTest(shape=shape.name):
                # Does not fit as drawn...
                self.assertFalse(fit.placement_fits(shape.cells, INTERIOR_3x2))
                # ...but does fit once turned.
                self.assertTrue(fit.fits_in_region(shape.cells, INTERIOR_3x2))


class TestTooBig(unittest.TestCase):
    def test_bar_of_four_never_fits_either_way_round(self):
        for name in ("bar4_h", "bar4_v"):
            with self.subTest(shape=name):
                shape = shapes.BY_NAME[name]
                self.assertEqual(
                    fit.label_shape(shape.cells, INTERIOR_3x2), fit.TOO_BIG
                )


class TestCircles(unittest.TestCase):
    """A circle is judged as the square box it sits in."""

    def test_circle_is_stored_as_its_bounding_square(self):
        self.assertEqual(shapes.BY_NAME["circle3"].size, (3, 3))
        self.assertEqual(len(shapes.BY_NAME["circle3"].cells), 9)

    def test_small_circles_fit(self):
        self.assertEqual(
            fit.label_shape(shapes.BY_NAME["circle1"].cells, INTERIOR_3x2),
            fit.FITS_AS_DRAWN,
        )
        self.assertEqual(
            fit.label_shape(shapes.BY_NAME["circle2"].cells, INTERIOR_3x2),
            fit.FITS_AS_DRAWN,
        )

    def test_circle_three_wide_needs_a_three_by_three_space(self):
        # The interior is only 2 tall, so a 3-wide circle cannot go in, and
        # turning a circle changes nothing.
        self.assertEqual(
            fit.label_shape(shapes.BY_NAME["circle3"].cells, INTERIOR_3x2),
            fit.TOO_BIG,
        )
        self.assertTrue(fit.fits_in_region(
            shapes.BY_NAME["circle3"].cells, fit.rectangle_region(3, 3)
        ))

    def test_a_circle_can_never_be_a_rotation_only_shape(self):
        # Its box is square, so it has a single orientation.
        for name in ("circle1", "circle2", "circle3", "circle4"):
            with self.subTest(shape=name):
                self.assertEqual(
                    len(fit.orientations(shapes.BY_NAME[name].cells)), 1
                )


class TestReflections(unittest.TestCase):
    """Mirror images are off by default, and the switch really works."""

    # A Z-shaped hole. The S tetromino is its mirror image, so S can only go in
    # if mirroring is allowed. Turning S is not enough.
    Z_HOLE = frozenset([(0, 0), (1, 0), (1, 1), (2, 1)])

    def test_mirror_image_does_not_fit_by_default(self):
        ess = shapes.BY_NAME["ess_h"]
        self.assertFalse(fit.fits_in_region(
            ess.cells, self.Z_HOLE, allow_reflections=False
        ))

    def test_mirror_image_fits_when_reflections_are_allowed(self):
        ess = shapes.BY_NAME["ess_h"]
        self.assertTrue(fit.fits_in_region(
            ess.cells, self.Z_HOLE, allow_reflections=True
        ))


class TestCountingTheAnswer(unittest.TestCase):
    def test_shapes_are_judged_one_at_a_time_not_packed_together(self):
        # Three 2x2 squares could never all be crammed into a 3x2 space, but
        # each one fits on its own, so the answer is 3.
        square = [(0, 0), (1, 0), (0, 1), (1, 1)]
        self.assertEqual(
            fit.count_fitting([square, square, square], INTERIOR_3x2), 3
        )

    def test_mixed_scene_counts_only_the_shapes_that_fit(self):
        hand = [
            shapes.BY_NAME["single"].cells,    # fits
            shapes.BY_NAME["square4"].cells,   # fits
            shapes.BY_NAME["bar3_v"].cells,    # fits once turned
            shapes.BY_NAME["bar4_h"].cells,    # too big
            shapes.BY_NAME["circle3"].cells,   # too big
        ]
        self.assertEqual(fit.count_fitting(hand, INTERIOR_3x2), 3)

    def test_same_hand_with_rotation_switched_off_counts_one_fewer(self):
        hand = [
            shapes.BY_NAME["single"].cells,
            shapes.BY_NAME["square4"].cells,
            shapes.BY_NAME["bar3_v"].cells,
            shapes.BY_NAME["bar4_h"].cells,
            shapes.BY_NAME["circle3"].cells,
        ]
        self.assertEqual(
            fit.count_fitting(hand, INTERIOR_3x2, allow_rotation=False), 2
        )

    def test_the_answer_is_the_same_every_time_it_is_computed(self):
        hand = [s.cells for s in shapes.LIBRARY]
        answers = {fit.count_fitting(hand, INTERIOR_3x2) for _ in range(20)}
        self.assertEqual(len(answers), 1)


if __name__ == "__main__":
    unittest.main()
