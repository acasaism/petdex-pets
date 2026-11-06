#!/usr/bin/env python3
"""Generate the "chowder-pixel" Codex pet: Chowder (Cartoon Network) as shaded pixel art.

The poses, gags and traced parts are the cartoon variant's rig (../cartoon/make_pet.py, imported, never copied):
every pose is drawn once more through draw_character with a false colour per class (a class map, no
antialiasing) at 8 device px per logical px, and each 8x8 block becomes one pixel of a 96x104 grid. The grid is
then finished as pixel art: smoothed pattern spots, selective outlining (dark only on the silhouette and true
features, interior lines in a darker shade of their material), top-left light with hue-shifted ramps, a 1 px
light rim and grey effect marks in clear air. Frames are scaled x2 (nearest) into 192x208 cells.

What 96 px cannot hold from the traced art is hand-drawn on the grid: the eyes (glyphs for open, blink, happy
and squeeze eyes, with lids and gaze), the fang (one white pixel inside open mouths), and one-pixel gaps in
thin handles. Hands and the small "o" mouth are drawn bigger than the rig's. Frame checks (rule_*) stop the
build when the reduction loses an eye, the fang, a hand or a piece of a prop, or leaves lone pixels.

Usage:
    python3 -I make_pet.py   # runs the pixel frame checks, renders out/spritesheet.webp and copies it here
"""
from __future__ import annotations

import colorsys
from contextlib import contextmanager
import importlib.util
import os
import shutil
import sys
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import cairo
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
W, H = 96, 104          # logical grid of one frame: 2x2 cell px per pixel, fine enough for hands, eyes and spots
G = 8                   # device px per logical px: the rig is drawn 8x and reduced block by block
SCALE = 2               # logical px -> cell px
PAD_TOP = 0             # 104 * 2 = 208
CELL_W, CELL_H = 192, 208
COLS, ROWS = 8, 9


def load_rig():
    path = os.path.join(HERE, "..", "cartoon", "make_pet.py")
    spec = importlib.util.spec_from_file_location("chowder_rig", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chowder_rig"] = mod        # dataclasses look their module up while the file runs
    spec.loader.exec_module(mod)
    mod.SS = G / SCALE                       # rig strokes are cell px * SS device px wide
    return mod


rig = load_rig()
ROW_SPEC = rig.ROW_SPEC

# ---------------------------------------------------------------------------
# Class map
# ---------------------------------------------------------------------------
CLASSES = list(rig.CLASS_RGB)
CID = {k: i + 1 for i, k in enumerate(CLASSES)}
INK = 200
GLYPH_INK = 201         # hand-drawn lines (eye glyphs, hand outlines): always the dark outline colour
NONE = -1
INK_ADD = 0.5           # cell px added to the traced ink (the cartoon adds 0.9): thinner lines vanish at 8x
ID_LOOK = rig.Look({k: (CID[k] / 255, 0.0, 0.0) for k in CLASSES}, (INK / 255, 0.0, 0.0), ink_add=INK_ADD)
T_OPAQUE = 28           # of 64 device px: a block with less coverage stays transparent
T_INK = 14              # ink coverage that makes a block an ink pixel
PRIORITY = {"white": 16, "wood": 6, "fork": 10, "nose": 18, "stew": 18}   # small features beat ink (not fills)
PATTERNS = (("purple", "purple_d"), ("magenta", "magenta_d"))
MIN_SPOT = 8            # pattern spots smaller than this (px) are noise at 1x

# ---------------------------------------------------------------------------
# Palette: base colours are the rig's (measured on ref 01); shadow and highlight steps are hue-shifted
# ---------------------------------------------------------------------------
OUT = (30, 14, 36)
RIM = (255, 247, 242)
LINE = {"skin": (118, 102, 168), "purple": (62, 10, 72), "magenta": (126, 12, 92), "face": (64, 40, 96)}
FAMILY = {"skin": "skin", "tail_band": "skin", "purple": "purple", "purple_d": "purple", "magenta": "magenta",
          "magenta_d": "magenta", "cuff": "magenta"}
FEATURES = {"white", "mouth", "tongue", "nose", "fork", "wood", "bowl", "bowl_in", "pot", "pot_l", "pot_in",
            "stew", "shoes", "loop"}      # ink next to these stays dark: eyes, mouth, props


def rgb8(c) -> Tuple[int, int, int]:
    return tuple(int(round(v * 255)) for v in c)


def _shift(c, dv: float, ds: float, toward: float, dh: float) -> Tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
    d = (toward - h + 0.5) % 1.0 - 0.5
    h = (h + max(-dh, min(dh, d))) % 1.0
    return rgb8(colorsys.hsv_to_rgb(h, min(1.0, s * ds), max(0.0, min(0.96, v * dv))))


BASE = {k: rgb8(v) for k, v in rig.CLASS_RGB.items()}
SHADOW = {k: _shift(c, 0.80, 1.15, 0.72, 0.035) for k, c in BASE.items()}     # toward blue-violet, deeper
LIGHT = {k: _shift(c, 1.10, 0.82, 0.12, 0.025) for k, c in BASE.items()}      # toward warm yellow, paler
LIGHT["white"] = SHADOW["white"] = BASE["white"]                               # eye whites are a feature
UNSHADED = {"white", "mouth", "tongue", "nose", "stew"}


def grid_paint(draw, ids: bool = True) -> np.ndarray:
    """Draw in cell space at G device px per logical px without antialiasing; ids: the red channel of opaque
    pixels (else NONE), otherwise the RGBA array."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W * G, H * G)
    ctx = cairo.Context(surf)
    ctx.set_antialias(cairo.ANTIALIAS_NONE)
    ctx.scale(G / SCALE, G / SCALE)
    ctx.translate(0, -PAD_TOP)
    draw(ctx)
    surf.flush()
    a = np.ndarray((H * G, surf.get_stride() // 4, 4), np.uint8, surf.get_data())[:, :W * G]
    if not ids:
        return a[..., [2, 1, 0, 3]].copy()
    return np.where(a[..., 3] == 255, a[..., 2].astype(np.int32), NONE)


def blocks(a: np.ndarray) -> np.ndarray:
    return a.reshape(H, G, W, G).transpose(0, 2, 1, 3).reshape(H, W, G * G)


def reduce_classes(ids: np.ndarray) -> np.ndarray:
    b = blocks(ids)
    keys = np.array([CID[k] for k in CLASSES] + [INK])
    cnt = (b[..., None] == keys).sum(2)
    fills = cnt[..., :-1]
    out = np.where(cnt[..., -1] >= T_INK, INK, keys[:-1][fills.argmax(2)])
    out = np.where(fills.sum(2) == 0, INK, out)
    for k, t in PRIORITY.items():           # a 2 cell px handle is under a third of a block: it must beat ink
        out = np.where((out == INK) & (cnt[..., CID[k] - 1] >= t), CID[k], out)
    out = np.where(cnt.sum(2) >= T_OPAQUE, out, NONE)
    for base, pat in PATTERNS:              # spots by local majority over 3x3 blocks, not block by block
        nb, npat = (_box(cnt[..., CID[k] - 1].astype(float)) for k in (base, pat))
        fam = (out == CID[base]) | (out == CID[pat])
        spot = fam & (npat > 1.25 * nb)
        for blob in blobs(spot):
            if len(blob) < MIN_SPOT:
                spot[blob[:, 0], blob[:, 1]] = False
        out = np.where(fam, np.where(spot, CID[pat], CID[base]), out)
    return out


def _box(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1)
    return sum(p[dy:dy + H, dx:dx + W] for dy in range(3) for dx in range(3))


PM_KEYS = {k: tuple(int(round(c * 255)) for c in v) for k, v in rig.PM.items()}


def reduce_parts(rgba: np.ndarray) -> np.ndarray:
    """Logical grid of part indices (into rig.PM order; -1 none) by block majority of the part map."""
    key = rgba[..., 0].astype(np.int32) << 16 | rgba[..., 1].astype(np.int32) << 8 | rgba[..., 2]
    key = np.where(rgba[..., 3] == 255, key, -1)
    keys = np.array([r << 16 | g << 8 | b for r, g, b in PM_KEYS.values()])
    cnt = (blocks(key)[..., None] == keys).sum(2)
    return np.where(cnt.sum(2) > 0, cnt.argmax(2), -1), cnt


PART = {k: i for i, k in enumerate(PM_KEYS)}


def blobs(m: np.ndarray) -> List[np.ndarray]:
    """8-connected components of m as (n, 2) arrays of (y, x)."""
    seen, out = np.zeros_like(m), []
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        stack, comp = [(y0, x0)], []
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for yy in range(max(y - 1, 0), min(y + 2, m.shape[0])):
                for xx in range(max(x - 1, 0), min(x + 2, m.shape[1])):
                    if m[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        stack.append((yy, xx))
        out.append(np.array(comp))
    return out


def shift(m: np.ndarray, dy: int, dx: int, fill=False) -> np.ndarray:
    """out[y, x] = m[y + dy, x + dx]."""
    out = np.full_like(m, fill)
    ys, yd = (slice(dy, None), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, None))
    xs, xd = (slice(dx, None), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, None))
    out[yd, xd] = m[ys, xs]
    return out


N4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
N8 = N4 + ((-1, -1), (-1, 1), (1, -1), (1, 1))


def any_n(m: np.ndarray, ns=N4) -> np.ndarray:
    return np.logical_or.reduce([shift(m, dy, dx) for dy, dx in ns])


# ---------------------------------------------------------------------------
# One frame
# ---------------------------------------------------------------------------
class Grid:
    """Logical class map, part map and effect layers of one pose (mirrored for running-left)."""

    def __init__(self, p, mirror: bool = False):
        q = replace(p, fx=[], fx_back=[])
        self.pose = p
        with pixel_rig():
            ids = grid_paint(lambda c: rig.draw_character(c, q, ID_LOOK))
            self.part, self.pcnt = reduce_parts(grid_paint(lambda c: rig.draw_character(c, q, rig.PART_LOOK),
                                                           ids=False))
        self.cls = reduce_classes(ids)
        self.prop_hi = blocks(np.isin(ids, [CID["wood"], CID["fork"]])).any(2)    # any prop pixel in the block
        bridge_props(self.cls, self.prop_hi)
        self.eyes = stamp_eyes(self.cls, self.part, p)
        snap_fang(self.cls, self.pcnt[..., PART["fang"]], p)
        self.fx = fx_layer(p.fx)
        self.fx_back = fx_layer(p.fx_back)
        if mirror:
            for k in ("cls", "part", "pcnt", "prop_hi", "fx", "fx_back"):
                setattr(self, k, getattr(self, k)[:, ::-1].copy())


# hands grow about the point that must not move (thin fingers vanish at this size); the bowl hand is big enough
HAND_K = {"rest": (1.3, rig.WRIST_N), "open": (1.3, (0.0, 0.0)), "fist": (1.15, rig.GRIP),
          "bowl": (1.0, rig.BOWL_GRIP)}
rig.MOUTHS["o"] = rig.MOUTHS["o"][:2] + (0.8,)      # the small "o" mouth is a speck at the rig's 0.6


@contextmanager
def pixel_rig():
    """The rig as the pixel draws it: no eyes (glyphs replace them), bigger hands."""
    saved = rig.draw_eye, rig.draw_arm
    rig.draw_eye, rig.draw_arm = (lambda *a: None), draw_arm
    try:
        yield
    finally:
        rig.draw_eye, rig.draw_arm = saved


def draw_arm(ctx, a, far: bool, look) -> None:
    """rig.draw_arm with the hand scaled about the point that must not move: the wrist, or where the fist
    holds its handle or the hand holds the bowl, so props stay in hand."""
    hm = rig.hand_matrix(a, far)
    if a.held is not None:
        ctx.save()
        ctx.transform(hm)
        a.held(ctx, look)
        ctx.restore()
    k, pivot = HAND_K[a.hand]
    big = rig.mul(hm, rig.about(pivot, sx=k, sy=k))
    rig.draw_part(ctx, rig.HANDS[a.hand][0], look, big)
    rig.draw_part(ctx, "sleeve_n", look, rig.sleeve_matrix(a, far))


def bridge_props(cls: np.ndarray, prop_hi: np.ndarray) -> None:
    """A thin handle crossing a lip or cuff line can lose one pixel to the ink: an ink pixel on the prop that
    touches two separate pieces of the same prop colour becomes that colour again."""
    for k in ("wood", "fork"):
        label = np.zeros((H, W), int)
        for i, b in enumerate(blobs(cls == CID[k]), 1):
            label[b[:, 0], b[:, 1]] = i
        for y, x in zip(*np.nonzero(prop_hi & (cls == INK))):
            near = {label[y + dy, x + dx] for dy, dx in N8 if 0 <= y + dy < H and 0 <= x + dx < W}
            if len(near - {0}) >= 2:
                cls[y, x] = CID[k]


def snap_fang(cls: np.ndarray, fang: np.ndarray, p) -> None:
    """Open mouths: the fang is one white pixel hanging from the upper lip inside the mouth, under where the rig
    puts it (its 1 cell px tip lands on the lip line at this size)."""
    mouth = np.isin(cls, [CID["mouth"], CID["tongue"]])
    if p.mouth not in rig.OPEN_MOUTHS or fang.sum() < G * G // 2 or not mouth.any():
        return
    yy, xx = np.mgrid[0:H, 0:W]
    cy, cx = (yy * fang).sum() / fang.sum(), (xx * fang).sum() / fang.sum()
    ys, xs = np.nonzero(mouth)
    i = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
    y, x = ys[i], xs[i]
    while y > 0 and mouth[y - 1, x]:
        y -= 1
    cls[(fang > 0) & (cls == CID["white"])] = INK
    cls[y, x] = CID["white"]


def logical(pt) -> Tuple[float, float]:
    return pt[0] / SCALE, (pt[1] - PAD_TOP) / SCALE


SHUT = {"blink": ("....", "#..#", ".##."), "happy": ("....", ".##.", "#..#"),
        "squeeze": (("##..", "..##", "##.."), ("..##", "##..", "..##"))}      # > on the back eye, < on the front


def eye_oval(w: int, h: int) -> set:
    """Cells (y, x) of an oval filling a w x h box."""
    return {(y, x) for y in range(h) for x in range(w)
            if ((x - (w - 1) / 2) / (w / 2)) ** 2 + ((y - (h - 1) / 2) / (h / 2)) ** 2 <= 1.0}


def stamp_eyes(cls: np.ndarray, part: np.ndarray, p) -> List[Dict[str, int]]:
    """Hand-drawn eye pair: two outlined white ovals sharing a middle line, pupils 1 px wide; closed eyes as
    arcs (blink), carets (happy) or > < (squeeze); heavy lids as a dark line over the white."""
    (bx, by), (fx, fy) = (logical(rig.to_cell(p, *rig.EYES[k][0], head=True)) for k in ("eye_b", "eye_f"))
    big = p.eye_s >= 1.2
    iw, ih = (4, 6) if big else (3, 5)               # white area: taller than wide, like the show's eyes
    left = int(round((bx + fx) / 2 - (3 + 2 * iw) / 2))
    top = int(round((by + fy) / 2 - (ih + 2) / 2))
    px, py = p.pupil
    out = []
    for e, lid in enumerate((p.lid_b, p.lid_f)):
        x0 = left + e * (iw + 1)                     # this eye's box: x0 .. x0 + iw + 1, top .. top + ih + 1
        put = []                                     # (dy, dx, class) in box coordinates
        landed = {"shut": bool(p.shut), "white": 0, "ink": 0}
        pupils = []
        if p.shut:                                   # 4x3 glyphs, one column apart
            x0, mid = left + e * (iw + 2), ih // 2 + 1
            glyph = SHUT[p.shut] if p.shut != "squeeze" else SHUT["squeeze"][e]
            put = [(mid - 1 + y, x, GLYPH_INK) for y, row in enumerate(glyph) for x, ch in enumerate(row) if ch == "#"]
        else:
            oval = eye_oval(iw + 2, ih + 2)
            white = {(y, x) for y, x in oval if all(n in oval for n in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)))}
            put = [(y, x, CID["white"] if (y, x) in white else GLYPH_INK) for y, x in sorted(oval)]
            n = int(round(lid * ih))
            put += [(y, x, CID["skin"]) for y, x in white if y < n]
            put += [(y, x, GLYPH_INK) for y, x in white if y == n]
            col = (2 if px <= -6 else 3) if big else (1 if px <= -6 else iw)    # big eyes: pupils off the outline
            rows = [ih // 2] if p.pupil_s < 0.7 else [ih // 2, ih // 2 + 1]
            if py <= -8:
                rows = [r - 1 for r in rows]
            rows = [r + max(0, n + 1 - rows[0]) for r in rows]     # under a heavy lid (ref 05), not hidden by it
            pupils = [(r, col) for r in rows if (r, col) in white]
            put += [(r, c, GLYPH_INK) for r, c in pupils]
        for dy, dx, k in put:
            y, x = top + dy, x0 + dx
            if 0 <= y < H and 0 <= x < W and (part[y, x] != PART["hat"] or cls[y, x] in (INK, CID["skin"])):
                cls[y, x] = k
                landed["white" if k == CID["white"] else "ink"] += k != CID["skin"]
        landed["pupil"] = sum(1 for r, c in pupils if 0 <= top + r < H and 0 <= x0 + c < W
                              and cls[top + r, x0 + c] == GLYPH_INK)
        out.append(landed)
    return out


def fx_layer(fns) -> np.ndarray:
    """Effect marks as a logical RGBA layer: majority colour of blocks at least a third covered."""
    out = np.zeros((H, W, 4), np.uint8)
    if not fns:
        return out
    a = grid_paint(lambda c: [fn(c) for fn in fns], ids=False)
    key = (a[..., 0].astype(np.int32) << 16 | a[..., 1].astype(np.int32) << 8 | a[..., 2])
    key = np.where(a[..., 3] > 127, key, -1)
    b = blocks(key)
    for y, x in zip(*np.nonzero((b >= 0).sum(2) >= 22)):
        vals, n = np.unique(b[y, x][b[y, x] >= 0], return_counts=True)
        k = int(vals[n.argmax()])
        c = (k >> 16, k >> 8 & 255, k & 255)
        out[y, x] = (*(OUT if sum(c) < 150 else c), 255)
    return out


KEEP = ("white", "nose", "mouth", "tongue", "wood", "fork", "stew")    # small on purpose: never despeckled


def specks(rgba: np.ndarray) -> np.ndarray:
    """Opaque pixels whose colour none of their 8 neighbours shares."""
    key = rgba[..., 0].astype(np.int64) << 16 | rgba[..., 1].astype(np.int64) << 8 | rgba[..., 2]
    key = np.where(rgba[..., 3] > 0, key, -1)
    same = np.logical_or.reduce([shift(key, dy, dx, -2) == key for dy, dx in N8])
    return ~same & (key >= 0)


def finish(g: Grid) -> np.ndarray:
    """Logical RGBA frame from a Grid: outline, interior lines, shading, rim, effects."""
    cls = g.cls.copy()
    fig = cls != NONE
    ink = cls == INK
    # silhouette: fill specks outside the traced outline go, every other edge pixel becomes outline
    edge = fig & any_n(~fig)
    stray = edge & ~ink & any_n(ink) & ~np.isin(cls, [CID[k] for k in PRIORITY])
    cls[stray] = NONE
    fig = cls != NONE
    edge = fig & any_n(~fig) & ~np.isin(cls, [CID["wood"], CID["fork"]])
    # a mouth's outline is 2 px of ink at this size: the outer pixel would turn into a purple face line and
    # read as a shadow over the mouth, so it goes back to the face's own colour
    feat_ink = ink & any_n(np.isin(cls, [CID[k] for k in ("mouth", "tongue", "white", "nose")]), N8)
    for y, x in zip(*np.nonzero(ink & ~feat_ink & ~edge & any_n(feat_ink, N4))):
        near = {int(v) for v in cls[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2].ravel()} - {INK, GLYPH_INK, NONE}
        if len(near) == 1:
            cls[y, x] = near.pop()
    ink = cls == INK
    rgba = np.zeros((H, W, 4), np.uint8)
    names = {CID[k]: k for k in CLASSES}
    group = np.where(np.isin(g.part, [PART[k] for k in ("mouth", "teeth", "fang")]), PART["face"], g.part)
    # form shadow: the lower right of each body part, where it ends against the air or another part; lines and
    # features drawn on a part (mouth, teeth, nose, eyes, ink) are part of it, or every mouth gets a dark halo
    on_part = fig & (np.isin(cls, [INK, GLYPH_INK] + [CID[k] for k in UNSHADED]))
    lit_side = np.zeros_like(fig)
    for i in np.unique(group[group >= 0]):
        r = (group == i) | on_part
        lit_side |= (group == i) & shift(r, 3, 3) & shift(r, 4, 0)
    lit = fig & ~shift(fig, -2, -2) & shift(fig, -1, -1)            # 1 px inside the top-left silhouette
    brim = (group == PART["face"]) & any_n(group == PART["hat"], ((-1, 0), (-2, 0), (-3, 0)))
    for cid, k in names.items():
        m = (cls == cid) & ~edge
        if not m.any():
            continue
        rgba[m] = (*BASE[k], 255)
        if k in UNSHADED:
            continue
        sh = m & (~lit_side | brim) & (group != PART["hand"])      # a hand is too small for form shadow
        rgba[sh] = (*SHADOW[k], 255)
        rgba[m & lit & ~sh] = (*LIGHT[k], 255)
    # interior ink: dark next to features, else a darker shade of the material it separates
    for y, x in zip(*np.nonzero(ink & ~edge)):
        near = {names.get(int(v)) for v in cls[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2].ravel()}
        fams = {FAMILY.get(k) for k in near}
        if near & FEATURES:
            c = OUT
        elif "purple" in fams:
            c = LINE["purple"]
        elif "magenta" in fams:
            c = LINE["magenta"]
        elif "skin" in fams:
            c = LINE["face"] if g.part[y, x] in (PART["face"], PART["hand"]) else LINE["skin"]   # mouth, fingers
        else:
            c = OUT
        rgba[y, x] = (*c, 255)
    rgba[edge | (cls == GLYPH_INK)] = (*OUT, 255)
    keep = edge | (cls == GLYPH_INK) | np.isin(cls, [CID[k] for k in KEEP])
    for _ in range(3):                      # 1 px specks of shade or line left by the 8x reduction: noise at 1x
        sp = specks(rgba) & ~keep
        if not sp.any():
            break
        for y, x in zip(*np.nonzero(sp)):
            win = np.array([rgba[y + dy, x + dx] for dy, dx in N8 if 0 <= y + dy < H and 0 <= x + dx < W])
            vals, n = np.unique(win[win[:, 3] > 0], axis=0, return_counts=True)
            rgba[y, x] = vals[n.argmax()]
    rim = any_n(fig, N8) & ~fig
    rim[0], rim[-1], rim[:, 0], rim[:, -1] = False, False, False, False     # scaled up, the border ring is the cell edge
    back = (g.fx_back[..., 3] > 0) & ~fig & ~rim
    rgba[back] = g.fx_back[back]
    rgba[rim] = (*RIM, 255)
    front = (g.fx[..., 3] > 0) & ~fig
    rgba[front] = g.fx[front]
    return rgba


# ---------------------------------------------------------------------------
# Frame checks: what the 8x reduction can break in a rig pose that passed the cartoon's checks (S7)
# ---------------------------------------------------------------------------
def rule_eyes(g: Grid, rgba: np.ndarray) -> Optional[str]:
    # every eye glyph shows: open eyes keep white and a pupil, closed ones their stroke (not hidden by the hat)
    for name, e in zip(("back", "front"), g.eyes):
        if (e["ink"] < 3) if e["shut"] else (e["white"] < 2 or e["pupil"] < 1):
            return f"{name} eye glyph hidden: {e}"
    return None


def rule_fang(g: Grid, rgba: np.ndarray) -> Optional[str]:
    # a fang the rig shows (at least half a pixel) survives as white, inside the mouth when the mouth is open
    hi = g.pcnt[..., PART["fang"]] + g.pcnt[..., PART["teeth"]]
    if hi.sum() < G * G // 2:
        return None
    white = (g.cls == CID["white"]) & any_n(hi > 0, N8 + ((0, 0),))
    if not white.any():
        return "the fang is lost"
    if g.pose.mouth in rig.OPEN_MOUTHS and not (white & any_n(np.isin(g.cls, [CID["mouth"], CID["tongue"]]), N8)).any():
        return "the fang is outside the open mouth"
    return None


def rule_props(g: Grid, rgba: np.ndarray) -> Optional[str]:
    # every piece of fork, spoon or ladle the rig shows stays one unbroken piece of prop colour
    want = len(blobs(g.prop_hi))
    got = len(blobs(np.isin(g.cls, [CID["wood"], CID["fork"]])))
    return None if want == got else f"{want} prop piece(s) in the rig, {got} in pixels"


def rule_hands(g: Grid, rgba: np.ndarray) -> Optional[str]:
    # every hand the rig shows (at least 2 px of it) keeps skin pixels: a hand is a feature
    hand = g.pcnt[..., PART["hand"]]
    for b in blobs(hand > 0):
        if hand[b[:, 0], b[:, 1]].sum() >= 2 * G * G and (g.cls[b[:, 0], b[:, 1]] == CID["skin"]).sum() < 2:
            return f"hand at {tuple(b.mean(0).round().astype(int))} lost its skin pixels"
    return None


def rule_specks(g: Grid, rgba: np.ndarray) -> Optional[str]:
    # no lone pixel of shade or line on the figure: the 8x reduction's noise (features, outline, rim, effects
    # excepted)
    keep = (np.isin(g.cls, [CID[k] for k in KEEP] + [GLYPH_INK, NONE]) | (rgba[..., :3] == OUT).all(-1)
            | (rgba[..., :3] == RIM).all(-1))
    n = int((specks(rgba) & ~keep).sum())
    return f"{n} lone pixel(s)" if n else None


RULES = [rule_eyes, rule_fang, rule_props, rule_hands, rule_specks]


def check_frames(grids: List[List[Grid]], frames: List[List[np.ndarray]]) -> None:
    """Run every rule on every frame; raise SystemExit naming row, frame and rule on any violation."""
    errors = [f"{name} frame {c}: {rule.__name__}: {msg}"
              for (name, _), row, imgs in zip(ROW_SPEC, grids, frames)
              for c, (g, rgba) in enumerate(zip(row, imgs))
              for rule in RULES for msg in [rule(g, rgba)] if msg]
    if errors:
        raise SystemExit("frame checks failed:\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------
def build_grids() -> List[List[Grid]]:
    rows = rig.row_poses()
    return [[Grid(p, mirror=name == "running-left") for p in poses] for (name, _), poses in zip(ROW_SPEC, rows)]


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    grids = build_grids()
    frames = [[finish(g) for g in row] for row in grids]
    check_frames(grids, frames)
    atlas = Image.new("RGBA", (COLS * CELL_W, ROWS * CELL_H), (0, 0, 0, 0))
    for r, row in enumerate(frames):
        for c, rgba in enumerate(row):
            im = Image.fromarray(rgba).resize((W * SCALE, H * SCALE), Image.NEAREST)
            atlas.paste(im, (c * CELL_W, r * CELL_H + PAD_TOP))
    out = os.path.join(OUT_DIR, "spritesheet.webp")
    atlas.save(out, "WEBP", lossless=True, quality=100, method=6, exact=True)
    shutil.copy2(out, os.path.join(HERE, "spritesheet.webp"))
    print("wrote", out, "and copied it next to pet.json")


if __name__ == "__main__":
    main()
