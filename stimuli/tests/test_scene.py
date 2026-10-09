"""Tests for how a scene is put together, and for the answer key it writes."""

import collections
import dataclasses
import unittest

from stimuli import fit, verify
from stimuli.config import DEFAULT
from stimuli.scene import (
    Container, balanced_answers, build_scene_with_retries, shape_mix_for_answer,
)


class TestContainerGeometry(unittest.TestCase):
    def setUp(self):
        self.container = Container(left=2, top=3, interior_width=3,
                                   interior_height=2, wall=1)

    def test_the_container_is_the_expected_size(self):
        # 3 wide inside plus a wall each side = 5; 2 tall inside plus a base
        # and no lid = 3.
        self.assertEqual(self.container.total_width, 5)
        self.assertEqual(self.container.total_height, 3)

    def test_the_interior_is_the_right_size_and_place(self):
        interior = self.container.interior
        self.assertEqual(len(interior), 6)
        self.assertEqual(fit.size_of(interior), (3, 2))
        # It starts one cell in from the left arm, level with the arm tops.
        self.assertIn((3, 3), interior)

    def test_the_container_is_open_at_the_top(self):
        # No filled cell sits above the interior.
        for c, r in self.container.interior:
            self.assertNotIn((c, r - 1), self.container.cells)

    def test_the_interior_and_the_walls_never_overlap(self):
        self.assertFalse(self.container.cells & self.container.interior)

    def test_the_base_is_directly_below_the_interior(self):
        for c, _ in self.container.interior:
            bottom = self.container.top + self.container.interior_height
            self.assertIn((c, bottom), self.container.cells)


class TestTheRecipe(unittest.TestCase):
    def test_the_mix_adds_up_to_the_shape_count(self):
        for answer in DEFAULT.answer_values:
            with self.subTest(answer=answer):
                mix = shape_mix_for_answer(answer, DEFAULT)
                self.assertEqual(sum(mix.values()), DEFAULT.n_loose_shapes)

    def test_every_scene_has_exactly_one_rotation_only_shape(self):
        for answer in DEFAULT.answer_values:
            with self.subTest(answer=answer):
                mix = shape_mix_for_answer(answer, DEFAULT)
                self.assertEqual(mix[fit.FITS_ROTATED],
                                 DEFAULT.rotation_shapes_per_scene)

    def test_an_impossible_answer_is_refused_rather_than_fudged(self):
        # 0 cannot happen while every scene carries a rotation shape, which
        # always fits. 6 is more shapes than there are.
        with self.assertRaises(ValueError):
            shape_mix_for_answer(0, DEFAULT)
        with self.assertRaises(ValueError):
            shape_mix_for_answer(DEFAULT.n_loose_shapes + 1, DEFAULT)


class TestBalancedAnswers(unittest.TestCase):
    def test_answers_are_spread_evenly(self):
        answers = balanced_answers(30, DEFAULT)
        counts = collections.Counter(answers)
        self.assertEqual(set(counts), set(DEFAULT.answer_values))
        self.assertEqual(set(counts.values()), {6})

    def test_the_same_seed_gives_the_same_order(self):
        self.assertEqual(balanced_answers(40, DEFAULT),
                         balanced_answers(40, DEFAULT))

    def test_a_different_seed_gives_a_different_order(self):
        other = dataclasses.replace(DEFAULT, seed=DEFAULT.seed + 1)
        self.assertNotEqual(balanced_answers(40, DEFAULT),
                            balanced_answers(40, other))


class TestBuiltScenes(unittest.TestCase):
    """Build a batch of real scenes and check everything about them."""

    @classmethod
    def setUpClass(cls):
        answers = balanced_answers(15, DEFAULT)
        cls.scenes = [
            build_scene_with_retries(i + 1, a, DEFAULT)
            for i, a in enumerate(answers)
        ]

    def test_the_answer_matches_the_fit_function(self):
        for scene in self.scenes:
            with self.subTest(scene=scene.index):
                counted = sum(1 for p in scene.placed if p.fits)
                self.assertEqual(counted, scene.answer)

    def test_every_scene_has_the_same_number_of_shapes(self):
        for scene in self.scenes:
            self.assertEqual(len(scene.placed), DEFAULT.n_loose_shapes)

    def test_every_scene_has_exactly_one_shape_needing_a_turn(self):
        for scene in self.scenes:
            with self.subTest(scene=scene.index):
                rotated = [p for p in scene.placed
                           if p.label == fit.FITS_ROTATED]
                self.assertEqual(len(rotated), 1)

    def test_loose_shapes_stay_outside_the_container(self):
        for scene in self.scenes:
            forbidden = scene.container.cells | scene.container.interior
            for placed in scene.placed:
                with self.subTest(scene=scene.index, shape=placed.name):
                    self.assertFalse(placed.cells & forbidden)

    def test_loose_shapes_never_overlap_each_other(self):
        for scene in self.scenes:
            seen = set()
            for placed in scene.placed:
                with self.subTest(scene=scene.index, shape=placed.name):
                    self.assertFalse(placed.cells & seen)
                seen |= placed.cells

    def test_everything_stays_on_the_grid(self):
        for scene in self.scenes:
            everything = set(scene.container.cells)
            for placed in scene.placed:
                everything |= placed.cells
            for c, r in everything:
                self.assertTrue(0 <= c < DEFAULT.grid_width)
                self.assertTrue(0 <= r < DEFAULT.grid_height)

    def test_the_same_seed_rebuilds_an_identical_scene(self):
        again = build_scene_with_retries(1, self.scenes[0].answer, DEFAULT)
        self.assertEqual(again.to_dict(), self.scenes[0].to_dict())

    def test_the_verifier_agrees_with_every_answer_key(self):
        # The round trip that matters: the generator's answer key, re-checked
        # by the separately written verifier.
        for scene in self.scenes:
            with self.subTest(scene=scene.index):
                self.assertEqual(verify.check_scene(scene.to_dict()), [])


class TestTheVerifierCatchesMistakes(unittest.TestCase):
    """A checker that always says 'fine' would be useless."""

    def setUp(self):
        self.record = build_scene_with_retries(1, 3, DEFAULT).to_dict()

    def test_a_clean_scene_passes(self):
        self.assertEqual(verify.check_scene(self.record), [])

    def test_a_wrong_answer_is_caught(self):
        self.record["answer"] += 1
        problems = verify.check_scene(self.record)
        self.assertTrue(any("ANSWER MISMATCH" in p for p in problems))

    def test_a_mislabelled_shape_is_caught(self):
        for shape in self.record["loose_shapes"]:
            if shape["fits"]:
                shape["fits"] = False
                shape["label"] = "too_big"
                break
        problems = verify.check_scene(self.record)
        self.assertTrue(any("recomputed fits=True" in p for p in problems))

    def test_an_overlapping_shape_is_caught(self):
        self.record["loose_shapes"][0]["cells"] = [
            self.record["container"]["interior_cells"][0]
        ]
        problems = verify.check_scene(self.record)
        self.assertTrue(any("overlaps" in p for p in problems))

    def test_the_verifier_measures_the_interior_for_itself(self):
        # Lie about the interior size in the key; the verifier works it out
        # from the container's own cells and should notice.
        self.record["container"]["interior_width"] += 1
        problems = verify.check_scene(self.record)
        self.assertTrue(any("interior measured" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
