#!/usr/bin/env python3
"""Validate Codex-format desktop pets: pet.json, atlas format, cell content and previews.

Usage: python3 -I tools/validate_pet.py [--compare OLD_SPRITESHEET] pets/<character>/<variant> [...]

Prints one PASS/FAIL/SKIP line per check per pet, then a summary, and exits 1 if any check
fails. INFO lines never fail: identical consecutive frames (usually unintended), and with
--compare the animation rows that differ from OLD_SPRITESHEET in any channel. The atlas
layout, frame counts, background and preview timing come from tools/make_previews.py.
"""
import argparse
import importlib.util
import io
import json
import pathlib
import re

from PIL import Image, ImageChops, ImageSequence

# -I drops the script directory from sys.path, so load the sibling module by path.
_spec = importlib.util.spec_from_file_location(
    "make_previews", pathlib.Path(__file__).resolve().with_name("make_previews.py"))
make_previews = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_previews)

CELL_W, CELL_H = make_previews.CELL_W, make_previews.CELL_H
ANIMATIONS, ATLAS_SIZE = make_previews.ANIMATIONS, make_previews.ATLAS_SIZE
COLUMNS = ATLAS_SIZE[0] // CELL_W

KEYS = {"id", "displayName", "description", "spritesheetPath"}
ID_PATTERN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
TEXT_LIMITS = {"displayName": 60, "description": 280}  # Petdex trims longer values
IMAGE_CHECKS = ["spritesheet format", "cell occupancy", "cell margins", "previews"]


def differs(a, b):
    """True if two same-mode images differ in any channel.

    getbbox() on an RGBA difference defaults to alpha_only=True and would miss colour-only
    changes in opaque areas, so every channel is checked explicitly.
    """
    return a.size != b.size or ImageChops.difference(a, b).getbbox(alpha_only=False) is not None


def sheet_target(pet_dir, path):
    """The file named by spritesheetPath, or None unless it is a .webp/.png inside pet_dir."""
    if not isinstance(path, str) or not path:
        return None
    target = (pet_dir / path).resolve()
    if target.is_relative_to(pet_dir.resolve()) and target.suffix in (".webp", ".png") and target.is_file():
        return target
    return None


def check_pet_json(pet_dir):
    """Return (problems, spritesheet path or None)."""
    try:
        meta = json.loads((pet_dir / "pet.json").read_bytes().decode("utf-8"))
    except (OSError, ValueError) as error:
        return [f"cannot read pet.json as UTF-8 JSON: {error}"], None
    if not isinstance(meta, dict):
        return ["pet.json is not a JSON object"], None
    problems = []
    if KEYS - meta.keys():
        problems.append("missing keys: " + ", ".join(sorted(KEYS - meta.keys())))
    if meta.keys() - KEYS:
        problems.append("unexpected keys: " + ", ".join(sorted(meta.keys() - KEYS)))
    if "id" in meta and not (isinstance(meta["id"], str) and ID_PATTERN.fullmatch(meta["id"])):
        problems.append(f"id {meta['id']!r} is not a lowercase hyphenated slug")
    for key, limit in TEXT_LIMITS.items():
        value = meta.get(key)
        if key in meta and not (isinstance(value, str) and value.strip() and len(value) <= limit):
            problems.append(f"{key} must be 1-{limit} characters")
    sheet_path = sheet_target(pet_dir, meta.get("spritesheetPath"))
    if "spritesheetPath" in meta and sheet_path is None:
        problems.append(f"spritesheetPath {meta['spritesheetPath']!r} is not a .webp or .png "
                        "file inside the pet dir")
    return problems, sheet_path


def webp_codec(data):
    """Fourcc of the first still-image chunk in a WebP file ('VP8L' is lossless), or None."""
    pos = 12
    while pos + 8 <= len(data):
        fourcc = data[pos:pos + 4]
        if fourcc in (b"VP8L", b"VP8 "):
            return fourcc.decode()
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        pos += 8 + size + (size & 1)
    return None


def check_format(path):
    """Return (problems, RGBA atlas or None when it cannot be used for the cell checks)."""
    data = path.read_bytes()
    problems = []
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if webp_codec(data) != "VP8L":
            problems.append("lossy or animated WebP (no VP8L image chunk); save it with lossless=True")
    elif not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ["not a WebP or PNG file"], None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, ValueError) as error:
        return problems + [f"cannot decode: {error}"], None
    if image.mode != "RGBA":
        problems.append(f"mode {image.mode}, expected RGBA")
    if image.size != ATLAS_SIZE:
        problems.append("size {}x{}, expected {}x{}".format(*image.size, *ATLAS_SIZE))
        return problems, None
    return problems, image.convert("RGBA")


def cells(sheet):
    """Yield (label, used, alpha channel) for every cell; labels look like 'idle[0]'."""
    alpha = sheet.getchannel("A")
    for row, (state, durations) in enumerate(ANIMATIONS):
        for col in range(COLUMNS):
            box = (col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H)
            yield f"{state}[{col}]", col < len(durations), alpha.crop(box)


def check_occupancy(sheet):
    problems = []
    for label, used, alpha in cells(sheet):
        peak = alpha.getextrema()[1]
        if used and peak == 0:
            problems.append(f"{label} is empty")
        elif not used and peak > 0:
            problems.append(f"{label} is unused but not fully transparent")
    return problems


def check_margins(sheet):
    problems = []
    for label, used, alpha in cells(sheet):
        ring = alpha.copy()
        ring.paste(0, (1, 1, CELL_W - 1, CELL_H - 1))
        if used and ring.getextrema()[1] > 0:
            problems.append(f"{label} touches the cell edge")
    return problems


def check_previews(preview, sheet):
    names = [f"{state}.gif" for state, _ in ANIMATIONS] + ["sheet.png"]
    problems = [f"{name} is missing" for name in names if not (preview / name).is_file()]
    try:
        if (preview / "sheet.png").is_file():
            with Image.open(preview / "sheet.png") as png:
                if differs(png.convert("RGB"), make_previews.contact_sheet(sheet)):
                    problems.append("sheet.png does not match the spritesheet")
        for state, durations in ANIMATIONS:
            if (preview / f"{state}.gif").is_file():
                with Image.open(preview / f"{state}.gif") as gif:
                    total = sum(frame.info.get("duration", 0) for frame in ImageSequence.Iterator(gif))
                if total != sum(durations):
                    problems.append(f"{state}.gif lasts {total} ms, expected {sum(durations)}")
    except (OSError, ValueError) as error:
        problems.append(f"cannot read a preview: {error}")
    return problems


def outcome(check, problems, hint=""):
    if not problems:
        return check, "PASS", ""
    detail = "; ".join(problems[:4])
    if len(problems) > 4:
        detail += f"; and {len(problems) - 4} more"
    return check, "FAIL", f"{detail} ({hint})" if hint else detail


def validate(pet_dir):
    """Run every check on one pet dir; return [(check, status, detail)] with PASS/FAIL/SKIP."""
    pet_dir = pathlib.Path(pet_dir)
    problems, sheet_path = check_pet_json(pet_dir)
    results = [outcome("pet.json", problems)]
    if sheet_path is None:
        return results + [(check, "SKIP", "no usable spritesheetPath") for check in IMAGE_CHECKS]
    problems, sheet = check_format(sheet_path)
    results.append(outcome("spritesheet format", problems))
    if sheet is None:
        return results + [(check, "SKIP", "spritesheet is not a readable atlas")
                          for check in IMAGE_CHECKS[1:]]
    results.append(outcome("cell occupancy", check_occupancy(sheet)))
    results.append(outcome("cell margins", check_margins(sheet)))
    hint = f"run: python3 -I tools/make_previews.py {pet_dir}"
    if not (pet_dir / "preview").is_dir():
        return results + [outcome("previews", ["no preview/ directory"], hint)]
    results.append(outcome("previews", check_previews(pet_dir / "preview", sheet), "stale, " + hint))
    return results


def duplicate_frames(sheet):
    """Labels like 'idle[1]=idle[2]' for consecutive used frames that are pixel-identical."""
    sheet = sheet.convert("RGBA")
    pairs = []
    for row, (state, durations) in enumerate(ANIMATIONS):
        frames = [sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
                  for col in range(len(durations))]
        pairs += [f"{state}[{col}]={state}[{col + 1}]"
                  for col in range(len(frames) - 1) if not differs(frames[col], frames[col + 1])]
    return pairs


def changed_rows(old, new):
    """Names of the animation rows whose pixels differ, in any channel, between two atlases."""
    old, new = old.convert("RGBA"), new.convert("RGBA")
    if old.size != new.size:
        return [state for state, _ in ANIMATIONS]
    boxes = [(0, row * CELL_H, old.width, (row + 1) * CELL_H) for row in range(len(ANIMATIONS))]
    return [state for (state, _), box in zip(ANIMATIONS, boxes) if differs(old.crop(box), new.crop(box))]


def main(argv):
    parser = argparse.ArgumentParser(description="Validate Codex-format desktop pets.")
    parser.add_argument("--compare", metavar="OLD_SPRITESHEET",
                        help="also list the rows that differ from this older spritesheet")
    parser.add_argument("pets", nargs="+", metavar="PET_DIR")
    args = parser.parse_args(argv)
    old = None
    if args.compare:
        try:  # read the bytes first so process substitution (/dev/fd/N) works
            old = Image.open(io.BytesIO(pathlib.Path(args.compare).read_bytes()))
        except (OSError, ValueError) as error:
            parser.error(f"cannot read {args.compare}: {error}")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for pet in args.pets:
        pet_dir = pathlib.Path(pet)
        for check, status, detail in validate(pet_dir):
            counts[status] += 1
            print(f"{status}  {pet_dir}  {check}" + (f": {detail}" if detail else ""))
        sheet_path = check_pet_json(pet_dir)[1]
        sheet = check_format(sheet_path)[1] if sheet_path is not None else None
        if sheet is not None and (dups := duplicate_frames(sheet)):
            shown = ", ".join(dups[:6]) + (f" and {len(dups) - 6} more" if len(dups) > 6 else "")
            print(f"INFO  {pet_dir}  duplicate consecutive frames: {shown}")
        if old is not None:
            sheet_path = check_pet_json(pet_dir)[1]
            if sheet_path is None:
                print(f"INFO  {pet_dir}  cannot compare: no usable spritesheetPath")
            else:
                rows = changed_rows(old, Image.open(io.BytesIO(sheet_path.read_bytes())))
                print(f"INFO  {pet_dir}  changed rows: {', '.join(rows) or 'none'}")
    print(f"summary: {len(args.pets)} pet(s), {counts['PASS']} passed, "
          f"{counts['FAIL']} failed, {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main(None))
