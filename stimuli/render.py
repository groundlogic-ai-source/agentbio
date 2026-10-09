"""Turning a scene into a picture.

Drawing only. This file never decides anything about the puzzle; it just paints
what scene.py already worked out. Polyominoes are drawn as filled grid cells.
Circles are drawn as real circles filling the square of cells they occupy, so
they read as circles while still being measured as that square.
"""

from PIL import Image, ImageDraw, ImageFont

from .config import DEFAULT


def _font(size):
    """A readable font, falling back to the built-in one if none is installed."""
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:        # very old Pillow
        return ImageFont.load_default()


def _draw_grid(draw, cfg, width_px, height_px):
    """The faint squared-paper lines, so every cell is visible."""
    px = cfg.cell_pixels
    for c in range(cfg.grid_width + 1):
        x = c * px
        draw.line([(x, 0), (x, height_px)], fill=cfg.grid_line_color, width=1)
    for r in range(cfg.grid_height + 1):
        y = r * px
        draw.line([(0, y), (width_px, y)], fill=cfg.grid_line_color, width=1)


def _draw_cells(draw, cells, color, cfg):
    """Fill in a set of grid cells, with a thin outline around each."""
    px = cfg.cell_pixels
    for c, r in sorted(cells):
        draw.rectangle(
            [c * px, r * px, c * px + px, r * px + px],
            fill=color, outline=cfg.outline_color, width=1,
        )


def _draw_circle(draw, cells, color, cfg):
    """Draw a circle filling the square of cells it occupies."""
    px = cfg.cell_pixels
    cols = [c for c, _ in cells]
    rows = [r for _, r in cells]
    x0, y0 = min(cols) * px, min(rows) * px
    x1, y1 = (max(cols) + 1) * px, (max(rows) + 1) * px
    draw.ellipse([x0, y0, x1 - 1, y1 - 1],
                 fill=color, outline=cfg.outline_color, width=1)


def render_scene(scene, cfg=None):
    """Return a Pillow image of one scene. No answer is printed on it."""
    cfg = cfg or scene.config or DEFAULT
    px = cfg.cell_pixels
    width_px = cfg.grid_width * px
    height_px = cfg.grid_height * px

    image = Image.new("RGB", (width_px + 1, height_px + 1),
                      cfg.background_color)
    draw = ImageDraw.Draw(image)

    _draw_grid(draw, cfg, width_px, height_px)
    _draw_cells(draw, scene.container.cells, scene.container_color, cfg)

    for placed in scene.placed:
        if placed.kind == "circle":
            _draw_circle(draw, placed.cells, placed.color, cfg)
        else:
            _draw_cells(draw, placed.cells, placed.color, cfg)

    return image


def render_contact_sheet(scenes, cfg=None, columns=6, label_height=34,
                         pad=10):
    """One big image of many scenes, with each computed answer printed below.

    This is the eyeball check: you can count the fitting shapes yourself and
    compare with the number the code printed.
    """
    cfg = cfg or (scenes[0].config if scenes else DEFAULT)
    tiles = [render_scene(s, cfg) for s in scenes]
    tile_w, tile_h = tiles[0].size

    rows = (len(tiles) + columns - 1) // columns
    cell_w = tile_w + pad
    cell_h = tile_h + label_height + pad
    sheet = Image.new("RGB",
                      (columns * cell_w + pad, rows * cell_h + pad),
                      "#FFFFFF")
    draw = ImageDraw.Draw(sheet)
    font = _font(max(13, cfg.cell_pixels // 2))

    for i, (tile, scene) in enumerate(zip(tiles, scenes)):
        col, row = i % columns, i // columns
        x = pad + col * cell_w
        y = pad + row * cell_h
        sheet.paste(tile, (x, y))
        caption = f"#{scene.index}   answer = {scene.answer}"
        draw.text((x + 2, y + tile_h + 6), caption, fill="#1A1A1A", font=font)

    return sheet
