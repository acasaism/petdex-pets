#!/usr/bin/env python3
"""Generate the "agallas-cartoon" Codex pet: Courage the Cowardly Dog in a flat cartoon style.

Everything is drawn procedurally with pycairo (Bezier paths, round caps/joins,
native antialiasing) at 2x and downscaled with Pillow LANCZOS.

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
        "A cartoon-style pink, cowardly dog with huge eyes and floppy dark ears who "
        "screams at every error but always gets the job done."
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
GROUND = 198.0  # bottom of the feet
NECK = (CX, 128.0)
HEAD_Y = 100.0  # head centre at rest


# ---------------------------------------------------------------------------
# Palette (flat fills, no gradients)
# ---------------------------------------------------------------------------
def rgb(r: int, g: int, b: int) -> Tuple[float, float, float]:
    return (r / 255.0, g / 255.0, b / 255.0)


OUT = rgb(36, 22, 40)          # outline
PINK = rgb(242, 162, 204)      # fur
PINK_FAR = rgb(216, 132, 182)  # far-side limbs in profile
EAR = rgb(48, 36, 56)          # ears and back spots
NOSE = rgb(24, 18, 30)
NOSE_HI = rgb(140, 128, 152)
WHITE = (1.0, 1.0, 1.0)
MOUTH = rgb(112, 22, 54)
TONGUE = rgb(242, 108, 140)
UVULA = rgb(214, 66, 104)
SWEAT = rgb(132, 202, 255)
YELLOW = rgb(255, 214, 72)
DUST = rgb(232, 224, 212)
LID = rgb(198, 204, 218)
LID_D = rgb(150, 156, 176)
GLASS = rgb(214, 238, 252)
GOLD = rgb(226, 170, 62)
BROWN = rgb(132, 80, 44)
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
            ctx.set_source_rgb(*OUT)
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
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(1.8)
        for x, y, ln in lines:
            ctx.new_path()
            ctx.move_to(x, y)
            ctx.line_to(x + ln, y)
            ctx.stroke()
    return fn


def fx_arcs(x: float, y: float, start: float, sweep: float, radii=(5.0, 9.0)) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.set_source_rgb(*OUT)
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
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(1.5)
        for ang in (-150, -90, -30):
            a = math.radians(ang)
            ctx.new_path()
            ctx.move_to(x + math.cos(a) * 7, y + math.sin(a) * 7)
            ctx.line_to(x + math.cos(a) * 10.5, y + math.sin(a) * 10.5)
            ctx.stroke()
    return fn


def prop_laptop(ctx: cairo.Context, glow: float = 0.0) -> None:
    def lid(c: cairo.Context) -> None:
        c.move_to(70, 160)
        c.line_to(122, 160)
        c.curve_to(125, 160, 126, 161, 126.5, 163)
        c.line_to(132, 184)
        c.line_to(60, 184)
        c.line_to(65.5, 163)
        c.curve_to(66, 161, 67, 160, 70, 160)
        c.close_path()

    def base(c: cairo.Context) -> None:
        c.move_to(56, 183)
        c.line_to(136, 183)
        c.curve_to(141, 183, 142, 186, 141, 189)
        c.curve_to(140, 192, 138, 193, 134, 193)
        c.line_to(58, 193)
        c.curve_to(54, 193, 52, 192, 51, 189)
        c.curve_to(50, 186, 51, 183, 56, 183)
        c.close_path()
    blob(ctx, [Shape(base)], LID_D)
    blob(ctx, [Shape(lid)], LID)
    # bone logo on the back of the lid
    bx, by = 96.0, 173.0
    bone = [Shape(ell(bx - 9, by - 2.4, 3.0, 3.0)), Shape(ell(bx - 9, by + 2.4, 3.0, 3.0)),
            Shape(ell(bx + 9, by - 2.4, 3.0, 3.0)), Shape(ell(bx + 9, by + 2.4, 3.0, 3.0)),
            Shape(lambda c: (c.rectangle(bx - 9, by - 2.2, 18, 4.4)))]
    blob(ctx, bone, PINK if glow < 0.5 else rgb(255, 196, 226), ow=1.1, weight=False)


# ---------------------------------------------------------------------------
# Front-facing character
# ---------------------------------------------------------------------------
Ear = Tuple[float, float, float]  # (start angle, end angle, length); 0 = hanging, 180 = up


@dataclass
class Pose:
    gx: float = 0.0
    gy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    breath: float = 0.0
    hdx: float = 0.0
    hdy: float = 0.0
    hrot: float = 0.0
    ear_l: Ear = (58.0, 6.0, 50.0)
    ear_r: Ear = (58.0, 6.0, 50.0)
    ear_wave: Tuple[float, float] = (0.0, 0.0)  # amplitude, phase
    eye_scale: float = 1.0
    eye_dy: float = 0.0
    pupil: Pt = (0.0, 0.0)
    pupil_r: float = 3.0
    gloss: bool = False
    lid: Tuple[float, float] = (0.0, 0.0)       # level (0 open .. 1 closed), slant (+ worried)
    lid_l: Optional[Tuple[float, float]] = None  # per-eye override
    mouth: str = "wavy"
    mouth_amt: float = 1.0
    mouth_phase: float = 0.0
    hand_l: Pt = (CX - 25, 161.0)
    hand_r: Pt = (CX + 25, 161.0)
    bend_l: float = 4.0
    bend_r: float = -4.0
    foot_l: Pt = (CX - 12, 194.5)
    foot_r: Pt = (CX + 12, 194.5)
    knee_l: float = -2.0
    knee_r: float = 2.0
    tail: float = 0.0
    legs: bool = True
    hands_front: bool = True
    arms_over_props: bool = False
    before: List[Callable] = field(default_factory=list)   # world-space, behind character
    behind_head: List[Callable] = field(default_factory=list)
    after: List[Callable] = field(default_factory=list)    # world-space, after character
    fx: List[Callable] = field(default_factory=list)       # cell-space overlays


def ear_spine(side: int, ear: Ear, wave: Tuple[float, float]) -> List[Pt]:
    a0, a1, length = ear
    u = min(max((a0 - 90.0) / 90.0, 0.0), 1.0)
    root = (side * (24.0 - 7.0 * u), -13.0 - 9.0 * u)

    def d(a: float) -> Pt:
        r = math.radians(a)
        return (side * math.sin(r), math.cos(r))
    d0, d1 = d(a0), d(a1)
    c = (root[0] + d0[0] * length * 0.48, root[1] + d0[1] * length * 0.48)
    tip = (c[0] + d1[0] * length * 0.55, c[1] + d1[1] * length * 0.55)
    pts = qbez(root, c, tip, 26)
    amp, ph = wave
    if amp:
        out = []
        n = len(pts)
        for i, p in enumerate(pts):
            t = i / (n - 1)
            a = pts[max(i - 1, 0)]
            b = pts[min(i + 1, n - 1)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(tx, ty) or 1.0
            off = amp * t * math.sin(t * 2.6 * math.pi + ph)
            out.append((p[0] - ty / ln * off, p[1] + tx / ln * off))
        pts = out
    return pts


def draw_ear(ctx: cairo.Context, side: int, ear: Ear, wave: Tuple[float, float]) -> None:
    spine = ear_spine(side, ear, wave)
    wmax = 12.5
    path = ribbon(spine, lambda t: wmax * (0.62 + 0.38 * min(1.0, t * 2.4)) - 1.2 * t)
    blob(ctx, [Shape(path)], EAR)


def draw_eye(ctx: cairo.Context, ex: float, ey: float, rx: float, ry: float, side: int,
             pupil: Pt, pr: float, lid: Tuple[float, float], gloss: bool) -> None:
    level, slant = lid
    eye = ell(ex, ey, rx, ry)
    if level >= 0.95:  # closed: pink lid with a lash curve
        fill_outline(ctx, eye, PINK, WD * 1.1)

        def lash(c: cairo.Context) -> None:
            c.move_to(ex - rx * 0.8, ey + 1)
            c.curve_to(ex - rx * 0.3, ey + 5, ex + rx * 0.3, ey + 5, ex + rx * 0.8, ey + 1)
        line(ctx, lash, WD)
        return
    ctx.new_path()
    eye(ctx)
    ctx.set_source_rgb(*WHITE)
    ctx.fill()
    ctx.save()
    ctx.new_path()
    eye(ctx)
    ctx.clip()
    px, py = ex + pupil[0], ey + pupil[1]
    ctx.set_source_rgb(*NOSE)
    ctx.new_path()
    ell(px, py, pr, pr * 1.08)(ctx)
    ctx.fill()
    if gloss:
        ctx.set_source_rgb(*WHITE)
        ctx.new_path()
        ell(px - pr * 0.38, py - pr * 0.42, pr * 0.34, pr * 0.34)(ctx)
        ctx.fill()
    if level > 0.0:
        # lid line: inner end (towards the nose) higher when slant > 0 (worried)
        yl = ey - ry + 2 * ry * level
        inner_x, outer_x = ex - side * (rx + 2), ex + side * (rx + 2)
        y_in, y_out = yl - slant * ry * 0.6, yl + slant * ry * 0.6

        def lidp(c: cairo.Context) -> None:
            c.move_to(inner_x, y_in)
            c.line_to(outer_x, y_out)
            c.line_to(outer_x, ey - ry - 4)
            c.line_to(inner_x, ey - ry - 4)
            c.close_path()
        ctx.new_path()
        lidp(ctx)
        ctx.set_source_rgb(*PINK)
        ctx.fill()
        line(ctx, lambda c: (c.move_to(inner_x, y_in), c.line_to(outer_x, y_out)), WD * 1.1)
    ctx.restore()
    line(ctx, eye, WD * 1.15)


def scream_jaw(a: float) -> PathFn:
    return ell(0, 16 + 10 * a, 20 + 4 * a, 17 + 11 * a)


def scream_hole(a: float) -> PathFn:
    top = 11.0
    bot = top + 12 + 25 * a
    hw = 12 + 5 * a

    def f(c: cairo.Context) -> None:
        c.new_sub_path()
        c.move_to(-hw, top)
        c.curve_to(-hw * 0.5, top - 4, hw * 0.5, top - 4, hw, top)
        c.curve_to(hw * 1.15, top + (bot - top) * 0.55, hw * 0.6, bot, 0, bot)
        c.curve_to(-hw * 0.6, bot, -hw * 1.15, top + (bot - top) * 0.55, -hw, top)
        c.close_path()
    return f


def draw_mouth(ctx: cairo.Context, p: Pose) -> None:
    kind = p.mouth
    if kind == "wavy":
        pts = [(x, 26.5 + 1.2 * math.sin(x * 0.75 + p.mouth_phase)) for x in np.linspace(-9, 9, 10)]
        line(ctx, spline(pts, closed=False), WD)
        line(ctx, lambda c: (c.move_to(-10.5, 24.5), c.line_to(-9, 26.5)), WD * 0.8)
        line(ctx, lambda c: (c.move_to(10.5, 24.5), c.line_to(9, 26.5)), WD * 0.8)
    elif kind == "grimace":
        top, bot, hw = 21.5, 29.5, 10.5

        def teeth(c: cairo.Context) -> None:
            c.move_to(-hw, top + 1)
            c.curve_to(-hw * 0.4, top - 0.6, hw * 0.4, top - 0.6, hw, top + 1)
            c.curve_to(hw + 1.5, top + 4, hw + 0.5, bot - 1, hw - 1, bot)
            c.curve_to(hw * 0.4, bot + 1, -hw * 0.4, bot + 1, -hw + 1, bot)
            c.curve_to(-hw - 0.5, bot - 1, -hw - 1.5, top + 4, -hw, top + 1)
            c.close_path()
        fill_outline(ctx, teeth, WHITE, WD)
        mid = (top + bot) / 2 + 0.3
        line(ctx, lambda c: (c.move_to(-hw + 0.5, mid),
                             c.curve_to(-3, mid + 0.8, 3, mid - 0.8, hw - 0.5, mid)), WD * 0.75)
        for x in (-5.5, 0.0, 5.5):
            line(ctx, lambda c, x=x: (c.move_to(x, top + 0.6), c.line_to(x, bot - 0.4)), WD * 0.7)
    elif kind == "smile":
        def sm(c: cairo.Context) -> None:
            c.move_to(-11, 21.5)
            c.curve_to(-4, 23.5, 4, 23.5, 11, 21.5)
            c.curve_to(9, 29, 4, 31, 0, 31)
            c.curve_to(-4, 31, -9, 29, -11, 21.5)
            c.close_path()
        ctx.new_path()
        sm(ctx)
        ctx.set_source_rgb(*MOUTH)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        sm(ctx)
        ctx.clip()
        ctx.set_source_rgb(*TONGUE)
        ell(0, 31.5, 6.5, 4.0)(ctx)
        ctx.fill()
        ctx.set_source_rgb(*WHITE)
        ctx.rectangle(-12, 19, 24, 6.4)
        ctx.fill()
        ctx.restore()
        line(ctx, sm, WD)
    elif kind == "o":
        a = p.mouth_amt
        fill_outline(ctx, ell(0, 27, 3.8 * a + 1, 4.6 * a + 1), MOUTH, WD)
    elif kind == "open":
        a = p.mouth_amt
        hole = ell(0, 26 + 1.5 * a, 6.5 + 2 * a, 5 + 3 * a)
        ctx.new_path()
        hole(ctx)
        ctx.set_source_rgb(*MOUTH)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        hole(ctx)
        ctx.clip()
        ctx.set_source_rgb(*TONGUE)
        ell(0, 30 + 4 * a, 6 + 2 * a, 4)(ctx)
        ctx.fill()
        ctx.restore()
        line(ctx, hole, WD)
    elif kind == "scream":
        a = p.mouth_amt
        hole = scream_hole(a)
        ctx.new_path()
        hole(ctx)
        ctx.set_source_rgb(*MOUTH)
        ctx.fill()
        ctx.save()
        ctx.new_path()
        hole(ctx)
        ctx.clip()
        bot = 11 + 12 + 25 * a
        wag = 3.0 * math.sin(p.mouth_phase)
        ctx.set_source_rgb(*TONGUE)
        ell(wag, bot - 3, 11 + 3 * a, 7 + 3 * a, wag * 2)(ctx)
        ctx.fill()
        line(ctx, lambda c: (c.move_to(wag, bot - 8 - 2 * a), c.line_to(wag * 1.2, bot - 3)), WD * 0.8)
        # uvula
        uv = 0.8 * math.sin(p.mouth_phase * 1.7)

        def uvula(c: cairo.Context) -> None:
            c.move_to(-2.4, 8)
            c.line_to(2.4, 8)
            c.curve_to(2.4, 13, 3.4 + uv, 16, 0 + uv, 17.5)
            c.curve_to(-3.4 + uv, 16, -2.4, 13, -2.4, 8)
            c.close_path()
        fill_outline(ctx, uvula, UVULA, WD * 0.7)
        # two upper teeth
        hw = 12 + 5 * a
        for sx in (-1, 1):
            def tooth(c: cairo.Context, sx=sx) -> None:
                x0 = sx * (hw - 7)
                c.move_to(x0 - 3.2, 9)
                c.line_to(x0 + 3.2, 9)
                c.line_to(x0 + 2.2, 16)
                c.curve_to(x0 + 1, 17, x0 - 1, 17, x0 - 2.2, 16)
                c.close_path()
            fill_outline(ctx, tooth, WHITE, WD * 0.7)
        ctx.restore()
        line(ctx, hole, WD * 1.1)


def draw_head(ctx: cairo.Context, p: Pose) -> None:
    for side, ear in ((-1, p.ear_l), (1, p.ear_r)):
        wave = (p.ear_wave[0], p.ear_wave[1] + (0 if side < 0 else 1.7))
        draw_ear(ctx, side, ear, wave)
    for fn in p.behind_head:
        fn(ctx)
    shapes = [Shape(ell(0, 0, 31, 27))]
    if p.mouth == "scream":
        shapes.append(Shape(scream_jaw(p.mouth_amt)))
    else:
        shapes.append(Shape(ell(0, 18, 16.5, 14)))
    blob(ctx, shapes, PINK)
    # eyes
    es = p.eye_scale
    ey = -9.0 + p.eye_dy - (2.5 if p.mouth == "scream" else 0.0)
    rx, ry = 12.4 * es, 14.8 * es
    for side in (-1, 1):
        lid = p.lid_l if (side < 0 and p.lid_l is not None) else p.lid
        draw_eye(ctx, side * 12.0 * min(es, 1.06), ey, rx, ry, side,
                 p.pupil, p.pupil_r, lid, p.gloss)
    draw_mouth(ctx, p)
    # nose
    ny = 13.5 if p.mouth != "scream" else 6.0
    ctx.new_path()
    ell(0, ny, 6.8, 4.7)(ctx)
    ctx.set_source_rgb(*NOSE)
    ctx.fill()
    ctx.new_path()
    ell(-2.3, ny - 1.6, 2.1, 1.1, -10)(ctx)
    ctx.set_source_rgb(*NOSE_HI)
    ctx.fill()


def head_matrix(p: Pose) -> cairo.Matrix:
    m = cairo.Matrix()
    m.translate(NECK[0], NECK[1] - p.breath * 1.2)
    m.rotate(math.radians(p.hrot))
    m.translate(p.hdx, HEAD_Y - NECK[1] + p.hdy)
    return m


def torso_path(breath: float) -> PathFn:
    top, bottom, hw = 124.0, 178.0, 16.0

    def f(c: cairo.Context) -> None:
        c.save()
        c.translate(CX, bottom)
        c.scale(1 + 0.03 * breath, 1 + 0.035 * breath)
        c.translate(-CX, -bottom)
        c.move_to(CX - 8, top)
        c.curve_to(CX - 12, top + 12, CX - hw - 1, bottom - 28, CX - hw, bottom - 12)
        c.curve_to(CX - hw + 1, bottom - 2, CX - 8, bottom + 1, CX, bottom + 1)
        c.curve_to(CX + 8, bottom + 1, CX + hw - 1, bottom - 2, CX + hw, bottom - 12)
        c.curve_to(CX + hw + 1, bottom - 28, CX + 12, top + 12, CX + 8, top)
        c.close_path()
        c.restore()
    return f


def draw_arm(ctx: cairo.Context, side: int, hand: Pt, bend: float, breath: float) -> None:
    sh = (CX + side * 10.0, 137.0 - breath * 1.0)
    blob(ctx, [Shape(noodle(sh, hand, bend, bend * 0.6), width=5.0),
               Shape(ell(hand[0], hand[1], 4.6, 4.2))], PINK)


def draw_front(ctx: cairo.Context, p: Pose) -> None:
    ctx.save()
    ctx.translate(CX + p.gx, GROUND + p.gy)
    ctx.rotate(math.radians(p.rot))
    ctx.scale(p.sx, p.sy)
    ctx.translate(-CX, -GROUND)
    for fn in p.before:
        fn(ctx)
    # tail (behind the body)
    tw = p.tail
    tail_spine = qbez((CX + 6, 170), (CX + 30, 176 - tw * 2), (CX + 33 + tw * 4, 148 - abs(tw)))
    blob(ctx, [Shape(ribbon(tail_spine, lambda t: 5.0 - 2.4 * t))], PINK)
    # legs
    if p.legs:
        for side, foot, knee in ((-1, p.foot_l, p.knee_l), (1, p.foot_r, p.knee_r)):
            hip = (CX + side * 7.0, 172.0)
            blob(ctx, [Shape(noodle(hip, (foot[0], foot[1] - 1.5), knee, knee * 0.5), width=5.6),
                       Shape(ell(foot[0] + side * 2.6, foot[1], 7.2, 3.7))], PINK)
    # body + back spots peeking around the flanks
    body = torso_path(p.breath)
    blob(ctx, [Shape(body)], PINK)
    ctx.save()
    ctx.new_path()
    body(ctx)
    ctx.clip()
    ctx.set_source_rgb(*EAR)
    for (x, y, rx, ry) in ((CX - 16, 143, 6.0, 5.0), (CX + 17, 156, 5.5, 6.5), (CX - 17, 166, 4, 4)):
        ctx.new_path()
        ell(x, y, rx, ry)(ctx)
        ctx.fill()
    ctx.restore()
    if not p.hands_front:
        draw_arm(ctx, -1, p.hand_l, p.bend_l, p.breath)
        draw_arm(ctx, 1, p.hand_r, p.bend_r, p.breath)
    # head
    ctx.save()
    ctx.transform(head_matrix(p))
    draw_head(ctx, p)
    ctx.restore()
    if p.hands_front and not p.arms_over_props:
        draw_arm(ctx, -1, p.hand_l, p.bend_l, p.breath)
        draw_arm(ctx, 1, p.hand_r, p.bend_r, p.breath)
    for fn in p.after:
        fn(ctx)
    if p.arms_over_props:
        draw_arm(ctx, -1, p.hand_l, p.bend_l, p.breath)
        draw_arm(ctx, 1, p.hand_r, p.bend_r, p.breath)
    ctx.restore()
    for fn in p.fx:
        fn(ctx)


# ---------------------------------------------------------------------------
# Side view (running right)
# ---------------------------------------------------------------------------
def draw_side(ctx: cairo.Context, i: int, n: int = 8) -> None:
    phi = 2 * math.pi * i / n
    bob = 2.4 * abs(math.cos(phi)) - 1.2
    hip = (78.0, 166.0 + bob)
    sh = (101.0, 147.0 + bob)
    hx, hy = 116.0, 119.0 + bob * 0.6
    ground = GROUND

    # speed lines and dust behind
    k = i % 4
    fx_speed([(14 + k * 2, 124, 20 - k * 3), (8 + (k * 3) % 7, 148, 26), (18 - k, 172, 16 + k * 2)])(ctx)
    if i in (0, 4):
        fx_dust(44, 192, 1.0)(ctx)
    if i in (1, 5):
        fx_dust(32, 189, 0.8)(ctx)

    def leg(ph: float, color) -> None:
        fx_ = hip[0] + 4 + 18 * math.cos(ph)
        lift = 14 * max(0.0, -math.sin(ph))
        foot = (fx_, ground - 4.0 - lift)
        bend = -8.0 if lift > 1 else -3.5
        tilt = -22 * max(0.0, -math.sin(ph)) * (1 if math.cos(ph) > 0 else -0.6)
        blob(ctx, [Shape(noodle(hip, (foot[0], foot[1] - 1.0), bend, bend * 0.4), width=5.6, fill=color),
                   Shape(ell(foot[0] + 3.0, foot[1], 7.4, 3.7, tilt), fill=color)], color)

    def arm(ph: float, color) -> None:
        sw = math.cos(ph + math.pi)
        hx_ = sh[0] + 22 * sw
        hy_ = sh[1] + 13 - 10 * max(sw, 0.0) + 2 * min(sw, 0.0)
        bend = 3.0 if sw > 0 else -3.0
        blob(ctx, [Shape(noodle(sh, (hx_, hy_), bend, bend * 0.5), width=5.0, fill=color),
                   Shape(ell(hx_, hy_, 4.5, 4.1), fill=color)], color)

    def ear(root: Pt, length: float, lift: float, ph: float, wmax: float) -> None:
        sp = []
        for j in range(26):
            t = j / 25
            sp.append((root[0] - length * t,
                       root[1] - lift * t + 4.0 * t * math.sin(2 * math.pi * (t * 1.15) - ph)
                       + 7 * t * t))
        blob(ctx, [Shape(ribbon(sp, lambda t: wmax * (0.6 + 0.4 * min(1.0, t * 2.4)) - 1.0 * t))], EAR)

    # tail streaming back
    tail_spine = []
    for j in range(20):
        t = j / 19
        tail_spine.append((hip[0] - 6 - 30 * t,
                           hip[1] - 2 - 13 * t + 3.2 * t * math.sin(2 * math.pi * (t * 0.9) - phi)))
    blob(ctx, [Shape(ribbon(tail_spine, lambda t: 5.0 - 2.4 * t))], PINK)

    leg(phi + math.pi, PINK_FAR)
    arm(phi + math.pi, PINK_FAR)
    # ears stream back behind the head
    ear((hx - 4, hy - 18), 46, 15, phi + 1.2, 10.5)
    ear((hx - 8, hy - 6), 46, -2, phi, 11.5)
    leg(phi, PINK)
    near_forward = -math.cos(phi) > 0  # arms swing opposite to the same-side leg
    if not near_forward:
        arm(phi, PINK)

    # torso (with a neck reaching into the head) + back spots
    torso = ell(90, 156 + bob, 20, 13, -36)
    neck = ell(104, 143 + bob, 9, 9)
    blob(ctx, [Shape(torso), Shape(neck)], PINK)
    ctx.save()
    ctx.new_path()
    torso(ctx)
    ctx.clip()
    ctx.set_source_rgb(*EAR)
    for (x, y, r) in ((82, 146, 5.2), (93, 140, 4.2), (73, 156, 4.2)):
        ctx.new_path()
        ell(x, y + bob, r * 1.15, r)(ctx)
        ctx.fill()
    ctx.restore()

    # head (profile facing right, screaming while running)
    ctx.save()
    ctx.translate(hx, hy)
    ctx.rotate(math.radians(4 * math.sin(phi * 2)))
    shapes = [Shape(ell(0, 0, 26, 24)), Shape(ell(22, 5, 18, 10, 6)),
              Shape(ell(17, 15.5, 13, 9.5, 22))]
    blob(ctx, shapes, PINK)
    hole = ell(21, 13, 9.5, 5.8, 20)
    ctx.new_path()
    hole(ctx)
    ctx.set_source_rgb(*MOUTH)
    ctx.fill()
    ctx.save()
    ctx.new_path()
    hole(ctx)
    ctx.clip()
    ctx.set_source_rgb(*TONGUE)
    ell(21, 18, 8.5, 4.2, 20)(ctx)
    ctx.fill()
    ctx.restore()
    line(ctx, hole, WD)
    ctx.new_path()
    ell(40, 2.0, 5.6, 4.5, 10)(ctx)
    ctx.set_source_rgb(*NOSE)
    ctx.fill()
    ctx.new_path()
    ell(38.4, 0.2, 1.9, 1.0)(ctx)
    ctx.set_source_rgb(*NOSE_HI)
    ctx.fill()
    for (ex, ey, rx, ry) in ((6.0, -13.0, 10.0, 12.6), (18.0, -11.0, 11.0, 13.6)):
        draw_eye(ctx, ex, ey, rx, ry, 1, (3.8, -0.5), 1.8, (0.0, 0.0), False)
    ctx.restore()

    if near_forward:
        arm(phi, PINK)
    if i in (2, 6):
        fx_sweat(hx - 30, hy - 30, -30, 0.9)(ctx)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def row_idle() -> List[Pose]:
    base = Pose(lid=(0.06, 0.25))
    seq = [
        dict(breath=0.0, pupil=(0, 0), tail=0.0, mouth_phase=0.0),
        dict(breath=0.6, pupil=(0, 0), tail=0.6, mouth_phase=0.8),
        dict(breath=1.0, pupil=(-4.0, 0.5), tail=1.0, mouth_phase=1.6, hrot=-2),
        dict(breath=0.6, pupil=(4.0, 0.5), tail=0.6, mouth_phase=2.4, hrot=2),
        dict(breath=0.2, pupil=(0, 0), tail=0.0, mouth_phase=3.2, lid=(1.0, 0.0)),
        dict(breath=0.0, pupil=(0, -0.5), tail=-0.4, mouth_phase=4.0),
    ]
    out = []
    for k, s in enumerate(seq):
        sway = (2.5 * math.sin(k / 6 * 2 * math.pi))
        p = replace(base, ear_l=(58 + sway, 6 + sway, 50), ear_r=(58 - sway, 6 - sway, 50), **s)
        out.append(p)
    return out


def row_waving() -> List[Pose]:
    hands = [(CX + 50, 90), (CX + 56, 99), (CX + 50, 90), (CX + 42, 84)]
    bends = [13, 11, 13, 14]
    out = []
    for k, (h, b) in enumerate(zip(hands, bends)):
        arcs = [fx_arcs(h[0], h[1], 200 if k % 2 == 0 else -40, 60, (8.0, 12.5))] if k in (1, 3) else []
        out.append(Pose(mouth="smile", hrot=4, hdx=1, pupil=(1.0, -1.0), pupil_r=3.2,
                        lid=(0.0, 0.0), hand_r=h, bend_r=b, hand_l=(CX - 25, 161),
                        ear_l=(60, 8, 50), ear_r=(66 + 4 * (k % 2), 14, 48),
                        tail=[0.0, 1.0, 0.0, -1.0][k], fx=arcs))
    return out


def row_jumping() -> List[Pose]:
    crouch = Pose(sy=0.86, sx=1.08, eye_scale=1.1, pupil_r=1.7, mouth="o", mouth_amt=0.8,
                  ear_l=(48, 12, 48), ear_r=(48, 12, 48), knee_l=-7, knee_r=7,
                  foot_l=(CX - 15, 194.5), foot_r=(CX + 15, 194.5),
                  hand_l=(CX - 15, 150), hand_r=(CX + 15, 150), bend_l=-6, bend_r=6,
                  fx=[fx_bang(146, 60, 1.0)])
    up = Pose(gy=-20, sy=1.1, sx=0.94, eye_scale=1.15, pupil_r=1.5, mouth="open", mouth_amt=0.8,
              ear_l=(34, 12, 50), ear_r=(34, 12, 50), knee_l=-1, knee_r=1,
              foot_l=(CX - 8, 196), foot_r=(CX + 8, 196),
              hand_l=(CX - 27, 112), hand_r=(CX + 27, 112), bend_l=8, bend_r=-8,
              fx=[fx_bang(150, 46, 0.9)])
    peak = Pose(gy=-21, eye_scale=1.18, pupil_r=1.3, mouth="scream", mouth_amt=0.55, mouth_phase=1.0,
                ear_l=(128, 146, 44), ear_r=(128, 146, 44), ear_wave=(2.0, 0.5),
                knee_l=-8, knee_r=8, foot_l=(CX - 14, 188), foot_r=(CX + 14, 188),
                hand_l=(CX - 36, 110), hand_r=(CX + 36, 110), bend_l=6, bend_r=-6,
                fx=[fx_sweat(40, 70, -30), fx_sweat(152, 72, 30)])
    down = Pose(gy=-9, sy=1.05, sx=0.96, eye_scale=1.12, pupil_r=1.6, mouth="open", mouth_amt=1.0,
                ear_l=(160, 172, 44), ear_r=(160, 172, 44), ear_wave=(1.5, 2.0),
                knee_l=-2, knee_r=2, foot_l=(CX - 10, 196), foot_r=(CX + 10, 196),
                hand_l=(CX - 30, 118), hand_r=(CX + 30, 118), bend_l=6, bend_r=-6)
    land = Pose(sy=0.9, sx=1.07, mouth="grimace", pupil=(0, 1), pupil_r=2.8, lid=(0.25, 0.3),
                ear_l=(70, 30, 46), ear_r=(70, 30, 46), knee_l=-6, knee_r=6,
                foot_l=(CX - 15, 194.5), foot_r=(CX + 15, 194.5),
                hand_l=(CX - 24, 158), hand_r=(CX + 24, 158), bend_l=-5, bend_r=5,
                fx=[fx_dust(60, 194, 1.1), fx_dust(132, 194, 1.1)])
    return [crouch, up, peak, down, land]


def row_failed() -> List[Pose]:
    jit = [-2.2, 2.2, -1.6, 2.6, -2.6, 1.6, -2.0, 2.0]
    out = []
    for k in range(8):
        flail = 4.0 * math.sin(k * math.pi / 2)
        fx = [fx_shake(CX - 46, 150, -1), fx_shake(CX + 46, 150, 1)]
        if k % 2 == 0:
            fx.append(fx_sweat(28 + (k % 4) * 3, 64 + k, -40))
        else:
            fx.append(fx_sweat(164 - (k % 4) * 3, 62 + k, 40))
        out.append(Pose(gx=jit[k], rot=0.9 * jit[k], sy=1.03, sx=0.98,
                        eye_scale=1.14, pupil_r=1.25,
                        pupil=(0.8 * jit[k] * 0.5, -1.0), mouth="scream",
                        mouth_amt=0.85 + 0.15 * (k % 2), mouth_phase=k * 1.3,
                        ear_l=(174, 181 + 3 * (k % 2), 50), ear_r=(174, 181 + 3 * ((k + 1) % 2), 50),
                        ear_wave=(2.4, k * 1.6), hdy=-3,
                        hand_l=(CX - 36, 108 + flail), hand_r=(CX + 36, 108 - flail),
                        bend_l=7, bend_r=-7,
                        knee_l=4 if k % 2 else -3, knee_r=-4 if k % 2 else 3,
                        foot_l=(CX - 14, 194.5), foot_r=(CX + 14, 194.5), fx=fx))
    return out


def row_waiting() -> List[Pose]:
    out = []
    trem = [-0.8, 0.8, -0.6, 0.8, -0.8, 0.6]
    for k in range(6):
        bob = 2.0 * math.sin(k / 6 * 2 * math.pi)
        out.append(Pose(gx=trem[k], hrot=8 + 1.5 * math.sin(k * math.pi / 3), hdx=1,
                        pupil=(1.5, -4.0), pupil_r=4.6, gloss=True, lid=(0.12, 0.45),
                        mouth="wavy", mouth_phase=k * 1.1,
                        hand_l=(CX - 3.5, 140), hand_r=(CX + 3.5, 141), bend_l=-9, bend_r=9,
                        ear_l=(64, 14 + trem[k] * 3, 50), ear_r=(92, 34 - trem[k] * 3, 48),
                        knee_l=4.5, knee_r=-4.5, foot_l=(CX - 13, 194.5), foot_r=(CX + 13, 194.5),
                        tail=0.3 * trem[k],
                        fx=[fx_question(150, 46 + bob)] + ([fx_shake(CX - 34, 186, -1, 2, 0.6),
                                                             fx_shake(CX + 34, 186, 1, 2, 0.6)]
                                                            if k % 2 == 0 else [])))
    return out


def row_working() -> List[Pose]:
    code = rgb(150, 236, 168)
    particles = [(CX - 50, "1", 0), (CX + 50, "0", 14), (CX - 46, "{}", 26), (CX + 47, "1", 38)]
    out = []
    for k in range(6):
        up_l = k % 2 == 0
        hl = (CX - 17, 155 if up_l else 162)
        hr = (CX + 17, 162 if up_l else 155)
        down = hr if up_l else hl
        fx = [fx_taps(down[0], down[1] + 1), fx_star(CX - 22 + (k * 17) % 44, 154 - (k % 3) * 2, 3.4)]
        for (x, txt, off) in particles:
            t = ((k * 8 + off) % 48) / 48.0
            fx.append(fx_glyph(x + 3 * math.sin(t * 6.0), 150 - 46 * t, txt, code, 11.5 if len(txt) == 1 else 10.0))
        if k in (1, 4):
            fx.append(fx_sweat(150, 74 + k, 35, 0.9))
        if k in (2, 5):
            fx.append(fx_sweat(42, 76 - k, -35, 0.85))
        out.append(Pose(hdy=4 + (1.0 if up_l else 0.0), hrot=(-2 if up_l else 2),
                        pupil=(0.0, 5.5), pupil_r=2.6, lid=(0.28, 0.32), mouth="grimace",
                        hand_l=hl, hand_r=hr, bend_l=6, bend_r=-6, legs=False,
                        ear_l=(66 + (6 if up_l else 0), 20, 48), ear_r=(66 + (0 if up_l else 6), 20, 48),
                        tail=2.0 if up_l else -1.5, arms_over_props=True,
                        after=[lambda c: prop_laptop(c)], fx=fx))
    return out


def magnifier_fx(p: Pose, lens_local: Pt, look: Pt) -> Callable:
    """Draw the magnifier (lens over the viewer-left eye) plus the holding arm."""
    def fn(ctx: cairo.Context) -> None:
        m = head_matrix(p)
        ctx.save()
        ctx.transform(m)
        lx, ly = lens_local
        R = 16.5
        # handle from the rim down-left towards the paw
        ang = math.radians(118)
        h0 = (lx + math.cos(ang) * R, ly + math.sin(ang) * R)
        h1 = (lx + math.cos(ang) * (R + 26), ly + math.sin(ang) * (R + 26))
        blob(ctx, [Shape(lambda c: (c.move_to(*h0), c.line_to(*h1)), width=5.2)], BROWN)
        lens = ell(lx, ly, R, R)
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*GLASS)
        ctx.fill()
        # magnified eye behind the glass
        ctx.save()
        ctx.new_path()
        lens(ctx)
        ctx.clip()
        ex, ey = -12.0, -9.0
        ctx.translate(lx, ly)
        ctx.scale(1.32, 1.32)
        ctx.translate(-lx, -ly)
        draw_eye(ctx, ex, ey, 12.4, 14.8, -1, look, 3.6, (0.0, 0.0), True)
        ctx.restore()
        # glare
        ctx.set_source_rgb(*WHITE)
        ctx.set_line_width(2.2)
        ctx.new_path()
        ctx.arc(lx, ly, R - 4.5, math.radians(200), math.radians(245))
        ctx.stroke()
        # rim
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*OUT)
        ctx.set_line_width(6.6)
        ctx.stroke()
        ctx.new_path()
        lens(ctx)
        ctx.set_source_rgb(*GOLD)
        ctx.set_line_width(3.0)
        ctx.stroke()
        hx, hy = m.transform_point(*h1)
        ctx.restore()
        # holding arm and paw (world space)
        draw_arm(ctx, -1, (hx, hy), -6, p.breath)
    return fn


def row_review() -> List[Pose]:
    scan = [-2.0, 0.0, 2.0, 3.0, 1.0, -1.0]
    out = []
    for k in range(6):
        s = scan[k]
        p = Pose(hrot=-5, pupil=(s * 1.4, 1.0), pupil_r=2.8,
                 lid=(0.0, 0.0), lid_l=None, mouth="wavy", mouth_phase=k * 0.6,
                 hand_l=(CX - 30, 150), hand_r=(CX + 13, 132), bend_r=-8,
                 ear_l=(62, 10, 50), ear_r=(70, 20, 50), breath=0.5 * math.sin(k * math.pi / 3),
                 tail=0.6 * math.sin(k * math.pi / 3))
        # right eye squints; left eye is magnified by the glass
        p = replace(p, lid=(0.48, -0.25))
        lens = (-12.0 + s, -8.0 + 0.5 * abs(s))
        p = replace(p, after=[magnifier_fx(p, lens, (s * 1.6, 1.0))])
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render(draw: Callable[[cairo.Context], None]) -> Image.Image:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_W * SS, CELL_H * SS)
    ctx = cairo.Context(surf)
    ctx.scale(SS, SS)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    draw(ctx)
    surf.flush()
    # cairo ARGB32 = premultiplied BGRA (little endian); "BGRa" unpremultiplies.
    big = Image.frombuffer("RGBA", (CELL_W * SS, CELL_H * SS), bytes(surf.get_data()),
                           "raw", "BGRa", surf.get_stride(), 1)
    small = big.resize((CELL_W, CELL_H), Image.LANCZOS)  # Pillow resamples RGBA premultiplied
    arr = np.array(small)
    arr[arr[:, :, 3] == 0] = 0
    return Image.fromarray(arr)


def build_frames() -> List[List[Image.Image]]:
    rows: List[List[Image.Image]] = []
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_idle()])
    run_r = [render(lambda c, i=i: draw_side(c, i)) for i in range(8)]
    rows.append(run_r)
    rows.append([ImageOps.mirror(im) for im in run_r])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_waving()])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_jumping()])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_failed()])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_waiting()])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_working()])
    rows.append([render(lambda c, p=p: draw_front(c, p)) for p in row_review()])
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
    for path in ("/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


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
        flat = []
        for im in frames:
            bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
            bg.alpha_composite(im)
            flat.append(bg.convert("RGB"))
        flat[0].save(os.path.join(OUT_DIR, f"row-{name}.gif"), save_all=True,
                     append_images=flat[1:], duration=durations[name], loop=0, disposal=2)


def zoom(atlas: Image.Image, cells: Sequence[Tuple[int, int]], scale: int, path: str) -> None:
    tiles = []
    for r, c in cells:
        crop = atlas.crop((c * CELL_W, r * CELL_H, (c + 1) * CELL_W, (r + 1) * CELL_H))
        bg = Image.new("RGBA", crop.size, (128, 128, 128, 255))
        bg.alpha_composite(crop)
        tiles.append(bg.resize((CELL_W * scale, CELL_H * scale), Image.NEAREST))
    out = Image.new("RGB", (CELL_W * scale * len(tiles), CELL_H * scale), (128, 128, 128))
    for k, t in enumerate(tiles):
        out.paste(t.convert("RGB"), (k * CELL_W * scale, 0))
    out.save(path)


def install(src_dir: str) -> None:
    targets = [os.path.expanduser(f"~/.codex/pets/{PET_ID}"), os.path.expanduser(f"~/.petdex/pets/{PET_ID}")]
    for t in targets:
        if os.path.exists(t):
            sys.exit(f"refusing to overwrite existing {t}")
    for t in targets:
        os.makedirs(t)
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
    zoom(atlas, [(0, 0), (5, 0), (1, 2)], 3, os.path.join(OUT_DIR, "zoom.png"))
    export_gifs(rows)
    print("wrote", pet_dir)
    if "--install" in sys.argv:
        install(pet_dir)


if __name__ == "__main__":
    main()
