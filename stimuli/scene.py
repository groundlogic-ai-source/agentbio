"""Building one scene: a container plus loose shapes scattered around it.

Nothing here decides an answer by eye. A scene is assembled from a recipe, and
then the answer is computed from the geometry by the fit function.

Grid coordinates are (column, row) with (0, 0) at the top-left of the grid.
Rows grow downward, so "above" means a smaller row number.
"""

import random
from dataclasses import dataclass, field

from . import fit, shapes
from .config import Config, DEFAULT


# ---- The container ----------------------------------------------------

@dataclass(frozen=True)
class Container:
    """A U shape open at the top: a left arm, a right arm, and a base."""

    left: int            # grid column of the outer edge of the left arm
    top: int             # grid row of the tops of the two arms
    interior_width: int
    interior_height: int
    wall: int            # thickness of each arm and of the base

    @property
    def total_width(self):
        return self.interior_width + 2 * self.wall

    @property
    def total_height(self):
        return self.interior_height + self.wall   # open at the top, so no lid

    @property
    def interior(self):
        """The empty cells a loose shape has to fit into.

        Between the two arms, above the base, and reaching up to the height of
        the arm tops - exactly as the brief defines it.
        """
        c0 = self.left + self.wall
        return frozenset(
            (c0 + dc, self.top + dr)
            for dc in range(self.interior_width)
            for dr in range(self.interior_height)
        )

    @property
    def cells(self):
        """The filled cells of the container itself (both arms and the base)."""
        out = set()
        # Left and right arms run the full height of the container.
        for dr in range(self.total_height):
            for dc in range(self.wall):
                out.add((self.left + dc, self.top + dr))
                out.add((self.left + self.total_width - 1 - dc, self.top + dr))
        # The base runs the full width, along the bottom.
        for dc in range(self.total_width):
            for dr in range(self.wall):
                out.add((self.left + dc,
                         self.top + self.total_height - 1 - dr))
        return frozenset(out)


# ---- A placed loose shape --------------------------------------------

@dataclass(frozen=True)
class PlacedShape:
    name: str            # which library shape it is
    kind: str            # "poly" or "circle"
    label: str           # fits_without_rotation / fits_only_with_rotation / too_big
    fits: bool           # does it fit, under the scene's rotation setting
    cells: frozenset     # where it sits on the grid (absolute coordinates)
    color: str
    measure: int         # difficulty: slack if it fits, cells-from-fitting if not


@dataclass
class Scene:
    index: int
    seed: int
    container: Container
    container_color: str
    background_color: str
    placed: list
    answer: int
    config: Config = field(default_factory=lambda: DEFAULT, repr=False)

    def to_dict(self):
        """The answer key for this scene, as plain data for the JSON file."""
        return {
            "scene_index": self.index,
            "seed": self.seed,
            "answer": self.answer,
            "puzzle": "container_fit",
            "rule_description":
                "number of loose shapes that fit inside the container, "
                "judged one shape at a time",
            "allow_rotation": self.config.allow_rotation,
            "allow_reflections": self.config.allow_reflections,
            "grid": {"width": self.config.grid_width,
                     "height": self.config.grid_height},
            "background_color": self.background_color,
            "difficulty_band": {
                "slack_band": list(self.config.slack_band),
                "near_miss_band": list(self.config.near_miss_band),
            },
            "container": {
                "left": self.container.left,
                "top": self.container.top,
                "interior_width": self.container.interior_width,
                "interior_height": self.container.interior_height,
                "wall_thickness": self.container.wall,
                "color": self.container_color,
                "cells": sorted(map(list, self.container.cells)),
                "interior_cells": sorted(map(list, self.container.interior)),
            },
            "loose_shapes": [
                {
                    "name": p.name,
                    "kind": p.kind,
                    "label": p.label,
                    "fits": p.fits,
                    "color": p.color,
                    "difficulty_measure": p.measure,
                    "difficulty_measure_kind": (
                        "cells_from_fitting" if p.label == fit.TOO_BIG
                        else "bounding_box_slack"
                    ),
                    "cells": sorted(map(list, p.cells)),
                }
                for p in self.placed
            ],
        }


# ---- Placement helpers ------------------------------------------------

def _neighbourhood(cells, radius):
    """Every cell within `radius` steps of these cells, diagonals included.

    Used to keep a clear gap around things, so two loose shapes never end up
    touching or even corner-to-corner.
    """
    out = set()
    for c, r in cells:
        for dc in range(-radius, radius + 1):
            for dr in range(-radius, radius + 1):
                out.add((c + dc, r + dr))
    return out


def _can_place(cells, blocked, cfg):
    """Is this a legal spot: on the grid, and clear of everything else?"""
    for c, r in cells:
        if not (0 <= c < cfg.grid_width and 0 <= r < cfg.grid_height):
            return False
        if (c, r) in blocked:
            return False
    return True


def _place_shape(shape, blocked, rng, cfg, tries=400):
    """Find a random legal spot for one shape. Returns its cells, or None."""
    width, height = shape.size
    max_c = cfg.grid_width - width
    max_r = cfg.grid_height - height
    if max_c < 0 or max_r < 0:
        return None
    for _ in range(tries):
        offset_c = rng.randint(0, max_c)
        offset_r = rng.randint(0, max_r)
        cells = frozenset((c + offset_c, r + offset_r)
                          for c, r in shape.cells)
        if _can_place(cells, blocked, cfg):
            return cells
    return None


# ---- The recipe -------------------------------------------------------

def shape_mix_for_answer(answer, cfg):
    """How many shapes of each category a scene with this answer needs.

    Every scene keeps the same total number of shapes and the same number of
    fits-only-with-rotation shapes, so the amount of looking required stays the
    same. Only the fits-as-drawn / too-big split changes, and that is what
    moves the answer.
    """
    n_rotation = cfg.rotation_shapes_per_scene
    n_as_drawn = answer - n_rotation
    n_too_big = cfg.n_loose_shapes - n_rotation - n_as_drawn
    if n_as_drawn < 0 or n_too_big < 0:
        raise ValueError(
            f"answer {answer} is impossible with {cfg.n_loose_shapes} shapes "
            f"and {n_rotation} rotation-only shape(s) per scene"
        )
    return {
        fit.FITS_AS_DRAWN: n_as_drawn,
        fit.FITS_ROTATED: n_rotation,
        fit.TOO_BIG: n_too_big,
    }


def pick_shape(pool, rng, cfg):
    """Choose one shape from a category's pool.

    The pool is lopsided: there are many more large shapes than small ones, so
    picking uniformly would fill every scene with the biggest pieces. Instead,
    pick a size first and then a shape of that size, which spreads the sizes
    out. Circles are picked separately at a set rate, because stratifying them
    by cell count would make the large ones far too common.
    """
    circles = [item for item in pool if item[0].kind == "circle"]
    polys = [item for item in pool if item[0].kind != "circle"]

    if circles and (not polys or rng.random() < cfg.circle_probability):
        return rng.choice(circles)
    if not polys:
        return rng.choice(circles)
    if not cfg.stratify_by_cell_count:
        return rng.choice(polys)

    by_size = {}
    for item in polys:
        by_size.setdefault(item[0].n_cells, []).append(item)
    size = rng.choice(sorted(by_size))
    return rng.choice(by_size[size])


def build_scene(index, answer, cfg=DEFAULT, master_seed=None):
    """Build one scene whose computed answer is `answer`.

    The answer is not written in by hand: shapes are chosen by category, then
    the fit function counts them, and we assert the two agree.
    """
    master_seed = cfg.seed if master_seed is None else master_seed
    seed = master_seed * 1_000_003 + index
    rng = random.Random(seed)

    buckets = shapes.banded_library(cfg)
    mix = shape_mix_for_answer(answer, cfg)

    # Pick the shapes. Repeats within a scene are allowed, which keeps scenes
    # varied even though some categories hold few shapes.
    chosen = []
    for label, count in mix.items():
        pool = buckets[label]
        if count and not pool:
            raise ValueError(
                f"no shapes available in category {label} inside the "
                f"difficulty band; widen slack_band or near_miss_band"
            )
        chosen.extend(pick_shape(pool, rng, cfg) for _ in range(count))
    rng.shuffle(chosen)   # so category never correlates with drawing order

    # Colours, picked with no reference to the answer.
    container_color = rng.choice(cfg.container_palette)
    scene_loose_color = rng.choice(cfg.loose_palette)
    background_color = rng.choice(cfg.background_palette)

    # Place the container, keeping it one cell clear of the grid edges.
    probe = Container(0, 0, cfg.interior_width, cfg.interior_height,
                      cfg.wall_thickness)
    left = rng.randint(1, cfg.grid_width - probe.total_width - 1)
    top = rng.randint(1, cfg.grid_height - probe.total_height - 1)
    container = Container(left, top, cfg.interior_width,
                          cfg.interior_height, cfg.wall_thickness)

    # Loose shapes must stay outside the container, which means out of its
    # walls AND out of its interior, with a clear gap around everything.
    blocked = _neighbourhood(
        container.cells | container.interior, cfg.min_gap
    )

    placed = []
    for shape, measure in chosen:
        cells = _place_shape(shape, blocked, rng, cfg)
        if cells is None:
            return None     # this layout did not work out; caller retries
        label = fit.label_shape(shape.cells, container.interior,
                                cfg.allow_rotation, cfg.allow_reflections)
        fits = fit.fits_in_region(shape.cells, container.interior,
                                  cfg.allow_rotation, cfg.allow_reflections)
        color = (rng.choice(cfg.loose_palette) if cfg.per_shape_colors
                 else scene_loose_color)
        placed.append(PlacedShape(
            name=shape.name, kind=shape.kind, label=label,
            fits=fits, cells=cells, color=color, measure=measure,
        ))
        blocked |= _neighbourhood(cells, cfg.min_gap)

    computed = fit.count_fitting(
        [shape.cells for shape, _ in chosen], container.interior,
        cfg.allow_rotation, cfg.allow_reflections,
    )
    # A safety net: if the recipe and the fit function ever disagree, that is a
    # bug, and we want it to stop the run rather than ship a wrong answer key.
    assert computed == answer, (
        f"recipe asked for answer {answer} but the fit function says {computed}"
    )

    return Scene(index=index, seed=seed, container=container,
                 container_color=container_color,
                 background_color=background_color, placed=placed,
                 answer=computed, config=cfg)


def build_scene_with_retries(index, answer, cfg=DEFAULT, master_seed=None,
                             attempts=60):
    """Keep trying layouts until the shapes all find room."""
    for attempt in range(attempts):
        scene = build_scene(index + attempt * 100_000, answer, cfg,
                            master_seed)
        if scene is not None:
            scene.index = index    # keep the human-facing numbering tidy
            return scene
    raise RuntimeError(
        f"could not lay out scene {index} (answer {answer}) in {attempts} "
        f"attempts; the grid may be too small for {cfg.n_loose_shapes} shapes"
    )


def balanced_answers(n_scenes, cfg=DEFAULT):
    """A list of `n_scenes` answers, spread as evenly as possible.

    Deterministic: the same seed always gives the same order.
    """
    values = list(cfg.answer_values)
    rng = random.Random(cfg.seed)
    out = []
    while len(out) < n_scenes:
        block = values[:]
        rng.shuffle(block)
        out.extend(block)
    return out[:n_scenes]
