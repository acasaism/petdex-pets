#!/usr/bin/env python3
"""Tests for tools/validate_pet.py, on synthetic pets and on the committed pets.

Usage: python3 -I -m unittest discover -s tools -p 'test_*.py' -v

Synthetic pets are written to temporary directories: a lossless WebP atlas with a small
opaque dot centred in every used cell, plus previews rendered by tools/make_previews.py.
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

from PIL import Image, ImageChops, ImageDraw

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vp = load("validate_pet")
mp = vp.make_previews
W, H = mp.CELL_W, mp.CELL_H
DOT = (200, 80, 120, 255)
META = {
    "id": "test-pet",
    "displayName": "Test Pet",
    "description": "A synthetic pet.",
    "spritesheetPath": "spritesheet.webp",
}


def make_sheet():
    """A valid atlas: a small opaque dot centred in every used cell."""
    sheet = Image.new("RGBA", mp.ATLAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for row, (_, durations) in enumerate(mp.ANIMATIONS):
        for col in range(len(durations)):
            x, y = col * W + W // 2, row * H + H // 2
            draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=DOT)
    return sheet


def quietly(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


class AtlasContractTest(unittest.TestCase):
    def test_layout_matches_the_codex_atlas(self):
        self.assertEqual(mp.ATLAS_SIZE, (1536, 1872))
        self.assertEqual((W, H), (192, 208))
        self.assertEqual([state for state, _ in mp.ANIMATIONS],
                         ["idle", "running-right", "running-left", "waving", "jumping",
                          "failed", "waiting", "running", "review"])
        self.assertEqual([len(d) for _, d in mp.ANIMATIONS], [6, 8, 8, 4, 5, 8, 6, 6, 6])


class SyntheticPetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pet = pathlib.Path(tmp.name)

    def write(self, sheet=None, meta=None, previews=True, **save_args):
        sheet = make_sheet() if sheet is None else sheet
        meta = META if meta is None else meta
        (self.pet / "pet.json").write_text(json.dumps(meta), encoding="utf-8")
        sheet.save(self.pet / "spritesheet.webp", "WEBP", **(save_args or {"lossless": True}))
        if previews:
            quietly(mp.render, self.pet)

    def results(self):
        return {check: (status, detail) for check, status, detail in vp.validate(self.pet)}

    def assertAllPass(self):
        for check, (status, detail) in self.results().items():
            self.assertEqual(status, "PASS", f"{check}: {detail}")

    def assertFails(self, check, *words):
        status, detail = self.results()[check]
        self.assertEqual(status, "FAIL", f"{check}: {detail}")
        for word in words:
            self.assertIn(word, detail)

    def test_valid_pet_passes(self):
        self.write()
        self.assertEqual(list(self.results()), ["pet.json", "spritesheet format", "cell occupancy",
                                                "cell margins", "previews"])
        self.assertAllPass()

    def test_lossless_webp_with_vp8x_header_passes(self):
        exif = b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00"  # metadata forces VP8X
        self.write(lossless=True, exif=exif)
        self.assertEqual((self.pet / "spritesheet.webp").read_bytes()[12:16], b"VP8X")
        self.assertAllPass()

    def test_png_spritesheet_passes(self):
        self.write(previews=False, meta=dict(META, spritesheetPath="sheet.png"))
        make_sheet().save(self.pet / "sheet.png")
        quietly(mp.render, self.pet)
        self.assertAllPass()

    def test_missing_previews_are_skipped(self):
        self.write(previews=False)
        self.assertEqual(self.results()["previews"][0], "SKIP")

    def test_wrong_atlas_size(self):
        self.write(make_sheet().crop((0, 0, 1536, 1864)), previews=False)
        self.assertFails("spritesheet format", "1536x1872")

    def test_lossy_webp(self):
        self.write(lossless=False, quality=90)
        self.assertFails("spritesheet format", "lossy")

    def test_empty_used_cell(self):
        sheet = make_sheet()
        sheet.paste((0, 0, 0, 0), (2 * W, 3 * H, 3 * W, 4 * H))
        self.write(sheet)
        self.assertFails("cell occupancy", "waving[2]")

    def test_non_transparent_trailing_cell(self):
        sheet = make_sheet()
        sheet.putpixel((7 * W + W // 2, H // 2), (0, 0, 0, 1))  # idle uses columns 0-5 only
        self.write(sheet)
        self.assertFails("cell occupancy", "idle[7]")

    def test_pixel_on_cell_border(self):
        edges = {"left": (3 * W, 5 * H + 100), "right": (4 * W - 1, 5 * H + 100),
                 "top": (3 * W + 90, 5 * H), "bottom": (3 * W + 90, 6 * H - 1)}
        for edge, xy in edges.items():
            with self.subTest(edge):
                sheet = make_sheet()
                sheet.putpixel(xy, DOT)
                self.write(sheet)
                self.assertFails("cell margins", "failed[3]")

    def test_extra_or_missing_pet_json_key(self):
        extra = dict(META, author="me")
        missing = {k: v for k, v in META.items() if k != "description"}
        for meta, key in ((extra, "author"), (missing, "description")):
            with self.subTest(key):
                self.write(meta=meta, previews=False)
                self.assertFails("pet.json", key)

    def test_bad_id(self):
        for bad in ("Test-Pet", "test_pet", "test--pet", "-test", "test-", ""):
            with self.subTest(bad):
                self.write(meta=dict(META, id=bad), previews=False)
                self.assertFails("pet.json", "id")

    def test_display_name_too_long(self):
        self.write(meta=dict(META, displayName="x" * 60), previews=False)
        self.assertEqual(self.results()["pet.json"][0], "PASS")
        self.write(meta=dict(META, displayName="x" * 61), previews=False)
        self.assertFails("pet.json", "displayName")

    def test_spritesheet_path_outside_pet_dir(self):
        self.write(meta=dict(META, spritesheetPath="../spritesheet.webp"), previews=False)
        self.assertFails("pet.json", "spritesheetPath")

    def test_stale_sheet_png(self):
        self.write()
        sheet = make_sheet()
        x, y = 2 * W + W // 2, 8 * H + H // 2
        sheet.putpixel((x, y), (10, 200, 30, 255))  # colour-only change in review[2]
        sheet.save(self.pet / "spritesheet.webp", "WEBP", lossless=True)
        self.assertFails("previews", "sheet.png", "make_previews.py")

    def test_wrong_gif_duration(self):
        self.write()
        frames = [Image.new("RGB", (8, 8), (i * 60, 0, 0)) for i in range(4)]
        frames[0].save(self.pet / "preview" / "waving.gif", save_all=True,
                       append_images=frames[1:], duration=100, loop=0)
        self.assertFails("previews", "waving.gif", "make_previews.py")

    def test_missing_preview_file(self):
        self.write()
        (self.pet / "preview" / "running.gif").unlink()
        self.assertFails("previews", "running.gif", "make_previews.py")

    def test_cli_exit_codes(self):
        self.write()
        code, out = quietly(vp.main, [str(self.pet)])
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)
        self.write(meta=dict(META, id="Bad Id"))
        code, out = quietly(vp.main, [str(self.pet)])
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)


class CompareTest(unittest.TestCase):
    def test_colour_only_change_is_detected(self):
        old = make_sheet()
        new = old.copy()
        new.putpixel((W // 2, 8 * H + H // 2), (10, 200, 30, 255))  # review[0], alpha unchanged
        # The pitfall: the default getbbox() on an RGBA difference only looks at alpha.
        self.assertIsNone(ImageChops.difference(old, new).getbbox())
        self.assertEqual(vp.changed_rows(old, new), ["review"])
        self.assertEqual(vp.changed_rows(old, old.copy()), [])

    def test_cli_compare_reports_changed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            pet = pathlib.Path(tmp) / "pet"
            pet.mkdir()
            (pet / "pet.json").write_text(json.dumps(META), encoding="utf-8")
            new = make_sheet()
            new.putpixel((W // 2, 3 * H + H // 2), (10, 200, 30, 255))  # waving[0]
            new.save(pet / "spritesheet.webp", "WEBP", lossless=True)
            make_sheet().save(pathlib.Path(tmp) / "old.webp", "WEBP", lossless=True)
            code, out = quietly(vp.main, ["--compare", str(pathlib.Path(tmp) / "old.webp"), str(pet)])
        self.assertEqual(code, 0, out)
        self.assertIn("changed rows: waving", out)


class CommittedPetsTest(unittest.TestCase):
    def test_committed_pets_pass_every_check(self):
        for variant in ("cartoon", "pixel"):
            with self.subTest(variant):
                results = vp.validate(ROOT / "pets" / "courage" / variant)
                self.assertEqual(len(results), 5)
                for check, status, detail in results:
                    self.assertEqual(status, "PASS", f"{check}: {detail}")


if __name__ == "__main__":
    unittest.main()
