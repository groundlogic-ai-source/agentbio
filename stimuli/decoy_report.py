"""How often could a simpler rule explain the answers?

    python3 -m stimuli.decoy_report --dir out/ --csv decoys.csv

If a viewer guessed a much simpler rule - "count all the shapes", "count the
squares" - would they get the same numbers? Where a simple rule gives the same
answer as the real rule, that scene does not help tell the two rules apart.

This REPORTS ONLY. It never removes, filters or changes a scene. Knowing how
distinguishable the scenes are is part of the finding, not something to tidy
away.
"""

import argparse
import csv
import json
import pathlib

from .verify import load_records


def _is_solid_square(cells):
    """True if these cells form a filled square block (1x1, 2x2, ...)."""
    cols = [c for c, _ in cells]
    rows_ = [r for _, r in cells]
    width = max(cols) - min(cols) + 1
    height = max(rows_) - min(rows_) + 1
    return width == height and len(cells) == width * height


def decoy_values(record):
    """Work out what each simple alternative rule would answer for this scene."""
    shapes_ = record["loose_shapes"]
    cells_per_shape = [[tuple(c) for c in s["cells"]] for s in shapes_]

    # Rule: "count every loose shape."
    total_shapes = len(shapes_)

    # Rule: "count the square ones." Circles are stored as their square of
    # cells, so a circle is excluded by checking what it is drawn as.
    n_squares = sum(
        1 for s, cells in zip(shapes_, cells_per_shape)
        if s["kind"] == "poly" and _is_solid_square(cells)
    )

    # A second reading of "squares": anything made of cells, versus circles.
    n_not_circles = sum(1 for s in shapes_ if s["kind"] != "circle")

    # Rule: "count the shapes of one particular colour." We take the colour
    # that appears most often among the loose shapes.
    colors = [s["color"] for s in shapes_]
    most_common_color_count = max(colors.count(c) for c in set(colors))

    return {
        "total_shapes": total_shapes,
        "n_squares": n_squares,
        "n_not_circles": n_not_circles,
        "most_common_color_count": most_common_color_count,
    }


RULE_NOTES = {
    "total_shapes": "count every loose shape",
    "n_squares": "count the solid square pieces",
    "n_not_circles": "count everything that is not a circle",
    "most_common_color_count": "count the shapes of the most common colour",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="out",
                        help="folder of generated scenes (default: out)")
    parser.add_argument("--csv", default=None,
                        help="also write the per-scene table to this CSV file")
    parser.add_argument("--per-scene", action="store_true",
                        help="print every scene, not just the summary")
    args = parser.parse_args(argv)

    records = [record for _, record in load_records(args.dir)]
    rules = list(RULE_NOTES)

    rows = []
    for record in records:
        values = decoy_values(record)
        answer = record["answer"]
        row = {"scene": record["scene_index"], "true_answer": answer}
        for rule in rules:
            row[rule] = values[rule]
            row[f"{rule}_differs"] = int(values[rule] != answer)
        row["differs_from_all"] = int(
            all(values[rule] != answer for rule in rules)
        )
        rows.append(row)

    if args.per_scene:
        header = f"{'scene':>6} {'answer':>7} " + " ".join(
            f"{rule:>24}" for rule in rules
        )
        print(header)
        print("-" * len(header))
        for row in rows:
            cells = " ".join(
                f"{row[rule]:>24}" if row[f'{rule}_differs']
                else f"{str(row[rule]) + ' (same)':>24}"
                for rule in rules
            )
            print(f"{row['scene']:>6} {row['true_answer']:>7} {cells}")
        print()

    n = len(rows)
    print(f"Decoy report over {n} scenes")
    print("A scene 'tells the rules apart' when the simple rule gives a "
          "different number from the true answer.")
    print()
    for rule in rules:
        differs = sum(row[f"{rule}_differs"] for row in rows)
        note = RULE_NOTES[rule]
        print(f"  {rule:<26} ({note})")
        print(f"  {'':<26} tells them apart in {differs}/{n} scenes "
              f"({100 * differs / n:.0f}%)")
        if differs == 0:
            print(f"  {'':<26} NOTE: never distinguishable - this rule gives "
                  f"the same number as the true answer in every scene.")
        print()

    all_differ = sum(row["differs_from_all"] for row in rows)
    print(f"  scenes that tell the true rule apart from ALL of the above: "
          f"{all_differ}/{n} ({100 * all_differ / n:.0f}%)")

    # Honest note about a rule that cannot say anything under this config.
    colors = {s["color"] for record in records
              for s in record["loose_shapes"]}
    per_scene_colors = max(
        len({s["color"] for s in record["loose_shapes"]})
        for record in records
    )
    if per_scene_colors == 1:
        print()
        print("  Note on colour: every loose shape in a scene shares one "
              "colour (per_shape_colors is off in the config), so the colour "
              "rule is identical to 'count every loose shape' by "
              "construction, not by chance. Turn per_shape_colors on in "
              "config.py to make it a real alternative rule.")

    if args.csv:
        with open(args.csv, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote per-scene table to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
