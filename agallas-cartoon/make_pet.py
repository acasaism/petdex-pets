#!/usr/bin/env python3
"""Generate the "agallas-cartoon" Codex pet: Courage the Cowardly Dog in a flat cartoon style.

v2: the character model is traced from the official 3/4-left model pose (bean body,
antenna ears with rust-brown flaps, huge wedge nose, tall oval eyes, wire limbs with
cross-shaped paws, star tail). Everything is drawn procedurally with pycairo (Bezier
paths, round caps/joins, native antialiasing) at 2x and downscaled with Pillow LANCZOS.

Usage:
    python3 -I make_pet.py            # render atlas, previews, GIFs into ./out
    python3 -I make_pet.py --install  # also install into ~/.codex/pets and ~/.petdex/pets
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Sequence, Tuple

import cairo
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
PET_ID = "agallas-cartoon"
PET_JSON = {
    "id": PET_ID,
    "displayName": "Agallas (cartoon)",
    "description": (
        "A cartoon-style pink, cowardly dog with huge worried eyes and a giant wedge nose "
        "who screams at every error but always gets the job done."
    ),
    "spritesheetPath": "spritesheet.webp",
}

# ---------------------------------------------------------------------------
# Atlas geometry
# ---------------------------------------------------------------------------
CELL_W, CELL_H = 192, 208
COLS, ROWS = 8, 9
ATLAS_W, ATLAS_H = COLS * CELL_W, ROWS * CELL_H  # 1536 x 1872
SS = 2  # supersampling factor
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

CX = 96.0       # character centre line
GROUND = 200.0  # model ground line (bottom of the feet)
REF_IMAGE = "/tmp/claude-695000033/-home-anton-casais/03a0a754-38c7-4292-926f-6a2ac4514355/images/3.png"


# ---------------------------------------------------------------------------
# Palette (flat fills, no gradients)
# ---------------------------------------------------------------------------
def rgb(r: int, g: int, b: int) -> Tuple[float, float, float]:
    return (r / 255.0, g / 255.0, b / 255.0)


OUT = rgb(20, 14, 18)          # thick black outline
FUR = rgb(230, 162, 175)       # dusty pink
BROWN_EAR = rgb(176, 85, 42)   # rust-brown ear flaps and nose band
NOSE = rgb(20, 14, 18)
WHITE = (1.0, 1.0, 1.0)
MOUTH = rgb(128, 22, 40)
TONGUE = rgb(236, 112, 128)
GRIN = rgb(250, 248, 238)       # off-white clenched teeth
RED = rgb(222, 40, 40)
TONGUE_S = rgb(232, 102, 122)     # scream tongue (pink-red)
TONGUE_LINE = rgb(194, 64, 90)
UVULA_S = rgb(184, 57, 78)
FXGREY = rgb(201, 201, 210)       # effect marks readable on light and dark
RIM_RGB = (255, 247, 242)          # light outer rim behind the silhouette
RIM_ALPHA = 0.92
RIM_R = 1.75                       # rim width (final px)
WOOD = rgb(176, 112, 64)
SWEAT = rgb(132, 202, 255)
YELLOW = rgb(255, 214, 72)
DUST = rgb(232, 224, 212)
LID = rgb(198, 204, 218)
LID_D = rgb(150, 156, 176)
GLASS = rgb(214, 238, 252)
GOLD = rgb(226, 170, 62)
BUBBLE = (1.0, 1.0, 1.0)

W = 2.3                 # visible width of the outer contour (final px)
WD = 1.75               # detail lines (mouth, lids, eye rims)
WEIGHT = (0.45, 0.65)   # extra offset stroke: heavier line on bottom-right


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
Pt = Tuple[float, float]
PathFn = Callable[[cairo.Context], None]


def lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def qbez(p0: Pt, c: Pt, p1: Pt, n: int = 24) -> List[Pt]:
    out = []
    for i in range(n):
        t = i / (n - 1)
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]))
    return out


def ell(cx: float, cy: float, rx: float, ry: float, rot: float = 0.0) -> PathFn:
    def f(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(math.radians(rot))
        ctx.scale(rx, ry)
        ctx.new_sub_path()
        ctx.arc(0, 0, 1, 0, 2 * math.pi)
        ctx.close_path()
        ctx.restore()
    return f


def spline(points: Sequence[Pt], closed: bool = True) -> PathFn:
    """Catmull-Rom spline through the points, emitted as cubic Beziers."""
    pts = list(points)

    def f(ctx: cairo.Context) -> None:
        n = len(pts)
        ctx.new_sub_path()
        ctx.move_to(*pts[0])
        last = n if closed else n - 1
        for i in range(last):
            p0 = pts[(i - 1) % n] if closed else pts[max(i - 1, 0)]
            p1 = pts[i % n]
            p2 = pts[(i + 1) % n] if closed else pts[i + 1]
            p3 = pts[(i + 2) % n] if closed else pts[min(i + 2, n - 1)]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            ctx.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
        if closed:
            ctx.close_path()
    return f


def ribbon(spine: Sequence[Pt], w_fn: Callable[[float], float], cap: bool = True) -> PathFn:
    """Closed outline of a tapered stroke along a spine (ears, tails)."""
    n = len(spine)
    left, right = [], []
    for i, p in enumerate(spine):
        t = i / (n - 1)
        a = spine[max(i - 1, 0)]
        b = spine[min(i + 1, n - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / ln, tx / ln
        w = w_fn(t) / 2
        left.append((p[0] + nx * w, p[1] + ny * w))
        right.append((p[0] - nx * w, p[1] - ny * w))
    pts = left[:]
    if cap:
        p = spine[-1]
        a = spine[-2]
        tx, ty = p[0] - a[0], p[1] - a[1]
        ln = math.hypot(tx, ty) or 1.0
        tx, ty = tx / ln, ty / ln
        nx, ny = -ty, tx
        r = w_fn(1.0) / 2
        for k in range(1, 8):
            ang = math.pi * k / 8
            pts.append((p[0] + nx * r * math.cos(ang) + tx * r * math.sin(ang),
                        p[1] + ny * r * math.cos(ang) + ty * r * math.sin(ang)))
    pts += right[::-1]
    return spline(pts, closed=True)


def noodle(a: Pt, b: Pt, bend: float = 0.0, bend2: Optional[float] = None) -> PathFn:
    """Open cubic from a to b; bend offsets the controls perpendicular to a->b."""
    if bend2 is None:
        bend2 = bend
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln, dx / ln
    c1 = (a[0] + dx / 3 + nx * bend, a[1] + dy / 3 + ny * bend)
    c2 = (a[0] + 2 * dx / 3 + nx * bend2, a[1] + 2 * dy / 3 + ny * bend2)

    def f(ctx: cairo.Context) -> None:
        ctx.move_to(*a)
        ctx.curve_to(c1[0], c1[1], c2[0], c2[1], b[0], b[1])
    return f


@dataclass
class Shape:
    path: PathFn
    width: Optional[float] = None   # None: closed fill; else open stroke width
    fill: Optional[Tuple[float, float, float]] = None


def blob(ctx: cairo.Context, shapes: Sequence[Shape], fill, ow: float = W,
         weight: bool = True) -> None:
    """Draw a union of shapes: outline pass for all, then fill pass for all."""
    offsets = [(0.0, 0.0)]
    if weight:
        offsets.append(WEIGHT)
    ctx.set_source_rgb(*OUT)
    for ox, oy in offsets:
        dx, dy = ctx.device_to_user_distance(ox * SS, oy * SS)
        for s in shapes:
            ctx.save()
            ctx.translate(dx, dy)
            ctx.new_path()
            s.path(ctx)
            ctx.restore()
            ctx.set_line_width(2 * ow if s.width is None else s.width + 2 * ow)
            ctx.stroke()
    for s in shapes:
        ctx.set_source_rgb(*(s.fill or fill))
        ctx.new_path()
        s.path(ctx)
        if s.width is None:
            ctx.fill()
        else:
            ctx.set_line_width(s.width)
            ctx.stroke()


def line(ctx: cairo.Context, path: PathFn, width: float = WD, color=OUT) -> None:
    ctx.set_source_rgb(*color)
    ctx.set_line_width(width)
    ctx.new_path()
    path(ctx)
    ctx.stroke()


def fill_outline(ctx: cairo.Context, path: PathFn, fill, width: float = WD) -> None:
    ctx.new_path()
    path(ctx)
    ctx.set_source_rgb(*fill)
    ctx.fill_preserve()
    ctx.set_source_rgb(*OUT)
    ctx.set_line_width(width)
    ctx.stroke()


# ---------------------------------------------------------------------------
# Effects and props (cell coordinates)
# ---------------------------------------------------------------------------
def fx_sweat(x: float, y: float, rot: float = 0.0, s: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(math.radians(rot))
        ctx.scale(s, s)

        def drop(c: cairo.Context) -> None:
            c.move_to(0, -6)
            c.curve_to(1.5, -3, 4.2, -0.5, 4.2, 2.2)
            c.curve_to(4.2, 4.8, 2.2, 6.4, 0, 6.4)
            c.curve_to(-2.2, 6.4, -4.2, 4.8, -4.2, 2.2)
            c.curve_to(-4.2, -0.5, -1.5, -3, 0, -6)
            c.close_path()
        fill_outline(ctx, drop, SWEAT, 1.5)
        ctx.set_source_rgb(*WHITE)
        ell(-1.6, 2.4, 1.0, 1.6, 20)(ctx)
        ctx.fill()
        ctx.restore()
    return fn


def fx_shake(x: float, y: float, side: int, n: int = 3, size: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        for k in range(n):
            r = (7 + 5 * k) * size
            ctx.new_path()
            a0 = math.radians(-35 if side > 0 else 145)
            ctx.arc(x - side * 6, y, r, a0, a0 + math.radians(70))
            ctx.set_source_rgb(*FXGREY)
            ctx.set_line_width(1.8)
            ctx.stroke()
    return fn


def fx_bang(x: float, y: float, s: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.translate(x, y)
        ctx.scale(s, s)

        def bar(c: cairo.Context) -> None:
            c.move_to(-3.4, -12)
            c.line_to(3.4, -12)
            c.line_to(1.6, 4)
            c.line_to(-1.6, 4)
            c.close_path()
        blob(ctx, [Shape(bar), Shape(ell(0, 9.5, 2.6, 2.6))], YELLOW, ow=1.4, weight=False)
        ctx.restore()
    return fn


def fx_question(x: float, y: float) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        def bubble(c: cairo.Context) -> None:
            ell(x, y, 14, 13)(c)
        def tail(c: cairo.Context) -> None:
            c.move_to(x - 7, y + 8)
            c.line_to(x - 15, y + 20)
            c.line_to(x - 1, y + 11)
            c.close_path()
        blob(ctx, [Shape(bubble), Shape(tail)], BUBBLE, ow=1.6, weight=False)

        def q(c: cairo.Context) -> None:
            c.new_sub_path()
            c.arc(x, y - 3.5, 4.6, math.radians(200), math.radians(400))
            c.curve_to(x + 2.2, y - 0.4, x + 0.2, y + 0.6, x, y + 3.6)
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(2.6)
        ctx.new_path()
        q(ctx)
        ctx.stroke()
        ctx.new_path()
        ell(x, y + 8.0, 1.6, 1.6)(ctx)
        ctx.fill()
    return fn


def fx_dust(x: float, y: float, s: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        shapes = [Shape(ell(x - 5 * s, y, 4.2 * s, 3.4 * s)),
                  Shape(ell(x + 0.5 * s, y - 2.2 * s, 5 * s, 4.2 * s)),
                  Shape(ell(x + 6 * s, y, 3.8 * s, 3.2 * s))]
        blob(ctx, shapes, DUST, ow=1.3, weight=False)
    return fn


def fx_speed(lines: Sequence[Tuple[float, float, float]]) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.set_source_rgb(*FXGREY)
        ctx.set_line_width(1.8)
        for x, y, ln in lines:
            ctx.new_path()
            ctx.move_to(x, y)
            ctx.line_to(x + ln, y)
            ctx.stroke()
    return fn


def fx_arcs(x: float, y: float, start: float, sweep: float, radii=(5.0, 9.0)) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.set_source_rgb(*FXGREY)
        ctx.set_line_width(1.7)
        for r in radii:
            ctx.new_path()
            ctx.arc(x, y, r, math.radians(start), math.radians(start + sweep))
            ctx.stroke()
    return fn


def fx_star(x: float, y: float, r: float = 4.5, color=YELLOW) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        def star(c: cairo.Context) -> None:
            for k in range(8):
                ang = math.pi / 4 * k - math.pi / 2
                rr = r if k % 2 == 0 else r * 0.38
                px, py = x + rr * math.cos(ang), y + rr * math.sin(ang)
                if k == 0:
                    c.move_to(px, py)
                else:
                    c.line_to(px, py)
            c.close_path()
        fill_outline(ctx, star, color, 1.2)
    return fn


def fx_glyph(x: float, y: float, text: str, color, size: float = 10.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(size)
        ext = ctx.text_extents(text)
        ctx.new_path()
        ctx.move_to(x - ext.width / 2 - ext.x_bearing, y + ext.height / 2)
        ctx.text_path(text)
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(2.6)
        ctx.stroke_preserve()
        ctx.set_source_rgb(*color)
        ctx.fill()
        ctx.restore()
    return fn


def fx_taps(x: float, y: float) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.set_source_rgb(*FXGREY)
        ctx.set_line_width(1.5)
        for ang in (-150, -90, -30):
            a = math.radians(ang)
            ctx.new_path()
            ctx.move_to(x + math.cos(a) * 7, y + math.sin(a) * 7)
            ctx.line_to(x + math.cos(a) * 10.5, y + math.sin(a) * 10.5)
            ctx.stroke()
    return fn


# ---------------------------------------------------------------------------
# Character model (traced from the official 3/4-left model sheet pose).
# Model units match a 1000x1108 reference drawing; S maps them to cell px.
# ---------------------------------------------------------------------------
S = 0.16
MX, MY = 500.0, 1090.0          # model point mapped to (CX, GROUND)
OWM = 2.2 / S                   # outer contour (model units)
LWM = 1.8 / S                   # detail lines (eye rims, mouth, chin)
LIMB_OW = 1.25 / S              # outline of the wire limbs
THIN = 0.8 / S                  # whiskers, creases

EAR_ROOT_L = (458.0, 192.0)
EAR_ROOT_R = (636.0, 218.0)
SHOULDER_L = (522.0, 700.0)
SHOULDER_R = (588.0, 700.0)
HIP_L = (455.0, 940.0)
HIP_R = (688.0, 936.0)
LEAN_PIVOT = (575.0, 950.0)

BODY_PTS = [(470, 520), (640, 430), (722, 470), (766, 560), (790, 660), (796, 760), (782, 850),
            (746, 915), (680, 962), (585, 982), (492, 968), (434, 930), (409, 862), (404, 782),
            (412, 718), (432, 684), (445, 610)]
CRANIUM_PTS = [(440, 192), (520, 178), (612, 198), (672, 246), (692, 310), (690, 390), (672, 446),
               (610, 505), (515, 548), (420, 540), (338, 478), (308, 385), (330, 268), (385, 212)]
SNOUT_PTS = [(330, 330), (240, 372), (172, 425), (135, 470), (168, 560), (220, 614), (300, 624),
             (362, 652), (428, 664), (492, 648), (520, 600), (520, 545), (470, 480), (400, 410)]
TIP_PTS = [(150, 488), (90, 484), (52, 510), (44, 556), (66, 598), (125, 610), (190, 602), (210, 540)]
NOSE_PTS = [(116, 456), (230, 452), (330, 474), (412, 520), (350, 572), (262, 626), (210, 612),
            (160, 560), (122, 488)]
NOSE_BAND = [(126, 462), (230, 458), (326, 480), (404, 521), (330, 524), (240, 512), (168, 496),
             (132, 482)]
JAW = [(508, 544), (566, 524), (624, 486), (660, 436), (676, 384)]
CHIN = [(424, 662), (490, 648), (517, 606), (521, 566), (508, 542)]


@dataclass
class Pose:
    gx: float = 0.0          # cell px
    gy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0         # whole-figure rotation (deg) about the feet
    lean: float = 0.0        # upper-body rotation (deg) about the hips
    # ears: (stalk angle, stalk length, flap angle, flap length, flap width); angles from "up", clockwise
    ear_l: Tuple[float, float, float, float, float] = (-18.0, 95.0, -70.0, 258.0, 72.0)
    ear_r: Tuple[float, float, float, float, float] = (49.0, 138.0, 98.0, 256.0, 80.0)
    ear_wave: Tuple[float, float] = (0.0, 0.0)
    eye_scale: float = 1.0
    pupil: Pt = (0.0, 0.0)
    pupil_s: float = 1.0
    lid: Tuple[float, float] = (0.0, 0.0)
    lid_l: Optional[Tuple[float, float]] = None
    blink: bool = False
    brow: float = 1.0        # worry amount
    brow_up: float = 0.0
    mouth: str = "smile"
    mouth_amt: float = 1.0
    hand_l: Pt = (514.0, 812.0)
    hand_r: Pt = (578.0, 818.0)
    bend_l: float = 4.0
    bend_r: float = 4.0
    foot_l: Pt = (452.0, 1056.0)
    foot_r: Pt = (690.0, 1058.0)
    knee_l: float = 6.0
    knee_r: float = -6.0
    tail_rot: float = 0.0
    breath: float = 0.0
    legs: bool = True
    before: List[Callable] = field(default_factory=list)   # model space, unleaned, behind
    after: List[Callable] = field(default_factory=list)    # model space, leaned, front
    after_arms: List[Callable] = field(default_factory=list)  # model space, leaned, above the arms
    front: List[Callable] = field(default_factory=list)       # model space, unleaned, in front of all
    fx: List[Callable] = field(default_factory=list)       # cell space


def pose_matrix(p: Pose) -> cairo.Matrix:
    m = cairo.Matrix()
    m.translate(CX + p.gx, GROUND + p.gy)
    m.rotate(math.radians(p.rot))
    m.scale(p.sx, p.sy)
    m.scale(S, S)
    m.translate(-MX, -MY)
    return m


def lean_matrix(p: Pose) -> cairo.Matrix:
    m = cairo.Matrix()
    m.translate(*LEAN_PIVOT)
    m.rotate(math.radians(p.lean))
    m.translate(-LEAN_PIVOT[0], -LEAN_PIVOT[1])
    return m


def to_cell(p: Pose, x: float, y: float, leaned: bool = True) -> Pt:
    if leaned:
        x, y = lean_matrix(p).transform_point(x, y)
    return pose_matrix(p).transform_point(x, y)


def dirv(angle: float) -> Pt:
    a = math.radians(angle)
    return (math.sin(a), -math.cos(a))


def draw_ear(ctx: cairo.Context, root: Pt, ear, wave: Tuple[float, float]) -> None:
    sa, sl, fa, fl, fw = ear
    d = dirv(sa)
    base = (root[0] + d[0] * sl, root[1] + d[1] * sl)
    ctl = (root[0] + d[0] * sl * 0.5 - d[1] * 6, root[1] + d[1] * sl * 0.5 + d[0] * 6)
    line(ctx, lambda c: (c.move_to(*root), c.curve_to(ctl[0], ctl[1], ctl[0], ctl[1], base[0], base[1])),
         17.0, OUT)
    df = dirv(fa)
    nx, ny = -df[1], df[0]
    amp, ph = wave
    pts = []
    for i in range(26):
        t = i / 25
        off = 10 * math.sin(math.pi * t) + amp * t * math.sin(2 * math.pi * t + ph)
        pts.append((base[0] + df[0] * fl * t + nx * off, base[1] + df[1] * fl * t + ny * off))

    def wfn(t: float) -> float:
        return 15 + (fw - 15) * (math.sin(math.pi * min(t * 1.08, 1.0)) ** 0.7) - 7 * t

    flap = ribbon(pts, wfn)
    blob(ctx, [Shape(flap)], OUT, ow=OWM * 0.45, weight=False)
    # rust-brown inner face on the upper side (towards the head when the ear stands up)
    side = 1.0 if ny < 0 else -1.0
    if abs(ny) < 0.35:
        mid = pts[len(pts) // 2]
        side = 1.0 if (nx * (560 - mid[0])) > 0 else -1.0
    bpts = []
    for i, p in enumerate(pts):
        t = i / 25
        s_ = math.sin(math.pi * t)
        bpts.append((p[0] + nx * side * fw * 0.2 * s_, p[1] + ny * side * fw * 0.2 * s_))
    ctx.save()
    ctx.new_path()
    flap(ctx)
    ctx.clip()
    ctx.new_path()
    ribbon(bpts[2:24], lambda t: fw * 0.46 * (math.sin(math.pi * t) ** 0.8) + 2, cap=True)(ctx)
    ctx.set_source_rgb(*BROWN_EAR)
    ctx.fill()
    ctx.restore()


def star_path(cx: float, cy: float, r1: float, r2: float, rot: float) -> PathFn:
    def f(c: cairo.Context) -> None:
        c.new_sub_path()
        for k in range(10):
            ang = math.radians(rot + 36 * k) - math.pi / 2
            r = r1 if k % 2 == 0 else r2
            x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
            if k == 0:
                c.move_to(x, y)
            else:
                c.line_to(x, y)
        c.close_path()
    return f


def plus_paw(ctx: cairo.Context, x: float, y: float, angle: float, size: float = 1.0,
             fill=None) -> None:
    """Tiny cross-shaped hand/foot: three rounded lobes plus the wrist, pointing along angle."""
    fill = fill or FUR
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(math.radians(angle))
    ctx.scale(size, size)
    shapes = [Shape(ell(0, 0, 12, 12)), Shape(ell(-19, 3, 13, 10.5)), Shape(ell(19, 3, 13, 10.5)),
              Shape(ell(0, 19, 10.5, 13))]
    blob(ctx, shapes, fill, ow=LIMB_OW, weight=False)
    ctx.restore()


def draw_limb(ctx: cairo.Context, a: Pt, b: Pt, bend: float, width: float, paw: float,
              fill=None) -> None:
    fill = fill or FUR
    blob(ctx, [Shape(noodle(a, b, bend, bend * 0.6), width=width)], fill, ow=LIMB_OW, weight=False)
    ang = math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) + 180.0
    plus_paw(ctx, b[0], b[1], ang, paw, fill)


def draw_eye(ctx: cairo.Context, cx: float, cy: float, rx: float, ry: float, rot: float,
             pupil: Pt, ps: float, lid: Tuple[float, float], side: int, blink: bool) -> None:
    eye = ell(cx, cy, rx, ry, rot)
    if blink:
        fill_outline(ctx, eye, FUR, LWM)
        line(ctx, lambda c: (c.move_to(cx - rx * 0.85, cy + 8),
                             c.curve_to(cx - rx * 0.3, cy + 40, cx + rx * 0.3, cy + 40, cx + rx * 0.85, cy + 8)),
             LWM)
        return
    ctx.new_path()
    eye(ctx)
    ctx.set_source_rgb(*WHITE)
    ctx.fill()
    ctx.save()
    ctx.new_path()
    eye(ctx)
    ctx.clip()
    px, py = pupil
    ctx.new_path()
    ell(px, py, 13 * ps, 24 * ps, rot * 0.5)(ctx)
    ctx.set_source_rgb(*NOSE)
    ctx.fill()
    if ps > 0.6:
        ctx.new_path()
        ell(px + 3 * ps, py - 9 * ps, 5.5 * ps, 6.5 * ps)(ctx)
        ctx.set_source_rgb(*WHITE)
        ctx.fill()
    level, slant = lid
    if level > 0:
        yl = cy - ry + 2 * ry * level
        x_in, x_out = cx + side * (rx + 12), cx - side * (rx + 12)
        y_in, y_out = yl - slant * ry * 0.35, yl + slant * ry * 0.35

        def lidp(c: cairo.Context) -> None:
            c.move_to(x_in, y_in)
            c.line_to(x_out, y_out)
            c.line_to(x_out, cy - ry - 30)
            c.line_to(x_in, cy - ry - 30)
            c.close_path()
        ctx.new_path()
        lidp(ctx)
        ctx.set_source_rgb(*FUR)
        ctx.fill()
        line(ctx, lambda c: (c.move_to(x_in, y_in), c.line_to(x_out, y_out)), LWM)
    ctx.restore()
    line(ctx, eye, LWM * 1.1)


def brow(ctx: cairo.Context, p0: Pt, c: Pt, p1: Pt, w: float = 24.0) -> None:
    pts = qbez(p0, c, p1, 18)
    path = ribbon(pts, lambda t: 3 + w * math.sin(math.pi * (0.15 + 0.85 * t)) ** 1.2)
    ctx.new_path()
    path(ctx)
    ctx.set_source_rgb(*OUT)
    ctx.fill()


def offset_curve(pts: Sequence[Pt], d: float) -> List[Pt]:
    out = []
    n = len(pts)
    for i, p in enumerate(pts):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, n - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty) or 1.0
        out.append((p[0] - ty / ln * d, p[1] + tx / ln * d))
    return out


def jaw_dense() -> List[Pt]:
    path = []
    for i in range(len(JAW) - 1):
        for t in np.linspace(0, 1, 6, endpoint=False):
            path.append(lerp(JAW[i], JAW[i + 1], float(t)))
    path.append(JAW[-1])
    return path


def draw_mouth(ctx: cairo.Context, p: Pose) -> None:
    kind = p.mouth
    if kind in ("smile", "frown", "flat"):
        line(ctx, spline(JAW, closed=False), LWM)
        curl = {"smile": 1.0, "frown": -1.0, "flat": 0.0}[kind] * p.mouth_amt
        line(ctx, lambda c: (c.move_to(640, 360 - 4 * curl),
                             c.curve_to(660, 372 + 12 * curl, 690, 366 + 10 * curl, 708, 352 - 8 * curl)), LWM)
    elif kind == "grit":
        # short clenched grin sitting on the jaw line near the cheek (not along the whole jaw)
        line(ctx, spline(JAW[:3], closed=False), LWM)
        cx, cy, ang = 620.0, 478.0, -30.0
        L, H, r = 150.0, 88.0 * p.mouth_amt, 32.0 * p.mouth_amt
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(math.radians(ang))

        def grin(c: cairo.Context) -> None:
            x0, x1, y0, y1 = -L / 2, L / 2, -H / 2, H / 2
            c.new_sub_path()
            c.move_to(x0 + r, y0 + 4)
            c.curve_to(-L * 0.15, y0 + 9, L * 0.15, y0 + 9, x1 - r, y0 + 4)
            c.arc(x1 - r, y0 + r + 2, r, -math.pi / 2, 0)
            c.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
            c.curve_to(L * 0.15, y1 + 6, -L * 0.15, y1 + 6, x0 + r, y1)
            c.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
            c.arc(x0 + r, y0 + r + 2, r, math.pi, math.pi * 1.5)
            c.close_path()
        ctx.new_path()
        grin(ctx)
        ctx.set_source_rgb(*GRIN)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        grin(ctx)
        ctx.clip()
        tl = THIN * 1.25
        line(ctx, lambda c: (c.move_to(-L / 2, 3), c.curve_to(-L * 0.2, 7, L * 0.2, 7, L / 2, 3)), tl)
        for x in (-L * 0.2, L * 0.03, L * 0.25):
            line(ctx, lambda c, x=x: (c.move_to(x, -H / 2), c.line_to(x, H / 2 + 6)), tl)
        ctx.restore()
        line(ctx, grin, LWM)
        ctx.restore()
    elif kind == "open":
        jd = jaw_dense()
        upper = offset_curve(jd, -14)
        a = p.mouth_amt
        lower = [(x + 30 * a * math.sin(math.pi * i / (len(jd) - 1)),
                  y + 95 * a * math.sin(math.pi * (i / (len(jd) - 1)) ** 0.8))
                 for i, (x, y) in enumerate(jd)]
        hole = spline(upper + lower[::-1], closed=True)
        ctx.new_path()
        hole(ctx)
        ctx.set_source_rgb(*MOUTH)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        hole(ctx)
        ctx.clip()
        ctx.set_source_rgb(*TONGUE)
        ell(600, 600 + 30 * (1 - a), 70, 45, -30)(ctx)
        ctx.fill()
        band = offset_curve(jd, 2)
        ctx.new_path()
        ribbon(band, lambda t: 30)(ctx)
        ctx.set_source_rgb(*GRIN)
        ctx.fill()
        tl = THIN * 1.25
        line(ctx, spline(offset_curve(jd, 17), closed=False), tl)
        top, bot = offset_curve(jd, -16), offset_curve(jd, 17)
        n = len(jd)
        for f in (0.3, 0.52, 0.74):
            i = int(round(f * (n - 1)))
            line(ctx, lambda c, a=top[i], b=bot[i]: (c.move_to(*a), c.line_to(*b)), tl)
        ctx.restore()
        line(ctx, hole, LWM)
    elif kind == "o":
        a = p.mouth_amt
        hole = ell(548, 560, 26 + 12 * a, 30 + 18 * a, -20)
        fill_outline(ctx, hole, MOUTH, LWM)
        line(ctx, spline(JAW[2:], closed=False), LWM)


def draw_face_details(ctx: cairo.Context, p: Pose) -> None:
    line(ctx, spline(CHIN, closed=False), OWM * 0.85)
    draw_mouth(ctx, p)
    ctx.set_source_rgb(*OUT)
    for (x, y) in ((450, 560), (405, 603), (458, 606)):
        ctx.new_path()
        ell(x, y, 7.5, 8.5)(ctx)
        ctx.fill()
    line(ctx, lambda c: (c.move_to(495, 584), c.curve_to(530, 588, 560, 594, 590, 606)), THIN)
    # snout tip: dot and whiskers
    ctx.new_path()
    ell(106, 550, 8, 9)(ctx)
    ctx.set_source_rgb(*OUT)
    ctx.fill()
    for (a, b) in (((78, 520), (22, 490)), ((64, 538), (2, 548)), ((82, 508), (52, 470))):
        line(ctx, lambda c, a=a, b=b: (c.move_to(*a), c.line_to(*b)), THIN)
    # nose wedge: black with a rust-brown top band
    ctx.new_path()
    spline(NOSE_PTS)(ctx)
    ctx.set_source_rgb(*OUT)
    ctx.fill()
    ctx.new_path()
    spline(NOSE_BAND)(ctx)
    ctx.set_source_rgb(*BROWN_EAR)
    ctx.fill()


def draw_eyes(ctx: cairo.Context, p: Pose) -> None:
    es = p.eye_scale
    eyes = [((532, 335 - 20 * (es - 1)), 70 * es, 132 * es, 3, (545, 347), 1),
            ((372, 302 - 20 * (es - 1)), 60 * es, 110 * es, 15, (392, 325), -1)]
    for (c, rx, ry, rot, pup, side) in eyes:
        lid = p.lid_l if (side < 0 and p.lid_l is not None) else p.lid
        pup = (pup[0] + p.pupil[0], pup[1] + p.pupil[1] - 20 * (es - 1))
        draw_eye(ctx, c[0], c[1], rx, ry, rot, pup, p.pupil_s, lid, side, p.blink)
    if not p.blink:
        line(ctx, lambda c: (c.move_to(494, 462 + 40 * (es - 1)),
                             c.curve_to(512, 470, 530, 472, 550, 470)), THIN * 1.3)
        line(ctx, lambda c: (c.move_to(288, 374), c.curve_to(296, 392, 306, 402, 322, 410)), THIN * 1.3)
    # worried brows: inner ends go up with worry
    w = p.brow
    up = p.brow_up + 25 * (es - 1)
    brow(ctx, (308, 224 + 12 * (1 - w) - up), (360, 176 - up), (433, 156 - 16 * (w - 1) - up))
    brow(ctx, (523, 160 - 16 * (w - 1) - up), (586, 168 - up), (606, 248 + 12 * (1 - w) - up))


def draw_character(ctx: cairo.Context, p: Pose) -> None:
    ctx.save()
    ctx.transform(pose_matrix(p))
    for fn in p.before:
        fn(ctx)
    lm = lean_matrix(p)
    if p.legs:
        for hip, foot, knee in ((HIP_L, p.foot_l, p.knee_l), (HIP_R, p.foot_r, p.knee_r)):
            h = lm.transform_point(*hip)
            draw_limb(ctx, h, foot, knee, 16.0, 1.0)
    ctx.save()
    ctx.transform(lm)
    draw_ear(ctx, EAR_ROOT_L, p.ear_l, p.ear_wave)
    draw_ear(ctx, EAR_ROOT_R, p.ear_r, (p.ear_wave[0], p.ear_wave[1] + 1.9))
    blob(ctx, [Shape(star_path(836, 772, 54, 22, 20 + p.tail_rot))], FUR, ow=OWM)
    body = spline(BODY_PTS)
    ctx.save()
    by = 980
    ctx.translate(600, by)
    ctx.scale(1 + 0.012 * p.breath, 1 + 0.02 * p.breath)
    ctx.translate(-600, -by)
    blob(ctx, [Shape(body), Shape(spline(CRANIUM_PTS)), Shape(ell(722, 372, 44, 66)),
               Shape(spline(SNOUT_PTS)), Shape(spline(TIP_PTS))], FUR, ow=OWM)
    ctx.save()
    ctx.new_path()
    body(ctx)
    ctx.clip()
    ctx.set_source_rgb(*OUT)
    for (x, y, rx, ry) in ((786, 588, 28, 40), (778, 690, 40, 54)):
        ctx.new_path()
        ell(x, y, rx, ry)(ctx)
        ctx.fill()
    ctx.set_source_rgb(*FUR)
    ctx.new_path()
    ell(790, 690, 13, 23)(ctx)
    ctx.fill()
    ctx.restore()
    draw_face_details(ctx, p)
    draw_eyes(ctx, p)
    ctx.restore()
    for fn in p.after:
        fn(ctx)
    draw_limb(ctx, SHOULDER_L, p.hand_l, p.bend_l, 13.0, 0.95)
    draw_limb(ctx, SHOULDER_R, p.hand_r, p.bend_r, 13.0, 0.95)
    for fn in p.after_arms:
        fn(ctx)
    ctx.restore()
    for fn in p.front:
        fn(ctx)
    ctx.restore()
    for fn in p.fx:
        fn(ctx)


# ---------------------------------------------------------------------------
# Scream (failed): the whole dog becomes a gaping, bristling face
# ---------------------------------------------------------------------------
SCREAM_S = 0.182                     # scream ball is traced in 1080x1080 reference units
SCREAM_C = (560.0, 525.0)            # reference point mapped to the cell anchor
SC_OW = 1.6 / SCREAM_S               # outer contour
SC_LW = 1.5 / SCREAM_S               # eyes, mouth, teeth
SC_HAIR = 0.75 / SCREAM_S            # hair strokes
SC_TEETH = rgb(239, 234, 162)        # show-style pale yellow teeth (scream only)
SC_MOUTH = rgb(232, 48, 42)
SC_TONGUE = rgb(168, 72, 61)
SC_TONGUE_EDGE = rgb(232, 135, 74)
SC_TONGUE_IN = rgb(122, 44, 38)
SC_UVULA = rgb(242, 162, 58)
SC_UVULA_IN = rgb(250, 214, 92)
BALL_C, BALL_R = (560.0, 650.0), (212.0, 228.0)


def _ball_pt(theta: float, scale: float = 1.0) -> Pt:
    return (BALL_C[0] + BALL_R[0] * scale * math.cos(theta), BALL_C[1] + BALL_R[1] * scale * math.sin(theta))


def _lens(a: Pt, b: Pt, w: float) -> List[Pt]:
    pts = []
    for i in range(18):
        t = i / 17
        pts.append(lerp(a, b, t))
    return pts


def _tooth(cx: float, cy: float, w: float, h: float, rot: float, dot: bool = False) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(math.radians(rot))
        r = min(w, h) * 0.38

        def path(c: cairo.Context) -> None:
            x0, y0, x1, y1 = -w / 2, -h / 2, w / 2, h / 2
            c.new_sub_path()
            c.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
            c.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
            c.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
            c.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
            c.close_path()
        fill_outline(ctx, path, SC_TEETH, SC_LW)
        if dot:
            fill_outline(ctx, ell(-w * 0.12, h * 0.05, w * 0.14, w * 0.14), WHITE, SC_LW * 0.8)
        ctx.restore()
    return fn


def draw_scream(ctx: cairo.Context, k: int, with_fx: bool = True) -> None:
    """Failed row: the whole dog becomes one bristling, screaming fur ball (after the show)."""
    rng = np.random.default_rng(4000 + k)
    shake = [(-1.6, 0.4), (1.6, -0.4), (-1.0, -0.6), (1.8, 0.5), (-1.8, 0.0), (1.0, 0.6), (-1.4, -0.4), (1.4, 0.2)][k]
    ctx.save()
    ctx.translate(CX + 2 + shake[0], 106 + shake[1])
    ctx.rotate(math.radians(1.2 * shake[0]))
    ctx.scale(SCREAM_S, SCREAM_S)
    ctx.translate(-SCREAM_C[0], -SCREAM_C[1])

    # four thin limbs sticking out radially, quivering
    limbs = [((395, 440), (282, 402), 1.0), ((735, 700), (836, 702), 1.0),
             ((420, 862), (234, 912), 1.1), ((590, 875), (602, 988), 1.1)]
    bases = []
    for j, (a0, b0, paw) in enumerate(limbs):
        q = 12 * math.sin(k * 2.3 + j * 1.7)
        dx, dy = b0[0] - a0[0], b0[1] - a0[1]
        ln = math.hypot(dx, dy)
        b = (b0[0] - dy / ln * q, b0[1] + dx / ln * q)
        blob(ctx, [Shape(noodle(a0, b, 0, 0), width=13.0)], FUR, ow=6.5, weight=False)
        ang = math.degrees(math.atan2(b[0] - a0[0], -(b[1] - a0[1]))) + 180.0
        ctx.save()
        ctx.translate(*b)
        ctx.rotate(math.radians(ang))
        blob(ctx, [Shape(ell(0, 0, 11, 11)), Shape(ell(-17, 3, 12, 9.5)), Shape(ell(17, 3, 12, 9.5)),
                   Shape(ell(0, 17, 9.5, 12))], FUR, ow=6.5, weight=False)
        ctx.restore()
        bases.append((a0, b))

    # stiff spike ears pointing up and to the right (brown with a black underside), vibrating
    for (base, tip, w) in (((655, 250), (752, 52), 54), ((690, 425), (952, 196), 62)):
        vib = 5 * math.sin(k * 2.9 + base[0])
        tip = (tip[0] + vib, tip[1] - vib * 0.5)
        spine = _lens(base, tip, w)
        flap = ribbon(spine, lambda t, w=w: 8 + (w - 8) * math.sin(math.pi * min(t * 1.05, 1.0)) ** 0.8 - 6 * t)
        ctx.new_path()
        flap(ctx)
        ctx.set_source_rgb(*OUT)
        ctx.fill()
        tx, ty = tip[0] - base[0], tip[1] - base[1]
        ln = math.hypot(tx, ty)
        nx, ny = -ty / ln, tx / ln
        if ny > 0:
            nx, ny = -nx, -ny
        brown = [(p[0] + nx * w * 0.14 * math.sin(math.pi * i / 17), p[1] + ny * w * 0.14 * math.sin(math.pi * i / 17))
                 for i, p in enumerate(spine)]
        ctx.save()
        ctx.new_path()
        flap(ctx)
        ctx.clip()
        ctx.new_path()
        ribbon(brown[1:17], lambda t, w=w: 2 + w * 0.6 * math.sin(math.pi * t) ** 0.8)(ctx)
        ctx.set_source_rgb(*BROWN_EAR)
        ctx.fill()
        ctx.restore()

    # bristling fur ball: irregular spikes that flicker between frames
    spikes = []
    angles = list(np.linspace(math.radians(-58), math.radians(152), 34)) + \
        list(np.linspace(math.radians(196), math.radians(248), 6))
    for i, th in enumerate(angles):
        th = th + math.radians(rng.uniform(-2.5, 2.5))
        L = rng.uniform(0.1, 0.36) if (i + k) % 2 == 0 else rng.uniform(0.06, 0.2)
        d = math.radians(rng.uniform(1.5, 2.8))
        p0, p1 = _ball_pt(th - d, 0.94), _ball_pt(th + d, 0.94)
        tip = _ball_pt(th + math.radians(rng.uniform(-3, 3)), 1.0 + L)
        spikes.append(Shape(lambda c, p0=p0, p1=p1, tip=tip: (c.move_to(*p0), c.line_to(*tip),
                                                            c.line_to(*p1), c.close_path())))
    snout = spline([(470, 520), (380, 548), (300, 562), (292, 600), (360, 622), (450, 612)])
    snout_tip = spline([(300, 560), (252, 556), (228, 580), (236, 612), (282, 622), (318, 604)])
    head = spline([(380, 400), (470, 360), (600, 380), (660, 450), (560, 520), (420, 520)])
    blob(ctx, spikes + [Shape(ell(BALL_C[0], BALL_C[1], BALL_R[0], BALL_R[1])), Shape(head),
                        Shape(snout), Shape(snout_tip)], FUR, ow=SC_OW)
    # thin hair strokes and trembling hatch marks at the limb bases
    for i in range(22):
        th = math.radians(-50 + 200 * i / 21 + rng.uniform(-4, 4))
        a, b = _ball_pt(th, 0.9), _ball_pt(th + math.radians(rng.uniform(-6, 6)), 1.08 + rng.uniform(0, 0.3))
        line(ctx, lambda c, a=a, b=b: (c.move_to(*a), c.line_to(*b)), SC_HAIR)
    for (a0, b) in bases:
        dx, dy = b[0] - a0[0], b[1] - a0[1]
        ln = math.hypot(dx, dy)
        ux, uy = dx / ln, dy / ln
        for s_ in range(3):
            m = (a0[0] + ux * (40 + 16 * s_), a0[1] + uy * (40 + 16 * s_))
            jit = 6 * math.sin(k * 3.1 + s_)
            line(ctx, lambda c, m=m, jit=jit: (c.move_to(m[0] - uy * 22 + ux * jit, m[1] + ux * 22 + uy * jit),
                                              c.line_to(m[0] + uy * 22 - ux * jit, m[1] - ux * 22 - uy * jit)),
                 SC_HAIR * 1.1)

    # huge red crescent mouth opening towards the lower right
    a = 0.94 + 0.06 * (k % 2)
    mouth = spline([(470, 600), (560, 582), (640, 598), (694, 660), (702, 752 * a + 40 * (1 - a)),
                    (652, 838 * a + 40 * (1 - a)), (540, 874 * a + 30 * (1 - a)), (420, 868 * a + 30 * (1 - a)),
                    (340, 818), (318, 740), (362, 688), (420, 652)])
    ctx.new_path()
    mouth(ctx)
    ctx.set_source_rgb(*SC_MOUTH)
    ctx.fill()
    ctx.save()
    ctx.new_path()
    mouth(ctx)
    ctx.clip()
    # uvula: orange "U" at the back of the mouth
    u = (lambda c: (c.move_to(596, 640), c.curve_to(590, 720, 664, 722, 662, 642)))
    line(ctx, u, 30, SC_UVULA)
    line(ctx, u, 11, SC_UVULA_IN)
    for (x, y, w, h, r) in ((410, 836, 62, 74, -8), (494, 838, 60, 70, -2), (566, 816, 54, 64, 12),
                            (624, 782, 46, 54, 30)):
        _tooth(x, y, w, h, r)(ctx)
    _tooth(556, 618, 60, 86, 4)(ctx)
    _tooth(432, 690, 82, 104, -14, dot=True)(ctx)
    ctx.restore()
    line(ctx, mouth, SC_LW * 1.15)
    # wedge nose, snout dots and whiskers
    ctx.new_path()
    spline([(304, 540), (372, 528), (440, 556), (430, 590), (380, 622), (340, 610), (312, 578)])(ctx)
    ctx.set_source_rgb(*OUT)
    ctx.fill()
    ctx.new_path()
    spline([(312, 544), (372, 534), (432, 558), (420, 572), (372, 570), (330, 562)])(ctx)
    ctx.set_source_rgb(*BROWN_EAR)
    ctx.fill()
    ctx.set_source_rgb(*OUT)
    for (x, y) in ((262, 596), (404, 624), (432, 634), (420, 610)):
        ctx.new_path()
        ell(x, y, 5.5, 6.5)(ctx)
        ctx.fill()
    for (p0, p1) in (((246, 572), (206, 546)), ((240, 588), (200, 584)), ((250, 566), (232, 534))):
        line(ctx, lambda c, p0=p0, p1=p1: (c.move_to(*p0), c.line_to(*p1)), SC_HAIR)
    # fang hanging under the snout
    _tooth(318, 650, 48, 70, 10)(ctx)
    # two huge tall eyes with small pupils (tilted head)
    look = 5 * math.sin(k * 2.1)
    for (cx, cy, rx, ry, rot, px, py) in ((445, 335, 84, 120, 22, 462 + look, 340),
                                          (588, 360, 100, 142, 18, 568 + look, 332)):
        eye = ell(cx, cy, rx, ry, rot)
        fill_outline(ctx, eye, WHITE, SC_LW)
        ctx.new_path()
        ell(px, py, 13, 21, rot)(ctx)
        ctx.set_source_rgb(*OUT)
        ctx.fill()
    # the tongue goes on top of everything, with only its own outline
    ph = k * math.pi / 4
    tpts = [(640 - 470 * t, 736 - 10 * (1 - t) ** 2 + 24 * math.sin(2 * math.pi * (1.6 * t) - ph) * (0.3 + 0.7 * t))
            for t in np.linspace(0, 1, 40)]
    tongue = ribbon(tpts, lambda t: 50 - 6 * t)
    ctx.new_path()
    tongue(ctx)
    ctx.set_source_rgb(*SC_TONGUE_EDGE)
    ctx.set_line_width(13)
    ctx.stroke_preserve()
    ctx.set_source_rgb(*SC_TONGUE)
    ctx.fill()
    ctx.save()
    ctx.new_path()
    tongue(ctx)
    ctx.clip()
    for off in (-9, 10):
        line(ctx, spline(offset_curve(tpts[3:-2], off), closed=False), 6, SC_TONGUE_IN)
    ctx.restore()
    ctx.restore()
    if not with_fx:
        return
    fx_shake(20, 150, -1, 3, 0.9)(ctx)
    fx_shake(172, 118, 1, 3, 0.9)(ctx)
    if k % 2 == 0:
        fx_sweat(30 + (k % 4) * 2, 40 + k, -40)(ctx)
    else:
        fx_sweat(150 - (k % 4) * 2, 30 + k, 40)(ctx)


# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------
CRT = rgb(230, 223, 192)        # show-style beige CRT computer
CRT_D = rgb(196, 186, 146)
CRT_SCREEN = rgb(127, 224, 224)
CRT_TEXT = rgb(46, 140, 150)
DESK = rgb(200, 116, 58)
DESK_D = rgb(166, 90, 44)
WORK_S = 0.86                   # scene scale for the work row (Courage + computer must fit)
WORK_GX = 30.0


def prop_crt(k: int) -> Callable:
    """Chunky beige CRT on a base unit, screen facing Courage (model faces left; the row is mirrored)."""
    def fn(ctx: cairo.Context) -> None:
        ow = OWM * 0.6
        # base unit
        blob(ctx, [Shape(lambda c: (c.move_to(-330, 742), c.line_to(-60, 736), c.line_to(-40, 754),
                                    c.line_to(-40, 822), c.line_to(-330, 824), c.close_path()))], CRT_D, ow=ow,
             weight=False)
        blob(ctx, [Shape(lambda c: c.rectangle(-200, 754, 160, 68))], CRT, ow=ow, weight=False)
        line(ctx, lambda c: (c.move_to(-180, 790), c.line_to(-80, 790)), THIN * 1.4)
        ctx.new_path()
        ell(-70, 806, 7, 7)(ctx)
        ctx.set_source_rgb(*CRT_TEXT if k % 2 else OUT)
        ctx.fill()
        # deep monitor body (side) and stand
        blob(ctx, [Shape(lambda c: c.rectangle(-170, 700, 90, 46))], CRT_D, ow=ow, weight=False)
        blob(ctx, [Shape(lambda c: (c.move_to(-200, 362), c.line_to(-325, 418), c.line_to(-325, 652),
                                    c.line_to(-200, 712), c.close_path()))], CRT_D, ow=ow, weight=False)
        # front bezel facing right, thick, with a curved glowing screen
        bezel = (lambda c: (c.move_to(-210, 352), c.curve_to(-120, 336, -60, 330, -10, 334),
                            c.line_to(-6, 714), c.curve_to(-60, 718, -120, 720, -210, 722), c.close_path()))
        blob(ctx, [Shape(bezel)], CRT, ow=ow, weight=False)
        screen = (lambda c: (c.move_to(-182, 392), c.curve_to(-120, 372, -70, 370, -36, 376),
                             c.curve_to(-26, 500, -26, 560, -36, 676), c.curve_to(-80, 684, -130, 684, -182, 680),
                             c.curve_to(-192, 580, -192, 480, -182, 392), c.close_path()))
        ctx.new_path()
        screen(ctx)
        ctx.set_source_rgb(*CRT_SCREEN)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        screen(ctx)
        ctx.clip()
        for j in range(8):
            y = 410 + j * 32 - (k * 16) % 32
            x0 = -168 + (j * 37 % 3) * 12
            ln = 40 + (j * 53 + k * 29) % 70
            line(ctx, lambda c, y=y, x0=x0, ln=ln: (c.move_to(x0, y), c.line_to(x0 + ln, y)), 12, CRT_TEXT)
        ctx.new_path()
        ctx.arc(-110, 500, 120, math.radians(200), math.radians(250))
        ctx.set_source_rgb(*WHITE)
        ctx.set_line_width(12)
        ctx.stroke()
        ctx.restore()
        line(ctx, screen, LWM * 0.8)
        # chunky keyboard on the desk in front of Courage
        blob(ctx, [Shape(lambda c: (c.move_to(40, 786), c.line_to(410, 776), c.line_to(430, 806),
                                    c.line_to(430, 826), c.line_to(30, 828), c.line_to(30, 808), c.close_path()))],
             CRT, ow=ow, weight=False)
        for j in range(5):
            x = 70 + j * 68
            line(ctx, lambda c, x=x: (c.move_to(x, 792), c.line_to(x + 50, 791)), THIN * 1.6, CRT_D)
        line(ctx, lambda c: (c.move_to(32, 808), c.line_to(430, 806)), THIN * 1.2)
    return fn


def prop_desk(ctx: cairo.Context) -> None:
    ow = OWM * 0.6
    blob(ctx, [Shape(lambda c: c.rectangle(-340, 860, 1160, 226))], DESK_D, ow=ow, weight=False)
    blob(ctx, [Shape(lambda c: c.rectangle(-356, 822, 1192, 40))], DESK, ow=ow, weight=False)
    line(ctx, lambda c: (c.move_to(-290, 930), c.line_to(-60, 930)), THIN * 1.6)
    ctx.new_path()
    ell(-175, 960, 12, 9)(ctx)
    ctx.set_source_rgb(*OUT)
    ctx.fill()


def work_pose(k: int) -> Pose:
    up = k % 2 == 0
    hl = (236, 760 if up else 782)
    hr = (330, 782 if up else 760)
    return Pose(gx=WORK_GX, sx=WORK_S, sy=WORK_S, gy=(-1.0 if up else 0.0), lean=-7 - (1.5 if up else 0.0), legs=False,
                mouth="grit", mouth_amt=0.75, brow=1.15, pupil=(-22, 8), pupil_s=0.85, lid=(0.24, 0.45),
                hand_l=hl, hand_r=hr, bend_l=-26, bend_r=-30,
                ear_l=(-24 + (5 if up else 0), 92, -72 + (8 if up else 0), 236, 70),
                ear_r=(40, 120, 70 - (0 if up else 8), 226, 76),
                tail_rot=(14 if up else -10), before=[prop_crt(k)], front=[prop_desk])


def draw_work(ctx: cairo.Context, k: int, with_fx: bool = True) -> None:
    """Running (work) row: Courage hunched over the show's beige CRT, typing frantically."""
    p = work_pose(k)
    ctx.save()
    ctx.translate(CELL_W, 0)
    ctx.scale(-1, 1)        # the model faces left; mirror so he faces the monitor on the right
    draw_character(ctx, replace(p, fx=[]))
    if with_fx:
        # cyan glow spilling from the screen onto his face (flickers subtly)
        ctx.save()
        ctx.transform(pose_matrix(p))
        g = cairo.RadialGradient(-60, 520, 30, -60, 520, 300)
        alpha = 0.30 + 0.05 * (k % 3 == 1)
        g.add_color_stop_rgba(0, CRT_SCREEN[0], CRT_SCREEN[1], CRT_SCREEN[2], alpha)
        g.add_color_stop_rgba(1, CRT_SCREEN[0], CRT_SCREEN[1], CRT_SCREEN[2], 0.0)
        ctx.set_source(g)
        ctx.new_path()
        ell(-60, 520, 300, 300)(ctx)
        ctx.fill()
        ctx.restore()
    ctx.restore()
    if not with_fx:
        return

    def cellpt(x: float, y: float) -> Pt:
        cx, cy = to_cell(p, x, y)
        return (CELL_W - cx, cy)
    down = p.hand_r if k % 2 == 0 else p.hand_l
    tx, ty = cellpt(*down)
    fx_taps(tx, ty - 3)(ctx)
    if k % 2 == 0:
        x, y = cellpt(120, 715)
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(math.radians(-12))
        fx_glyph(0, 0, "TICKA", WHITE, 10.5)(ctx)
        ctx.restore()
    if k in (1, 4):
        fx_sweat(*cellpt(560, 150), 30, 0.9)(ctx)


def magnifier(look: Pt, lens_c: Pt) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        lx, ly = lens_c
        R = 132
        h0 = (lx + R * 0.5, ly + R * 0.87)
        h1 = (lx + R * 0.5 + 60, ly + R * 0.87 + 150)
        blob(ctx, [Shape(lambda c: (c.move_to(*h0), c.line_to(*h1)), width=30)], WOOD, ow=LIMB_OW, weight=False)
        lens = ell(lx, ly, R, R)
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*GLASS)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        lens(ctx)
        ctx.clip()
        ctx.translate(lx, ly)
        ctx.scale(1.25, 1.25)
        ctx.translate(-lx, -ly)
        draw_eye(ctx, 532, 335, 70, 132, 3, (545 + look[0], 347 + look[1]), 1.1, (0.0, 0.0), 1, False)
        ctx.restore()
        ctx.new_path()
        ctx.arc(lx, ly, R - 30, math.radians(200), math.radians(245))
        ctx.set_source_rgb(*WHITE)
        ctx.set_line_width(16)
        ctx.stroke()
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(44)
        ctx.stroke()
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*GOLD)
        ctx.set_line_width(20)
        ctx.stroke()
    return fn


# ---------------------------------------------------------------------------
# Rows (the model faces left; running-right is the mirrored run cycle)
# ---------------------------------------------------------------------------
def row_idle() -> List[Pose]:
    out = []
    pupils = [(0, 0), (0, 0), (-12, 4), (10, 0), (0, 0), (0, -3)]
    for k in range(6):
        s = math.sin(k / 6 * 2 * math.pi)
        out.append(Pose(breath=s, pupil=pupils[k], blink=(k == 4), tail_rot=8 * s,
                        ear_l=(-18, 95, -70 + 4 * s, 258, 72), ear_r=(49, 138, 98 - 4 * s, 256, 80),
                        brow_up=3 * s))
    return out


def row_run_left() -> List[Pose]:
    out = []
    for i in range(8):
        phi = 2 * math.pi * i / 8
        feet = []
        for ph, hip in ((phi, HIP_L), (phi + math.pi, HIP_R)):
            x = hip[0] - 70 + 150 * math.cos(ph)
            lift = 110 * max(0.0, math.sin(ph))
            feet.append((x, 1056 - lift))
        sw = math.cos(phi)
        flap = 8 * math.sin(phi * 2)
        k = i % 4
        fx = [fx_speed([(160 + k * 2, 100, 16 - k * 3), (164 - (k * 3) % 5, 128, 16), (160 + k, 156, 12 + k * 2)])]
        if i in (0, 4):
            fx.append(fx_dust(150, 194, 1.0))
        if i in (2, 6):
            fx.append(fx_sweat(156, 60, 35, 0.9))
        out.append(Pose(gx=2, gy=-4 * abs(math.sin(phi)), lean=-11, mouth="grit", mouth_amt=0.7,
                        pupil=(-14, 2), pupil_s=0.75, brow=1.3, eye_scale=1.05,
                        ear_l=(34, 80, 102 + flap, 178, 56), ear_r=(74, 100, 114 - flap, 172, 62),
                        ear_wave=(16, phi), tail_rot=20 * sw,
                        foot_l=feet[0], foot_r=feet[1], knee_l=26, knee_r=26,
                        hand_l=(400 - 60 * sw, 640 + 40 * sw), hand_r=(470 + 60 * sw, 620 - 40 * sw),
                        bend_l=-16, bend_r=16, fx=fx))
    return out


def row_waving() -> List[Pose]:
    hands = [(790, 470), (830, 525), (790, 470), (750, 432)]
    out = []
    for k, h in enumerate(hands):
        p = Pose(mouth="open", mouth_amt=0.9, brow=0.25, pupil=(6, -6), hand_r=h, bend_r=-30,
                 hand_l=(514, 812), ear_l=(-10, 95, -45, 250, 64), ear_r=(35, 130, 60 + 6 * (k % 2), 250, 72),
                 tail_rot=[0, 15, 0, -15][k])
        cx, cy = to_cell(p, h[0], h[1])
        if k in (1, 3):
            p.fx = [fx_arcs(cx, cy, -60 if k == 1 else 200, 60, (9.0, 13.5))]
        out.append(p)
    return out


def row_jumping() -> List[Pose]:
    crouch = Pose(sy=0.88, sx=1.07, mouth="grit", mouth_amt=0.9, eye_scale=1.08, pupil_s=0.7,
                  brow=1.3, ear_l=(-30, 90, -150, 240, 62), ear_r=(60, 130, 150, 240, 70),
                  foot_l=(430, 1056), foot_r=(712, 1058), knee_l=20, knee_r=-20,
                  hand_l=(500, 760), hand_r=(570, 765), fx=[fx_bang(36, 60, 1.0)])
    up = Pose(gy=-16, sy=1.07, sx=0.95, mouth="open", mouth_amt=0.7, eye_scale=1.12, pupil_s=0.6,
              brow=1.4, ear_l=(-14, 90, -170, 230, 60), ear_r=(30, 125, 170, 230, 66),
              foot_l=(462, 1066), foot_r=(678, 1068), knee_l=2, knee_r=-2,
              hand_l=(440, 610), hand_r=(640, 600), bend_l=-12, bend_r=12, fx=[fx_bang(30, 46, 0.9)])
    peak = Pose(gy=-8, mouth="open", mouth_amt=1.0, eye_scale=1.15, pupil_s=0.55, brow=1.5,
                ear_l=(-14, 80, -48, 196, 58), ear_r=(28, 105, 46, 196, 64), ear_wave=(10, 0.5),
                foot_l=(470, 1010), foot_r=(670, 1012), knee_l=30, knee_r=-30,
                hand_l=(380, 600), hand_r=(700, 590), bend_l=-14, bend_r=14,
                fx=[fx_sweat(30, 70, -30), fx_sweat(168, 72, 30)])
    down = Pose(gy=-1, sy=1.03, sx=0.97, mouth="open", mouth_amt=0.6, eye_scale=1.1, pupil_s=0.65, brow=1.3,
                ear_l=(-10, 76, -30, 178, 58), ear_r=(24, 98, 34, 178, 64), ear_wave=(8, 2.0),
                foot_l=(458, 1066), foot_r=(684, 1068), knee_l=3, knee_r=-3,
                hand_l=(420, 640), hand_r=(660, 640), bend_l=-12, bend_r=12)
    land = Pose(sy=0.9, sx=1.06, mouth="smile", brow=0.7, pupil=(0, 6),
                ear_l=(-40, 85, -120, 245, 64), ear_r=(70, 125, 125, 245, 72),
                foot_l=(428, 1056), foot_r=(712, 1058), knee_l=18, knee_r=-18,
                hand_l=(500, 800), hand_r=(590, 806),
                fx=[fx_dust(54, 196, 1.1), fx_dust(142, 196, 1.1)])
    return [crouch, up, peak, down, land]


def row_waiting() -> List[Pose]:
    trem = [-0.8, 0.8, -0.6, 0.8, -0.8, 0.6]
    out = []
    for k in range(6):
        bob = 2.0 * math.sin(k / 6 * 2 * math.pi)
        fx = [fx_question(160, 34 + bob)]
        if k % 2 == 0:
            fx += [fx_shake(64, 192, -1, 2, 0.6), fx_shake(132, 192, 1, 2, 0.6)]
        out.append(Pose(gx=trem[k], mouth="grit", mouth_amt=0.7, brow=1.5, brow_up=8,
                        pupil=(12, -26), pupil_s=1.05, lid=(0.0, 0.0),
                        ear_l=(-35, 85, -150 + 4 * trem[k], 240, 62), ear_r=(68, 125, 150 - 4 * trem[k], 240, 70),
                        hand_l=(486, 612), hand_r=(530, 618), bend_l=-20, bend_r=-26,
                        knee_l=-8 + 4 * trem[k], knee_r=8 - 4 * trem[k],
                        foot_l=(470, 1056), foot_r=(676, 1058), tail_rot=6 * trem[k], fx=fx))
    return out


def row_review() -> List[Pose]:
    scan = [-24.0, 0.0, 24.0, 36.0, 12.0, -12.0]
    out = []
    for k in range(6):
        s = scan[k]
        lens = (532 + s * 0.6, 330)
        handle_end = (lens[0] + 132 * 0.5 + 60, lens[1] + 132 * 0.87 + 150)
        out.append(Pose(mouth="flat", brow=0.9, pupil=(s * 0.5, 4), lid_l=(0.5, 0.3), lid=(0.0, 0.0),
                        hand_r=handle_end, bend_r=10, hand_l=(470, 668), bend_l=-14,
                        ear_l=(-18, 95, -66, 258, 64), ear_r=(49, 138, 92, 256, 72),
                        tail_rot=5 * math.sin(k), breath=0.6 * math.sin(k * math.pi / 3),
                        after=[magnifier((s * 0.7, 6), lens)]))
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def paint(draw: Callable[[cairo.Context], None]) -> Image.Image:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_W * SS, CELL_H * SS)
    ctx = cairo.Context(surf)
    ctx.scale(SS, SS)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    draw(ctx)
    surf.flush()
    # cairo ARGB32 = premultiplied BGRA (little endian); "BGRa" unpremultiplies.
    return Image.frombuffer("RGBA", (CELL_W * SS, CELL_H * SS), bytes(surf.get_data()),
                            "raw", "BGRa", surf.get_stride(), 1).copy()


def dilate(alpha: np.ndarray, r: float) -> np.ndarray:
    """Grey-scale dilation of an alpha mask with a disc of radius r (pixels)."""
    ri = int(math.ceil(r))
    h, w = alpha.shape
    pad = np.pad(alpha, ri)
    out = np.zeros_like(alpha)
    for dy in range(-ri, ri + 1):
        for dx in range(-ri, ri + 1):
            d = math.hypot(dx, dy)
            if d > r + 0.5:
                continue
            k = min(1.0, r + 0.5 - d)  # soft rim edge
            out = np.maximum(out, pad[ri + dy:ri + dy + h, ri + dx:ri + dx + w] * k)
    return out


def render(draw: Callable[[cairo.Context], None],
           rim_draw: Optional[Callable[[cairo.Context], None]] = None) -> Image.Image:
    big = paint(draw)
    if rim_draw is not None:
        sil = np.asarray(paint(rim_draw))[:, :, 3].astype(np.float32) / 255.0
        rim_a = dilate(sil, RIM_R * SS)
        rim = np.zeros(sil.shape + (4,), np.uint8)
        rim[:, :, :3] = RIM_RGB
        rim[:, :, 3] = np.round(rim_a * RIM_ALPHA * 255).astype(np.uint8)
        layer = Image.fromarray(rim)
        layer.alpha_composite(big)
        big = layer
    small = big.resize((CELL_W, CELL_H), Image.LANCZOS)  # Pillow resamples RGBA premultiplied
    arr = np.array(small)
    arr[arr[:, :, 3] == 0] = 0
    return Image.fromarray(arr)


def front(p: Pose) -> Image.Image:
    return render(lambda c: draw_character(c, p), lambda c: draw_character(c, replace(p, fx=[])))


def build_frames() -> List[List[Image.Image]]:
    run_left = [front(p) for p in row_run_left()]
    rows = [
        [front(p) for p in row_idle()],
        [ImageOps.mirror(im) for im in run_left],
        run_left,
        [front(p) for p in row_waving()],
        [front(p) for p in row_jumping()],
        [render(lambda c, k=k: draw_scream(c, k), lambda c, k=k: draw_scream(c, k, False)) for k in range(8)],
        [front(p) for p in row_waiting()],
        [render(lambda c, k=k: draw_work(c, k), lambda c, k=k: draw_work(c, k, False)) for k in range(6)],
        [front(p) for p in row_review()],
    ]
    for (name, count), frames in zip(ROW_SPEC, rows):
        assert len(frames) == count, (name, len(frames))
    return rows


def build_atlas(rows: List[List[Image.Image]]) -> Image.Image:
    atlas = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    for r, frames in enumerate(rows):
        for c, im in enumerate(frames):
            atlas.paste(im, (c * CELL_W, r * CELL_H))
    return atlas


def font(size: int) -> ImageFont.ImageFont:
    path = "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf"
    return ImageFont.truetype(path, size) if os.path.exists(path) else ImageFont.load_default()


def cell(atlas: Image.Image, r: int, c: int) -> Image.Image:
    return atlas.crop((c * CELL_W, r * CELL_H, (c + 1) * CELL_W, (r + 1) * CELL_H))


def on_bg(im: Image.Image, color=(128, 128, 128, 255)) -> Image.Image:
    bg = Image.new("RGBA", im.size, color)
    bg.alpha_composite(im)
    return bg


def contact_sheet(atlas: Image.Image, path: str) -> None:
    label_w = 150
    sheet = Image.new("RGBA", (label_w + ATLAS_W, ATLAS_H), (128, 128, 128, 255))
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(22), font(18)
    for r, (name, count) in enumerate(ROW_SPEC):
        y = r * CELL_H
        d.text((8, y + CELL_H // 2 - 26), f"{r} {name}", fill=(255, 255, 255, 255), font=f1)
        d.text((8, y + CELL_H // 2 + 4), f"{count} frames", fill=(225, 225, 225, 255), font=f2)
        for c in range(COLS + 1):
            x = label_w + c * CELL_W
            d.line([(x, y), (x, y + CELL_H)], fill=(108, 108, 108, 255))
        d.line([(0, y), (label_w + ATLAS_W, y)], fill=(108, 108, 108, 255))
    sheet.alpha_composite(atlas, (label_w, 0))
    sheet.convert("RGB").save(path)


def export_gifs(rows: List[List[Image.Image]]) -> None:
    durations = {"idle": 170, "running-right": 80, "running-left": 80, "waving": 150,
                 "jumping": 130, "failed": 70, "waiting": 150, "running": 90, "review": 170}
    for (name, _), frames in zip(ROW_SPEC, rows):
        flat = [on_bg(im).convert("RGB") for im in frames]
        flat[0].save(os.path.join(OUT_DIR, f"row-{name}.gif"), save_all=True,
                     append_images=flat[1:], duration=durations[name], loop=0, disposal=2)


def zoom(atlas: Image.Image, cells: Sequence[Tuple[int, int]], scale: int, path: str) -> None:
    tiles = [on_bg(cell(atlas, r, c)).resize((CELL_W * scale, CELL_H * scale), Image.LANCZOS)
             for r, c in cells]
    out = Image.new("RGB", (CELL_W * scale * len(tiles), CELL_H * scale), (128, 128, 128))
    for k, t in enumerate(tiles):
        out.paste(t.convert("RGB"), (k * CELL_W * scale, 0))
    out.save(path)


def vs_reference(atlas: Image.Image, ref_path: str, path: str) -> None:
    if not os.path.exists(ref_path):
        return
    ref = Image.open(ref_path).convert("RGBA")
    h = 416
    ref = ref.resize((round(ref.width * h / ref.height), h), Image.LANCZOS)
    ours = cell(atlas, 0, 0).resize((CELL_W * 2, CELL_H * 2), Image.LANCZOS)
    out = Image.new("RGBA", (ref.width + ours.width + 30, h + 30), (255, 255, 255, 255))
    out.alpha_composite(ref, (0, 30))
    out.alpha_composite(ours, (ref.width + 30, 30))
    d = ImageDraw.Draw(out)
    d.text((6, 4), "reference", fill=(0, 0, 0, 255), font=font(18))
    d.text((ref.width + 36, 4), "agallas-cartoon v2 idle[0] (2x)", fill=(0, 0, 0, 255), font=font(18))
    out.convert("RGB").save(path)


def before_after(atlas: Image.Image, old_path: str, path: str) -> None:
    if not os.path.exists(old_path):
        return
    old = Image.open(old_path).convert("RGBA")
    pairs = [("v1 idle", old, 0), ("v2 idle", atlas, 0), ("v1 failed", old, 5), ("v2 failed", atlas, 5)]
    tw, th = CELL_W * 2, CELL_H * 2
    out = Image.new("RGBA", (tw * 4, th + 28), (128, 128, 128, 255))
    d = ImageDraw.Draw(out)
    for i, (label, src, r) in enumerate(pairs):
        t = on_bg(cell(src, r, 0)).resize((tw, th), Image.LANCZOS)
        out.alpha_composite(t, (i * tw, 28))
        d.text((i * tw + 8, 4), label, fill=(255, 255, 255, 255), font=font(18))
    out.convert("RGB").save(path)


def dark_light_check(atlas: Image.Image, path: str) -> None:
    picks = [(0, 0, "idle c0"), (1, 3, "running-right c3"), (5, 3, "failed c3"), (3, 1, "waving c1")]
    tw, th = CELL_W * 2, CELL_H * 2
    out = Image.new("RGBA", (tw * len(picks), (th + 26) * 2), (90, 90, 90, 255))
    d = ImageDraw.Draw(out)
    for j, bg in enumerate(((245, 245, 245, 255), (30, 32, 44, 255))):
        for i, (r, c, label) in enumerate(picks):
            t = on_bg(cell(atlas, r, c), bg).resize((tw, th), Image.LANCZOS)
            out.alpha_composite(t, (i * tw, j * (th + 26) + 26))
            d.text((i * tw + 6, j * (th + 26) + 4), f"{label} on #{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}",
                   fill=(255, 255, 255, 255), font=font(16))
    out.convert("RGB").save(path)


def vs_reference_cells(atlas: Image.Image, refs: Sequence[str], cells: Sequence[Tuple[int, int]],
                       dark_cell: Tuple[int, int], path: str) -> None:
    """References scaled to 416 px high, chosen cells at 2x on light, plus one cell on #1E202C."""
    h = 416
    tiles = []
    for rp in refs:
        if os.path.exists(rp):
            im = Image.open(rp).convert("RGBA")
            tiles.append(on_bg(im.resize((round(im.width * h / im.height), h), Image.LANCZOS), (255, 255, 255, 255)))
    for (r, c) in cells:
        tiles.append(on_bg(cell(atlas, r, c), (245, 245, 245, 255)).resize((CELL_W * 2, h), Image.LANCZOS))
    tiles.append(on_bg(cell(atlas, *dark_cell), (30, 32, 44, 255)).resize((CELL_W * 2, h), Image.LANCZOS))
    out = Image.new("RGBA", (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), h), (90, 90, 90, 255))
    x = 0
    for t in tiles:
        out.alpha_composite(t, (x, 0))
        x += t.width + 10
    out.convert("RGB").save(path)


def install(src_dir: str) -> None:
    targets = [os.path.expanduser(f"~/.codex/pets/{PET_ID}"), os.path.expanduser(f"~/.petdex/pets/{PET_ID}")]
    backup = os.path.join(HERE, "backup-v1")
    if not os.path.isfile(os.path.join(backup, "spritesheet.webp")):
        sys.exit("refusing to overwrite: backup-v1 is missing")
    for t in targets:
        os.makedirs(t, exist_ok=True)
        extra = set(os.listdir(t)) - {"pet.json", "spritesheet.webp"}
        if extra:
            sys.exit(f"refusing to touch unexpected files in {t}: {sorted(extra)}")
        for fn in ("pet.json", "spritesheet.webp"):
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(t, fn))
        print("installed", t)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    pet_dir = os.path.join(OUT_DIR, PET_ID)
    os.makedirs(pet_dir, exist_ok=True)
    rows = build_frames()
    atlas = build_atlas(rows)
    atlas.save(os.path.join(pet_dir, "spritesheet.webp"), "WEBP", lossless=True, quality=100,
               method=6, exact=True)
    with open(os.path.join(pet_dir, "pet.json"), "w", encoding="utf-8") as fh:
        json.dump(PET_JSON, fh, indent=2)
        fh.write("\n")
    contact_sheet(atlas, os.path.join(OUT_DIR, "preview.png"))
    zoom(atlas, [(0, 0), (5, 0), (8, 0)], 3, os.path.join(OUT_DIR, "zoom.png"))
    vs_reference(atlas, REF_IMAGE, os.path.join(OUT_DIR, "vs-reference.png"))
    before_after(atlas, os.path.join(HERE, "backup-v1", "spritesheet.webp"),
                 os.path.join(OUT_DIR, "before-after.png"))
    dark_light_check(atlas, os.path.join(OUT_DIR, "dark-light-check.png"))
    imgs = os.path.dirname(REF_IMAGE)
    vs_reference_cells(atlas, [os.path.join(imgs, "4.png")], [(5, 0), (5, 4)], (5, 0),
                       os.path.join(OUT_DIR, "vs-reference-scream.png"))
    vs_reference_cells(atlas, [os.path.join(imgs, "6.png"), os.path.join(imgs, "8.png")], [(7, 0), (7, 3)], (7, 0),
                       os.path.join(OUT_DIR, "vs-reference-computer.png"))
    export_gifs(rows)
    print("wrote", pet_dir)
    if "--install" in sys.argv:
        install(pet_dir)


if __name__ == "__main__":
    main()
