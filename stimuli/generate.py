"""Command line tool: write a set of scenes as PNGs plus JSON answer keys.

    python3 -m stimuli.generate --n 60 --seed 123 --out out/

Each scene produces two files with the same number:
    scene_0001.png   the picture the viewer sees (no answer on it)
    scene_0001.json  the answer key: answer, per-shape labels, seed, colours
"""

import argparse
import dataclasses
import json
import pathlib

from .config import DEFAULT, Config
from .render import render_scene
from .scene import balanced_answers, build_scene_with_retries


def build_set(n_scenes, cfg):
    """Build `n_scenes` scenes with answers spread evenly."""
    answers = balanced_answers(n_scenes, cfg)
    return [
        build_scene_with_retries(i + 1, answer, cfg)
        for i, answer in enumerate(answers)
    ]


def write_set(scenes, out_dir, cfg):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for scene in scenes:
        stem = f"scene_{scene.index:04d}"
        render_scene(scene, cfg).save(out_dir / f"{stem}.png")
        record = scene.to_dict()
        record["png"] = f"{stem}.png"
        (out_dir / f"{stem}.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        manifest.append(record)

    # One file holding the settings used, so a run can be reproduced exactly.
    (out_dir / "config_used.json").write_text(
        json.dumps(cfg.to_dict(), indent=2, default=list) + "\n"
    )
    # One line per scene, handy for spreadsheets and for the other tools.
    with open(out_dir / "answers.jsonl", "w") as handle:
        for record in manifest:
            handle.write(json.dumps(record) + "\n")
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=30,
                        help="how many scenes to make (default 30)")
    parser.add_argument("--seed", type=int, default=DEFAULT.seed,
                        help="seed; the same seed always gives the same set")
    parser.add_argument("--out", default="out",
                        help="folder to write into (default: out)")
    parser.add_argument("--allow-reflections", action="store_true",
                        help="count mirror images as fitting (off by default)")
    parser.add_argument("--no-rotation", action="store_true",
                        help="do not allow turning shapes when judging fit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    cfg = dataclasses.replace(
        DEFAULT,
        seed=args.seed,
        allow_reflections=args.allow_reflections,
        allow_rotation=not args.no_rotation,
    )
    scenes = build_set(args.n, cfg)
    manifest = write_set(scenes, args.out, cfg)

    counts = {}
    for record in manifest:
        counts[record["answer"]] = counts.get(record["answer"], 0) + 1
    print(f"wrote {len(manifest)} scenes to {args.out}/")
    print("answers used:",
          ", ".join(f"{a}: {counts[a]}" for a in sorted(counts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
