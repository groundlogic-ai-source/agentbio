"""Deciding whether a loose shape fits inside the container's interior.

This is the heart of the puzzle. It is deliberately dumb and exhaustive: it
tries every allowed orientation of the shape, and every possible position
inside the interior, and says "fits" if any one of those lands the whole shape
inside. There is no randomness and no visual judgement anywhere in here, so the
same shape and the same interior always give the same answer.

A "shape" here is just a set of (column, row) cells. A circle is handled by the
shape library, which turns it into the square of cells it would cover, exactly
as the brief asks ("a circle 3 cells wide needs a 3x3 space").
"""

# A set of cells. (0, 0) is the top-left of whatever we are measuring.
# Columns grow to the right, rows grow downward.


def normalize(cells):
    """Slide a set of cells so its top-left corner sits at (0, 0).

    This lets us compare two shapes without caring where they were drawn.
    """
    min_c = min(c for c, _ in cells)
    min_r = min(r for _, r in cells)
    return frozenset((c - min_c, r - min_r) for c, r in cells)


def rotate_90(cells):
    """Turn a set of cells 90 degrees clockwise."""
    # A clockwise quarter turn sends (column, row) to (-row, column).
    return normalize([(-r, c) for c, r in cells])


def reflect(cells):
    """Mirror a set of cells left-to-right."""
    return normalize([(-c, r) for c, r in cells])


def orientations(cells, allow_rotation=True, allow_reflections=False):
    """Every version of the shape we are allowed to try, duplicates removed.

    The first item is always the shape exactly as it was drawn. That matters,
    because "fits without rotation" means "fits in that first orientation".
    """
    drawn = normalize(cells)
    result = [drawn]
    seen = {drawn}

    candidates = []
    if allow_rotation:
        turned = drawn
        for _ in range(3):
            turned = rotate_90(turned)
            candidates.append(turned)
    if allow_reflections:
        mirrored = reflect(drawn)
        candidates.append(mirrored)
        if allow_rotation:
            turned = mirrored
            for _ in range(3):
                turned = rotate_90(turned)
                candidates.append(turned)

    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            result.append(cand)
    return result


def size_of(cells):
    """The width and height of the smallest box that holds these cells."""
    cells = normalize(cells)
    return (max(c for c, _ in cells) + 1, max(r for _, r in cells) + 1)


def placement_fits(cells, region):
    """True if this exact orientation can be slid somewhere fully inside region.

    `region` is a set of cells that count as "inside". We try every offset
    within the region's bounding box. Written against a free-form set of cells
    rather than a rectangle, so it still works if the interior is ever an odd
    shape rather than a plain box.
    """
    if not cells:
        return True
    if not region:
        return False

    cells = normalize(cells)
    shape_w, shape_h = size_of(cells)

    region_cols = [c for c, _ in region]
    region_rows = [r for _, r in region]
    min_c, max_c = min(region_cols), max(region_cols)
    min_r, max_r = min(region_rows), max(region_rows)

    region_w = max_c - min_c + 1
    region_h = max_r - min_r + 1
    if shape_w > region_w or shape_h > region_h:
        return False

    for offset_c in range(min_c, max_c - shape_w + 2):
        for offset_r in range(min_r, max_r - shape_h + 2):
            if all((c + offset_c, r + offset_r) in region
                   for c, r in cells):
                return True
    return False


def fits_in_region(cells, region, allow_rotation=True, allow_reflections=False):
    """True if the shape fits inside the region in any allowed orientation."""
    for variant in orientations(cells, allow_rotation, allow_reflections):
        if placement_fits(variant, region):
            return True
    return False


def rectangle_region(width, height):
    """The set of cells making up a plain width x height box."""
    return frozenset((c, r) for c in range(width) for r in range(height))


# ---- Labels ------------------------------------------------------------

FITS_AS_DRAWN = "fits_without_rotation"
FITS_ROTATED = "fits_only_with_rotation"
TOO_BIG = "too_big"


def label_shape(cells, region, allow_rotation=True, allow_reflections=False):
    """Sort a shape into one of the three categories the brief defines.

    - fits_without_rotation: fits just as it is drawn
    - fits_only_with_rotation: does not fit as drawn, but fits once turned
    - too_big: never fits, however you turn it
    """
    if placement_fits(cells, region):
        return FITS_AS_DRAWN
    if fits_in_region(cells, region, allow_rotation, allow_reflections):
        return FITS_ROTATED
    return TOO_BIG


def count_fitting(shape_cell_sets, region, allow_rotation=True,
                  allow_reflections=False):
    """The scene's answer: how many of these shapes fit, judged one at a time.

    Shapes are never packed together; each is measured against the empty
    interior on its own, which is what the puzzle's rule says.
    """
    return sum(
        1 for cells in shape_cell_sets
        if fits_in_region(cells, region, allow_rotation, allow_reflections)
    )
