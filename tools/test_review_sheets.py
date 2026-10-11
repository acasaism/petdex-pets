#!/usr/bin/env python3
"""Tests for tools/review_sheets.py, on synthetic pets and on the committed cartoon pet.

Usage: python3 -I -m unittest discover -s tools -p 'test_*.py' -v

Synthetic pets get a lossless WebP atlas with a small opaque dot centred in every used cell,
except failed[2], whose dot is shifted: the diff band must light up there and nowhere else.
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest

from PIL import Image, ImageChops, ImageDraw

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rs = load("review_sheets")
mp = rs.make_previews
W, H = mp.CELL_W, mp.CELL_H
DOT = (200, 80, 120, 255)
MOVED = (5, 2)  # failed[2]
META = {
    "id": "test-pet",
    "displayName": "Test Pet",
    "description": "A synthetic pet.",
    "spritesheetPath": "spritesheet.webp",
}
STATES = [state for state, _ in mp.ANIMATIONS]
EXPECTED = {"overview-light.png", "overview-dark.png", "checklist.md",
            *(f"motion-{row}-{state}.png" for row, state in enumerate(STATES))}
CRITERIA = ["likeness", "hands/limbs", "mouth/teeth", "eyes", "props",
            "markings & motion (bands b-c)", "dark background", "loop seam"]


def make_sheet():
    """A dot centred in every used cell; the failed[2] dot sits 30 px further right."""
    sheet = Image.new("RGBA", mp.ATLAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for row, (_, durations) in enumerate(mp.ANIMATIONS):
        for col in range(len(durations)):
            x, y = col * W + W // 2 + (30 if (row, col) == MOVED else 0), row * H + H // 2
            draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=DOT)
    return sheet


def red_pixels(image):
    """Pixels painted by the red diff overlay: red well above both green and blue."""
    r, g, b = image.convert("RGB").split()
    return ImageChops.subtract(r, ImageChops.lighter(g, b)).point(
        lambda v: 255 if v > 100 else 0).histogram()[255]


def quietly(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


def cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


class SyntheticPetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.pet = self.tmp / "pet"
        self.pet.mkdir()
        (self.pet / "pet.json").write_text(json.dumps(META), encoding="utf-8")
        make_sheet().save(self.pet / "spritesheet.webp", "WEBP", lossless=True)
        self.out = self.tmp / "out"

    def run_cli(self, *args):
        code, out = quietly(rs.main, [*args, str(self.pet)])
        self.assertEqual(code, 0, out)
        return out

    def test_default_run_writes_every_sheet_into_pet_review(self):
        out = self.run_cli()
        review = self.pet / "review"
        self.assertEqual({path.name for path in review.iterdir()}, EXPECTED)
        for name in EXPECTED:
            self.assertIn(str(review / name), out)

    def test_diff_band_marks_only_the_moved_frame(self):
        sheet = rs.load_sheet(self.pet)
        bands = rs.motion_bands(sheet, MOVED[0])
        self.assertEqual([title[:3] for title, _ in bands], ["(a)", "(b)", "(c)", "(d)"])
        tiles = bands[1][1]
        self.assertEqual([label for label, _ in tiles[:3]], ["r5c0 failed", "r5c1 failed", "r5c2 failed"])
        self.assertEqual(tiles[2][1].size, (2 * W, 2 * H))
        self.assertGreater(red_pixels(tiles[2][1]), 0)
        self.assertEqual(red_pixels(tiles[1][1]), 0)
        self.assertEqual(red_pixels(bands[3][1][2][1]), 0, "failed[7] matches failed[0]: no seam")
        for label, tile in rs.motion_bands(sheet, 0)[1][1]:
            self.assertEqual(red_pixels(tile), 0, label)

    def test_overviews_use_their_backgrounds(self):
        self.assertEqual(rs.LIGHT[:3], (0xF0, 0xF0, 0xF0))
        self.assertEqual(rs.DARK[:3], (0x1E, 0x20, 0x2C))
        self.run_cli("--out", str(self.out))
        for name, colour in (("overview-light.png", rs.LIGHT), ("overview-dark.png", rs.DARK)):
            with Image.open(self.out / name) as image:
                counts = image.convert("RGB").getcolors(image.width * image.height)
            self.assertEqual(max(counts)[1], colour[:3], name)

    def test_baseline_compares_only_the_changed_row(self):
        old = make_sheet()
        old.putpixel((W // 2, 3 * H + H // 2), (10, 200, 30, 255))  # waving[0], colour only
        old.save(self.tmp / "old.webp", "WEBP", lossless=True)
        out = self.run_cli("--out", str(self.out), "--baseline", str(self.tmp / "old.webp"))
        self.assertTrue((self.out / "compare-baseline.png").is_file())
        self.assertIn("compare-baseline.png  changed rows: waving\n", out)
        self.assertIn("compare-baseline.png", (self.out / "checklist.md").read_text(encoding="utf-8"))

    def test_unchanged_baseline_from_a_pipe_is_skipped(self):
        data = (self.pet / "spritesheet.webp").read_bytes()
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        writer = threading.Thread(target=lambda: (os.write(write_fd, data), os.close(write_fd)))
        writer.start()
        out = self.run_cli("--out", str(self.out), "--baseline", f"/dev/fd/{read_fd}")
        writer.join()
        self.assertIn("no rows changed", out)
        self.assertFalse((self.out / "compare-baseline.png").exists())

    def test_refs_skip_non_images_and_keep_at_most_six(self):
        refs = self.tmp / "refs"
        refs.mkdir()
        for i in range(7):
            Image.new("RGB", (300, 200), (90, 140, 20 * i)).save(refs / f"ref-{i}.png")
        (refs / "notes.txt").write_text("not an image", encoding="utf-8")
        (refs / "broken.jpg").write_bytes(b"not a jpeg")
        out = self.run_cli("--out", str(self.out), "--refs", str(refs))
        self.assertTrue((self.out / "vs-reference.png").is_file())
        self.assertIn("skipped unreadable reference broken.jpg", out)
        self.assertIn("vs-reference.png  6 reference image(s)", out)
        self.assertIn("vs-reference.png", (self.out / "checklist.md").read_text(encoding="utf-8"))

    def test_checklist_has_a_row_per_state_and_every_criterion(self):
        self.run_cli("--out", str(self.out))
        text = (self.out / "checklist.md").read_text(encoding="utf-8")
        table = [line for line in text.splitlines() if line.startswith("|")]
        self.assertEqual(rs.CRITERIA, CRITERIA)
        self.assertEqual(cells(table[0])[1:], CRITERIA)
        self.assertEqual(len(table[2:]), 9)
        for line, state in zip(table[2:], STATES):
            self.assertIn(state, cells(line)[0])
            self.assertEqual(cells(line)[1:], [""] * len(CRITERIA))
        for name in EXPECTED - {"checklist.md"}:
            self.assertIn(f"- [ ] {name}", text)
        self.assertIn("- [ ] overview-dark.png:", text)
        self.assertNotIn("vs-reference.png", text)


class CommittedPetTest(unittest.TestCase):
    def test_cartoon_pet_gets_every_sheet_and_stays_untouched(self):
        pet = ROOT / "pets" / "courage" / "cartoon"
        before = sorted(path.name for path in pet.iterdir())
        with tempfile.TemporaryDirectory() as tmp:
            written, _ = quietly(rs.review, pet, pathlib.Path(tmp))
            self.assertEqual({path.name for path in pathlib.Path(tmp).iterdir()}, EXPECTED)
        self.assertEqual({path.name for path in written}, EXPECTED)
        self.assertEqual(sorted(path.name for path in pet.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
