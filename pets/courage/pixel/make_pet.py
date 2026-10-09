#!/usr/bin/env python3
"""Procedural pixel-art generator for the "Agallas" Codex desktop pet (v2).

Agallas is the Spanish name of Courage, the cowardly pink dog from
"Courage the Cowardly Dog". The base model is a 3/4 view facing left:
antenna-like ear stalks ending in black/rust leaf flaps, a long snout with a
huge black wedge nose (rust top band), one jelly-bean head+body with a cheek
bump, huge tall eyes with worried brows, wire-thin limbs with cross-shaped
hands and feet, black rear spots and a small pink star tail.

Each frame is drawn on a 64x69 logical canvas, outlined in near-black, wrapped
in a 1px light rim (for dark UIs) and scaled x3 with nearest-neighbour into a
192x208 atlas cell (one transparent padding row on top). The atlas is
8 columns x 9 rows = 1536x1872 px.

Usage:
    python3 -I make_pet.py [OUTPUT_DIR]
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Atlas geometry
# ---------------------------------------------------------------------------
W, H = 64, 69
SCALE = 3
CELL_W, CELL_H = 192, 208
PAD_TOP = CELL_H - H * SCALE  # 1 transparent row above each scaled frame
COLS, ROWS = 8, 9
ATLAS_W, ATLAS_H = COLS * CELL_W, ROWS * CELL_H  # 1536 x 1872
ANKLE_Y = 62  # ankle row when standing; toe bar on 63, toe nub on 64

ROW_SPEC = [
    ("idle", 6),
    ("running-right", 8),
    ("running-left", 8),
    ("waving", 4),
    ("jumping", 5),
    ("failed", 8),
    ("waiting", 6),
    ("running", 6),
    ("review", 6),
]

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
OUT = (34, 22, 30)
RIM = (255, 247, 242)          # light outer rim for dark backgrounds
PINK = (230, 162, 175)         # #E6A2AF dusty pink fur
PINK_D = (198, 126, 145)
PINK_L = (244, 196, 204)
LINE_PINK = (185, 112, 127)    # #B9707F interior detail lines (never black inside the body)
LINE_PINK_D = (169, 96, 111)   # #A9606F mouth / freckles
BRASS = (176, 122, 42)         # #B07A2A magnifier handle
BRASS_D = (128, 86, 26)
BLACK = (22, 16, 20)
BROWN = (176, 85, 42)          # #B0552A rust
WHITE = (255, 255, 255)
EYE_S = (222, 226, 238)
TEETH = (250, 248, 238)        # #FAF8EE
RED = (214, 44, 48)
RED_D = (140, 22, 36)
TONGUE = (240, 122, 142)
UVULA = (168, 40, 72)
FX = (206, 208, 214)           # light grey effect marks
SWEAT = (124, 196, 255)
SWEAT_L = (226, 246, 255)
YELLOW = (255, 210, 64)
DUST = (232, 226, 216)
GRAY = (150, 158, 172)
GRAY_D = (96, 102, 118)
GRAY_L = (204, 210, 222)
SCREEN = (24, 36, 48)
CODE = (96, 224, 160)
CODE2 = (120, 190, 255)
WOOD = (150, 96, 56)
WOOD_D = (110, 68, 40)
GOLD = (226, 172, 60)
GOLD_D = (170, 116, 36)
GLASS = (206, 238, 252)
TEETH_Y = (239, 234, 162)      # scream teeth #EFEAA2
TONGUE_B = (168, 72, 61)       # scream tongue #A8483D
TONGUE_LINE = (122, 46, 40)
ORANGE = (232, 135, 74)        # tongue outline #E8874A
UVULA_O = (242, 162, 58)       # uvula #F2A23A
RED_S = (232, 48, 42)          # scream mouth #E8302A
BEIGE = (230, 223, 192)        # CRT plastic #E6DFC0
BEIGE_D = (192, 182, 146)
BEIGE_DD = (150, 140, 108)
GLOW = (127, 224, 224)         # screen #7FE0E0
GLOW_D = (52, 150, 160)

YY, XX = np.mgrid[0:H, 0:W].astype(float)


# ---------------------------------------------------------------------------
# Raster primitives (boolean masks over the logical canvas)
# ---------------------------------------------------------------------------
def ellipse(cx, cy, rx, ry) -> np.ndarray:
    return ((XX - cx) / rx) ** 2 + ((YY - cy) / ry) ** 2 <= 1.0


def ellipse_rot(cx, cy, r_minor, r_major, lean_deg) -> np.ndarray:
    """Ellipse whose major axis points `lean_deg` clockwise from straight up."""
    t = math.radians(lean_deg)
    ux, uy = math.sin(t), -math.cos(t)
    vx, vy = -uy, ux
    px, py = XX - cx, YY - cy
    a = px * ux + py * uy
    b = px * vx + py * vy
    return (a / r_major) ** 2 + (b / r_minor) ** 2 <= 1.0


def capsule(p0, p1, r0, r1=None) -> np.ndarray:
    r1 = r0 if r1 is None else r1
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    t = np.zeros_like(XX) if l2 == 0 else np.clip(((XX - x0) * dx + (YY - y0) * dy) / l2, 0, 1)
    d = np.hypot(XX - (x0 + t * dx), YY - (y0 + t * dy))
    return d <= r0 + (r1 - r0) * t


def polygon(points) -> np.ndarray:
    img = Image.new("L", (W, H), 0)
    ImageDraw.Draw(img).polygon([(float(x), float(y)) for x, y in points], fill=1)
    return np.array(img, bool)


def wire(points, width: int = 1) -> np.ndarray:
    """Crisp 1px polyline (Bresenham via PIL)."""
    img = Image.new("L", (W, H), 0)
    pts = [(round(x), round(y)) for x, y in points]
    d = ImageDraw.Draw(img)
    if len(pts) == 1:
        d.point(pts, fill=1)
    else:
        d.line(pts, fill=1, width=width)
    return np.array(img, bool)


def wire2(points) -> np.ndarray:
    """2px-wide limb: each segment is thickened across its dominant direction."""
    m = np.zeros((H, W), bool)
    for a, b in zip(points, points[1:]):
        seg = wire([a, b])
        if abs(b[0] - a[0]) >= abs(b[1] - a[1]):
            m |= seg | look(seg, 0, -1)
        else:
            m |= seg | look(seg, -1, 0)
    return m


def rect(x0, y0, x1, y1) -> np.ndarray:
    return (XX >= x0) & (XX <= x1) & (YY >= y0) & (YY <= y1)


def look(m, dx, dy) -> np.ndarray:
    """out[y, x] = m[y + dy, x + dx] (False outside)."""
    out = np.zeros_like(m)
    out[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)] = \
        m[max(0, dy):H - max(0, -dy), max(0, dx):W - max(0, -dx)]
    return out


def dilate(m) -> np.ndarray:
    return m | look(m, 1, 0) | look(m, -1, 0) | look(m, 0, 1) | look(m, 0, -1)


def ik(a, b, l1, l2, prefer):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d >= l1 + l2 - 1e-3 or d < 1e-6:
        return None
    x = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(l1 * l1 - x * x, 0.0))
    ux, uy = dx / d, dy / d
    mx, my = a[0] + ux * x, a[1] + uy * x
    j1, j2 = (mx - uy * h, my + ux * h), (mx + uy * h, my - ux * h)
    s1 = (j1[0] - mx) * prefer[0] + (j1[1] - my) * prefer[1]
    s2 = (j2[0] - mx) * prefer[0] + (j2[1] - my) * prefer[1]
    return j1 if s1 >= s2 else j2


class Canvas:
    def __init__(self) -> None:
        self.px = np.zeros((H, W, 4), np.uint8)

    def put(self, mask, color) -> None:
        self.px[mask] = (*color, 255)

    def dot(self, x, y, color) -> None:
        x, y = int(round(x)), int(round(y))
        if 0 <= x < W and 0 <= y < H:
            self.px[y, x] = (*color, 255)

    def part(self, mask, color, outline=True, shade=None, light=None, depth=2, inner=OUT) -> None:
        """Fill a part. Its ring is OUT where it falls on transparency (silhouette) and
        `inner` where it falls on already-drawn pixels (interior detail line)."""
        if outline:
            ring = dilate(mask) & ~mask
            opaque = self.px[..., 3] > 0
            self.put(ring & opaque, inner)
            self.put(ring & ~opaque, OUT)
        self.put(mask, color)
        shadow = np.zeros_like(mask)
        if shade is not None:
            shadow = mask & ~(look(mask, 1, 0) & look(mask, 0, depth))
            self.put(shadow, shade)
        if light is not None:
            lit = mask & ~(look(mask, -1, 0) & look(mask, 0, -1)) & ~shadow
            self.put(lit, light)

    def glyph(self, rows, x0, y0, cmap) -> None:
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch in cmap:
                    self.dot(x0 + i, y0 + j, cmap[ch])

    def finish(self) -> np.ndarray:
        sil = self.px[..., 3] > 0
        coloured = sil & ~np.all(self.px[..., :3] == OUT, axis=-1)
        self.put(dilate(coloured) & ~sil, OUT)          # close the dark outline
        sil = self.px[..., 3] > 0
        self.put(dilate(sil) & ~sil, RIM)               # light outer rim
        return self.px


# ---------------------------------------------------------------------------
# Character model (base: 3/4 view facing left). Landmarks were measured from
# the reference sheet resampled to this canvas.
# ---------------------------------------------------------------------------
BEAN = [(29, 13.6), (33, 13.2), (37, 13.8), (40, 15.5), (42, 18), (42.6, 21), (43, 25),
        (43.5, 29), (44.3, 32.5), (45.4, 35.5), (46.4, 38.5), (47, 42), (47.1, 45.5),
        (46.7, 49), (45.8, 52.5), (44, 55.3), (41.5, 57), (37, 58), (32, 57.8), (29, 56.6),
        (27.2, 54.2), (26.4, 50.5), (26.3, 46), (26.6, 42.5), (27.2, 40), (22, 38), (21, 30),
        (20.8, 24), (21.5, 19), (23.5, 16), (26, 14.3)]
NOSE = [(10.6, 29.3), (15.5, 29.0), (20.5, 29.6), (27.6, 32.4), (21.6, 37.2), (16.4, 38.9),
        (13.4, 37.2), (11.0, 33.4)]
MOUTH_LINE = [(43.8, 22.0), (43.2, 23.6), (41.8, 24.8), (40.2, 25.6), (39.0, 25.8)]
OPEN_MOUTH = [(27.6, 36.0), (31.0, 36.4), (34.6, 35.6), (38.0, 33.6), (40.8, 30.6), (42.6, 27.0),
              (43.6, 28.8), (42.2, 33.2), (39.2, 36.8), (35.2, 38.8), (31.2, 39.2), (28.2, 38.4)]
STAR = ["..#..",
        "#####",
        ".###.",
        ".#.#."]
EAR_L = (-32, 5.2, -71, 13.4)   # stalk angle, stalk length, flap angle, flap length
EAR_R = (51, 7.4, 100, 13.4)
EAR_BASE_L = (29.8, 13.8)
EAR_BASE_R = (39.2, 15.8)
LEFT_EYE = (24.6, 20.8, 3.85, 6.05)
RIGHT_EYE = (33.6, 22.15, 4.0, 7.95)
SHOULDERS = ((32.5, 43.0), (36.5, 43.0))
HIPS = ((29.5, 56.5), (40.5, 56.5))
FEET = ((29.0, ANKLE_Y), (41.0, ANKLE_Y))


# Cross-shaped hands (pink fill, black outline), as on the model sheet.
HANDS = {
    "hang": (["..##..",            # wrist on top, lobes left/right/down
              "######",
              "######",
              "..##..",
              "..##.."], (2.0, 0.0)),
    "open": (["..##..",            # open four-lobe cross
              "######",
              "######",
              "..##.."], (2.5, 1.5)),
    "flower": (["..##..",          # rounded four-petal hand with a dark centre (scream)
                "..##..",
                "######",
                "######",
                "..##..",
                "..##.."], (2.5, 2.5)),
}
PLUM = (104, 46, 70)


def hand_mask(kind: str, x: float, y: float) -> np.ndarray:
    rows, (ax, ay) = HANDS[kind]
    x0, y0 = int(math.floor(x - ax + 0.5)), int(math.floor(y - ay + 0.5))
    m = np.zeros((H, W), bool)
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch == "#" and 0 <= y0 + j < H and 0 <= x0 + i < W:
                m[y0 + j, x0 + i] = True
    return m


def draw_hand(c: "Canvas", kind: str, x: float, y: float) -> None:
    m = hand_mask(kind, x, y)
    c.part(m, PINK, inner=OUT)
    ys, xs = np.where(m)
    c.dot(xs.max(), ys.max() - (1 if kind == "hang" else 0), PINK_D)
    if kind == "flower":
        cx, cy = int(math.floor(x + 0.5)), int(math.floor(y + 0.5))
        c.put(rect(cx - 1, cy - 1, cx, cy) & m, PLUM)


# Arm specs: (points in body coords from shoulder to hand, hand kind, drawn behind the body)
HANG_ARMS = (([(31, 41.5), (31, 47)], "hang", False), ([(38, 42), (38, 48)], "hang", False))


@dataclass
class Pose:
    gx: int = 0                  # whole character offset
    gy: int = 0
    hdx: int = 0                 # head offset relative to body
    hdy: int = 0
    bdy: int = 0                 # torso offset (crouch / bob)
    ears: tuple = (EAR_L, EAR_R)
    eyes: object = "open"        # open | wide | closed | squint, or (left, right)
    pupil: tuple = (0, 0)
    brows: int = 0               # extra lift of the brows in px
    mouth: str = "smile"         # smile | grit | open | o | scream | none
    uvula: int = 0
    arms: tuple = HANG_ARMS      # see HANG_ARMS
    legs: Optional[tuple] = None  # ((ankle, ...), ...) absolute; None = standing
    legs_air: bool = False
    tail_dy: int = 0
    nose_dy: int = 0
    pre: list = field(default_factory=list)     # drawn before the character
    extras: list = field(default_factory=list)  # drawn on top


def blend_offset(y: float, top: float = 36.0, bottom: float = 46.0) -> float:
    """Weight of the head offset for a point at height y (1 on the head, 0 on the legs)."""
    if y <= top:
        return 1.0
    if y >= bottom:
        return 0.0
    return 1.0 - (y - top) / (bottom - top)


def draw_ear(c: Canvas, base, spec, flip_brown: bool) -> None:
    sa, sl, fa, fl = spec
    a = math.radians(sa)
    end = (base[0] + math.sin(a) * sl, base[1] - math.cos(a) * sl)
    c.put(wire([base, end]), OUT)
    f = math.radians(fa)
    tip = (end[0] + math.sin(f) * fl, end[1] - math.cos(f) * fl)
    mid = ((end[0] + tip[0]) / 2, (end[1] + tip[1]) / 2)
    flap = ellipse_rot(mid[0], mid[1], 1.75, fl / 2, fa)
    c.part(flap, BLACK)
    # rust inner face, offset to one side of the leaf
    nx, ny = math.cos(f), math.sin(f)  # perpendicular to the flap direction
    s = -0.6 if flip_brown else 0.6
    inner = ellipse_rot(mid[0] + nx * s, mid[1] + ny * s, 0.95, fl / 2 * 0.72, fa)
    c.put(inner & flap, BROWN)


def cross(cx: float, cy: float) -> np.ndarray:
    x, y = int(round(cx)), int(round(cy))
    return rect(x - 1, y, x + 1, y) | rect(x, y - 1, x, y + 1)


def foot(cx: float, ay: float) -> np.ndarray:
    x, y = int(round(cx)), int(round(ay))
    return rect(x - 2, y + 1, x + 2, y + 1) | rect(x, y, x, y + 2)


def draw_character(p: Pose) -> np.ndarray:
    c = Canvas()
    gx, gy = p.gx, p.gy

    def Hd(x, y):  # head-space point
        return (x + gx + p.hdx, y + gy + p.hdy + p.bdy)

    def Bd(x, y):  # body-space point
        return (x + gx, y + gy + p.bdy)

    ctx = {"p": p, "Hd": Hd, "Bd": Bd, "hands": []}
    for fn in p.pre:
        fn(c, ctx)

    scream = p.mouth == "scream"

    # Ears: thin stalks behind the head, ending in leaf flaps
    draw_ear(c, Hd(*EAR_BASE_L), p.ears[0], flip_brown=False)
    draw_ear(c, Hd(*EAR_BASE_R), p.ears[1], flip_brown=True)

    # Star tail behind the rear
    tx, ty = Bd(47.5, 44.5 + p.tail_dy)
    star = np.zeros((H, W), bool)
    for j, row in enumerate(STAR):
        for i, ch in enumerate(row):
            if ch == "#":
                xx, yy = int(round(tx)) + i, int(round(ty)) + j
                if 0 <= xx < W and 0 <= yy < H:
                    star[yy, xx] = True
    c.part(star, PINK)

    # Wire legs with cross feet
    for k in range(2):
        hip = Bd(*HIPS[k])
        if p.legs is not None:
            ankle = (p.legs[k][0] + gx, p.legs[k][1])
        elif p.legs_air:
            ankle = (hip[0] + (-0.5 if k == 0 else 0.5), hip[1] + 5.0)
        else:
            ankle = (FEET[k][0] + gx, ANKLE_Y)
        knee = ik(hip, ankle, 3.3, 3.3, (-1, 0))
        pts = [hip, ankle] if knee is None else [hip, knee, ankle]
        c.part(wire(pts) | foot(*ankle), PINK)

    ctx["hands"] = [Bd(*spec[0][-1]) for spec in p.arms]

    def draw_arm(spec) -> None:
        pts, kind, _behind = spec
        pts = [Bd(*q) for q in pts]
        c.put(wire2(pts), LINE_PINK_D)          # thin darker-fur limb, never black inside
        if kind != "none":
            draw_hand(c, kind, *pts[-1])

    for spec in p.arms:
        if spec[2]:
            draw_arm(spec)

    # Jelly-bean head + body, muzzle and cheek bump
    pts = []
    for x, y in BEAN:
        w = blend_offset(y)
        pts.append((x + gx + p.hdx * w, y + gy + p.bdy + p.hdy * w))
    bean = polygon(pts)
    bean |= ellipse(*Hd(22.2, 32.6), 11.5, 7.2)        # snout bridge / muzzle
    bean |= ellipse(*Hd(44.0, 23.3), 2.2, 2.3)          # cheek bump
    if scream:
        bean |= ellipse(*Hd(30.5, 45.5), 13.5, 11.0)    # dropped jaw
    c.part(bean, PINK, shade=PINK_D, light=PINK_L)

    # Rear spots (one with a pink oval inside)
    inside = bean & look(bean, 1, 0) & look(bean, -1, 0) & look(bean, 0, 1) & look(bean, 0, -1)
    sp1 = ellipse(*Bd(43.4, 37.0), 1.5, 1.9)
    sp2 = ellipse(*Bd(43.8, 42.2), 2.0, 2.5)
    c.put(inside & (sp1 | sp2), BLACK)

    # Mouth
    if scream:
        mcx, mcy = Hd(30.5, 45.5)
        mo = ellipse(mcx, mcy, 10.5, 8.0)
        c.part(mo, RED)
        c.put(mo & ellipse(mcx + 3.0, mcy + 1.5, 6.0, 5.0), RED_D)
        top = int(np.where(mo.any(axis=1))[0].min())
        bot = int(np.where(mo.any(axis=1))[0].max())
        for fx in (-7, -2, 3, 8):           # upper fangs
            x = int(round(mcx)) + fx
            c.put(mo & (rect(x, top, x + 1, top + 1) | rect(x, top + 2, x, top + 2)), TEETH)
        for fx in (-4, 1, 6):               # lower fangs
            x = int(round(mcx)) + fx
            c.put(mo & (rect(x, bot - 1, x + 1, bot) | rect(x, bot - 2, x, bot - 2)), TEETH)
        ux = int(round(mcx)) + 5 + p.uvula
        c.put(rect(ux, top + 3, ux + 1, top + 4), UVULA)
        tongue = [(mcx + 1, mcy + 4.5), (mcx - 5, mcy + 6.0), (mcx - 10, mcy + 8.5), (mcx - 14, mcy + 7.5),
                  (mcx - 16, mcy + 5.0 + p.uvula * 0.5)]
        tm = capsule(tongue[0], tongue[1], 1.6) | capsule(tongue[1], tongue[2], 1.6) | \
            capsule(tongue[2], tongue[3], 1.5) | capsule(tongue[3], tongue[4], 1.3)
        c.part(tm, TONGUE)
        c.put(wire(tongue[1:4]) & tm, RED)
    elif p.mouth != "none":
        if p.mouth in ("smile", "grit"):
            c.put(wire([Hd(x, y) for x, y in MOUTH_LINE]), LINE_PINK_D)
        if p.mouth == "open":
            om = polygon([Hd(x, y) for x, y in OPEN_MOUTH])
            c.part(om, RED)
            c.put(om & ellipse(*Hd(38.5, 33.0), 3.2, 2.6), RED_D)
            top_edge = om & ~look(om, 0, -1)
            c.put(top_edge & (XX < Hd(40.5, 0)[0]), TEETH)
            low = om & ~look(om, 0, 1)
            c.put(low & ~top_edge & (XX > Hd(29.5, 0)[0]) & (XX < Hd(39.0, 0)[0]), TONGUE)
        elif p.mouth == "grit":
            x0, y0 = Hd(36, 29)
            x0, y0 = int(round(x0)), int(round(y0))
            g = rect(x0, y0, x0 + 6, y0 + 4) & ~rect(x0, y0, x0, y0) & ~rect(x0 + 6, y0 + 4, x0 + 6, y0 + 4)
            c.part(g, TEETH)
            c.put(g & (YY == y0 + 2), OUT)
            c.put(g & ((XX == x0 + 2) | (XX == x0 + 4)), OUT)
        elif p.mouth == "o":
            om = ellipse(*Hd(25.5, 38.4), 2.4, 1.5)    # little yelp at the jaw, under the snout
            c.part(om, RED_D)
            c.put(om & ~look(om, 0, 1), TONGUE)
        # muzzle freckles and whisker
        if p.mouth in ("smile", "grit"):
            for fx, fy in ((28, 35), (26, 37), (29, 37)):
                c.dot(*Hd(fx, fy), LINE_PINK_D)
            c.put(wire([Hd(31, 36), Hd(34, 36.4)]), LINE_PINK_D)
        else:
            c.dot(*Hd(26, 35.5), LINE_PINK_D)

    # Snout tip with a dot and whiskers, then the huge wedge nose
    c.part(ellipse(*Hd(9.3, 34 + p.nose_dy), 3.3, 3.0), PINK, shade=PINK_D, depth=1, inner=LINE_PINK)
    c.dot(*Hd(9, 34 + p.nose_dy), OUT)
    c.put(wire([Hd(6, 32.5 + p.nose_dy), Hd(3.6, 31.4 + p.nose_dy)]), OUT)
    c.put(wire([Hd(6, 35.2 + p.nose_dy), Hd(3.6, 36.2 + p.nose_dy)]), OUT)
    nose = polygon([Hd(x, y + p.nose_dy) for x, y in NOSE])
    c.part(nose, BLACK)
    nx0, ny0 = Hd(11.0, 31.2 + p.nose_dy)
    band = nose & (YY < ny0 + (XX - nx0) * 0.06) & look(nose, 0, -1) & (XX < nx0 + 13.5)
    c.put(band, BROWN)

    # Eyes: huge tall ovals, worried brows, lower-lid lines
    kinds = p.eyes if isinstance(p.eyes, tuple) else (p.eyes, p.eyes)
    for k, (eye, kind) in enumerate(zip((LEFT_EYE, RIGHT_EYE), kinds)):
        ex, ey, rx, ry = eye
        wide = kind == "wide" or scream
        if wide:
            rx, ry = rx + 0.4, ry + 0.5
        cx_, cy_ = Hd(ex, ey)
        em = ellipse(cx_, cy_, rx, ry)
        if kind == "closed":
            c.part(em, PINK)
            lid_y = int(round(cy_ + ry * 0.35))
            c.put(em & (YY == lid_y), OUT)
            c.put(em & (YY == lid_y - 1) & ((XX < cx_ - rx + 1.5) | (XX > cx_ + rx - 1.5)), OUT)
            continue
        c.part(em, WHITE)
        # pupil (towards the snout, slightly low)
        ox, oy = p.pupil
        if wide:
            px0 = int(round(cx_ - 0.5)) + ox
            py0 = int(round(cy_ - 0.5)) + oy
            c.put(rect(px0, py0, px0, py0 + 1) & em, BLACK)
        else:
            px0 = int(round(cx_ + 0.4 - 1)) + ox
            py0 = int(round(cy_ + 0.6 - 1)) + oy
            c.put(rect(px0, py0, px0 + 1, py0 + 1) & em, BLACK)
        if kind == "squint":
            lid_y = int(round(cy_ - 0.5))
            c.put(em & (YY < lid_y), PINK_D)
            c.put(em & (YY == lid_y), OUT)
    lift = p.brows + (2 if scream else 0)
    c.put(wire([Hd(21, 16.2 - lift), Hd(24, 14.6 - lift), Hd(27.6, 13.2 - lift)]), OUT)
    c.dot(*Hd(27.6, 12.4 - lift), OUT)
    c.put(wire([Hd(33.2, 12.6 - lift), Hd(35.8, 13.4 - lift), Hd(38.0, 15.6 - lift)]), OUT)
    c.dot(*Hd(33.2, 11.8 - lift), OUT)

    for spec in p.arms:
        if not spec[2]:
            draw_arm(spec)

    for fn in p.extras:
        fn(c, ctx)
    return c.finish()


# ---------------------------------------------------------------------------
# Effects and props
# ---------------------------------------------------------------------------
def sweat(x: int, y: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        bx, by = ctx["Hd"](x, y)
        c.glyph(["..s..", ".sss.", ".lss.", "..s.."], int(round(bx)) - 2, int(round(by)),
                {"s": SWEAT, "l": SWEAT_L})
    return fn


def clasp() -> Callable:
    """Compact clasped paws in front of the chest."""
    def fn(c: Canvas, ctx) -> None:
        cx, cy = ctx["Bd"](35.0, 39.6)
        c.part(ellipse(cx, cy, 2.6, 1.9), PINK, shade=PINK_D, depth=1, inner=LINE_PINK)
        c.dot(cx, cy - 0.5, LINE_PINK)
    return fn


def bang(x: int, y: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        c.glyph(["##", "##", "##", "##", "..", "##"], x, y, {"#": YELLOW})
    return fn


def bubble(dy: int) -> Callable:
    rows = ["..ooooooo..",
            ".owwwwwwwo.",
            "owwwkkkwwwo",
            "owwkwwwkwwo",
            "owwwwwwkwwo",
            "owwwwwkwwwo",
            "owwwwkwwwwo",
            "owwwwwwwwwo",
            "owwwwkwwwwo",
            ".owwwwwwwo.",
            "..owoooooo.",
            ".oo........"]

    def fn(c: Canvas, ctx) -> None:
        c.glyph(rows, 50, 3 + dy, {"o": OUT, "w": WHITE, "k": OUT})
    return fn


def vibration(phase: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        for k, (x, y) in enumerate(((6, 42), (8, 50))):
            yy = y + (phase if k == 0 else -phase)
            c.glyph([".#", "#.", "#.", ".#"], x, yy, {"#": FX})
            c.glyph(["#.", ".#", ".#", "#."], 63 - x - 1 + (k * 0), yy, {"#": FX})
    return fn


def speed_lines(phase: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        for k, y in enumerate((40, 46, 52)):
            length = 3 + ((phase + k * 3) % 4)
            x0 = 52 + ((phase + k) % 3)
            c.put(rect(x0, y, x0 + length, y), FX)
    return fn


def dust(xs) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        for x in xs:
            puff = ellipse(x, 62.4, 2.5, 1.8) | ellipse(x + 2.8, 63.0, 1.8, 1.3)
            c.put(puff, (198, 198, 204))
            c.put(puff & ~look(puff, 0, -1) | (puff & ~look(puff, -1, 0)), (228, 228, 232))
    return fn


def laptop(frame: int) -> Callable:
    """Little laptop on a stool, screen facing Agallas, code scrolling."""
    def fn(c: Canvas, ctx) -> None:
        # stool
        c.part(rect(3, 53, 25, 54), WOOD, shade=WOOD_D, depth=1)
        c.part(rect(5, 55, 6, 63) | rect(22, 55, 23, 63), WOOD_D)
        # keyboard base and lid
        c.part(rect(8, 51, 24, 52), GRAY, light=GRAY_L)
        c.part(rect(5, 39, 16, 50), GRAY_D)
        c.put(rect(6, 40, 15, 49), SCREEN)
        for k, y in enumerate(range(41, 49, 2)):
            n = 2 + ((frame + k * 2) % 6)
            indent = (k % 2) * 2
            c.put(rect(7 + indent, y, min(14, 7 + indent + n), y), CODE if k % 2 == 0 else CODE2)
        if frame % 2 == 0:
            c.put(rect(14, 47, 14, 48), WHITE)  # blinking cursor
        c.put(rect(9, 51, 23, 51) & (XX % 2 == 0), GRAY_D)  # keys
    return fn


def key_spark(active: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        hx, hy = ctx["hands"][active]
        x, y = int(round(hx)) - 1, int(round(hy)) - 4
        c.put(rect(x, y - 1, x, y + 1) | rect(x - 1, y, x + 1, y), YELLOW)
    return fn


def magnifier(look_dx: int) -> Callable:
    """Magnifying glass over the near eye, showing a huge scanning eye."""
    def fn(c: Canvas, ctx) -> None:
        lx, ly = ctx["Hd"](RIGHT_EYE[0], RIGHT_EYE[1] - 1)
        hand = ctx["Hd"](50.5, 22.0)                       # grip just outside the cheek
        start, tail = (lx + 6.0, ly + 1.2), (hand[0] + 2.8, hand[1] + 1.0)
        handle = capsule(start, tail, 0.9)
        c.put(wire2([ctx["Bd"](46.0, 35.0), (hand[0] + 0.5, hand[1] + 2.5)]), LINE_PINK_D)  # short arm
        c.part(handle, BRASS, shade=BRASS_D, depth=1, inner=BRASS_D)
        c.part(ellipse(lx, ly, 6.2, 6.2), GOLD, shade=GOLD_D, depth=1)
        c.put(ellipse(lx, ly, 5.0, 5.0), GLASS)
        eye = ellipse(lx, ly, 4.0, 4.4)
        c.part(eye, WHITE)
        px0 = int(round(lx - 1.0)) + look_dx
        py0 = int(round(ly - 1.0))
        c.put(rect(px0, py0, px0 + 2, py0 + 2) & eye, BLACK)
        c.dot(lx - 3, ly - 4, WHITE)
        c.dot(lx - 4, ly - 3, WHITE)
        # fingers over the handle: the hand is drawn on top, the handle shows in the notches
        hm = hand_mask("open", *hand)
        draw_hand(c, "open", *hand)
        ring = dilate(hm) & ~hm
        c.put(handle & ring, BRASS)
    return fn


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def ear(spec, d_stalk=0.0, d_flap=0.0, k_stalk=1.0, k_flap=1.0):
    return (spec[0] + d_stalk, spec[1] * k_stalk, spec[2] + d_flap, spec[3] * k_flap)


def row_idle():
    breath = [0, 0, 1, 1, 0, 0]
    pupils = [(0, 0), (0, 0), (-1, 0), (-1, 0), (0, 0), (1, 0)]
    twitch = [0, 0, 4, 4, 0, -3]
    frames = []
    for i in range(6):
        frames.append(Pose(
            hdy=breath[i], pupil=pupils[i], eyes="closed" if i == 4 else "open",
            ears=(ear(EAR_L, d_flap=twitch[i]), ear(EAR_R, d_flap=-twitch[i])),
            tail_dy=1 if i in (2, 3) else 0))
    return [draw_character(p) for p in frames]


RUN_STEP = [(-4.0, 0.0), (-2.0, 0.0), (0.0, 0.0), (2.5, 0.6),
            (4.0, 2.6), (2.0, 4.0), (-1.5, 3.6), (-3.5, 1.6)]
RUN_BOB = [0, 1, 0, -1, 0, 1, 0, -1]


def row_run_left():
    frames = []
    for i in range(8):
        b = RUN_BOB[i]
        legs = []
        for k in range(2):
            dx, lift = RUN_STEP[(i + 4 * k) % 8]
            legs.append((HIPS[k][0] + dx, ANKLE_Y - lift))
        swing = RUN_STEP[i][0]
        arms = []
        for sh, sgn in (((33, 42), 1), ((37, 42), -1)):
            hx = 35 + 3.8 * swing * sgn
            hy = 44.5 - 0.25 * abs(swing)
            if hx >= 35:                                       # back arm: behind the body
                arms.append(([sh, (hx, hy)], "open", True))
            else:                                              # forward arm: from the front edge, short
                fx, fy = 26.0 - 2.2 * abs(swing), 45.0 + 0.25 * abs(swing)
                arms.append(([(28.0, 42.5), (fx, fy)], "open", False))
        arms = tuple(arms)
        flap = [0, 6, 10, 6, 0, -6, -10, -6][i]
        extras = [speed_lines(i)]
        if i in (0, 4):
            back = legs[1 if i == 0 else 0][0]
            extras.append(dust([max(legs[0][0], legs[1][0]) + 4]))
        if i in (2, 6):
            extras.append(sweat(47, 18 - (i // 6)))
        frames.append(Pose(
            gy=b, hdx=-1, eyes="wide", pupil=(-1, 0), mouth="grit",
            ears=((22, 4.5, 85 + flap, 11.5), (62, 5.5, 108 - flap, 11.5)),
            legs=tuple(legs), arms=arms, tail_dy=-b, extras=extras))
    return [draw_character(p) for p in frames]


def row_run_right():
    return [np.ascontiguousarray(f[:, ::-1]) for f in row_run_left()]


def row_waving():
    elbow = (49.0, 34.5)
    hands = [(50, 28), (53, 28.5), (55, 30), (53, 28.5)]
    frames = []
    for i in range(4):
        frames.append(Pose(
            mouth="open", pupil=(1, 0),
            ears=(ear(EAR_L, d_flap=4 * (i % 2)), ear(EAR_R, d_flap=-4 * (i % 2))),
            arms=(HANG_ARMS[0], ([(46, 38.5), elbow, hands[i]], "open", False)),
            tail_dy=-(i % 2)))
    return [draw_character(p) for p in frames]


def row_jumping():
    frames = [
        Pose(hdy=1, bdy=2, eyes="wide", mouth="grit",
             ears=((-40, 4.0, -100, 12.0), (60, 5.5, 118, 12.0)),
             arms=(([(31, 41.5), (31, 45)], "hang", False), ([(38, 42), (38, 45.5)], "hang", False))),
        Pose(gy=-3, legs_air=True, eyes="wide", mouth="o",
             ears=((-45, 4.0, -125, 11.0), (62, 5.0, 130, 11.0)),
             arms=(([(27, 44), (17, 45.5)], "open", False), ([(46.6, 41.5), (52, 37)], "open", False)),
             extras=[bang(9, 14)]),
        Pose(gy=-5, legs_air=True, eyes="wide", mouth="open", brows=1,
             ears=((-55, 4.0, -95, 11.0), (60, 4.5, 95, 11.0)),
             arms=(([(27, 43.5), (17, 47)], "open", False), ([(46.6, 40), (50.5, 36), (52, 31)], "open", False)),
             extras=[sweat(15, 18), sweat(50, 22)]),
        Pose(gy=-2, legs_air=True, eyes="wide", mouth="o",
             ears=((-28, 3.5, -60, 8.5), (38, 3.5, 62, 8.5)),
             arms=(([(27, 44.5), (17.5, 45)], "open", False), ([(46.6, 41), (52, 35)], "open", False))),
        Pose(hdy=1, bdy=1, mouth="grit",
             ears=(ear(EAR_L, d_flap=-10), ear(EAR_R, d_flap=12)),
             extras=[dust([21, 46])]),
    ]
    return [draw_character(p) for p in frames]


SCREAM_LIMBS = [(13, 21), (8, 28), (56, 40), (56, 51), (12, 59), (36, 61)]


def draw_scream(i: int) -> np.ndarray:
    """The iconic scream: one bristling fur ball, huge eyes, stiff spike ears,
    a huge red mouth with pale yellow teeth and a long wavy tongue."""
    c = Canvas()
    gx = [-1, 1, -1, 1, 0, 1, -1, 1][i]
    gy = [0, 1, 0, 1, 1, 0, 1, 0][i]

    def P(x, y):
        return (x + gx, y + gy)

    bcx, bcy = P(29.0, 43.0)

    # Radial wire limbs with cross hands/feet (behind the ball)
    for k, (hx, hy) in enumerate(SCREAM_LIMBS):
        jit = 1 if (i + k) % 2 else 0
        hand = P(hx + (jit if k % 2 else -jit) * 0.0, hy + (jit if k in (2, 3) else 0))
        c.part(wire([(bcx, bcy), hand]), PINK)
        draw_hand(c, "flower", *hand)

    # Stiff spike ears pointing up and right
    quiver = [0, 2, -1, 3, -2, 1, -3, 2][i]
    for base, tip, minor in (((36.4, 15.8), (42.0 + quiver * 0.3, 4.6), 1.5),
                             ((38.3, 26.2), (55.4, 12.8 + quiver * 0.3), 1.9)):
        bx, by = P(*base)
        tx, ty = P(*tip)
        lean = math.degrees(math.atan2(tx - bx, -(ty - by)))
        half = math.hypot(tx - bx, ty - by) / 2
        mx, my = (bx + tx) / 2, (by + ty) / 2
        blade = ellipse_rot(mx, my, minor, half, lean)
        c.part(blade, BLACK)
        t = math.radians(lean)
        nx, ny = -math.cos(t), -math.sin(t)  # towards the upper-left side of the blade
        inner = ellipse_rot(mx + nx * 0.6, my + ny * 0.6, minor * 0.55, half * 0.75, lean)
        c.put(inner & blade, BROWN)

    # Bristling ball: smooth around the face, jagged spikes on the lower/right edge
    pts = []
    n = 40
    shift = 0.5 if i % 2 else 0.0
    for v in range(n):
        ang = (v + shift) * 2 * math.pi / n
        deg = math.degrees(ang)
        spiky = deg <= 170 or deg >= 315
        if spiky and v % 2 == 1:
            r = 17.0 + ((v * 7 + i * 3) % 5) * 0.45
        else:
            r = 14.2
        pts.append((bcx + r * math.cos(ang), bcy + r * math.sin(ang)))
    ball = polygon(pts)
    ball |= ellipse(*P(30.0, 30.0), 12.0, 10.0)      # head around the eyes
    ball |= ellipse(*P(18.0, 37.5), 5.5, 3.6)        # snout bridge
    c.part(ball, PINK, shade=PINK_D)

    # Huge red mouth toward the lower right
    mcx, mcy = P(29.5, 46.0)
    mo = ellipse(mcx, mcy, 10.0, 9.0)
    c.part(mo, RED_S)
    # orange "U" uvula at the back
    c.glyph(["#..#", "#..#", ".##."], int(round(mcx)) + 4, int(round(mcy)) - 6, {"#": UVULA_O})
    # big rounded pale-yellow teeth: dangling uppers and a lower row
    for tx_, ty_, rx_, ry_ in ((21.8, 42.6, 1.8, 2.9), (25.6, 43.2, 1.6, 2.0), (29.8, 39.4, 1.5, 1.7),
                               (21.6, 53.0, 1.6, 1.6), (26.8, 53.4, 1.8, 1.6), (31.8, 51.8, 1.5, 1.6)):
        c.part(ellipse(*P(tx_, ty_), rx_, ry_), TEETH_Y)

    # Snout tip and the wedge nose on the left
    c.part(ellipse(*P(12.6, 37.8), 3.1, 2.6), PINK, shade=PINK_D, depth=1)
    c.dot(*P(11.6, 38.0), OUT)
    c.put(wire([P(9.6, 36.6), P(7.6, 35.6)]) | wire([P(9.6, 38.8), P(7.6, 39.8)]), OUT)
    nose = polygon([P(x, y) for x, y in ((13.6, 33.6), (20.0, 33.6), (24.6, 35.6), (19.6, 39.8),
                                          (16.0, 39.4), (13.6, 36.4))])
    c.part(nose, BLACK)
    c.put(nose & (YY <= P(0, 35.0)[1]) & (XX < P(22.6, 0)[0]) & look(nose, 0, -1), BROWN)

    # Huge eyes with tiny pupils
    for ex, ey, rx, ry, lean, (px, py) in ((22.4, 24.6, 4.8, 10.0, -10, (21.4, 21.6)),
                                           (32.2, 25.6, 6.4, 11.0, -10, (31.0, 21.0))):
        em = ellipse_rot(*P(ex, ey), rx, ry, lean)
        c.part(em, WHITE)
        qx, qy = P(px, py)
        c.put(rect(int(round(qx)), int(round(qy)), int(round(qx)) + 1, int(round(qy)) + 2) & em, BLACK)
        c.dot(qx + 1, qy, WHITE)

    # Tremble ticks at the limb bases
    for k, (hx, hy) in enumerate(SCREAM_LIMBS):
        hx, hy = P(hx, hy)
        dx, dy = hx - bcx, hy - bcy
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        r0 = 18.5 if k >= 2 else 14.0
        bx, by = bcx + ux * r0, bcy + uy * r0
        side = 1 if (i + k) % 2 else -1
        ox, oy = -uy * 2.2 * side, ux * 2.2 * side
        c.put(wire([(bx + ox, by + oy), (bx + ox + ux * 1.6, by + oy + uy * 1.6)]), OUT)

    if i in (1, 2, 5, 6):
        sx, sy = P(13 if i in (1, 5) else 47, 12 + (i % 2))
        c.glyph(["..s..", ".sss.", ".lss.", "..s.."], int(round(sx)) - 2, int(round(sy)),
                {"s": SWEAT, "l": SWEAT_L})

    # Long wavy tongue, drawn last so nothing crosses it
    phase = i * math.pi / 4
    samples = []
    x = 31.0
    while x >= 8.5:
        along = 31.0 - x
        amp = 1.7 * min(1.0, along / 6.0)
        y = 48.5 + amp * math.sin(along * 0.5 - phase)
        samples.append(P(x, y))
        x -= 0.5
    tongue = np.zeros((H, W), bool)
    for a, b in zip(samples, samples[1:]):
        t = (31.0 - (a[0] - gx)) / 22.5
        tongue |= capsule(a, b, 2.1 - 0.5 * t)
    c.put(dilate(tongue) & ~tongue, ORANGE)
    c.put(tongue, TONGUE_B)
    mid = samples[6:-4]
    c.put(wire(mid) & tongue, TONGUE_LINE)
    return c.finish()


def row_failed():
    return [draw_scream(i) for i in range(8)]


def row_waiting():
    tremble = [0, 1, 0, -1, 0, 1]
    bob = [0, -1, -1, 0, 0, -1]
    frames = []
    for i in range(6):
        frames.append(Pose(
            gx=tremble[i], pupil=(0, -3), mouth="grit", brows=1,
            ears=((-40, 4.5, -122, 11.0), (60, 6.0, 145, 11.0)),
            arms=(([(28.5, 47.5), (29.5, 45.5), (31.5, 43)], "open", False),
                  ([(40.5, 47.5), (39.5, 45.5), (38.5, 43)], "open", False)),
            tail_dy=1, extras=[bubble(bob[i])]))
    return [draw_character(p) for p in frames]


COMPUTER_OX = 3


def computer(frame: int) -> Callable:
    """Chunky beige CRT on a desktop case, glowing teal screen, beige keyboard, desk."""
    def fn(c0: Canvas, ctx) -> None:
        c = Canvas()
        # desk (top surface + front edge + leg)
        c.part(rect(0, 47, 33, 48), WOOD, shade=WOOD_D, depth=1)
        c.part(rect(2, 50, 3, 63) | rect(29, 50, 30, 63), WOOD_D)
        # desktop case under the monitor
        case = rect(1, 37, 19, 45)
        c.part(case, BEIGE, shade=BEIGE_D, light=WHITE, depth=1)
        c.put(rect(11, 40, 17, 40), BEIGE_DD)
        c.put(rect(3, 42, 5, 43), BEIGE_DD)
        # CRT: bulky back on the left, screen face on the right
        back = polygon([(1, 19), (10, 15), (10, 35), (1, 32)])
        c.part(back, BEIGE_D, shade=BEIGE_DD, depth=1)
        bezel = rect(9, 14, 17, 36)
        c.part(bezel, BEIGE, shade=BEIGE_D, light=WHITE, depth=1)
        screen = rect(11, 16, 16, 32) & ~rect(11, 16, 11, 16) & ~rect(16, 32, 16, 32)
        c.put(screen, GLOW)
        for k, y in enumerate(range(18, 31, 2)):
            n = 1 + ((frame + k * 3) % 4)
            c.put(rect(12, y, min(15, 12 + n), y), GLOW_D)
        c.put(rect(15, 16, 16, 17), WHITE)  # glare
        # chunky keyboard
        kb = polygon([(15, 44.5), (33, 44.5), (34, 46.5), (14, 46.5)])
        c.part(kb, BEIGE, shade=BEIGE_D, depth=1)
        c.put(kb & (YY == 45) & (XX % 2 == 1) & (XX > 15) & (XX < 33), BEIGE_DD)
        # translate the whole prop layer right by COMPUTER_OX and lay it under the character
        layer = np.roll(c.px, COMPUTER_OX, axis=1)
        layer[:, :COMPUTER_OX] = 0
        m = layer[..., 3] > 0
        c0.px[m] = layer[m]
    return fn


def tick_burst(active: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        hx, hy = ctx["hands"][active]
        x, y = int(round(hx)), int(round(hy)) - 4
        c.put(rect(x, y - 2, x, y - 1) | wire([(x - 3, y - 1), (x - 2, y)]) | wire([(x + 3, y - 1), (x + 2, y)]),
              YELLOW)
    return fn


def row_working():
    frames = []
    for i in range(6):
        active = i % 2
        far_hand = (20.5, 43 if active == 0 else 44)
        near_hand = (27.5, 43 if active == 1 else 44)
        extras = [tick_burst(active)]
        if i in (2, 3):
            extras.append(sweat(50, 14 + (i - 2)))
        pose = Pose(
            gx=5, hdy=1, bdy=1, pupil=(-2, -1), mouth="grit", brows=1,
            ears=((-5 + 3 * active, 4.0, 70, 10.0), (35, 4.0, 80 - 4 * active, 9.0)),
            arms=(([(36, 44), far_hand], "open", True), ([(33, 42), near_hand], "open", False)),
            pre=[computer(i)], extras=extras)
        f = draw_character(pose)
        frames.append(np.ascontiguousarray(f[:, ::-1]))   # face right, toward the monitor
    return frames


def row_review():
    scan = [-1, 0, 1, 1, 0, -1]
    frames = []
    for i in range(6):
        frames.append(Pose(
            hdx=scan[i], eyes=("squint", "open"), pupil=(scan[i], 0),
            ears=(ear(EAR_L, d_flap=6), EAR_R),
            arms=(HANG_ARMS[0],),
            extras=[magnifier(scan[i] * 2)]))
    return [draw_character(p) for p in frames]


ROW_BUILDERS = [row_idle, row_run_right, row_run_left, row_waving, row_jumping,
                row_failed, row_waiting, row_working, row_review]


# ---------------------------------------------------------------------------
# Atlas, previews, validation
# ---------------------------------------------------------------------------
def to_cell(frame: np.ndarray) -> Image.Image:
    cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    big = Image.fromarray(frame, "RGBA").resize((W * SCALE, H * SCALE), Image.NEAREST)
    cell.paste(big, (0, PAD_TOP))
    return cell


def build_rows():
    rows = []
    for (name, count), builder in zip(ROW_SPEC, ROW_BUILDERS):
        frames = builder()
        assert len(frames) == count, (name, len(frames), count)
        rows.append(frames)
    return rows


def margin_report(rows) -> list:
    issues = []
    for (name, _), frames in zip(ROW_SPEC, rows):
        for i, f in enumerate(frames):
            a = f[..., 3] > 0
            if a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any():
                issues.append(f"{name}[{i}] touches the canvas edge")
    return issues


def make_atlas(rows) -> Image.Image:
    atlas = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    for r, frames in enumerate(rows):
        for col, f in enumerate(frames):
            atlas.paste(to_cell(f), (col * CELL_W, r * CELL_H))
    return atlas


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_preview(atlas: Image.Image, path: str) -> None:
    label_w = 150
    prev = Image.new("RGBA", (ATLAS_W + label_w, ATLAS_H), (128, 128, 128, 255))
    d = ImageDraw.Draw(prev)
    f = font(24)
    for r, (name, count) in enumerate(ROW_SPEC):
        y = r * CELL_H
        d.text((8, y + CELL_H // 2 - 24), f"{r} {name}", fill=(255, 255, 255, 255), font=f)
        d.text((8, y + CELL_H // 2 + 6), f"{count} frames", fill=(225, 225, 225, 255), font=f)
        d.line([(0, y), (ATLAS_W + label_w, y)], fill=(105, 105, 105, 255))
    for col in range(COLS + 1):
        x = label_w + col * CELL_W
        d.line([(x, 0), (x, ATLAS_H)], fill=(105, 105, 105, 255))
    prev.alpha_composite(atlas, (label_w, 0))
    prev.convert("RGB").save(path)


def make_vs_reference(rows, ref_path: str, path: str) -> None:
    idle = to_cell(rows[0][0]).resize((CELL_W * 2, CELL_H * 2), Image.NEAREST)
    ref = Image.open(ref_path).convert("RGBA")
    ref.thumbnail((CELL_W * 2, CELL_H * 2))
    out = Image.new("RGBA", (CELL_W * 4 + 30, CELL_H * 2 + 36), (255, 255, 255, 255))
    d = ImageDraw.Draw(out)
    f = font(20)
    d.text((8, 6), "reference", fill=(0, 0, 0, 255), font=f)
    d.text((CELL_W * 2 + 30, 6), "agallas pixel v2 idle[0] (x2)", fill=(0, 0, 0, 255), font=f)
    out.alpha_composite(ref, (0, 36))
    out.alpha_composite(idle, (CELL_W * 2 + 30, 36))
    out.convert("RGB").save(path)


def make_vs_cell(frame, ref_path: str, path: str, label: str) -> None:
    cell = to_cell(frame).resize((CELL_W * 2, CELL_H * 2), Image.NEAREST)
    ref = Image.open(ref_path).convert("RGBA")
    ref.thumbnail((CELL_W * 3, CELL_H * 2))
    out = Image.new("RGBA", (ref.width + CELL_W * 2 + 30, CELL_H * 2 + 36), (255, 255, 255, 255))
    d = ImageDraw.Draw(out)
    f = font(20)
    d.text((8, 6), "reference", fill=(0, 0, 0, 255), font=f)
    d.text((ref.width + 30, 6), f"agallas pixel v2 {label} (x2)", fill=(0, 0, 0, 255), font=f)
    out.alpha_composite(ref, (0, 36))
    out.alpha_composite(cell, (ref.width + 30, 36))
    out.convert("RGB").save(path)


LINE_PICKS = [(0, 0), (1, 2), (2, 4), (3, 1), (4, 2), (6, 1), (7, 1), (8, 1)]


def make_before_after(before_path: str, atlas: Image.Image, path: str) -> None:
    before = Image.open(before_path).convert("RGBA")
    label_w = 90
    out = Image.new("RGBA", (label_w + CELL_W * 2 * len(LINE_PICKS), CELL_H * 4 + 10), (235, 235, 235, 255))
    d = ImageDraw.Draw(out)
    f = font(22)
    for row, (name, sheet) in enumerate((("v2", before), ("v3", atlas))):
        y = row * (CELL_H * 2 + 10)
        d.text((10, y + CELL_H - 12), name, fill=(0, 0, 0, 255), font=f)
        for i, (r, c_) in enumerate(LINE_PICKS):
            cell = sheet.crop((c_ * CELL_W, r * CELL_H, (c_ + 1) * CELL_W, (r + 1) * CELL_H))
            cell = cell.resize((CELL_W * 2, CELL_H * 2), Image.NEAREST)
            out.alpha_composite(cell, (label_w + i * CELL_W * 2, y))
    out.convert("RGB").save(path)


HAND_PICKS = [(0, 0), (1, 3), (3, 1), (4, 1), (6, 1), (7, 1), (8, 1), (5, 0)]


def make_hands_before_after(before_path: str, atlas: Image.Image, path: str,
                            picks=None, labels=("v3", "v4")) -> None:
    """v3 vs v4 torso/hand crops at 3x NEAREST, two versions stacked per cell."""
    before = Image.open(before_path).convert("RGBA")
    lx0, ly0, lx1, ly1 = 4, 18, 60, 66                      # logical crop
    box = (lx0 * SCALE, ly0 * SCALE + PAD_TOP, lx1 * SCALE, ly1 * SCALE + PAD_TOP)
    cw, ch = (box[2] - box[0]) * 3, (box[3] - box[1]) * 3
    picks = picks or HAND_PICKS
    cols = 4
    out = Image.new("RGBA", (cols * (cw + 8), 2 * ((len(picks) + cols - 1) // cols) * (ch + 30)),
                    (235, 235, 235, 255))
    d = ImageDraw.Draw(out)
    f = font(22)
    for i, (r, c_) in enumerate(picks):
        gx_, gy_ = (i % cols) * (cw + 8), (i // cols) * 2 * (ch + 30)
        for k, (name, sheet) in enumerate(((labels[0], before), (labels[1], atlas))):
            cell = sheet.crop((c_ * CELL_W + box[0], r * CELL_H + box[1], c_ * CELL_W + box[2], r * CELL_H + box[3]))
            cell = cell.resize((cw, ch), Image.NEAREST)
            y = gy_ + k * (ch + 30)
            d.text((gx_ + 6, y + 2), f"{name} {ROW_SPEC[r][0]} c{c_}", fill=(0, 0, 0, 255), font=f)
            out.alpha_composite(cell, (gx_, y + 28))
    out.convert("RGB").save(path)


def make_dark_light(rows, path: str) -> None:
    picks = [(0, 0), (2, 1), (3, 1), (5, 0), (6, 0), (7, 0), (8, 0)]
    out = Image.new("RGBA", (CELL_W * len(picks), CELL_H * 2), (0, 0, 0, 255))
    d = ImageDraw.Draw(out)
    d.rectangle([0, 0, out.width, CELL_H], fill=(245, 245, 245, 255))
    d.rectangle([0, CELL_H, out.width, CELL_H * 2], fill=(30, 32, 44, 255))
    for i, (r, c_) in enumerate(picks):
        cell = to_cell(rows[r][c_])
        out.alpha_composite(cell, (i * CELL_W, 0))
        out.alpha_composite(cell, (i * CELL_W, CELL_H))
    out.convert("RGB").save(path)


def save_gif(frames, path: str, duration: int) -> None:
    imgs = []
    for f in frames:
        cell = to_cell(f)
        rgb = Image.new("RGB", cell.size, (0, 255, 0))
        rgb.paste(cell, mask=cell.split()[3])
        p = rgb.quantize(colors=255, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
        pal = p.getpalette()[:765]
        arr = np.array(p)
        arr[np.array(cell.split()[3]) == 0] = 255
        q = Image.fromarray(arr, "P")
        q.putpalette(pal + [0, 255, 0])
        imgs.append(q)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=duration,
                 loop=0, transparency=255, disposal=2)


def validate(out_dir: str) -> list:
    report = []
    im = Image.open(os.path.join(out_dir, "spritesheet.webp"))
    im.load()
    report.append(f"format={im.format} size={im.size} mode={im.mode}")
    ok = im.format == "WEBP" and im.size == (ATLAS_W, ATLAS_H) and im.mode == "RGBA"
    a = np.array(im)[..., 3]
    empty_used, dirty_unused = [], []
    for r, (name, count) in enumerate(ROW_SPEC):
        for col in range(COLS):
            cell = a[r * CELL_H:(r + 1) * CELL_H, col * CELL_W:(col + 1) * CELL_W]
            if col < count and not (cell > 0).any():
                empty_used.append((name, col))
            if col >= count and cell.any():
                dirty_unused.append((name, col))
    report.append(f"used cells={sum(n for _, n in ROW_SPEC)} empty_used={empty_used}")
    report.append(f"unused cells={sum(COLS - n for _, n in ROW_SPEC)} non_zero_alpha_unused={dirty_unused}")
    report.append(f"distinct alpha values={sorted(set(np.unique(a).tolist()))}")
    with open(os.path.join(out_dir, "pet.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    keys = sorted(meta)
    report.append(f"pet.json keys={keys}")
    ok = ok and not empty_used and not dirty_unused
    ok = ok and keys == sorted(["id", "displayName", "description", "spritesheetPath"])
    ok = ok and meta["id"] == "agallas" and meta["spritesheetPath"] == "spritesheet.webp"
    report.append("VALID" if ok else "INVALID")
    return report


PET_JSON = {
    "id": "agallas",
    "displayName": "Agallas",
    "description": ("A timid dusty-pink dog with antenna ears, a huge wedge nose and enormous "
                    "worried eyes who screams at everything but always comes through in the end."),
    "spritesheetPath": "spritesheet.webp",
}

GIF_MS = {"idle": 220, "running-right": 90, "running-left": 90, "waving": 160,
          "jumping": 140, "failed": 80, "waiting": 160, "running": 110, "review": 200}

REFERENCE = "/tmp/claude-695000033/-home-anton-casais/03a0a754-38c7-4292-926f-6a2ac4514355/images/3.png"


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "build")
    os.makedirs(out_dir, exist_ok=True)

    rows = build_rows()
    for issue in margin_report(rows):
        print("WARN", issue)

    atlas = make_atlas(rows)
    atlas.save(os.path.join(out_dir, "spritesheet.webp"), "WEBP", lossless=True, quality=100, method=6)
    with open(os.path.join(out_dir, "pet.json"), "w", encoding="utf-8") as fh:
        json.dump(PET_JSON, fh, indent=2)
        fh.write("\n")

    make_preview(atlas, os.path.join(here, "preview.png"))
    if os.path.exists(REFERENCE):
        make_vs_reference(rows, REFERENCE, os.path.join(here, "vs-reference.png"))
    make_dark_light(rows, os.path.join(here, "dark-light-check.png"))
    before = os.path.join(here, "backup-v4", "spritesheet.webp")
    if os.path.exists(before):
        make_hands_before_after(before, atlas, os.path.join(here, "v41-before-after.png"),
                                picks=[(8, 1), (8, 4), (1, 3), (2, 5)], labels=("v4", "v4.1"))
    img_dir = os.path.dirname(REFERENCE)
    for ref_name, (r, c_), out_name, label in (("4.png", (5, 0), "vs-reference-scream.png", "failed[0]"),
                                               ("6.png", (7, 0), "vs-reference-computer.png", "running[0]")):
        ref_path = os.path.join(img_dir, ref_name)
        if os.path.exists(ref_path):
            make_vs_cell(rows[r][c_], ref_path, os.path.join(here, out_name), label)
    for (name, _), frames in zip(ROW_SPEC, rows):
        save_gif(frames, os.path.join(here, f"row_{name}.gif"), GIF_MS[name])

    for line in validate(out_dir):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
