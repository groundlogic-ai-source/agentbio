"""All the knobs for the container-fit stimulus generator.

Everything that controls what a scene looks like lives here, so you can change
the puzzle without touching any of the logic. Nothing in this file does work;
it only holds settings.
"""

from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    # ---- The grid the scene is drawn on (in cells) ----
    grid_width: int = 20
    grid_height: int = 14

    # ---- The container's interior (the empty space inside the U) ----
    # This is the space a loose shape has to fit into. It must NOT be square,
    # otherwise "fits only when rotated" can never happen: in a square space,
    # anything that fits turned also fits unturned.
    interior_width: int = 4
    interior_height: int = 3
    wall_thickness: int = 1   # how many cells thick each arm and the base are

    # ---- The pool of shapes to draw from ----
    # Interior size and shape size are locked together. Enlarging the
    # interior without enlarging the shapes breaks the puzzle: in a 4x3
    # interior every shape of 4 cells or fewer fits, so there would be no
    # too-big shapes at all and almost nothing would need turning.
    max_shape_cells: int = 6
    max_circle_width: int = 5

    # ---- The difficulty band (see difficulty.py) ----
    # Shapes that fit are scored by bounding-box slack: spare width plus spare
    # height at the snuggest orientation. 0 is an exactly-filling fit, and the
    # largest possible here is (4-1) + (3-1) = 5 for a single cell.
    slack_band: tuple = (0, 3)
    # Shapes that do not fit are scored by how many cells would have to come
    # off before they would. 1 is a near miss; larger is obviously too big.
    near_miss_band: tuple = (1, 2)

    # The pool is dominated by the largest shapes (there are far more 6-cell
    # pieces than 3-cell ones), so shapes are picked by first choosing a size
    # and then a shape of that size. Without this, almost every loose shape in
    # every scene would be a 6-cell blob.
    stratify_by_cell_count: bool = True
    # How often a loose shape is a circle rather than a polyomino.
    circle_probability: float = 0.2

    # ---- How the fit question is decided ----
    allow_rotation: bool = True        # 90-degree turns allowed when judging fit
    allow_reflections: bool = False    # mirror images allowed (off, per the brief)

    # ---- Scene composition ----
    n_loose_shapes: int = 5
    # Every scene contains exactly this many shapes that fit ONLY after being
    # rotated. The rest of the shapes split between "fits as drawn" and
    # "too big", and that split is what makes the answer vary.
    rotation_shapes_per_scene: int = 1
    # The possible answers, spread evenly across the generated set.
    # With 5 shapes and 1 guaranteed rotation-shape, the answer is
    # 1 + (number of fits-as-drawn shapes), so it ranges 1..5.
    answer_values: tuple = (1, 2, 3, 4, 5)

    # Minimum empty cells between a loose shape and anything else (container or
    # another loose shape), so shapes read as separate objects.
    min_gap: int = 1

    # ---- Colour ----
    # Colours are picked independently of the answer, so colour is never a clue.
    # The two palettes are kept in separate hue families, cool for the
    # container and warm for the loose shapes. Colour still varies freely and
    # is still independent of the answer, but the container can never be
    # mistaken for a loose shape - which matters now that a 6-cell loose shape
    # can itself be U-shaped.
    container_palette: tuple = (
        "#2F5C8F",  # deep blue
        "#6A4A8C",  # deep purple
        "#27694F",  # deep green
    )
    loose_palette: tuple = (
        "#C25E4A",  # terracotta
        "#D4A23C",  # amber
        "#B5567A",  # rose
    )
    # False: every loose shape in a scene shares one colour (your choice).
    # True:  each loose shape gets its own colour from the palette, which makes
    #        the "number of shapes of one colour" decoy rule meaningful.
    per_shape_colors: bool = True

    # ---- Drawing ----
    # Backgrounds vary per scene too, so surface appearance stays
    # decorrelated from the rule, as the research plan requires.
    background_palette: tuple = (
        "#FFFFFF",  # white
        "#FAF7F2",  # warm off-white
        "#F4F6F8",  # cool off-white
        "#F7F4FA",  # faint lilac
    )

    cell_pixels: int = 22
    grid_line_color: str = "#D8D8D8"
    background_color: str = "#FFFFFF"
    outline_color: str = "#2B2B2B"   # thin outline around filled cells
    # The container is drawn with a heavier outline, a second cue that it is
    # the container and not one more loose shape.
    container_outline_width: int = 3

    # ---- Reproducibility ----
    seed: int = 20261009

    def to_dict(self):
        return asdict(self)


DEFAULT = Config()
