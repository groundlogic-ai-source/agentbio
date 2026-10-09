"""All the knobs for the container-fit stimulus generator.

Everything that controls what a scene looks like lives here, so you can change
the puzzle without touching any of the logic. Nothing in this file does work;
it only holds settings.
"""

from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    # ---- The grid the scene is drawn on (in cells) ----
    grid_width: int = 14
    grid_height: int = 10

    # ---- The container's interior (the empty space inside the U) ----
    # This is the space a loose shape has to fit into. It must NOT be square,
    # otherwise "fits only when rotated" can never happen: in a square space,
    # anything that fits turned also fits unturned.
    interior_width: int = 3
    interior_height: int = 2
    wall_thickness: int = 1   # how many cells thick each arm and the base are

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
    container_palette: tuple = (
        "#3B6EA5",  # blue
        "#7A5C9E",  # purple
        "#2E7D62",  # green
    )
    loose_palette: tuple = (
        "#C25E4A",  # terracotta
        "#D4A23C",  # amber
        "#5A7D9A",  # slate
    )
    # False: every loose shape in a scene shares one colour (your choice).
    # True:  each loose shape gets its own colour from the palette, which makes
    #        the "number of shapes of one colour" decoy rule meaningful.
    per_shape_colors: bool = False

    # ---- Drawing ----
    cell_pixels: int = 28
    grid_line_color: str = "#D8D8D8"
    background_color: str = "#FFFFFF"
    outline_color: str = "#2B2B2B"   # thin outline around filled cells

    # ---- Reproducibility ----
    seed: int = 20251009

    def to_dict(self):
        return asdict(self)


DEFAULT = Config()
