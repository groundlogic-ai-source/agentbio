"""One image holding many scenes, with the computed answer under each.

    python3 -m stimuli.contact_sheet --n 30 --out contact_sheet.png

This is the eyeball check. Count the fitting shapes yourself and compare with
the printed number. If they ever disagree, that is worth knowing about.
"""

import argparse
import dataclasses

from .config import DEFAULT
from .generate import build_set
from .render import render_contact_sheet
from .scene import build_scene_with_retries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=30,
                        help="how many scenes to show (default 30)")
    parser.add_argument("--seed", type=int, default=DEFAULT.seed,
                        help="seed; the same seed shows the same scenes")
    parser.add_argument("--out", default="contact_sheet.png",
                        help="file to write (default: contact_sheet.png)")
    parser.add_argument("--columns", type=int, default=6,
                        help="scenes per row (default 6)")
    parser.add_argument("--cell-pixels", type=int, default=16,
                        help="cell size; smaller keeps the sheet manageable")
    args = parser.parse_args(argv)

    cfg = dataclasses.replace(DEFAULT, seed=args.seed,
                              cell_pixels=args.cell_pixels)
    scenes = build_set(args.n, cfg)
    sheet = render_contact_sheet(scenes, cfg, columns=args.columns)
    sheet.save(args.out)
    print(f"wrote {args.out} with {len(scenes)} scenes "
          f"({sheet.size[0]}x{sheet.size[1]} pixels)")
    print("answers left to right, top to bottom:",
          " ".join(str(s.answer) for s in scenes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
