#!/usr/bin/env python3
"""Render review sheets so that a pet's visual review is exhaustive and mechanical.

Usage: python3 -I tools/review_sheets.py [--out DIR] [--baseline OLD_SPRITESHEET] [--refs DIR] PET_DIR

Writes into DIR (default PET_DIR/review/), labelling every cell like 'r5c3 failed':
  overview-light.png, overview-dark.png  every used cell at 1x over #F0F0F0 and over #1E202C
  motion-<row>-<state>.png               one per row at 2x: (a) the frames, (b) the frames with
                                         red where they differ from c0, (c) an onion skin of all
                                         frames, (d) the loop seam: last frame, c0 and their diff
  compare-baseline.png                   with --baseline: old over new for every changed row
  vs-reference.png                       with --refs: up to 6 reference images next to idle c0
                                         and failed c0
  checklist.md                           the review template, filled in by hand
Then LOOK at every image. The atlas layout comes from tools/make_previews.py and the row
comparison from tools/validate_pet.py.
"""
import argparse
import functools
import importlib.util
import io
import pathlib
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont

# -I drops the script directory from sys.path, so load the sibling module by path. It is
# registered first because dataclasses look their module up in sys.modules (Python 3.14).
_spec = importlib.util.spec_from_file_location(
    "validate_pet", pathlib.Path(__file__).resolve().with_name("validate_pet.py"))
validate_pet = importlib.util.module_from_spec(_spec)
sys.modules["validate_pet"] = validate_pet
_spec.loader.exec_module(validate_pet)
make_previews = validate_pet.make_previews

CELL_W, CELL_H = make_previews.CELL_W, make_previews.CELL_H
ANIMATIONS, ATLAS_SIZE = make_previews.ANIMATIONS, make_previews.ATLAS_SIZE

LIGHT, DARK = (240, 240, 240, 255), (30, 32, 44, 255)  # #F0F0F0, #1E202C
BOARD, INK = (72, 72, 80), (255, 255, 255)  # around the cells, so cell edges stay visible
RED = (255, 0, 0, 255)
RED_OPACITY = 0.6
THRESHOLD = 16  # per-channel difference ignored as antialiasing noise
SCALE = 2
REF_H, MAX_REFS = 2 * CELL_H, 6
# Image extensions to pick up, and the only decoders allowed: wikis often serve WebP under .png/.jpg names.
REF_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
REF_FORMATS = ["PNG", "JPEG", "WEBP"]
FONT = ImageFont.load_default(size=16)
LINE_H, GAP = 22, 8
CRITERIA = ["likeness", "hands/limbs", "mouth/teeth", "eyes", "props",
            "markings & motion (bands b-c)", "dark background", "loop seam"]


def load_sheet(pet_dir):
    """The pet's atlas as RGBA; exits when pet.json names no usable spritesheet."""
    problems, path = validate_pet.check_pet_json(pet_dir)
    if path is None:
        raise SystemExit(f"{pet_dir}: {'; '.join(problems) or 'no usable spritesheetPath'}")
    with Image.open(path) as image:
        sheet = image.convert("RGBA")
    if sheet.size != ATLAS_SIZE:
        raise SystemExit(f"{pet_dir}: unexpected atlas size {sheet.size}")
    return sheet


def frames(sheet, row):
    """The used cells of one animation row, as RGBA images."""
    return [sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
            for col in range(len(ANIMATIONS[row][1]))]


def label(row, col, suffix=""):
    return f"r{row}c{col} {ANIMATIONS[row][0]}{suffix}"


def visible(cell):
    """Copy with the colour of fully transparent pixels zeroed; that colour is never shown."""
    opaque = cell.getchannel("A").point(lambda a: 255 if a else 0)
    return Image.composite(cell, Image.new("RGBA", cell.size), opaque)


def diff_mask(a, b):
    """Mask of the pixels where two cells differ by more than THRESHOLD in any channel."""
    diff = ImageChops.difference(visible(a), visible(b))
    return functools.reduce(ImageChops.lighter, diff.split()).point(
        lambda v: 255 if v > THRESHOLD else 0)


def tile(cell, background=LIGHT, mask=None, scale=SCALE):
    """A cell over background, painted semi-transparent red under mask, scaled with NEAREST."""
    flat = Image.new("RGBA", cell.size, background)
    flat.alpha_composite(cell)
    if mask is not None:
        red = Image.blend(flat, Image.new("RGBA", cell.size, RED), RED_OPACITY)
        flat = Image.composite(red, flat, mask)
    return flat.convert("RGB").resize((cell.width * scale, cell.height * scale),
                                      Image.Resampling.NEAREST)


def onion(cells):
    """Every frame at equal alpha in one cell: what stays put is sharp, what moves is ghosted."""
    layers = [tile(cell, scale=1) for cell in cells]
    mean = layers[0]
    for count, layer in enumerate(layers[1:], start=2):
        mean = Image.blend(mean, layer, 1 / count)  # running mean keeps the weights equal
    return mean.resize((mean.width * SCALE, mean.height * SCALE), Image.Resampling.NEAREST)


def board(title, bands):
    """Stack bands of labelled tiles on a grey board.

    bands is [(band title or None, [(label, image)])]; every image gets its label above it.
    """
    width = GAP + max(sum(image.width + GAP for _, image in tiles) for _, tiles in bands)
    height = GAP + LINE_H + sum((LINE_H if heading else 0) + LINE_H + GAP
                                + max(image.height for _, image in tiles) for heading, tiles in bands)
    canvas = Image.new("RGB", (width, height), BOARD)
    draw = ImageDraw.Draw(canvas)
    draw.text((GAP, GAP), title, fill=INK, font=FONT)
    y = GAP + LINE_H
    for heading, tiles in bands:
        if heading:
            draw.text((GAP, y), heading, fill=INK, font=FONT)
            y += LINE_H
        x = GAP
        for text, image in tiles:
            draw.text((x, y), text, fill=INK, font=FONT)
            canvas.paste(image, (x, y + LINE_H))
            x += image.width + GAP
        y += LINE_H + GAP + max(image.height for _, image in tiles)
    return canvas


def overview(sheet, background):
    """Every used cell at 1x over background, at its atlas position; unused cells stay blank."""
    hex_colour = "#{:02X}{:02X}{:02X}".format(*background[:3])
    bands = [(None, [(label(row, col), tile(cell, background, scale=1))
                     for col, cell in enumerate(frames(sheet, row))])
             for row in range(len(ANIMATIONS))]
    return board(f"overview over {hex_colour}, 1x", bands)


def motion_bands(sheet, row):
    """The four bands of a motion sheet: [(title, [(label, image)])], all at 2x."""
    cells = frames(sheet, row)
    last = len(cells) - 1
    return [
        ("(a) frames", [(label(row, col), tile(cell)) for col, cell in enumerate(cells)]),
        (f"(b) red = differs from c0 (any channel > {THRESHOLD})",
         [(label(row, col), tile(cell, mask=diff_mask(cell, cells[0])))
          for col, cell in enumerate(cells)]),
        ("(c) onion skin: every frame at equal alpha; parts that should move but stay sharp are stuck",
         [(f"r{row}c0-c{last} {ANIMATIONS[row][0]}", onion(cells))]),
        ("(d) loop seam: last frame, c0, red = last differs from c0",
         [(label(row, last), tile(cells[last])), (label(row, 0), tile(cells[0])),
          (label(row, last, " vs c0"), tile(cells[last], mask=diff_mask(cells[last], cells[0])))]),
    ]


def compare_bands(old, new):
    """(changed state names, bands with the old frames above the new ones for each changed row)."""
    changed = validate_pet.changed_rows(old, new)
    bands = []
    for row, (state, _) in enumerate(ANIMATIONS):
        if state in changed:
            bands.append((f"r{row} {state}: baseline", [(label(row, col, " old"), tile(cell))
                                                         for col, cell in enumerate(frames(old, row))]))
            bands.append((f"r{row} {state}: new", [(label(row, col, " new"), tile(cell))
                                                    for col, cell in enumerate(frames(new, row))]))
    return changed, bands


def reference_images(folder):
    """Up to MAX_REFS (name, image REF_H high) pairs; other files and unreadable images are skipped."""
    found = []
    for path in sorted(folder.iterdir()):
        if len(found) == MAX_REFS:
            break
        if path.suffix.lower() not in REF_SUFFIXES or not path.is_file():
            continue
        try:  # decode only common still-image formats, whatever the extension says
            with Image.open(path, formats=REF_FORMATS) as image:
                ref = image.convert("RGBA")
        except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError) as error:
            print(f"skipped unreadable reference {path.name}: {error}")
            continue
        width = max(1, round(ref.width * REF_H / ref.height))
        flat = Image.new("RGBA", ref.size, LIGHT)
        flat.alpha_composite(ref)
        name = path.name.encode("ascii", "replace").decode()[:max(4, width // 9)]
        found.append((name, flat.convert("RGB").resize((width, REF_H), Image.Resampling.LANCZOS)))
    return found


def checklist(pet_dir, names):
    """The review template: every image to look at, then one table row per animation state."""
    lines = [f"# Review checklist: {pet_dir}", "",
             "Generated by tools/review_sheets.py. Open and LOOK at every image, then tick it.",
             "Fill every blank with OK or the concrete defect and its cell, e.g. `r5c3 hand has 6 fingers`.",
             "Motion bands: (a) frames, (b) red = differs from c0, (c) onion skin, (d) loop seam.",
             "", "## Images", ""]
    lines += [f"- [ ] {name}" for name in names]
    lines += ["", "## Pass 1: whole atlas", ""]
    lines += [f"- [ ] {name}: " for name in names if name.startswith("overview-")]
    if "compare-baseline.png" in names:
        lines.append("- [ ] compare-baseline.png: only the intended rows changed, as intended: ")
    if "vs-reference.png" in names:
        lines += ["", "## Reference", "", "- [ ] vs-reference.png: likeness against the references: "]
    lines += ["", "## Pass 2: every state", "",
              "| state | " + " | ".join(CRITERIA) + " |", "|---" * (len(CRITERIA) + 1) + "|"]
    lines += [f"| r{row} {state} |" + "  |" * len(CRITERIA) for row, (state, _) in enumerate(ANIMATIONS)]
    return "\n".join(lines) + "\n"


def review(pet_dir, out, baseline=None, refs=None):
    """Write every review image and checklist.md into out; print and return their paths."""
    sheet = load_sheet(pathlib.Path(pet_dir))
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def save(name, image, note=""):
        image.save(out / name)
        written.append(out / name)
        print(f"{out / name}  {note}" if note else out / name)

    save("overview-light.png", overview(sheet, LIGHT))
    save("overview-dark.png", overview(sheet, DARK))
    for row, (state, durations) in enumerate(ANIMATIONS):
        title = f"motion r{row} {state}: {len(durations)} frames, {SCALE}x"
        save(f"motion-{row}-{state}.png", board(title, motion_bands(sheet, row)))
    if baseline is not None:
        changed, bands = compare_bands(baseline, sheet)
        if bands:
            save("compare-baseline.png", board("baseline (old) above new, 2x", bands),
                 "changed rows: " + ", ".join(changed))
        else:
            print("skipped compare-baseline.png: no rows changed")
    if refs is not None:
        found = reference_images(refs)
        if found:
            pet = [(label(0, 0), tile(frames(sheet, 0)[0])), (label(5, 0), tile(frames(sheet, 5)[0]))]
            save("vs-reference.png", board(f"pet at 2x vs references {REF_H} px high", [(None, pet + found)]),
                 f"{len(found)} reference image(s)")
        else:
            print(f"skipped vs-reference.png: no readable images in {refs}")
    path = out / "checklist.md"
    path.write_text(checklist(pet_dir, [p.name for p in written]), encoding="utf-8")
    written.append(path)
    print(path)
    return written


def main(argv):
    parser = argparse.ArgumentParser(description="Render review sheets for a Codex-format pet.")
    parser.add_argument("--out", metavar="DIR", help="output directory (default: PET_DIR/review)")
    parser.add_argument("--baseline", metavar="OLD_SPRITESHEET",
                        help="also show old above new frames for every row that changed")
    parser.add_argument("--refs", metavar="DIR", help="folder of reference images (.png .jpg .jpeg .webp)")
    parser.add_argument("pet", metavar="PET_DIR")
    args = parser.parse_args(argv)
    baseline = None
    if args.baseline:
        try:  # read the bytes first so process substitution (/dev/fd/N) works
            with Image.open(io.BytesIO(pathlib.Path(args.baseline).read_bytes())) as image:
                baseline = image.convert("RGBA")
        except (OSError, ValueError) as error:
            parser.error(f"cannot read {args.baseline}: {error}")
    refs = pathlib.Path(args.refs) if args.refs else None
    if refs is not None and not refs.is_dir():
        parser.error(f"--refs {args.refs} is not a directory")
    pet_dir = pathlib.Path(args.pet)
    review(pet_dir, pathlib.Path(args.out) if args.out else pet_dir / "review", baseline, refs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(None))
