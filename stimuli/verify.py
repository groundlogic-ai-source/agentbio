"""Independently re-check every answer key.

    python3 -m stimuli.verify --dir out/

This is on purpose a SECOND implementation, written differently from the
generator so that a mistake in one is unlikely to be repeated in the other:

  * It reads only the PNG's companion JSON, and only the raw cell lists.
  * It works out the container's interior from the container's filled cells,
    rather than trusting the interior the generator recorded.
  * It represents a shape as a little grid of rows of True/False, and rotates
    it by flipping and transposing those rows, instead of transforming
    coordinates.
  * It tests placement by walking a plain 2-D array.

If the two implementations disagree about any scene, that scene is reported as
a mismatch. It never edits or deletes anything.
"""

import argparse
import json
import pathlib


# ---- Grid-of-rows representation -------------------------------------

def cells_to_rows(cells):
    """Turn a list of [column, row] pairs into a small grid of True/False."""
    cols = [c for c, _ in cells]
    rows_ = [r for _, r in cells]
    min_c, min_r = min(cols), min(rows_)
    width = max(cols) - min_c + 1
    height = max(rows_) - min_r + 1
    grid = [[False] * width for _ in range(height)]
    for c, r in cells:
        grid[r - min_r][c - min_c] = True
    return grid


def turn_clockwise(grid):
    """Rotate a grid of rows a quarter turn clockwise.

    Reverse the order of the rows, then read the result column by column.
    """
    return [list(column) for column in zip(*grid[::-1])]


def mirror(grid):
    """Flip a grid of rows left to right."""
    return [row[::-1] for row in grid]


def all_versions(grid, allow_rotation, allow_reflections):
    """Every version of a shape we are allowed to try, as grids of rows."""
    versions = []

    def add(candidate):
        if candidate not in versions:
            versions.append(candidate)

    add(grid)
    if allow_rotation:
        turned = grid
        for _ in range(3):
            turned = turn_clockwise(turned)
            add(turned)
    if allow_reflections:
        flipped = mirror(grid)
        add(flipped)
        if allow_rotation:
            turned = flipped
            for _ in range(3):
                turned = turn_clockwise(turned)
                add(turned)
    return versions


def goes_inside(grid, box_width, box_height):
    """Can this grid of rows be slid somewhere inside a plain box?

    The box here is the container interior, which is a rectangle. Walked
    position by position rather than compared as sizes, so the check stays
    honest about every cell.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    if height > box_height or width > box_width:
        return False
    for top in range(box_height - height + 1):
        for left in range(box_width - width + 1):
            ok = True
            for r, row in enumerate(grid):
                for c, filled in enumerate(row):
                    if filled:
                        inside_r = top + r
                        inside_c = left + c
                        if not (0 <= inside_r < box_height
                                and 0 <= inside_c < box_width):
                            ok = False
                            break
                if not ok:
                    break
            if ok:
                return True
    return False


# ---- Working out the interior from the container itself ---------------

def interior_box_from_container(container_cells):
    """How big is the hollow in the middle of the U?

    Take the rectangle the container's filled cells sit in, and count the cells
    inside that rectangle which are NOT filled. For a U shape those unfilled
    cells are exactly the interior. Done this way so the verifier does not have
    to believe the interior the generator wrote down.
    """
    filled = {(c, r) for c, r in container_cells}
    cols = [c for c, _ in filled]
    rows_ = [r for _, r in filled]
    empty = [
        (c, r)
        for c in range(min(cols), max(cols) + 1)
        for r in range(min(rows_), max(rows_) + 1)
        if (c, r) not in filled
    ]
    if not empty:
        return (0, 0), []
    e_cols = [c for c, _ in empty]
    e_rows = [r for _, r in empty]
    width = max(e_cols) - min(e_cols) + 1
    height = max(e_rows) - min(e_rows) + 1
    # Guard: the hollow should be a solid rectangle. If it is not, the shape
    # is not the U the puzzle assumes and we want to hear about it.
    if len(empty) != width * height:
        return None, empty
    return (width, height), empty


# ---- Checking one scene ----------------------------------------------

def check_scene(record):
    """Return a list of problems with this scene. Empty list means it is fine."""
    problems = []
    allow_rotation = record.get("allow_rotation", True)
    allow_reflections = record.get("allow_reflections", False)

    container_cells = [tuple(cell) for cell in record["container"]["cells"]]
    box, empty = interior_box_from_container(container_cells)
    if box is None:
        problems.append("the container's hollow is not a clean rectangle")
        return problems
    box_width, box_height = box

    stated = record["container"]
    if (box_width, box_height) != (stated["interior_width"],
                                   stated["interior_height"]):
        problems.append(
            f"interior measured from the drawing is {box_width}x{box_height} "
            f"but the key says {stated['interior_width']}x"
            f"{stated['interior_height']}"
        )

    # No loose shape may overlap another, the container, or the interior.
    occupied = set(container_cells) | set(empty)
    fitting = 0
    for shape in record["loose_shapes"]:
        cells = [tuple(cell) for cell in shape["cells"]]
        for cell in cells:
            if cell in occupied:
                problems.append(
                    f"shape {shape['name']} overlaps something at {cell}"
                )
        occupied.update(cells)

        grid = cells_to_rows(cells)
        versions = all_versions(grid, allow_rotation, allow_reflections)
        as_drawn = goes_inside(versions[0], box_width, box_height)
        any_way = any(goes_inside(v, box_width, box_height) for v in versions)

        if any_way:
            fitting += 1
        if any_way != shape["fits"]:
            problems.append(
                f"shape {shape['name']}: key says fits={shape['fits']}, "
                f"recomputed fits={any_way}"
            )

        if as_drawn:
            label = "fits_without_rotation"
        elif any_way:
            label = "fits_only_with_rotation"
        else:
            label = "too_big"
        if label != shape["label"]:
            problems.append(
                f"shape {shape['name']}: key says label={shape['label']}, "
                f"recomputed label={label}"
            )

    if fitting != record["answer"]:
        problems.append(
            f"ANSWER MISMATCH: key says {record['answer']}, "
            f"recomputed {fitting}"
        )
    return problems


def load_records(directory):
    directory = pathlib.Path(directory)
    files = sorted(directory.glob("scene_*.json"))
    if not files:
        raise SystemExit(f"no scene_*.json files found in {directory}")
    return [(f.name, json.loads(f.read_text())) for f in files]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="out",
                        help="folder of generated scenes (default: out)")
    parser.add_argument("--quiet", action="store_true",
                        help="only print the summary")
    args = parser.parse_args(argv)

    records = load_records(args.dir)
    bad = 0
    for name, record in records:
        problems = check_scene(record)
        if problems:
            bad += 1
            print(f"{name}:")
            for problem in problems:
                print(f"    {problem}")
        elif not args.quiet:
            print(f"{name}: ok (answer {record['answer']})")

    print()
    print(f"checked {len(records)} scenes, {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
