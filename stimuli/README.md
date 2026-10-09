# Puzzle 1: container-fit stimulus generator

Makes grid scenes for the example-efficiency study. Each scene shows a U-shaped
container and five loose shapes. The hidden rule is:

> **how many of the loose shapes would fit inside the container?**

Each shape is judged on its own against the empty interior. They are never
packed in together. Turning a shape 90 degrees is allowed; mirroring it is not.
A circle counts as the square it sits in, so a circle three cells wide needs a
3x3 space.

Nothing is decided by eye. Every answer comes from an exhaustive function that
tries each allowed orientation in every possible position, and every candidate
shape is admitted or rejected by a computed difficulty measure rather than by
judgement.

## What you need

Python 3 and Pillow. Nothing else. No install step: run the commands from the
repository root.

## The four commands

Make a set of scenes (PNG plus a JSON answer key for each):

    python3 -m stimuli.generate --n 60 --seed 123 --out out/

Re-check every answer with a separately written checker:

    python3 -m stimuli.verify --dir out/

Make one big sheet of 30 scenes with the answers printed underneath, to eyeball:

    python3 -m stimuli.contact_sheet --n 30 --out contact_sheet.png

Report how often a simpler rule would give the same numbers:

    python3 -m stimuli.decoy_report --dir out/ --csv decoys.csv

Run the tests:

    python3 -m unittest discover -s stimuli/tests -t .

## How a scene is built

Scenes are not drawn at random. Each one follows a recipe so that scenes are
equally hard to look at even though their answers differ:

* same grid and same container interior in every scene (4 wide, 3 tall)
* same number of loose shapes in every scene (five)
* exactly one shape that fits **only after being turned**, in every scene
* the remaining four split between "fits as drawn" and "too big", and that
  split is the only thing that moves the answer
* answers spread evenly over 1, 2, 3, 4, 5
* colours and background chosen with no reference to the answer, so surface
  appearance stays decorrelated from the rule
* every shape inside a pre-set difficulty band
* a seed, so any scene can be rebuilt exactly

Because every scene carries one shape that fits once turned, and that shape
always fits, the answer is never 0. The answer is `1 + (number of shapes that
fit as drawn)`.

## Why the interior is 4 wide and 3 tall, not square

"Fits only when rotated" cannot exist in a square interior: anything that fits
turned also fits unturned. The interior has to be longer in one direction for
that category to mean anything.

## Why shapes go up to 6 cells

Interior size and shape size are locked together, and getting this wrong
silently breaks the puzzle. Counts of usable shapes:

| interior | largest shape | fits as drawn | needs a turn | too big |
| --- | --- | --- | --- | --- |
| 3x2 | 4 cells | 17 | 9 | 2 |
| 4x3 | 4 cells | 27 | 1 | **0** |
| 4x2 | 5 cells | 36 | 28 | 27 |
| 4x3 | 6 cells | 193 | 81 | 38 |

Row two is the trap: enlarging the interior without enlarging the shapes leaves
nothing that is too big and almost nothing that needs turning, so every answer
would be 5.

## The difficulty band

The research plan asks for distractors to be chosen by a computed measure. Two
measures, one per side of the fit question (both in `difficulty.py`):

* A shape that **fits** is scored by **bounding-box slack**: spare width plus
  spare height at its snuggest orientation. 0 means it exactly spans the
  interior, a deceptively tight fit. 5 is the loosest possible here.
* A shape that **does not fit** is scored by its **near-miss distance**: the
  fewest cells that would have to come off before some placement worked, with
  the remainder staying in one connected piece. 1 is a near miss.

`slack_band` and `near_miss_band` in the config set which shapes are usable.
The defaults, `(0, 3)` and `(1, 2)`, drop the trivially small shapes and the
obviously oversized ones.

**One correction to the research plan.** The plan describes the first measure
as the leftover empty space after the tightest valid placement. In a
rectangular interior that cannot vary: every valid placement of a given shape
leaves exactly `interior area - shape area` cells empty, so there is no
tightest placement and the measure would only restate the shape's cell count.
Bounding-box slack is what that measure was reaching for, and it does vary.

## Why shapes are picked by size first

The pool is lopsided: of the shapes that fit as drawn, 114 have 6 cells and
only 2 have one. Picking uniformly would fill every scene with the largest
pieces. Instead a size is chosen first, then a shape of that size, which keeps
scenes visually varied. Circles are drawn at a separate set rate
(`circle_probability`) for the same reason.

## Why the container is a different colour family

Loose shapes can have up to 6 cells, which means a loose shape can itself be
U-shaped. So the container palette is cool, the loose palette is warm, the two
never overlap, and the container is drawn with a heavier outside edge. Colour
still varies freely and is still independent of the answer; it just cannot make
the container ambiguous.

## The files

| file | what it does |
| --- | --- |
| `config.py` | every setting, in one place |
| `fit.py` | decides whether a shape fits; the heart of the puzzle |
| `shapes.py` | generates the shape library and applies the difficulty band |
| `difficulty.py` | the two difficulty measures |
| `scene.py` | builds a scene from the recipe and computes its answer |
| `render.py` | draws a scene, and draws contact sheets |
| `generate.py` | the generate command |
| `verify.py` | a second, independent implementation of the fit check |
| `contact_sheet.py` | the contact sheet command |
| `decoy_report.py` | the decoy report; reports only, never filters |
| `tests/` | unit tests, all hand-checkable |

## Why the verifier is written twice

`verify.py` deliberately does not reuse `fit.py`. It reads only the recorded
cell lists, measures the container's interior from the container's own filled
cells instead of trusting what the generator wrote down, stores shapes as grids
of rows rather than sets of coordinates, and rotates them by transposing those
rows. Two implementations that disagree point at a bug; two that agree are
reasonable evidence the answer keys are right.

## Known limits

* Because the interior is a plain rectangle, "does it fit" reduces to comparing
  the shape's bounding box against 4 by 3. A solver who notices that never has
  to reason about placement. Giving the container arms of different heights
  would make the interior stepped and placement would start to matter, at the
  cost of a more complicated container.
* All 38 too-big shapes are within 2 cells of fitting, so the near-miss band
  cannot currently be made tighter than it already is.
