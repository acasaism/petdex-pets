#!/usr/bin/env python3
"""Procedural pixel-art generator for the "Agallas" Codex desktop pet.

Agallas is the Spanish name of Courage, the cowardly pink dog from
"Courage the Cowardly Dog". Every frame is drawn from geometric primitives on
a 48x52 logical canvas, outlined, and scaled x4 with nearest-neighbour into a
192x208 atlas cell. The atlas is 8 columns x 9 rows (1536x1872 px).

Usage:
    python3 -I make_pet.py [OUTPUT_DIR]

Writes spritesheet.webp, pet.json, preview.png and one GIF per row into
OUTPUT_DIR (default: ./build next to this script), then validates the atlas.
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
W, H = 48, 52
SCALE = 4
CELL_W, CELL_H = W * SCALE, H * SCALE  # 192 x 208
COLS, ROWS = 8, 9
ATLAS_W, ATLAS_H = COLS * CELL_W, ROWS * CELL_H  # 1536 x 1872
CX = 23.5  # exact horizontal centre of the logical canvas
GROUND_ANKLE = 45.0  # ankle height when standing; feet occupy rows 45..47

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
# Palette (Courage: pink coat, black ears / nose / back spots, big white eyes)
# ---------------------------------------------------------------------------
OUT = (40, 20, 44)
PINK = (242, 160, 202)
PINK_D = (206, 114, 168)
PINK_L = (253, 204, 229)
PINK_FAR = (214, 128, 180)
EAR = (50, 38, 56)
EAR_L = (98, 80, 106)
BLACK = (26, 18, 30)
NOSE_L = (128, 116, 140)
WHITE = (255, 255, 255)
EYE_S = (214, 220, 238)
MOUTH = (120, 26, 58)
TONGUE = (240, 104, 134)
UVULA = (220, 72, 108)
SWEAT = (124, 196, 255)
SWEAT_L = (226, 246, 255)
YELLOW = (255, 214, 72)
FX = (255, 246, 252)
DUST = (226, 218, 206)
GRAY = (156, 164, 178)
GRAY_D = (102, 108, 124)
GRAY_L = (206, 212, 222)
GOLD = (222, 168, 64)
GOLD_D = (168, 112, 40)
BROWN = (126, 76, 42)
GLASS = (206, 238, 252)

YY, XX = np.mgrid[0:H, 0:W].astype(float)


# ---------------------------------------------------------------------------
# Raster primitives (boolean masks over the logical canvas)
# ---------------------------------------------------------------------------
def ellipse(cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    return ((XX - cx) / rx) ** 2 + ((YY - cy) / ry) ** 2 <= 1.0


def ellipse_rot(cx: float, cy: float, r_minor: float, r_major: float, lean_deg: float) -> np.ndarray:
    """Ellipse whose major axis is tilted `lean_deg` clockwise from vertical."""
    t = math.radians(lean_deg)
    ux, uy = math.sin(t), -math.cos(t)
    vx, vy = -uy, ux
    px, py = XX - cx, YY - cy
    a = px * ux + py * uy
    b = px * vx + py * vy
    return (a / r_major) ** 2 + (b / r_minor) ** 2 <= 1.0


def capsule(p0, p1, r0: float, r1: Optional[float] = None) -> np.ndarray:
    """Segment p0->p1 with a radius that tapers linearly from r0 to r1."""
    r1 = r0 if r1 is None else r1
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if l2 == 0:
        t = np.zeros_like(XX)
    else:
        t = np.clip(((XX - x0) * dx + (YY - y0) * dy) / l2, 0.0, 1.0)
    d = np.hypot(XX - (x0 + t * dx), YY - (y0 + t * dy))
    return d <= r0 + (r1 - r0) * t


def polyline(points, r0: float, r1: Optional[float] = None) -> np.ndarray:
    r1 = r0 if r1 is None else r1
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths) or 1.0
    mask = np.zeros((H, W), bool)
    acc = 0.0
    for (a, b), seg in zip(zip(points, points[1:]), lengths):
        ra = r0 + (r1 - r0) * acc / total
        acc += seg
        rb = r0 + (r1 - r0) * acc / total
        mask |= capsule(a, b, ra, rb)
    return mask


def rect(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """Inclusive pixel rectangle."""
    return (XX >= x0) & (XX <= x1) & (YY >= y0) & (YY <= y1)


def look(m: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """out[y, x] = m[y + dy, x + dx] (False outside the canvas)."""
    out = np.zeros_like(m)
    out[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)] = \
        m[max(0, dy):H - max(0, -dy), max(0, dx):W - max(0, -dx)]
    return out


def dilate(m: np.ndarray) -> np.ndarray:
    return m | look(m, 1, 0) | look(m, -1, 0) | look(m, 0, 1) | look(m, 0, -1)


def ik(a, b, l1: float, l2: float, prefer) -> Optional[tuple]:
    """Two-bone IK joint between a and b, bending towards `prefer`. None if out of reach."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d >= l1 + l2 - 1e-3 or d < 1e-6:
        return None
    x = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(l1 * l1 - x * x, 0.0))
    ux, uy = dx / d, dy / d
    mx, my = a[0] + ux * x, a[1] + uy * x
    nx, ny = -uy, ux
    j1, j2 = (mx + nx * h, my + ny * h), (mx - nx * h, my - ny * h)
    s1 = (j1[0] - mx) * prefer[0] + (j1[1] - my) * prefer[1]
    s2 = (j2[0] - mx) * prefer[0] + (j2[1] - my) * prefer[1]
    return j1 if s1 >= s2 else j2


def limb(a, b, l1: float, l2: float, prefer, r: float) -> np.ndarray:
    j = ik(a, b, l1, l2, prefer)
    if j is None:
        return capsule(a, b, r)
    return capsule(a, j, r) | capsule(j, b, r)


class Canvas:
    def __init__(self) -> None:
        self.px = np.zeros((H, W, 4), np.uint8)

    def put(self, mask: np.ndarray, color) -> None:
        self.px[mask] = (*color, 255)

    def dot(self, x: int, y: int, color) -> None:
        if 0 <= x < W and 0 <= y < H:
            self.px[y, x] = (*color, 255)

    def part(self, mask, color, outline=True, shade=None, light=None, depth=2) -> None:
        """Fill a part; its outline ring is painted over whatever lies beneath."""
        if outline:
            self.put(dilate(mask) & ~mask, OUT)
        self.put(mask, color)
        shadow = np.zeros_like(mask)
        if shade is not None:
            shadow = mask & ~(look(mask, 1, 0) & look(mask, 0, depth))
            self.put(shadow, shade)
        if light is not None:
            lit = mask & ~(look(mask, -1, 0) & look(mask, 0, -1)) & ~shadow
            self.put(lit, light)

    def glyph(self, rows, x0: int, y0: int, cmap) -> None:
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch in cmap:
                    self.dot(x0 + i, y0 + j, cmap[ch])

    def outline(self) -> None:
        """Close the silhouette: outline only coloured pixels that touch transparency."""
        sil = self.px[..., 3] > 0
        coloured = sil & ~np.all(self.px[..., :3] == OUT, axis=-1)
        self.put(dilate(coloured) & ~sil, OUT)


GLYPH_CMAP = {"o": OUT, "m": MOUTH, "t": TONGUE, "w": WHITE, "u": UVULA, "p": PINK}

MOUTHS = {
    # nervous squiggle
    "nervous": [".oo..oo.",
                "o..oo..o"],
    # clenched-teeth grimace
    "grit": [".oooooooo.",
             "owwowwowwo",
             ".oooooooo."],
    # open grin (waving)
    "smile": ["oooooooo",
              "owwwwwwo",
              ".ommmmo.",
              "..oooo.."],
    # small gasp
    "o": [".oo.",
          "ommo",
          "otto",
          ".oo."],
    # wobbly worried frown (waiting)
    "frown": ["..oo..",
              ".o..o.",
              "o....o"],
    # thoughtful line, shifted to one side (review)
    "hmm": ["ooo.",
            "...o"],
}

QUESTION = [".###.",
            "#...#",
            "....#",
            "...#.",
            "..#..",
            ".....",
            "..#.."]

BONE = ["#....#",
        ".####.",
        "#....#"]


# ---------------------------------------------------------------------------
# Front-facing character
# ---------------------------------------------------------------------------
@dataclass
class Pose:
    dx: int = 0                 # whole character offset
    dy: int = 0
    body_dy: int = 0            # torso / shoulders / head (crouch, squash)
    head_dx: int = 0
    head_dy: int = 0
    ears: tuple = ((10, 15), (10, 15))   # per side: (angle from hanging, length)
    eyes: object = "open"       # str or (left, right): open | wide | closed | squint
    pupil: tuple = (0, 0)
    pupil_size: int = 2
    mouth: str = "nervous"
    uvula: int = 0
    arm_l: tuple = ((17, 38), None)       # (hand, elbow) in body coordinates
    arm_r: tuple = ((30, 38), None)
    arms_behind: tuple = (False, False)   # draw an arm behind the torso
    feet: str = "ground"        # ground | air
    foot_spread: int = 0
    tail_tip: tuple = (34.5, 31.0)
    spot: bool = True
    extras: list = field(default_factory=list)   # fn(canvas, ctx) drawn on top


def draw_front(p: Pose) -> np.ndarray:
    c = Canvas()
    gx, gy = p.dx, p.dy
    by = gy + p.body_dy
    hcx, hcy = CX + gx + p.head_dx, 21.5 + by + p.head_dy
    scream = p.mouth == "scream"
    ctx = {"gx": gx, "gy": gy, "by": by, "hcx": hcx, "hcy": hcy, "pose": p}

    # Tail (thin, curling up behind the right hip)
    base = (27.5 + gx, 39.0 + by)
    tip = (p.tail_tip[0] + gx, p.tail_tip[1] + by)
    mid = ((base[0] + tip[0]) / 2 + 1.5, (base[1] + tip[1]) / 2 + 1.0)
    c.part(polyline([base, mid, tip], 0.85, 0.7), PINK)

    # Legs and feet
    for side, hipx in ((-1, 20.5), (1, 26.5)):
        hip = (hipx + gx, 40.5 + by)
        if p.feet == "air":
            ankle = (hip[0] + side * 0.5, hip[1] + 4.3)
        else:
            ankle = (hipx + gx + side * p.foot_spread, GROUND_ANKLE)
        m = limb(hip, ankle, 2.6, 2.6, (side, 0), 1.0)
        m |= ellipse(ankle[0] + side * 1.0, ankle[1] + 1.4, 2.4, 1.3)
        c.part(m, PINK)

    ctx["hands"] = [None, None]

    def draw_arm(idx: int) -> None:
        side = -1 if idx == 0 else 1
        hand, elbow = (p.arm_l, p.arm_r)[idx]
        sh = (CX + side * 4.0 + gx, 32.5 + by)
        hd = (hand[0] + gx, hand[1] + by)
        if elbow is None:
            m = capsule(sh, hd, 1.0)
        else:
            el = (elbow[0] + gx, elbow[1] + by)
            m = capsule(sh, el, 1.0) | capsule(el, hd, 1.0)
        m |= ellipse(hd[0], hd[1], 1.5, 1.5)
        c.part(m, PINK)
        ctx["hands"][idx] = hd

    for idx in (0, 1):
        if p.arms_behind[idx]:
            draw_arm(idx)

    # Body with a black back spot peeking on the right flank
    body = ellipse(CX + gx, 36.5 + by, 4.8, 6.0)
    c.part(body, PINK, shade=PINK_D)
    if p.spot:
        c.put(body & ellipse(25.8 + gx, 33.8 + by, 1.3, 1.6), BLACK)

    # Ears: long black floppy ears; raised ears sit behind the head
    def ear_mask(side: int, angle: float, length: float) -> np.ndarray:
        ax, ay = hcx + side * 9.0, hcy - 6.0
        a = math.radians(angle)
        tip_ = (ax + side * math.sin(a) * length, ay + math.cos(a) * length)
        return capsule((ax, ay), tip_, 2.0, 1.6)

    ears = [(side, ang, ln) for side, (ang, ln) in zip((-1, 1), p.ears)]
    for side, ang, ln in ears:
        if ang >= 100:
            c.part(ear_mask(side, ang, ln), EAR, light=EAR_L)

    # Head: cranium + muzzle (+ dropped jaw when screaming)
    head = ellipse(hcx, hcy, 11.5, 8.8) | ellipse(hcx, hcy + 5.0, 6.5, 4.6)
    if scream:
        head |= ellipse(hcx, hcy + 10.0, 6.4, 5.4)
    c.part(head, PINK, shade=PINK_D, light=PINK_L, depth=1)

    for side, ang, ln in ears:
        if ang < 100:
            c.part(ear_mask(side, ang, ln), EAR, light=EAR_L)

    # Eyes: big, round, white
    eye_kinds = p.eyes if isinstance(p.eyes, tuple) else (p.eyes, p.eyes)
    for side, kind in zip((-1, 1), eye_kinds):
        wide = kind == "wide" or scream
        erx, ery = (3.4, 4.6) if wide else (3.2, 4.0)
        ecx, ecy = hcx + side * 4.0, hcy - 3.0 - (0.5 if wide else 0.0)
        em = ellipse(ecx, ecy, erx, ery)
        if kind == "closed":
            c.part(em, PINK_D)
            lid_y = int(math.floor(ecy))
            c.put(em & (YY == lid_y), OUT)
            continue
        c.part(em, WHITE)
        c.put(em & ~look(em, 0, 1), EYE_S)  # soft lower rim
        ox, oy = p.pupil
        if p.pupil_size == 2 and not wide:
            px0 = int(math.floor(ecx - 0.5)) + ox
            py0 = int(math.floor(ecy - 0.5)) + oy
            c.put(rect(px0, py0, px0 + 1, py0 + 1) & em, BLACK)
        else:
            px0 = int(math.floor(ecx - 0.5)) + (1 if side == 1 else 0) + ox
            py0 = int(math.floor(ecy)) + oy
            c.dot(px0, py0, BLACK)
        if kind == "squint":
            lid = em & (YY < math.floor(ecy) - 0)
            c.put(lid, PINK_D)
            c.put(em & (YY == math.floor(ecy)), OUT)

    # Nose: big black button
    ny = hcy + (2.0 if scream else 3.5)
    c.put(ellipse(hcx, ny, 2.9, 1.7), BLACK)
    c.dot(int(math.floor(hcx - 1.5)), int(math.floor(ny - 0.5)), NOSE_L)

    # Mouth
    if scream:
        mo = ellipse(hcx, hcy + 9.0, 4.3, 5.0)
        c.part(mo, MOUTH)
        ys = np.where(mo.any(axis=1))[0]
        top, bottom = ys.min(), ys.max()
        c.put(mo & (YY == top), WHITE)
        c.put(mo & (YY >= bottom - 1) & (np.abs(XX - hcx) < 3.2), TONGUE)
        ux = int(math.floor(hcx)) + p.uvula
        c.dot(ux, top + 1, UVULA)
        c.dot(ux, top + 2, UVULA)
    else:
        rows = MOUTHS[p.mouth]
        w = len(rows[0])
        x0 = int(round(hcx - w / 2 + 0.5))
        if p.mouth == "hmm":
            x0 += 2
        c.glyph(rows, x0, int(math.floor(hcy + 6.5)), GLYPH_CMAP)

    # Arms in front, with small round paws
    for idx in (0, 1):
        if not p.arms_behind[idx]:
            draw_arm(idx)

    for fn in p.extras:
        fn(c, ctx)
    c.outline()
    return c.px


# ---------------------------------------------------------------------------
# Effects and props
# ---------------------------------------------------------------------------
def sweat(x: int, y: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        x0, y0 = x + ctx["gx"], y + ctx["by"]
        c.glyph(["..s..", ".sss.", ".lss.", "..s.."], x0 - 2, y0, {"s": SWEAT, "l": SWEAT_L})
    return fn


def shake_marks(phase: int) -> Callable:
    """Vibration arcs on both sides of the trembling body."""
    def fn(c: Canvas, ctx) -> None:
        gx, by = ctx["gx"], int(ctx["by"])
        for k, (x, y) in enumerate(((13, 36), (11, 41))):
            yy = y + by + (phase if k == 0 else -phase)
            c.glyph([".#", "#.", "#.", ".#"], x + gx - k, yy, {"#": FX})
            c.glyph(["#.", ".#", ".#", "#."], 47 - x - 1 + gx + k, yy, {"#": FX})
    return fn


def question(dy: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        c.glyph(QUESTION, 37, 4 + dy, {"#": YELLOW})
    return fn


def bang(x: int, y: int) -> Callable:
    def fn(c: Canvas, ctx) -> None:
        c.glyph(["#", "#", "#", ".", "#"], x, y, {"#": YELLOW})
    return fn


def dust() -> Callable:
    def fn(c: Canvas, ctx) -> None:
        for x in (13.5, 33.5):
            c.put(ellipse(x, 46.5, 1.6, 1.1) | ellipse(x + (2 if x > 20 else -2), 46.8, 1.1, 0.8), DUST)
    return fn


def laptop(active: int) -> Callable:
    """Tiny laptop seen from behind the lid; `active` paw taps (0 left, 1 right)."""
    def fn(c: Canvas, ctx) -> None:
        gx = ctx["gx"]
        lid = rect(12 + gx, 35, 35 + gx, 44)
        c.part(lid, GRAY, shade=GRAY_D, light=GRAY_L)
        c.part(rect(10 + gx, 45, 37 + gx, 46), GRAY_D)
        c.glyph(BONE, 21 + gx, 38, {"#": PINK_L})
        for i, (hx, hy) in enumerate(ctx["hands"]):
            c.part(ellipse(hx, hy, 1.5, 1.5), PINK)
            if i == active:
                sx, sy = int(hx) + (-4 if i == 0 else 4), int(hy) - 2
                c.put(rect(sx, sy - 1, sx, sy + 1) | rect(sx - 1, sy, sx + 1, sy), YELLOW)
    return fn


def magnifier(look_dx: int) -> Callable:
    """Magnifying glass held over the left eye, showing a huge scanning eye."""
    def fn(c: Canvas, ctx) -> None:
        lx, ly = ctx["hcx"] - 4.0, ctx["hcy"] - 3.0
        hand = ctx["hands"][0]
        c.part(capsule((lx - 3.6, ly + 4.2), hand, 1.0), BROWN)
        c.part(ellipse(lx, ly, 5.6, 5.6), GOLD, shade=GOLD_D)
        c.put(ellipse(lx, ly, 4.5, 4.5), GLASS)
        eye = ellipse(lx, ly, 3.6, 3.6)
        c.part(eye, WHITE)
        px0 = int(math.floor(lx - 1.0)) + look_dx
        py0 = int(math.floor(ly - 1.0))
        c.put(rect(px0, py0, px0 + 2, py0 + 2) & eye, BLACK)
        c.dot(int(lx) - 3, int(ly) - 3, WHITE)
        c.dot(int(lx) - 2, int(ly) - 4, WHITE)
        c.part(ellipse(hand[0], hand[1], 1.5, 1.5), PINK)
    return fn


# ---------------------------------------------------------------------------
# Side view (running right)
# ---------------------------------------------------------------------------
LEG_CYCLE = [(5.0, 0.0), (2.5, 0.0), (0.0, 0.0), (-3.0, 0.8),
             (-5.5, 3.5), (-3.0, 5.5), (1.5, 5.0), (4.5, 2.0)]
BOB = [0, 1, 0, -1, 0, 1, 0, -1]
EAR_FLAP = [-3, -2, 0, 2, 3, 2, 0, -2]


def draw_side(phase: int) -> np.ndarray:
    c = Canvas()
    b = BOB[phase]

    def leg(hip, step, color):
        dx, lift = step
        ankle = (hip[0] + dx, GROUND_ANKLE - lift)
        m = limb(hip, ankle, 4.6, 4.6, (1, 0), 1.0)
        m |= ellipse(ankle[0] + 1.3, ankle[1] + 1.4, 2.6, 1.3)
        c.part(m, color)

    def arm(sh, step, color):
        hx = 1.5 - 0.6 * step[0]
        hand = (sh[0] + hx, sh[1] + 5.0 - 0.45 * max(hx, 0.0))
        m = limb(sh, hand, 3.5, 3.5, (-0.7, 0.7), 1.0) | ellipse(hand[0], hand[1], 1.5, 1.5)
        c.part(m, color)

    def ear(ax, ay, flap, length, r0, r1, color):
        mid = (ax - length * 0.55, ay + 0.5 + flap * 0.4)
        tip = (ax - length * 0.95, ay + 3.5 + flap * 1.3)
        c.part(polyline([(ax, ay), mid, tip], r0, r1), color, light=EAR_L)

    near = LEG_CYCLE[phase]
    far = LEG_CYCLE[(phase + 4) % 8]
    flap = EAR_FLAP[phase]

    # Far limbs (darker, behind the body)
    arm((25.0, 30.5 + b), near, PINK_FAR)
    leg((20.0, 38.0 + b), far, PINK_FAR)

    # Thin tail, waving behind
    w = [0, 1, 2, 1, 0, -1, -2, -1][phase]
    c.part(polyline([(16.5, 38.0 + b), (12.0, 36.0 + b + w * 0.5), (9.0, 31.5 + b + w)], 0.85, 0.7), PINK)

    # Torso leaning forward with three black spots along the back
    tcx, tcy, lean = 21.0, 34.5 + b, 30.0
    torso = ellipse_rot(tcx, tcy, 4.8, 6.8, lean)
    c.part(torso, PINK, shade=PINK_D)
    t = math.radians(lean)
    ux, uy = math.sin(t), -math.cos(t)
    vx, vy = -uy, ux
    spots = np.zeros((H, W), bool)
    for along, rad in ((3.0, 1.4), (-0.4, 1.8), (-3.8, 1.3)):
        sx = tcx + ux * along - vx * 2.0
        sy = tcy + uy * along - vy * 2.0
        spots |= ellipse(sx, sy, rad, rad)
    c.put(torso & spots, BLACK)

    # Near leg
    leg((19.0, 38.5 + b), near, PINK)

    hcx, hcy = 24.0, 20.5 + b
    ear(hcx - 1.0, hcy - 7.0, flap - 2, 10.0, 1.6, 1.3, BLACK)  # far ear peeks above
    head = ellipse(hcx, hcy, 8.2, 7.8) | ellipse(hcx + 8.0, hcy + 4.0, 5.5, 3.6)
    head |= ellipse(hcx + 7.5, hcy + 8.0, 4.0, 2.6)  # dropped jaw
    c.part(head, PINK, shade=PINK_D, light=PINK_L)

    # Eyes (far then near), tiny pupils staring ahead in terror
    fe = ellipse(hcx + 7.5, hcy - 3.5, 2.4, 3.7)
    c.part(fe, WHITE)
    c.dot(int(hcx + 8.5), int(hcy - 3.5), BLACK)
    ne = ellipse(hcx + 3.5, hcy - 3.0, 3.1, 4.0)
    c.part(ne, WHITE)
    c.put(ne & ~look(ne, 0, 1), EYE_S)
    c.dot(int(hcx + 5.0), int(hcy - 3.5), BLACK)

    # Big black nose on the snout tip
    c.put(ellipse(hcx + 13.5, hcy + 2.3, 2.2, 1.8), BLACK)
    c.dot(int(hcx + 12.5), int(hcy + 1.5), NOSE_L)

    # Screaming mouth under the snout
    mo = ellipse(hcx + 9.0, hcy + 7.0, 3.2, 2.1)
    c.part(mo, MOUTH)
    ys = np.where(mo.any(axis=1))[0]
    c.put(mo & (YY == ys.max()), TONGUE)
    c.put(mo & (YY == ys.min()) & (XX >= hcx + 9.5), WHITE)

    ear(hcx - 4.0, hcy - 6.0, flap, 12.0, 1.9, 1.5, EAR)  # near ear streaming back

    # Near arm on top
    arm((24.5, 31.5 + b), far, PINK)

    c.outline()
    return c.px


# ---------------------------------------------------------------------------
# Row definitions
# ---------------------------------------------------------------------------
REST_L = ((16, 38), None)
REST_R = ((31, 38), None)


def row_idle():
    breath = [0, 0, 1, 1, 1, 0]
    pupils = [(0, 0), (0, 0), (1, 0), (1, 0), (0, 0), (-1, 0)]
    ear_sway = [10, 10, 12, 13, 12, 11]
    tail = [0.0, 0.5, 1.5, 1.0, 0.0, -0.5]
    frames = []
    for i in range(6):
        frames.append(Pose(
            head_dy=breath[i], pupil=pupils[i],
            eyes="closed" if i == 4 else "open",
            ears=((ear_sway[i], 15), (ear_sway[i], 15)),
            arm_l=((16, 38 + breath[i]), None), arm_r=((31, 38 + breath[i]), None),
            arms_behind=(True, True),
            tail_tip=(34.5 + tail[i], 31.0)))
    return [draw_front(p) for p in frames]


def row_run_right():
    return [draw_side(i) for i in range(8)]


def row_run_left():
    return [np.ascontiguousarray(f[:, ::-1]) for f in row_run_right()]


def row_waving():
    elbow = (35.5, 30.5)
    angles = [-5, 15, 32, 15]
    frames = []
    for i, a in enumerate(angles):
        r = math.radians(a)
        hand = (round(elbow[0] + 8 * math.sin(r)), round(elbow[1] - 8 * math.cos(r)))
        frames.append(Pose(
            mouth="smile", pupil=(1, 0), ears=((8, 15), (14, 15)),
            arm_l=REST_L, arm_r=(hand, elbow), arms_behind=(True, False),
            tail_tip=(34.5 + (2 if i % 2 else 0), 31.0 - (1 if i % 2 else 0))))
    return [draw_front(p) for p in frames]


def row_jumping():
    frames = [
        # crouch: squashed, gritting teeth
        Pose(body_dy=2, ears=((22, 14), (22, 14)), eyes="wide", mouth="grit",
             arm_l=((16, 36), None), arm_r=((31, 36), None), arms_behind=(True, True)),
        # take-off: stretched, ears trailing down
        Pose(dy=-6, feet="air", ears=((2, 15), (2, 15)), eyes="wide", mouth="o",
             arm_l=((15, 27), (16, 32)), arm_r=((32, 27), (31, 32))),
        # peak: full fright, ears flung out
        Pose(dy=-8, feet="air", ears=((100, 10), (100, 10)), mouth="scream",
             arm_l=((12, 25), (15, 31)), arm_r=((35, 25), (32, 31)),
             tail_tip=(33.5, 26.0), extras=[bang(41, 12)]),
        # falling: ears flying up
        Pose(dy=-4, feet="air", ears=((140, 9), (140, 9)), eyes="wide", mouth="o",
             arm_l=((13, 29), (15, 33)), arm_r=((34, 29), (32, 33)), tail_tip=(34.0, 27.0)),
        # landing: squash and dust
        Pose(body_dy=1, ears=((40, 13), (40, 13)), eyes="wide", mouth="grit", foot_spread=1,
             arm_l=((15, 37), None), arm_r=((32, 37), None), arms_behind=(True, True),
             extras=[dust()]),
    ]
    return [draw_front(p) for p in frames]


def row_failed():
    dxs = [-1, 1, -1, 1, 0, 1, -1, 1]
    dys = [0, 0, -1, 0, 0, -1, 0, 0]
    ear_l = [172, 166, 176, 168, 174, 165, 178, 169]
    ear_r = [168, 175, 165, 177, 166, 174, 169, 176]
    uv = [0, 1, 0, -1, 0, 1, 0, -1]
    frames = []
    for i in range(8):
        extras = [shake_marks(i % 2)]
        if i in (2, 3):
            extras.append(sweat(7 - (i - 2), 11 - (i - 2)))
        if i in (6, 7):
            extras.append(sweat(40 + (i - 6), 11 - (i - 6)))
        frames.append(Pose(
            dx=dxs[i], dy=dys[i], mouth="scream", uvula=uv[i],
            ears=((ear_l[i], 10), (ear_r[i], 10)),
            arm_l=((9, 21), (13, 30)), arm_r=((38, 21), (34, 30)),
            foot_spread=1, tail_tip=(33.0, 25.5), extras=extras))
    return [draw_front(p) for p in frames]


def row_waiting():
    tremble = [0, 1, 0, -1, 0, 1]
    q = [0, -1, -1, 0, 0, -1]
    frames = []
    for i in range(6):
        frames.append(Pose(
            dx=tremble[i], pupil=(0, -2), mouth="frown",
            ears=((3, 15), (3, 15)),
            arm_l=((22, 32), (18, 36)), arm_r=((25, 32), (29, 36)),
            tail_tip=(33.0, 37.0), extras=[question(q[i])]))
    return [draw_front(p) for p in frames]


def row_working():
    pupils = [(-1, 2), (0, 2), (1, 2), (1, 2), (0, 2), (-1, 2)]
    ear_flap = [30, 38, 30, 38, 30, 38]
    head_bob = [0, 1, 0, 1, 0, 1]
    frames = []
    for i in range(6):
        active = i % 2
        lh = (19, 34 if active == 0 else 35)
        rh = (28, 34 if active == 1 else 35)
        extras = [laptop(active)]
        if i in (3, 4):
            extras.append(sweat(37, 17 + (i - 3)))
        frames.append(Pose(
            head_dy=head_bob[i], pupil=pupils[i], mouth="grit",
            ears=((ear_flap[i], 13), (ear_flap[(i + 1) % 6], 13)),
            arm_l=(lh, None), arm_r=(rh, None), extras=extras))
    return [draw_front(p) for p in frames]


def row_review():
    scan = [-1, 0, 1, 1, 0, -1]
    look_ = [-1, 0, 1, 1, 0, -1]
    frames = []
    for i in range(6):
        frames.append(Pose(
            head_dx=scan[i], eyes=("open", "squint"), pupil=(look_[i], 0), mouth="hmm",
            ears=((10, 15), (26, 14)),
            arm_l=((13, 28), (16, 34)), arm_r=((29, 31), (32, 36)),
            extras=[magnifier(look_[i])]))
    return [draw_front(p) for p in frames]


ROW_BUILDERS = [row_idle, row_run_right, row_run_left, row_waving, row_jumping,
                row_failed, row_waiting, row_working, row_review]


# ---------------------------------------------------------------------------
# Atlas, previews, validation
# ---------------------------------------------------------------------------
def to_cell(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(frame, "RGBA").resize((CELL_W, CELL_H), Image.NEAREST)


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


def make_preview(atlas: Image.Image, path: str) -> None:
    label_w = 150
    prev = Image.new("RGBA", (ATLAS_W + label_w, ATLAS_H), (128, 128, 128, 255))
    d = ImageDraw.Draw(prev)
    try:
        font = ImageFont.load_default(size=24)
    except TypeError:
        font = ImageFont.load_default()
    for r, (name, count) in enumerate(ROW_SPEC):
        y = r * CELL_H
        d.text((8, y + CELL_H // 2 - 24), f"{r} {name}", fill=(255, 255, 255, 255), font=font)
        d.text((8, y + CELL_H // 2 + 6), f"{count} frames", fill=(225, 225, 225, 255), font=font)
        d.line([(0, y), (ATLAS_W + label_w, y)], fill=(105, 105, 105, 255))
    for col in range(COLS + 1):
        x = label_w + col * CELL_W
        d.line([(x, 0), (x, ATLAS_H)], fill=(105, 105, 105, 255))
    prev.alpha_composite(atlas, (label_w, 0))
    prev.convert("RGB").save(path)


def save_gif(frames, path: str, duration: int) -> None:
    imgs = []
    for f in frames:
        cell = to_cell(f)
        rgb = Image.new("RGB", cell.size, (0, 255, 0))
        rgb.paste(cell, mask=cell.split()[3])
        p = rgb.quantize(colors=255, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
        pal = p.getpalette()[:765]
        # Map fully transparent pixels to a dedicated palette index 255.
        p.putpalette(pal + [0, 255, 0])
        alpha = np.array(cell.split()[3])
        arr = np.array(p)
        arr[alpha == 0] = 255
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
    unused_total = sum(COLS - n for _, n in ROW_SPEC)
    report.append(f"used cells={sum(n for _, n in ROW_SPEC)} empty_used={empty_used}")
    report.append(f"unused cells={unused_total} non_zero_alpha_unused={dirty_unused}")
    alpha_vals = sorted(set(np.unique(a).tolist()))
    report.append(f"distinct alpha values={alpha_vals}")
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
    "description": ("A timid pink dog with big round eyes and floppy black ears who screams "
                    "at everything but always comes through in the end."),
    "spritesheetPath": "spritesheet.webp",
}

GIF_MS = {"idle": 220, "running-right": 90, "running-left": 90, "waving": 160,
          "jumping": 140, "failed": 80, "waiting": 160, "running": 110, "review": 200}


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
    for (name, _), frames in zip(ROW_SPEC, rows):
        save_gif(frames, os.path.join(here, f"row_{name}.gif"), GIF_MS[name])

    for line in validate(out_dir):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
