# AI_LOG

A running record of what was decided and what changed in each working session.

---

## 2026-10-09 - Puzzle 1 (container-fit) stimulus generator

### Decided

* **Where the work lives.** A new self-contained `stimuli/` folder inside this
  repository. It shares nothing with the existing bio code.
* **Dependencies.** Python 3 and Pillow only. Pillow was already installed.
  Tests use the standard library's `unittest`, because `pytest` is not
  installed and adding it was avoidable.
* **The contradiction in the brief, and how it was settled.** The brief asked
  for the same mix of shape labels in every scene *and* for answers spread
  across the full range. Those cannot both hold: a fixed mix gives a fixed
  answer. Settled as: hold constant everything that governs how much looking a
  scene takes (grid, interior, five loose shapes, exactly one
  fits-only-with-rotation shape), and vary only the fits-as-drawn versus
  too-big split. The answer is then `1 + (number that fit as drawn)` and
  ranges over 1 to 5, balanced. Answer 0 cannot occur, because the guaranteed
  rotation shape always fits.
* **The interior cannot be square.** In a square interior nothing fits only
  after being turned, so that whole category would be empty. Settled on an
  interior 3 cells wide and 2 tall, on a 14x10 grid.
* **Colour.** All loose shapes in a scene share one colour. A
  `per_shape_colors` switch exists but is off. Consequence, recorded honestly
  in the decoy report: the "count the shapes of one colour" decoy is then the
  same rule as "count every loose shape" by construction rather than by
  chance.
* **Reflections** are off by default, with a config switch and a test proving
  the switch changes the outcome.
* **Repeats.** A scene may use the same library shape more than once. Without
  that, scenes with answer 1 would always show the same four too-big shapes.

### Built

* `stimuli/fit.py` - the fit decision. Exhaustive over orientations and
  positions, no randomness. Written against a free-form set of interior cells
  rather than a rectangle, so it still holds if the interior is ever an odd
  shape.
* `stimuli/shapes.py` - the fixed library: 17 polyominoes of 1 to 4 cells and
  4 circles. Against a 3x2 interior these sort into 12 fits-as-drawn, 5
  fits-only-with-rotation, 4 too-big.
* `stimuli/config.py` - every setting in one place.
* `stimuli/scene.py` - the recipe, container geometry, and placement with a
  clear gap around every object. The answer is computed by the fit function
  and asserted to match what the recipe asked for.
* `stimuli/render.py` - drawing, including the contact sheet layout.
* `stimuli/generate.py`, `verify.py`, `contact_sheet.py`, `decoy_report.py` -
  the four commands.
* `stimuli/tests/` - 50 tests, all hand-checkable.

### Checked

* 50 tests pass.
* 30 generated scenes verified by the independent checker: 0 mismatches.
* The checker was fed a corrupted answer, a mislabelled shape, an overlapping
  shape and a lie about the interior size, and reported each one. An earlier
  attempt at this looked like a missed catch but turned out to be a sabotage
  that wrote back the values already there.
* Contact sheet of 30 scenes rendered and looked at.
* Decoy report over 30 scenes: the true answer differs from "count every loose
  shape" in 80% of scenes, from "count the solid squares" in 100%, from "count
  everything that is not a circle" in 73%; 63% of scenes tell the true rule
  apart from all of them at once.

### Open questions for next time

* The only polyominoes of 1 to 4 cells that do not fit a 3x2 interior are the
  four-in-a-row bar, so "too big" shapes are always a long bar or a large
  circle. Worth deciding whether that matters.
* In some scenes the container blue and the loose-shape slate blue sit close
  together and the container is slower to pick out. Easy to change in the
  palettes if wanted.

### Not started, on purpose

Puzzle 2 (unstable objects) and the staircase session runner. Specs still to
come.

---

## 2026-10-09 (later) - Reworked after reading the ISEF research plan

### Decided

* **Colour decoys are on.** `per_shape_colors` is now true, and the background
  varies per scene as well, which is what the research plan asks for when it
  says surface appearance should stay decorrelated from the rule. The decoy
  report's colour rule now measures something real (87% distinguishable)
  instead of being a restatement of "count every shape".
* **Bigger container: interior 4 wide by 3 tall, on a 20x14 grid.**
* **Shapes now go up to 6 cells.** This was not a free choice. Interior size
  and shape size are locked together: with a 4x3 interior and shapes of 4
  cells or fewer there are ZERO too-big shapes and only one that needs
  turning, so every answer would be 5 and the puzzle would be dead. Measured
  counts for 4x3 with 6-cell shapes: 193 fit as drawn, 81 need a turn, 38 too
  big.
* **Interior stays a plain rectangle,** chosen over a stepped interior. Noted
  as a known limit: fit therefore reduces to a bounding-box comparison, and a
  solver can get every answer right without reasoning about placement.
* **Staircase starts at K=4,** not the K=2 written in the research plan. The
  plan text needs changing before submission.
* **Shapes are picked by size first, then by shape.** The pool is lopsided
  (114 of the fitting shapes have 6 cells, 2 have one cell), so uniform
  picking produced scenes of five near-identical blobs.
* **Container and loose palettes are now disjoint hue families** (cool versus
  warm), and the container is drawn with a heavier outside edge. With 6-cell
  shapes a loose shape can itself be U-shaped, and in the first render pass
  several scenes had a container the same colour as the loose shapes, which
  made it genuinely ambiguous which object was the container.

### Correction to the research plan

The plan scores distractors that fit by "the amount of leftover empty space
after the tightest valid placement". In a rectangular interior that quantity
cannot vary: every valid placement leaves exactly (interior area - shape area)
cells empty, so there is no tightest placement and the measure only restates
the shape's cell count. Verified across 3x2, 4x2 and 4x3 interiors.
Implemented instead as **bounding-box slack**: spare width plus spare height
at the snuggest fitting orientation, which ranges 0 to 5 here and does vary.
The plan's second measure (fewest cells to remove before it would fit) works
as written and is implemented unchanged.

### Built

* `stimuli/difficulty.py` - bounding-box slack, near-miss distance with a
  connectedness requirement, the circle equivalent, and the band filter.
* `stimuli/shapes.py` rewritten to enumerate polyominoes programmatically
  rather than listing them by hand, since 6-cell shapes alone have 216
  orientations. Generated counts match the known fixed-polyomino sequence
  (1, 2, 6, 19, 63, 216), which is checked by a test.
* Difficulty measures are now recorded per shape in each answer key.

### Checked

* 75 tests pass (was 50).
* 60 scenes generated, verified independently: 0 mismatches.
* Reproducibility from a seed still holds.
* Contact sheets reviewed twice; the first revealed the container/loose colour
  ambiguity, which the palette split fixed.

### Still to do

* The web app (agreed: generator first, then the app).
* The staircase runner, reliability check and composite scoring.
* Puzzle 2, physical stability.

### Open items for Evan

* Research plan says K=2; the code will use K=4. Change the plan text.
* Research plan's first difficulty measure needs rewording as above.
