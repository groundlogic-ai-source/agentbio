# Puzzle 1: container-fit stimulus generator

Makes grid scenes for the example-efficiency study. Each scene shows a U-shaped
container and five loose shapes. The hidden rule is:

> **how many of the loose shapes would fit inside the container?**

Each shape is judged on its own against the empty interior. They are never
packed in together. Turning a shape 90 degrees is allowed; mirroring it is not.
A circle counts as the square it sits in, so a circle three cells wide needs a
3x3 space.

Nothing is decided by eye. Every answer comes from an exhaustive function that
tries each allowed orientation in every possible position.

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

* same grid and same container interior in every scene
* same number of loose shapes in every scene (five)
* exactly one shape that fits **only after being turned**, in every scene
* the remaining four split between "fits as drawn" and "too big", and that
  split is the only thing that moves the answer
* answers spread evenly over 1, 2, 3, 4, 5
* colours chosen with no reference to the answer
* a seed, so any scene can be rebuilt exactly

Because every scene carries one shape that fits once turned, and that shape
always fits, the answer is never 0. The answer is `1 + (number of shapes that
fit as drawn)`.

## Why the interior is 3 wide and 2 tall, not square

"Fits only when rotated" cannot exist in a square interior: anything that fits
turned also fits unturned. The interior has to be longer in one direction for
that category to mean anything.

## The files

| file | what it does |
| --- | --- |
| `config.py` | every setting, in one place |
| `fit.py` | decides whether a shape fits; the heart of the puzzle |
| `shapes.py` | the fixed library of loose shapes |
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

* With a 3x2 interior and shapes of 1 to 4 cells, the only polyominoes that
  cannot fit are the four-in-a-row bar. So the "too big" shapes are always a
  long bar or a large circle. If more variety there matters, the options are a
  smaller interior or allowing 5-cell shapes.
* `per_shape_colors` is off, so every loose shape in a scene shares one colour.
  That makes the "count the shapes of one colour" decoy rule identical to
  "count every loose shape" by construction. The decoy report says so.
