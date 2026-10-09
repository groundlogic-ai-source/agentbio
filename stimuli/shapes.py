"""The library of loose shapes, generated rather than hand-written.

With shapes of up to 6 cells there are several hundred possible pieces, so the
library is enumerated by code: start from a single cell and repeatedly add one
neighbouring cell, keeping every distinct result.

Each entry is one DRAWN orientation. A bar lying down and the same bar standing
up are two separate entries on purpose, because the puzzle cares how a shape is
drawn: one may fit the container as it is while the other needs turning first.

Circles are stored as the square of cells they cover, which is how the brief
says to treat them: a circle 3 cells wide needs a 3x3 space.

Shapes are then filtered by the difficulty band in the config, so the set
actually used is controlled by a computed measure rather than by eye.
"""

from dataclasses import dataclass
from functools import lru_cache

from . import difficulty, fit


@dataclass(frozen=True)
class Shape:
    name: str
    kind: str            # "poly" (drawn as filled cells) or "circle"
    cells: frozenset     # the cells it occupies, normalized to (0, 0)

    @property
    def size(self):
        return fit.size_of(self.cells)

    @property
    def n_cells(self):
        return len(self.cells)


def _grow(pieces):
    """Every shape made by adding one cell to one of these shapes."""
    out = set()
    for piece in pieces:
        for c, r in piece:
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbour = (c + dc, r + dr)
                if neighbour not in piece:
                    out.add(fit.normalize(set(piece) | {neighbour}))
    return out


@lru_cache(maxsize=None)
def polyominoes(max_cells):
    """Every distinct drawn polyomino from 1 up to max_cells cells.

    Counts come out as 1, 2, 6, 19, 63, 216 for sizes 1 to 6, which are the
    known numbers of fixed polyominoes and a decent check that nothing is
    missing or double counted.
    """
    out = []
    current = {frozenset({(0, 0)})}
    for n in range(1, max_cells + 1):
        if n > 1:
            current = _grow(current)
        # Sorted so the naming is stable from run to run.
        for index, piece in enumerate(sorted(current, key=sorted)):
            out.append(Shape(name=f"poly{n}_{index:03d}", kind="poly",
                             cells=piece))
    return tuple(out)


@lru_cache(maxsize=None)
def circles(max_width):
    """Circles from 1 cell across up to max_width cells across."""
    return tuple(
        Shape(name=f"circle{w}", kind="circle",
              cells=fit.rectangle_region(w, w))
        for w in range(1, max_width + 1)
    )


@lru_cache(maxsize=None)
def full_library(max_cells, max_circle_width):
    """Every candidate shape, before any difficulty filtering."""
    return polyominoes(max_cells) + circles(max_circle_width)


def scored_library(cfg):
    """Every candidate shape with its label and difficulty measure attached.

    Returns a list of (Shape, label, measure).
    """
    library = full_library(cfg.max_shape_cells, cfg.max_circle_width)
    return [
        (shape,) + difficulty.score(shape, cfg.interior_width,
                                    cfg.interior_height, cfg.allow_rotation,
                                    cfg.allow_reflections)
        for shape in library
    ]


def banded_library(cfg):
    """The shapes actually used, sorted into the three categories.

    Only shapes whose difficulty measure falls inside the band set in the
    config get through, so the puzzle set is controlled by a number rather
    than by judgement.
    """
    buckets = {fit.FITS_AS_DRAWN: [], fit.FITS_ROTATED: [], fit.TOO_BIG: []}
    for shape, label, measure in scored_library(cfg):
        if difficulty.in_band(label, measure, cfg):
            buckets[label].append((shape, measure))
    return buckets


def library_summary(cfg):
    """Counts before and after the difficulty band, for reporting."""
    scored = scored_library(cfg)
    before = {fit.FITS_AS_DRAWN: 0, fit.FITS_ROTATED: 0, fit.TOO_BIG: 0}
    for _, label, _ in scored:
        before[label] += 1
    banded = banded_library(cfg)
    after = {label: len(items) for label, items in banded.items()}
    return before, after
