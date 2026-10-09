"""Scoring how hard each candidate shape is, so the puzzle set can be controlled.

The research plan asks for distractor shapes to be chosen by a computed
difficulty measure rather than by personal judgement, and for only shapes
inside a pre-set band to be used. This file computes those measures.

Two measures, one for each side of the fit question:

  * A shape that FITS is scored by its BOUNDING-BOX SLACK: the spare width plus
    the spare height at whichever allowed orientation fits most snugly. Slack 0
    means the shape is exactly as wide and as tall as the interior - a
    deceptively tight fit. A large slack means it is obviously small enough.

  * A shape that does NOT fit is scored by its NEAR-MISS DISTANCE: the fewest
    cells that would have to be removed before some placement would work. A
    distance of 1 is a near miss, which is the interesting case. A large
    distance means it is obviously too big.

A note on the first measure. The plan originally described it as the leftover
empty space after the tightest placement. In a rectangular interior that
quantity cannot vary: every valid placement of a given shape leaves exactly
(interior area - shape area) cells empty, so there is no tightest placement and
the measure would only restate the shape's cell count. Bounding-box slack is
the thing that measure was reaching for, and it does vary.
"""

import itertools

from . import fit


def bbox_slack(cells, interior_width, interior_height, allow_rotation=True,
               allow_reflections=False):
    """Spare width plus spare height at the snuggest orientation that fits.

    Returns None if the shape does not fit in any allowed orientation.
    """
    best = None
    for variant in fit.orientations(cells, allow_rotation, allow_reflections):
        width, height = fit.size_of(variant)
        if width <= interior_width and height <= interior_height:
            slack = (interior_width - width) + (interior_height - height)
            if best is None or slack < best:
                best = slack
    return best


def _is_connected(cells):
    """True if these cells form one piece, touching edge to edge."""
    cells = set(cells)
    if not cells:
        return False
    stack = [next(iter(cells))]
    seen = set()
    while stack:
        c, r = stack.pop()
        if (c, r) in seen:
            continue
        seen.add((c, r))
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (c + dc, r + dr) in cells:
                stack.append((c + dc, r + dr))
    return len(seen) == len(cells)


def near_miss_distance(cells, region, allow_rotation=True,
                       allow_reflections=False, cap=4):
    """Fewest cells to remove before the shape would fit somewhere.

    Only pieces that stay in one connected lump count, because a shape that has
    been broken in two is no longer the same object. Returns None if the shape
    is further than `cap` cells away from fitting.
    """
    cells = list(cells)
    for k in range(1, min(cap, len(cells) - 1) + 1):
        for keep in itertools.combinations(cells, len(cells) - k):
            if _is_connected(keep) and fit.fits_in_region(
                keep, region, allow_rotation, allow_reflections
            ):
                return k
    return None


def circle_near_miss(width, interior_width, interior_height):
    """How many cells narrower a circle would have to be before it fits.

    Removing single cells from a circle is meaningless, since a circle is
    judged as the square box it sits in. Shrinking its diameter is the
    equivalent move.
    """
    allowed = min(interior_width, interior_height)
    return max(0, width - allowed) or None


def score(shape, interior_width, interior_height, allow_rotation=True,
          allow_reflections=False):
    """Label a shape and give it the difficulty number for its side.

    Returns (label, measure). The measure is slack for shapes that fit and
    near-miss distance for shapes that do not. A measure of None on a too-big
    shape means it is far past fitting.
    """
    region = fit.rectangle_region(interior_width, interior_height)
    label = fit.label_shape(shape.cells, region, allow_rotation,
                            allow_reflections)

    if label == fit.TOO_BIG:
        if shape.kind == "circle":
            width, _ = shape.size
            return label, circle_near_miss(width, interior_width,
                                           interior_height)
        return label, near_miss_distance(shape.cells, region, allow_rotation,
                                         allow_reflections)

    return label, bbox_slack(shape.cells, interior_width, interior_height,
                             allow_rotation, allow_reflections)


def in_band(label, measure, cfg):
    """Is this shape inside the difficulty band set in the config?

    A measure of None (further from fitting than the cap) is always out of
    band: those are the obviously-too-big shapes the plan wants excluded.
    """
    if measure is None:
        return False
    if label == fit.TOO_BIG:
        low, high = cfg.near_miss_band
    else:
        low, high = cfg.slack_band
    return low <= measure <= high
