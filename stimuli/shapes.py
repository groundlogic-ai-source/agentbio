"""The fixed library of loose shapes a scene can draw from.

Each shape is stored in one specific drawn orientation, because the puzzle
cares about that: a bar lying down fits a short wide interior, while the same
bar standing up does not until you turn it. Those are two different library
entries on purpose.

Cells are (column, row) pairs with (0, 0) at the shape's own top-left.
Circles are stored as the full square of cells they would cover, which is how
the brief says to treat them: a circle 3 cells wide needs a 3x3 space.
"""

from dataclasses import dataclass

from . import fit


@dataclass(frozen=True)
class Shape:
    name: str
    kind: str            # "poly" (drawn as filled cells) or "circle"
    cells: frozenset     # the cells it occupies, normalized to (0, 0)

    @property
    def size(self):
        """Width and height in cells."""
        return fit.size_of(self.cells)

    @property
    def n_cells(self):
        return len(self.cells)


def _poly(name, cells):
    return Shape(name=name, kind="poly", cells=fit.normalize(cells))


def _circle(width):
    """A circle `width` cells across, stored as the square it covers."""
    return Shape(
        name=f"circle{width}",
        kind="circle",
        cells=fit.rectangle_region(width, width),
    )


# Every polyomino below has 1 to 4 cells, as the brief specifies.
# Suffix _h means drawn lying down, _v means drawn standing up.
POLYOMINOES = [
    _poly("single",   [(0, 0)]),
    _poly("domino_h", [(0, 0), (1, 0)]),
    _poly("domino_v", [(0, 0), (0, 1)]),
    _poly("bar3_h",   [(0, 0), (1, 0), (2, 0)]),
    _poly("bar3_v",   [(0, 0), (0, 1), (0, 2)]),
    _poly("corner_a", [(0, 0), (1, 0), (0, 1)]),
    _poly("corner_b", [(0, 0), (1, 0), (1, 1)]),
    _poly("square4",  [(0, 0), (1, 0), (0, 1), (1, 1)]),
    _poly("bar4_h",   [(0, 0), (1, 0), (2, 0), (3, 0)]),
    _poly("bar4_v",   [(0, 0), (0, 1), (0, 2), (0, 3)]),
    _poly("tee_h",    [(0, 0), (1, 0), (2, 0), (1, 1)]),
    _poly("tee_v",    [(0, 0), (0, 1), (0, 2), (1, 1)]),
    _poly("ell_h",    [(0, 0), (1, 0), (2, 0), (0, 1)]),
    _poly("ell_v",    [(0, 0), (0, 1), (0, 2), (1, 2)]),
    _poly("jay_v",    [(1, 0), (1, 1), (1, 2), (0, 2)]),
    _poly("ess_h",    [(1, 0), (2, 0), (0, 1), (1, 1)]),
    _poly("ess_v",    [(0, 0), (0, 1), (1, 1), (1, 2)]),
]

CIRCLES = [_circle(w) for w in (1, 2, 3, 4)]

LIBRARY = POLYOMINOES + CIRCLES

BY_NAME = {s.name: s for s in LIBRARY}


def label_library(region, allow_rotation=True, allow_reflections=False):
    """Sort the whole library into the three categories for a given interior.

    Returns a dict: category name -> list of Shapes. Which shapes land in which
    category depends entirely on the interior size, so this is recomputed from
    the config rather than hard-coded.
    """
    buckets = {fit.FITS_AS_DRAWN: [], fit.FITS_ROTATED: [], fit.TOO_BIG: []}
    for shape in LIBRARY:
        label = fit.label_shape(
            shape.cells, region, allow_rotation, allow_reflections
        )
        buckets[label].append(shape)
    return buckets
