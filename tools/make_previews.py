#!/usr/bin/env python3
"""Render preview GIFs and a contact sheet from a pet's committed spritesheet.

Usage: python3 -I tools/make_previews.py pets/<character>/<variant> [...]

Writes <pet>/preview/<state>.gif and <pet>/preview/sheet.png. Frame timing mirrors the
Codex pet tables that Orca ships (codex-pet-sprite-defaults.js).
"""
import json
import pathlib
import sys

from PIL import Image

CELL_W, CELL_H = 192, 208
BACKGROUND = (242, 242, 242, 255)


def app_state(frames, frame_ms, final_ms):
    return [final_ms if i == frames - 1 else frame_ms for i in range(frames)]


# (state, frame durations in ms); the row index is the position in this list.
ANIMATIONS = [
    ("idle", [1680, 660, 660, 840, 840, 1920]),
    ("running-right", app_state(8, 120, 220)),
    ("running-left", app_state(8, 120, 220)),
    ("waving", app_state(4, 140, 280)),
    ("jumping", app_state(5, 140, 280)),
    ("failed", app_state(8, 140, 240)),
    ("waiting", app_state(6, 150, 260)),
    ("running", app_state(6, 120, 220)),
    ("review", app_state(6, 150, 280)),
]


def frames_for(sheet, row, count):
    for col in range(count):
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        tile = Image.new("RGBA", (CELL_W, CELL_H), BACKGROUND)
        tile.alpha_composite(cell)
        yield tile.convert("RGB")


def render(pet_dir):
    meta = json.loads((pet_dir / "pet.json").read_text(encoding="utf-8"))
    sheet = Image.open(pet_dir / meta.get("spritesheetPath", "spritesheet.webp")).convert("RGBA")
    if sheet.size != (8 * CELL_W, 9 * CELL_H):
        raise SystemExit(f"{pet_dir}: unexpected atlas size {sheet.size}")
    out = pet_dir / "preview"
    out.mkdir(exist_ok=True)
    contact = Image.new("RGB", (8 * CELL_W, 9 * CELL_H), BACKGROUND[:3])
    for row, (state, durations) in enumerate(ANIMATIONS):
        frames = list(frames_for(sheet, row, len(durations)))
        frames[0].save(out / f"{state}.gif", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, disposal=2, optimize=False)
        for col, frame in enumerate(frames):
            contact.paste(frame, (col * CELL_W, row * CELL_H))
    contact.save(out / "sheet.png", optimize=True)
    print(f"{pet_dir}: {len(ANIMATIONS)} gifs + sheet.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        render(pathlib.Path(arg))
