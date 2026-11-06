#!/usr/bin/env python3
"""Generate the "chowder" Codex pet: Chowder (Cartoon Network) as a cut-out rig of traced show art.

Every body part is a vector path traced from the references (refs/01-canonical-3q.webp for the canonical
3/4 view facing right; refs 02, 03, 04 and 06 for donor hands, mouths and the fork) and baked into
rig_data.py, so this script needs only Pillow, pycairo and numpy. Model units are ref-01 pixels
(1200x1542). Parts are posed with affine transforms about pivots placed under ink lines, drawn back to front
with pycairo at 4x, given a dark outer contour and a light rim, and downscaled with Pillow LANCZOS. Every row
is a short gag (see each row_*() docstring): squash and stretch, follow-through on the hat and jowl, takes.

Usage:
    python3 -I make_pet.py   # runs the frame checks (check_frames), renders out/spritesheet.webp and copies it
                             # next to pet.json; a pose that breaks a rule stops the build first

Rig API (reused by the pixel variant):
    load_parts() -> {name: Part}: traced paths in model units, colours by class name (keys of CLASS_RGB).
    Look(colours, ink, ink_add, parts): pass false colours per class and ink_add=0 to render a class map;
        parts overrides the look per part name (the frame checks' part map, see part_look()).
    row_*() -> [Pose]: the poses of every row (running-left = mirrored running-right).
    draw_character(ctx, pose, look): one pose in cell space (192x208) on any cairo context; it keeps the
        caller's antialias setting. Strokes (ink_add, the jowl line, props, lids, wheel) use a round pen in
        device space, cell px * SS wide (SS = device px per cell px): set SS to your scale before drawing.
        The hat is drawn in face space and its follow-through bends only the lobes (hat_warp, hat_dy >= 0):
        it never leaves the head. The spoon and the ladle are rigid (SPOON, LADLE): held_stick() draws them
        with the fist's place and turn but not the body's squash, and grip_to() moves the fist to the target.
    to_cell(pose, x, y, head) / hand_matrix(arm, far): anchors for glyphs that must follow a part.
    pot_cell(x, y) / pot_space(pose): the pot stands in a fixed ground frame, not on the leaning body.
    Pose.fx and Pose.fx_back are grey effect marks in cell space, drawn by render() only.
"""
from __future__ import annotations

import base64
import json
import math
import os
import runpy
import shutil
import zlib
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import cairo
import numpy as np
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")

# ---------------------------------------------------------------------------
# Atlas geometry (Codex pet contract)
# ---------------------------------------------------------------------------
CELL_W, CELL_H = 192, 208
COLS, ROWS = 8, 9
ATLAS_W, ATLAS_H = COLS * CELL_W, ROWS * CELL_H  # 1536 x 1872
SS = 4  # supersampling factor: device px per cell px
ROW_SPEC = [("idle", 6), ("running-right", 8), ("running-left", 8), ("waving", 4), ("jumping", 5),
            ("failed", 8), ("waiting", 6), ("running", 6), ("review", 6)]
CX = 96.0       # cell x of the model anchor
GROUND = 196.0  # cell y of the model ground line
S = 0.098       # cell px per model unit
MX, MY = 600.0, 1542.0          # model point mapped to (CX, GROUND)

RGB = Tuple[float, float, float]
Pt = Tuple[float, float]


def rgb(h: str) -> RGB:
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


# ---------------------------------------------------------------------------
# Palette (measured on refs/01-canonical-3q.webp, see odd/tasks/chowder.md L4) and looks
# ---------------------------------------------------------------------------
CLASS_RGB: Dict[str, RGB] = {
    "white": (1.0, 1.0, 1.0), "skin": rgb("#B6ADD5"), "purple": rgb("#84227A"), "purple_d": rgb("#6E097B"),
    "magenta": rgb("#CA3995"), "magenta_d": rgb("#BE0D94"), "cuff": rgb("#E33B8D"), "shoes": rgb("#52145D"),
    "tail_band": rgb("#EAE5FB"), "nose": rgb("#E3798F"),
    "mouth": rgb("#7A1634"), "tongue": rgb("#EC8C9C"),     # refs 02-04
    "fork": rgb("#A9CBEA"), "wood": rgb("#D9A86A"), "bowl": rgb("#D6E0EC"), "bowl_in": rgb("#BCC5D0"),
    "loop": rgb("#5C4E7E"),
    "pot": rgb("#7E8AA6"), "pot_l": rgb("#A9B4CC"), "pot_in": rgb("#3E4458"), "stew": rgb("#F0C850"),
}
INK = rgb("#140C18")
FXGREY = rgb("#C9C9D2")       # effect marks readable on light and dark backgrounds
SWEAT = rgb("#84CAFF")
RIM_RGB = (255, 247, 242)
RIM_ALPHA = 0.92
RIM_R = 1.75                  # light rim outside the contour (cell px)
OUTER = 1.0                   # dark outer contour added around the silhouette (cell px)


@dataclass
class Look:
    """How classes become pixels: the pixel variant passes false colours and ink_add=0."""
    colours: Dict[str, RGB]
    ink: RGB
    ink_add: float = 0.9      # traced ink thickened by this stroke (cell px): the show reads bold at pet size
    parts: Dict[str, "Look"] = field(default_factory=dict)    # per-part overrides (the frame checks' part map)


LOOK = Look(CLASS_RGB, INK)


# ---------------------------------------------------------------------------
# Traced parts (rig_data.py)
# ---------------------------------------------------------------------------
Sub = Tuple[str, np.ndarray]


@dataclass
class Part:
    colour: List[Tuple[str, List[Sub]]]
    ink: List[Sub]
    sil: List[Sub]


def _subs(raw) -> List[Sub]:
    return [(cmds, np.cumsum(np.array(d, np.float64).reshape(-1, 2), axis=0)) for cmds, d in raw]


def load_parts() -> Dict[str, Part]:
    data = runpy.run_path(os.path.join(HERE, "rig_data.py"))["PARTS"]
    raw = json.loads(zlib.decompress(base64.b64decode(data)))
    return {name: Part([(k, _subs(s)) for k, s in p["colour"]], _subs(p["ink"]), _subs(p["sil"]))
            for name, p in raw.items()}


PARTS: Dict[str, Part] = {}


def part(name: str) -> Part:
    if not PARTS:
        PARTS.update(load_parts())
    return PARTS[name]


def add_subs(ctx: cairo.Context, subs: Sequence[Sub]) -> None:
    for cmds, pts in subs:
        i = 0
        for c in cmds:
            if c == "C":
                ctx.curve_to(*pts[i], *pts[i + 1], *pts[i + 2])
                i += 3
            elif c == "M":
                ctx.move_to(*pts[i])
                i += 1
            else:
                ctx.line_to(*pts[i])
                i += 1
        ctx.close_path()


def stroke_px(ctx: cairo.Context, cell_px: float) -> None:
    """Stroke the current path with a round pen `cell_px` cell px wide in device space, so line weight holds
    through squash and stretch as in hand-drawn animation (the context must draw SS device px per cell px)."""
    ctx.save()
    ctx.identity_matrix()
    ctx.set_line_width(cell_px * SS)
    ctx.stroke()
    ctx.restore()


def fill_part(ctx: cairo.Context, p: Part, look: Look) -> None:
    # vtracer's stacked layers start with a canvas-sized base shape: keep every layer inside the silhouette
    ctx.save()
    ctx.new_path()
    add_subs(ctx, p.sil)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    ctx.clip()
    for k, subs in p.colour:
        ctx.set_source_rgb(*look.colours[k])
        ctx.new_path()
        add_subs(ctx, subs)
        ctx.fill()
    ctx.restore()


def ink_part(ctx: cairo.Context, p: Part, look: Look) -> None:
    if not p.ink:
        return
    ctx.save()
    ctx.new_path()
    add_subs(ctx, p.ink)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    ctx.set_source_rgb(*look.ink)
    if look.ink_add > 0:
        ctx.fill_preserve()
        stroke_px(ctx, look.ink_add)
    else:
        ctx.fill()
    ctx.restore()


def draw_part(ctx: cairo.Context, name: str, look: Look, m: Optional[cairo.Matrix] = None) -> None:
    look = look.parts.get(name, look)
    ctx.save()
    if m is not None:
        ctx.transform(m)
    p = part(name)
    fill_part(ctx, p, look)
    ink_part(ctx, p, look)
    ctx.restore()


# ---------------------------------------------------------------------------
# Rig (model units; pivots sit under ink lines so the cuts hide)
# ---------------------------------------------------------------------------
LEAN_PIVOT = (640.0, 1460.0)
NECK = (650.0, 990.0)
BRIM_Y = 560.0                  # the face stretches below the hat brim
SH_N = (385.0, 1070.0)          # near shoulder (inside the sleeve dome)
WRIST_N = (392.0, 1260.0)       # cuff opening of the near sleeve at rest
SH_F = (1050.0, 1090.0)         # far shoulder (behind the robe)
FAR_SLEEVE = 0.9                # the far sleeve is the near one mirrored and a bit smaller
FAR_PIVOT = (1110.0, 1190.0)    # the far cuff + hand peeking out at rest
TAIL_ROOT = (215.0, 1405.0)
SHOE_C = (637.0, 1503.0)        # front shoe centre; the back shoe is a smaller copy behind it
SHOE_BACK = (189.0, -4.0, 0.85)
EYES = {"eye_b": ((813.5, 569.0), (836.0, 575.0), (7.0, 8.5)),   # centre, pupil centre, pupil radii
        "eye_f": ((905.3, 560.7), (884.0, 566.5), (6.5, 8.5))}
# donor mouths hang from their top centre: (part, anchor on the face, scale)
MOUTHS = {"open": ("mouth_open", (812.0, 682.0), 1.25), "scream": ("mouth_scream", (826.0, 668.0), 0.92),
          "panic": ("mouth_scream04", (826.0, 668.0), 0.92), "o": ("mouth_open", (822.0, 684.0), 0.6)}
FANG_BASE = (741.0, 680.0)      # ref-01 fang: middle of its broad edge (the sharp tip is above it)
PANIC_FANG = (-50.0, 58.0, -33.0)   # ref-04 mouth frame: just under the upper lip line, along its slope
# The hat never leaves the head: a lifted or tilted hat bares the skull under the brim, which reads as an
# empty head. It is drawn in face space (it squashes and stretches with the jowl) and its follow-through
# bends only the lobes, about the line through the ends of its brim. The brim sits ~30 units above the eyes
# (ref 01), so a hat landing on the head is pressed down at most ~12 units and squashes instead. Under the
# hat the traced face is a ragged band of skin that pokes out beside the brim on a tilted head: it is clipped
# to a dome through the ends of the brim.
BRIM_L, BRIM_R, DOME_H = (300.0, 600.0), (1050.0, 515.0), 220.0
HAT_H = 560.0                   # height of the lobe tips above the brim line
JOWL_LINE = 1.5                 # cell px: the face outline redrawn at one weight inside its silhouette
PUCKER = (850.0, 702.0, 34.0, 28.0)     # closed pursed lips in face space: centre, radii
# hands: (part, wrist in the part's coordinates); donor hands have the wrist at 0,0 and fingers along +y
HANDS = {"rest": ("hand_n", WRIST_N), "open": ("hand_open", (0.0, 0.0)), "fist": ("hand_fist", (0.0, 0.0)),
         "bowl": ("hand_bowl", (0.0, 0.0))}
GRIP = (20.0, 125.0)            # where a handle passes through the fist (its axis is the local x axis)


@dataclass
class Arm:
    angle: float = 0.0          # about the shoulder, deg, + = clockwise on screen (0 = hanging)
    length: float = 1.0         # sleeve stretch along its axis
    hand: str = "rest"
    hand_rot: float = 0.0       # extra rotation at the wrist
    mirror: bool = False        # thumb on the other side
    held: Optional[Callable] = None     # prop in the hand frame, drawn behind the hand
    root: Optional[Pt] = None   # shoulder pivot (default SH_N / SH_F); a raised arm hides its root behind the body


@dataclass
class Pose:
    gx: float = 0.0             # cell offset
    gy: float = 0.0
    sx: float = 1.0             # whole-figure squash/stretch about the ground point
    sy: float = 1.0
    rot: float = 0.0            # whole-figure rotation about the ground point (deg, + = clockwise)
    lean: float = 0.0           # upper body rotation (deg, + = clockwise) about the hem
    breath: float = 0.0         # body swell (-1..1)
    belly: float = 0.0          # belly wobble: + wide and short, - narrow and tall
    head_rot: float = 0.0       # head rotation about the neck (deg)
    head_dy: float = 0.0
    face_sx: float = 1.0        # jowl follow-through: the head (face and hat) squashes or stretches about the brim
    face_sy: float = 1.0
    hat_dy: float = 0.0         # hat pressed down onto the head (>= 0)
    hat_sx: float = 1.0         # lobe follow-through: spread, stretch and lean (deg, + = lobes back)
    hat_sy: float = 1.0
    hat_skew: float = 0.0
    eye_s: float = 1.0
    pupil: Pt = (0.0, 0.0)      # pupil offset from the ref-01 gaze (model units)
    pupil_s: float = 1.0
    lid_b: float = 0.0          # lid level of the back eye (0 open .. 1 closed)
    lid_f: float = 0.0
    shut: str = ""              # closed eyes: blink, happy (^ ^) or squeeze (> <)
    mouth: str = "smile"        # smile, open, o, sip, pucker (over held props), scream (ref 03), panic (ref 04)
    mouth_s: float = 1.0
    near: Arm = field(default_factory=Arm)
    far: Optional[Arm] = None   # None: the ref-01 far cuff and hand peeking out behind the robe
    far_rot: float = 0.0
    near_behind: bool = False   # raised near arm goes behind the body and the head
    far_front: bool = False     # far arm in front of the body (holding something up front)
    tail_rot: float = 0.0
    shoe_n: Tuple[float, float, float] = (0.0, 0.0, 0.0)    # dx, dy, rotation
    shoe_f: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    wheel: Optional[float] = None       # run gag phase (0..1): the legs become a wheel of loops
    back: List[Callable] = field(default_factory=list)      # props behind the figure (model space)
    mid: List[Callable] = field(default_factory=list)       # in front of the body, behind the near arm
    front: List[Callable] = field(default_factory=list)     # in front of everything
    fx: List[Callable] = field(default_factory=list)        # cell space, after the contour and rim
    fx_back: List[Callable] = field(default_factory=list)   # cell space, behind the rim


def pose_matrix(p: Pose) -> cairo.Matrix:
    m = cairo.Matrix()
    m.translate(CX + p.gx, GROUND + p.gy)
    m.rotate(math.radians(p.rot))
    m.scale(p.sx, p.sy)
    m.scale(S, S)
    m.translate(-MX, -MY)
    return m


def about(pivot: Pt, deg: float = 0.0, sx: float = 1.0, sy: float = 1.0, dx: float = 0.0,
          dy: float = 0.0) -> cairo.Matrix:
    """Rotate and scale about a pivot, then translate."""
    m = cairo.Matrix()
    m.translate(pivot[0] + dx, pivot[1] + dy)
    m.rotate(math.radians(deg))
    m.scale(sx, sy)
    m.translate(-pivot[0], -pivot[1])
    return m


def mul(*ms: cairo.Matrix) -> cairo.Matrix:
    """Compose so that the first matrix is applied last (like nested ctx.transform calls)."""
    out = cairo.Matrix()
    for m in reversed(ms):
        out = out.multiply(m)
    return out


def lean_matrix(p: Pose) -> cairo.Matrix:
    return about(LEAN_PIVOT, p.lean)


def head_matrix(p: Pose) -> cairo.Matrix:
    return about(NECK, p.head_rot, dy=p.head_dy - 10 * p.breath)


def face_matrix(p: Pose) -> cairo.Matrix:
    return about((NECK[0], BRIM_Y), sx=p.face_sx, sy=p.face_sy)


def hat_warp(p: Pose) -> Callable[[np.ndarray], np.ndarray]:
    """Face-space displacement of the hat's points: stretch (hat_sy), spread (hat_sx) and lean (hat_skew) grow
    with the square of the height above the brim line, so the lobes spring while the brim stays put."""
    assert p.hat_dy >= 0, "a lifted hat bares the head"
    (x0, y0), (x1, y1) = BRIM_L, BRIM_R
    k, cx, t = (y1 - y0) / (x1 - x0), (x0 + x1) / 2, math.tan(math.radians(p.hat_skew))

    def fn(pts: np.ndarray) -> np.ndarray:
        x, y = pts[:, 0], pts[:, 1]
        w = np.maximum(y0 + k * (x - x0) - y, 0.0) ** 2 / HAT_H    # height above the brim, squared / HAT_H
        return np.stack([x + (p.hat_sx - 1) * (x - cx) * w / HAT_H - t * w, y - (p.hat_sy - 1) * w + p.hat_dy], 1)
    return fn


def to_cell(p: Pose, x: float, y: float, head: bool = False) -> Pt:
    """Cell position of a model point on the (leaned) upper body or head."""
    ms = [pose_matrix(p), lean_matrix(p)] + ([head_matrix(p), face_matrix(p)] if head else [])
    return mul(*ms).transform_point(x, y)


def sleeve_matrix(a: Arm, far: bool) -> cairo.Matrix:
    m = cairo.Matrix()
    m.translate(*(a.root or (SH_F if far else SH_N)))
    m.rotate(math.radians(a.angle))
    if far:
        m.scale(-FAR_SLEEVE, FAR_SLEEVE * a.length)
    else:
        m.scale(1.0, a.length)
    m.translate(-SH_N[0], -SH_N[1])
    return m


def hand_matrix(a: Arm, far: bool) -> cairo.Matrix:
    wx, wy = sleeve_matrix(a, far).transform_point(*WRIST_N)
    s = FAR_SLEEVE if far else 1.0
    m = cairo.Matrix()
    m.translate(wx, wy)
    m.rotate(math.radians(a.angle + a.hand_rot))
    m.scale(-s if a.mirror else s, s)
    ax, ay = HANDS[a.hand][1]
    m.translate(-ax, -ay)
    return m


def draw_arm(ctx: cairo.Context, a: Arm, far: bool, look: Look) -> None:
    hm = hand_matrix(a, far)
    if a.held is not None:
        ctx.save()
        ctx.transform(hm)
        a.held(ctx, look)
        ctx.restore()
    draw_part(ctx, HANDS[a.hand][0], look, hm)
    draw_part(ctx, "sleeve_n", look, sleeve_matrix(a, far))     # the cuff covers the wrist


def aim(a: Arm, far: bool, target: Pt, side: float = 1.0) -> Arm:
    """Set the fist's rotation so a held handle (local +x through GRIP, or -x with side=-1) points at a
    model-space target."""
    a.hand_rot = 0.0
    m = hand_matrix(a, far)
    gx, gy = m.transform_point(*GRIP)
    ux, uy = m.transform_distance(side, 0.0)
    a.hand_rot = math.degrees(math.atan2(target[1] - gy, target[0] - gx) - math.atan2(uy, ux))
    return a


def stroke(ctx: cairo.Context, width: float, colour: RGB) -> None:
    ctx.set_source_rgb(*colour)
    stroke_px(ctx, width)


def outlined(ctx: cairo.Context, path: Callable[[cairo.Context], None], colour: RGB, look: Look,
             width: float = 1.3) -> None:
    """Flat fill with an ink outline, like the traced parts."""
    ctx.new_path()
    path(ctx)
    ctx.set_source_rgb(*colour)
    ctx.fill_preserve()
    stroke(ctx, width, look.ink)


def ellipse(cx: float, cy: float, rx: float, ry: float, rot: float = 0.0) -> Callable[[cairo.Context], None]:
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


def draw_eye(ctx: cairo.Context, name: str, p: Pose, look: Look) -> None:
    (cx, cy), (px, py), (rx, ry) = EYES[name]
    ctx.save()
    ctx.transform(about((cx, cy), sx=p.eye_s, sy=p.eye_s))
    e = part(name)
    if p.shut:
        ctx.set_source_rgb(*look.colours["skin"])
        ctx.new_path()
        add_subs(ctx, e.sil)
        ctx.fill()
        ctx.new_path()
        if p.shut == "blink":                  # lids down: a smiling arc
            ctx.move_to(cx - 46, cy + 6)
            ctx.curve_to(cx - 20, cy + 32, cx + 20, cy + 32, cx + 46, cy + 6)
        elif p.shut == "happy":                # ^ arc
            ctx.move_to(cx - 44, cy + 14)
            ctx.curve_to(cx - 22, cy - 22, cx + 22, cy - 22, cx + 44, cy + 14)
        else:                                  # squeeze: > on the back eye, < on the front eye
            side = 1 if name == "eye_b" else -1
            ctx.move_to(cx - 34 * side, cy - 30)
            ctx.line_to(cx + 30 * side, cy + 2)
            ctx.line_to(cx - 34 * side, cy + 30)
        stroke(ctx, 1.7, look.ink)
        ctx.restore()
        return
    fill_part(ctx, e, look)
    ctx.save()
    ctx.new_path()
    add_subs(ctx, e.sil)
    ctx.clip()
    ctx.set_source_rgb(*look.ink)
    ctx.new_path()
    ellipse(px + p.pupil[0], py + p.pupil[1], rx * p.pupil_s, ry * p.pupil_s)(ctx)
    ctx.fill()
    lid = p.lid_b if name == "eye_b" else p.lid_f
    if lid > 0:                 # heavy lid (ref 05): skin over the top of the eye, a lid line at its edge
        yl = cy - 64 + 128 * lid
        ctx.set_source_rgb(*look.colours["skin"])
        ctx.new_path()
        ctx.move_to(cx - 80, cy - 90)
        ctx.line_to(cx + 80, cy - 90)
        ctx.line_to(cx + 80, yl - 10)
        ctx.line_to(cx - 80, yl + 10)
        ctx.close_path()
        ctx.fill()
        ctx.move_to(cx - 80, yl + 10)
        ctx.line_to(cx + 80, yl - 10)
        stroke(ctx, 1.4, look.ink)
    ctx.restore()
    ink_part(ctx, e, look)
    ctx.restore()


SIP = (826.0, 712.0, 34.0, 40.0, -12.0)     # round tasting mouth in face space: centre, radii, rotation


def sip_path(p: Pose) -> Callable[[cairo.Context], None]:
    x, y, rx, ry, rot = SIP
    return ellipse(x, y, rx * p.mouth_s, ry * p.mouth_s, rot)


def draw_sip(ctx: cairo.Context, p: Pose, look: Look) -> None:
    """Round "o" mouth (show style: dark inside, tongue at the bottom, the fang under the upper lip)."""
    path = sip_path(p)
    ctx.new_path()
    path(ctx)
    ctx.set_source_rgb(*look.colours["mouth"])
    ctx.fill()
    ctx.save()
    ctx.new_path()
    path(ctx)
    ctx.clip()
    x, y, rx, ry, rot = SIP
    ctx.new_path()
    ellipse(x + 4, y + ry * 0.75 * p.mouth_s, rx * 0.8, ry * 0.45, rot)(ctx)
    ctx.set_source_rgb(*look.colours["tongue"])
    ctx.fill()
    ctx.restore()
    draw_part(ctx, "fang", look, about(FANG_BASE, -10, sx=0.62, sy=-0.62, dx=x - 16 - FANG_BASE[0],
                                       dy=y - ry * 0.8 * p.mouth_s - FANG_BASE[1]))
    ctx.new_path()
    path(ctx)
    stroke(ctx, 1.3, look.ink)


def mouth_matrix(p: Pose) -> Tuple[str, cairo.Matrix]:
    """Donor mouth part and its matrix in face space (donor mouths hang from their top centre)."""
    name, (ax, ay), s = MOUTHS[p.mouth]
    m = cairo.Matrix()
    m.translate(ax, ay)
    m.scale(s * p.mouth_s, s * p.mouth_s)
    return name, m


def draw_pucker(ctx: cairo.Context, p: Pose, look: Look) -> None:
    """Closed pursed lips (ref 05) with a puffed-cheek crease; drawn after the arms so a held spoon or ladle
    goes in between the lips."""
    x, y, rx, ry = PUCKER
    k = p.mouth_s
    ctx.save()
    ctx.transform(mul(head_matrix(p), face_matrix(p)))
    outlined(ctx, ellipse(x, y, rx * k, ry * k, -8), look.colours["skin"], look, 1.3)
    ctx.new_path()
    ctx.move_to(x - rx * 0.7 * k, y + 2)
    ctx.curve_to(x - rx * 0.2 * k, y - 6 * k, x + rx * 0.2 * k, y + 8 * k, x + rx * 0.7 * k, y)
    stroke(ctx, 0.9, look.ink)
    ctx.new_path()                             # cheek crease bulging around the puffed cheek
    ctx.arc(x - 70, y - 6, 40, math.radians(120), math.radians(220))
    stroke(ctx, 1.0, look.ink)
    ctx.restore()


def draw_mouth(ctx: cairo.Context, p: Pose, look: Look) -> None:
    if p.mouth == "pucker":                    # drawn by draw_character after the arms
        return
    if p.mouth == "smile":
        draw_part(ctx, "mouth", look)          # ref 01: cheek crease, smile line, fang
        return
    if p.mouth == "sip":
        draw_sip(ctx, p, look)
        return
    name, m = mouth_matrix(p)
    draw_part(ctx, name, look, m)
    if p.mouth == "panic":                     # the TV-capture mouth lost its fang: hang ref 01's under the lip
        fx_, fy_, rot = PANIC_FANG
        x, y = m.transform_point(fx_, fy_)
        draw_part(ctx, "fang", look, about(FANG_BASE, rot, sx=1.2, sy=-1.2, dx=x - FANG_BASE[0],
                                           dy=y - FANG_BASE[1]))


def dome(ctx: cairo.Context) -> None:
    (x0, y0), (x1, y1) = BRIM_L, BRIM_R
    ctx.save()
    ctx.translate((x0 + x1) / 2, (y0 + y1) / 2)
    ctx.rotate(math.atan2(y1 - y0, x1 - x0))
    ctx.scale(math.hypot(x1 - x0, y1 - y0) / 2, DOME_H)
    ctx.new_sub_path()
    ctx.arc(0, 0, 1, 0, 2 * math.pi)
    ctx.close_path()
    ctx.restore()


def below_brim(ctx: cairo.Context) -> None:
    """The half plane below the straight line through the ends of the brim."""
    (x0, y0), (x1, y1) = BRIM_L, BRIM_R
    k = (y1 - y0) / (x1 - x0)
    ctx.move_to(-3000, y0 + k * (-3000 - x0))
    ctx.line_to(4000, y0 + k * (4000 - x0))
    ctx.line_to(4000, 4000)
    ctx.line_to(-3000, 4000)
    ctx.close_path()


def jowl_line(ctx: cairo.Context, look: Look) -> None:
    """The face's outline at one weight, inside its silhouette: in ref 01 the collar's line ran beside the jowl's,
    so the traced jowl line alone thins out wherever the head moves off the collar."""
    ctx.save()
    ctx.new_path()
    add_subs(ctx, part("face").sil)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    ctx.clip_preserve()
    ctx.set_source_rgb(*look.ink)
    stroke_px(ctx, 2 * JOWL_LINE)
    ctx.restore()


def draw_hat(ctx: cairo.Context, p: Pose, look: Look) -> None:
    look = look.parts.get("hat", look)
    fn = hat_warp(p)
    h = part("hat")
    warp = lambda subs: [(cmds, fn(pts)) for cmds, pts in subs]
    bent = Part([(k, warp(s)) for k, s in h.colour], warp(h.ink), warp(h.sil))
    fill_part(ctx, bent, look)
    ink_part(ctx, bent, look)


def draw_head(ctx: cairo.Context, p: Pose, look: Look) -> None:
    ctx.save()
    ctx.transform(face_matrix(p))
    ctx.save()
    ctx.new_path()
    dome(ctx)
    below_brim(ctx)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.clip()
    draw_part(ctx, "face", look)
    jowl_line(ctx, look)
    ctx.restore()
    draw_mouth(ctx, p, look)
    draw_eye(ctx, "eye_b", p, look)
    draw_eye(ctx, "eye_f", p, look)
    draw_part(ctx, "nose", look)
    draw_hat(ctx, p, look)
    ctx.restore()


WHEEL_HUB, WHEEL_R, N_LEGS = (640.0, 1440.0), (430.0, 290.0), 10   # hub under the robe; wide like ref 03's band


def leg_loop(ctx: cairo.Context, length: float, w0: float, w1: float, bend: float) -> None:
    """One leg along +y: narrow at the hip, rounded at the foot, bowed sideways by `bend`."""
    r = w1 / 2
    ctx.move_to(-w0 / 2, 0)
    ctx.curve_to(-w0 / 2 + bend, length * 0.45, -r + bend * 0.6, length * 0.6, -r, length - r)
    ctx.arc_negative(0, length - r, r, math.pi, 0)
    ctx.curve_to(r + bend * 0.6, length * 0.6, w0 / 2 + bend, length * 0.45, w0 / 2, 0)
    ctx.close_path()


def draw_wheel(ctx: cairo.Context, phase: float, look: Look) -> None:
    """The show's run gag (ref 03): the legs blur into a wheel of lavender legs ending in shoes, wider than
    tall. It turns clockwise (the feet push back) two leg spacings per cycle; every leg's shape and depth
    depend only on its angle, so the loop is seamless. Legs bow forward so the tips trail."""
    hx, hy = WHEEL_HUB
    rx, ry = WHEEL_R
    angles = [2 * math.pi * (j + 2 * phase) / N_LEGS for j in range(N_LEGS)]
    for a in sorted(angles, key=lambda a: (round(math.cos(a), 6), round(math.sin(a), 6))):   # lower legs in front
        ex, ey = -rx * math.sin(a), ry * math.cos(a)
        length = math.hypot(ex, ey) * (0.9 + 0.1 * math.cos(a))
        ctx.save()
        ctx.translate(hx, hy)
        ctx.rotate(math.atan2(-ex, ey))
        ctx.new_path()
        leg_loop(ctx, length, 50, 100, -0.2 * length)
        ctx.set_source_rgb(*look.colours["skin"])
        ctx.fill_preserve()
        stroke(ctx, 1.3, look.ink)
        draw_part(ctx, "shoe", look, about(SHOE_C, sx=0.5, sy=0.5, dx=-SHOE_C[0], dy=length - 30 - SHOE_C[1]))
        ctx.restore()


def fx_wheel_arcs(p: Pose) -> Callable:
    """Grey speed arcs around the bottom of the leg wheel (cell space, behind the figure)."""
    def fn(ctx: cairo.Context) -> None:
        for d, a0, a1 in ((40, 10, 55), (40, 125, 170), (80, 16, 46), (80, 134, 164)):
            ctx.save()
            ctx.transform(pose_matrix(p))
            ctx.translate(*WHEEL_HUB)
            ctx.scale(WHEEL_R[0] + d, WHEEL_R[1] + d)
            ctx.new_path()
            ctx.arc(0, 0, 1, math.radians(a0), math.radians(a1))
            ctx.restore()          # the path keeps its place; stroke it in cell px
            fx_line(ctx, 1.4)
    return fn


def draw_feet(ctx: cairo.Context, p: Pose, look: Look) -> None:
    if p.wheel is not None:
        draw_wheel(ctx, p.wheel, look)
        return
    bx, by, bs = SHOE_BACK
    for (dx, dy, rot), m in ((p.shoe_f, about(SHOE_C, sx=bs, sy=bs, dx=bx, dy=by)),
                             (p.shoe_n, cairo.Matrix())):
        draw_part(ctx, "shoe", look, mul(about(SHOE_C, rot, dx=dx, dy=dy), m))


def draw_character(ctx: cairo.Context, p: Pose, look: Look = LOOK) -> None:
    """Draw one pose in cell space (192x208 units) on any cairo context."""
    ctx.save()
    ctx.transform(pose_matrix(p))
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for fn in p.back:
        fn(ctx, look)
    draw_feet(ctx, p, look)
    ctx.save()
    ctx.transform(lean_matrix(p))
    if p.far is None:
        draw_part(ctx, "far", look, about(FAR_PIVOT, p.far_rot))
    elif not p.far_front:
        draw_arm(ctx, p.far, True, look)
    if p.near_behind:                       # raised arm: it comes out from behind the shoulder and the head
        draw_arm(ctx, p.near, False, look)
    draw_part(ctx, "tail", look, about(TAIL_ROOT, p.tail_rot))
    draw_part(ctx, "body", look, about((640.0, 1460.0), sx=1 + 0.012 * p.breath + 0.05 * p.belly,
                                       sy=1 + 0.02 * p.breath - 0.035 * p.belly))
    ctx.save()
    ctx.transform(head_matrix(p))
    draw_head(ctx, p, look)
    ctx.restore()
    for fn in p.mid:
        fn(ctx, look)
    if not p.near_behind:
        draw_arm(ctx, p.near, False, look)
    if p.far is not None and p.far_front:
        draw_arm(ctx, p.far, True, look)
    if p.mouth == "pucker":
        draw_pucker(ctx, p, look)
    for fn in p.front:
        fn(ctx, look)
    ctx.restore()
    ctx.restore()


# ---------------------------------------------------------------------------
# Props (model space unless held: held props live in the fist frame, handle along local x through GRIP)
# ---------------------------------------------------------------------------
def held_fork(scale: float = 0.75) -> Callable:
    """The light blue fork of ref 02, traced in the same frame as its fist."""
    def fn(ctx: cairo.Context, look: Look) -> None:
        draw_part(ctx, "fork", look, about(GRIP, sx=scale, sy=scale))
    return fn


def held_stick(length: float, head: Optional[Callable[[cairo.Context, Look], None]], tail: float = 150.0,
               side: float = 1.0) -> Callable:
    """Wooden handle through the fist along local +x (side=-1: -x, so the thumb stays on top while the head
    hangs below), with a head drawn at its far end (local coords); head=None when it is inside a mouth."""
    def fn(ctx: cairo.Context, look: Look) -> None:
        m = ctx.get_matrix()
        gx, gy = m.transform_point(*GRIP)
        dx, dy = m.transform_distance(side, 0.0)
        ctx.save()
        ctx.identity_matrix()       # rigid: the prop takes the fist's place and turn but not the body's squash
        ctx.translate(gx, gy)
        ctx.rotate(math.atan2(dy, dx))
        det = m.xx * m.yy - m.xy * m.yx
        ctx.scale(math.sqrt(abs(det)), math.copysign(math.sqrt(abs(det)), det * side))
        ctx.new_path()
        ctx.move_to(-tail, 0)
        ctx.line_to(length, 0)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        stroke(ctx, 4.0, look.ink)
        ctx.move_to(-tail, 0)
        ctx.line_to(length, 0)
        stroke(ctx, 2.2, look.colours["wood"])
        if head is not None:
            ctx.translate(length, 0)
            head(ctx, look)
        ctx.restore()
    return fn


SPOON_LEN, LADLE_LEN = 78.0, 92.0     # how far each head reaches past the end of its handle (hand units)
# The spoon and the ladle are rigid: (handle from the grip to the head, end behind the fist) in hand units.
# A pose moves the fist to where the prop must reach (grip_to); it never stretches the prop to the target.
SPOON, LADLE = (250.0, 110.0), (300.0, 130.0)
LADLE_DIP = 152.0               # ladle length under the stew while stirring (the head and a bit of handle)


def ladle_head(ctx: cairo.Context, look: Look) -> None:
    outlined(ctx, ellipse(40, 0, 52, 40), look.colours["wood"], look, 1.2)


def spoon_head(ctx: cairo.Context, look: Look) -> None:
    outlined(ctx, ellipse(34, 0, 44, 28), look.colours["wood"], look, 1.2)
    ctx.new_path()
    ellipse(30, -4, 26, 14)(ctx)
    ctx.set_source_rgb(*look.colours["stew"])
    ctx.fill()


def clean_spoon_head(ctx: cairo.Context, look: Look) -> None:
    outlined(ctx, ellipse(34, 0, 44, 28), look.colours["wood"], look, 1.2)


POT_C, POT_W, POT_TOP, POT_H, POT_RY = 1215.0, 230.0, 1300.0, 240.0, 52.0   # centre x, half width, rim y,
#                                                                         wall height, rim half height


def pot_inside(ctx: cairo.Context, look: Look, k: int) -> None:
    """Rim opening, dark inside and yellow stew (ref 06 batter) with bubbles."""
    rim = ellipse(POT_C, POT_TOP, POT_W, POT_RY)
    outlined(ctx, rim, look.colours["pot_in"], look, 1.3)
    ctx.save()
    ctx.new_path()
    rim(ctx)
    ctx.clip()
    ctx.new_path()
    ellipse(POT_C, POT_TOP + 14, POT_W - 16, POT_RY - 10)(ctx)
    ctx.set_source_rgb(*look.colours["stew"])
    ctx.fill()
    for j in range(3):
        bx = POT_C - 120 + ((j * 97 + k * 53) % 240)
        outlined(ctx, ellipse(bx, POT_TOP + 8 + (j % 2) * 14, 13 + 4 * ((k + j) % 2), 8), look.colours["stew"],
                 look, 0.6)
    ctx.restore()


def pot_wall(ctx: cairo.Context, look: Look) -> None:
    """Front wall below the rim, the handles and a highlight."""
    def body(c: cairo.Context) -> None:
        c.move_to(POT_C - POT_W, POT_TOP)
        c.save()
        c.translate(POT_C, POT_TOP)
        c.scale(POT_W, POT_RY)
        c.arc_negative(0, 0, 1, math.pi, 0)
        c.restore()
        c.curve_to(POT_C + POT_W, POT_TOP + POT_H * 0.5, POT_C + POT_W - 18, POT_TOP + POT_H * 0.94,
                   POT_C + POT_W - 60, POT_TOP + POT_H)
        c.line_to(POT_C - POT_W + 60, POT_TOP + POT_H)
        c.curve_to(POT_C - POT_W + 18, POT_TOP + POT_H * 0.94, POT_C - POT_W, POT_TOP + POT_H * 0.5,
                   POT_C - POT_W, POT_TOP)
        c.close_path()
    for side in (-1, 1):
        outlined(ctx, ellipse(POT_C + side * (POT_W + 16), POT_TOP + 60, 34, 24), look.colours["pot"], look, 1.2)
    outlined(ctx, body, look.colours["pot"], look, 1.3)
    ctx.new_path()
    ctx.move_to(POT_C + POT_W - 80, POT_TOP + 85)
    ctx.curve_to(POT_C + POT_W - 62, POT_TOP + 130, POT_C + POT_W - 66, POT_TOP + 175, POT_C + POT_W - 88,
                 POT_TOP + 205)
    stroke(ctx, 2.0, look.colours["pot_l"])


POT_GX = -14.0                  # the pot stands still on the ground while the cook leans, hops and squashes


def pot_cell(x: float, y: float) -> Pt:
    """Cell position of a point in pot space."""
    return pose_matrix(Pose(gx=POT_GX)).transform_point(x, y)


def pot_space(p: Pose) -> cairo.Matrix:
    """From pot space (the ground frame: no lean, rotation or squash) into the leaned body space of pose p,
    where props are drawn."""
    inv_pose, inv_lean = pose_matrix(p), lean_matrix(p)
    inv_pose.invert()
    inv_lean.invert()
    return mul(inv_lean, inv_pose, pose_matrix(Pose(gx=POT_GX)))


def pot(k: int, p: Pose) -> Callable:
    """The pot stands in front of the robe hem; the arms are drawn after it."""
    def fn(ctx: cairo.Context, look: Look) -> None:
        ctx.save()
        ctx.transform(pot_space(p))
        pot_inside(ctx, look, k)
        pot_wall(ctx, look)
        ctx.restore()
    return fn


def stir(k: int, p: Pose, far: Arm, held: Callable, entry: Pt) -> Callable:
    """The pot with a held stick entering the stew at `entry` (pot space): only what is above the surface
    shows. The stirring fist stays above the rim and is drawn after this."""
    def fn(ctx: cairo.Context, look: Look) -> None:
        to_pot = pot_space(p)
        ctx.save()
        ctx.transform(to_pot)
        pot_inside(ctx, look, k)
        ctx.restore()
        ctx.save()
        ctx.save()
        ctx.transform(to_pot)
        ctx.new_path()
        ctx.rectangle(-5000, -5000, 12000, entry[1] + 5000)
        ctx.restore()                          # the path keeps its place; only the matrix is restored
        ctx.clip()
        ctx.transform(hand_matrix(far, True))
        held(ctx, look)
        ctx.restore()
        ctx.save()
        ctx.transform(to_pot)
        ctx.new_path()
        ellipse(entry[0], entry[1], 32, 9)(ctx)
        stroke(ctx, 0.9, look.ink)
        pot_wall(ctx, look)
        ctx.restore()
    return fn


def reach(a: Arm, far: bool, local: Pt, target: Pt, turn: float) -> Arm:
    """Set the arm's angle and length so the hand-local point `local` lands on `target`, the hand keeping the
    total rotation `turn` (deg)."""
    s = FAR_SLEEVE if far else 1.0
    a.hand_rot, a.angle = 0.0, turn
    m = hand_matrix(a, far)
    wx, wy = sleeve_matrix(a, far).transform_point(*WRIST_N)
    px, py = m.transform_point(*local)
    root = a.root or (SH_F if far else SH_N)
    vx, vy = target[0] - (px - wx) - root[0], target[1] - (py - wy) - root[1]
    ux = (-s if far else 1.0) * (WRIST_N[0] - SH_N[0])
    dy = s * (WRIST_N[1] - SH_N[1])
    a.length = math.sqrt(max(vx * vx + vy * vy - ux * ux, 1.0)) / dy
    a.angle = math.degrees(math.atan2(vy, vx) - math.atan2(a.length * dy, ux))
    a.hand_rot = turn - a.angle
    return a


def grip_to(a: Arm, target: Pt, along: float, side: float = 1.0) -> Arm:
    """Slide the far fist along its handle (aim() it first) so the point `along` hand units out the handle
    lands on `target`; the arm takes up the slack."""
    return reach(a, True, (GRIP[0] + side * along, GRIP[1]), target, a.angle + a.hand_rot)


BOWL_W = 190.0


def bowl_at(c: Pt) -> Callable:
    """Empty light blue-grey bowl (ref 06) centred at c, rim up."""
    def fn(ctx: cairo.Context, look: Look) -> None:
        x, y = c

        def body(cc: cairo.Context) -> None:
            cc.move_to(x - BOWL_W, y)
            cc.curve_to(x - BOWL_W + 10, y + 120, x - 80, y + 150, x, y + 150)
            cc.curve_to(x + 80, y + 150, x + BOWL_W - 10, y + 120, x + BOWL_W, y)
            cc.close_path()
        outlined(ctx, body, look.colours["bowl"], look, 1.3)
        outlined(ctx, ellipse(x, y, BOWL_W, 44), look.colours["bowl"], look, 1.3)
        ctx.new_path()
        ellipse(x, y + 6, BOWL_W - 28, 30)(ctx)
        ctx.set_source_rgb(*look.colours["bowl_in"])
        ctx.fill()
        for dx, dy in ((-40, 8), (36, 14)):
            outlined(ctx, ellipse(x + dx, y + dy, 9, 6), look.colours["stew"], look, 0.5)
    return fn


# ---------------------------------------------------------------------------
# Effects (cell coordinates; grey, no outline, no rim)
# ---------------------------------------------------------------------------
def fx_line(ctx: cairo.Context, width: float = 1.8) -> None:
    ctx.set_source_rgb(*FXGREY)
    ctx.set_line_width(width)
    ctx.stroke()


def fx_shake(x: float, y: float, side: int, n: int = 3, size: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        for k in range(n):
            r = (7 + 5 * k) * size
            a0 = math.radians(-35 if side > 0 else 145)
            ctx.new_path()
            ctx.arc(x - side * 6, y, r, a0, a0 + math.radians(70))
            fx_line(ctx)
    return fn


def fx_speed(lines: Sequence[Tuple[float, float, float]]) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        for x, y, ln in lines:
            ctx.new_path()
            ctx.move_to(x, y)
            ctx.line_to(x + ln, y)
            fx_line(ctx)
    return fn


def fx_arcs(x: float, y: float, start: float, sweep: float, radii=(9.0, 13.5)) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        for r in radii:
            ctx.new_path()
            ctx.arc(x, y, r, math.radians(start), math.radians(start + sweep))
            fx_line(ctx, 1.7)
    return fn


def fx_dust(x: float, y: float, s: float = 1.0) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        for dx, dy, r in ((-5, 0, 3.6), (0.5, -2, 4.4), (6, 0, 3.3)):
            ctx.new_path()
            ellipse(x + dx * s, y + dy * s, r * s, r * 0.8 * s)(ctx)
            ctx.set_source_rgb(*FXGREY)
            ctx.fill()
    return fn


def fx_drop(x: float, y: float, rot: float = 0.0, s: float = 1.0, colour: RGB = SWEAT) -> Callable:
    def fn(ctx: cairo.Context) -> None:
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(math.radians(rot))
        ctx.scale(s, s)
        ctx.new_path()
        ctx.move_to(0, -6)
        ctx.curve_to(1.5, -3, 4.2, -0.5, 4.2, 2.2)
        ctx.curve_to(4.2, 4.8, 2.2, 6.4, 0, 6.4)
        ctx.curve_to(-2.2, 6.4, -4.2, 4.8, -4.2, 2.2)
        ctx.curve_to(-4.2, -0.5, -1.5, -3, 0, -6)
        ctx.close_path()
        ctx.set_source_rgb(*colour)
        ctx.fill_preserve()
        ctx.set_source_rgb(*INK)
        ctx.set_line_width(1.4)
        ctx.stroke()
        ctx.new_path()
        ellipse(-1.5, 2.4, 1.0, 1.5, 20)(ctx)
        ctx.set_source_rgb(1, 1, 1)
        ctx.fill()
        ctx.restore()
    return fn


def fx_steam(x: float, y: float, k: int) -> Callable:
    """Two wavy wisps rising from a pot; phase k shifts them up."""
    def fn(ctx: cairo.Context) -> None:
        for j, dx in enumerate((-9.0, 7.0)):
            ph = (k + 3 * j) % 6
            y0 = y - ph * 2.0
            ctx.new_path()
            ctx.move_to(x + dx + 2.6 * math.sin(ph), y0)
            for i in range(1, 5):
                ctx.line_to(x + dx + 2.6 * math.sin(i * 1.3 + ph), y0 - i * 3.2)
            fx_line(ctx)
    return fn


def fx_sniff(x: float, y: float, n: int = 2) -> Callable:
    """Little curls of smell streaming into the nose from the right."""
    def fn(ctx: cairo.Context) -> None:
        for i in range(n):
            ctx.new_path()
            ox, oy = x + 7 + 7 * i, y - 3 - 4 * i
            ctx.move_to(ox + 7, oy)
            ctx.curve_to(ox + 4, oy - 4, ox + 1, oy + 4, ox - 2, oy)
            fx_line(ctx, 1.5)
    return fn


def fx_clink(x: float, y: float, k: int = 0, rays: Sequence[float] = (), r: Pt = (4.0, 9.0)) -> Callable:
    """Impact spark: short lines radiating from the point where cutlery hits the bowl (rays: their angles in
    deg; default a fan over the top)."""
    def fn(ctx: cairo.Context) -> None:
        for a in rays or [-160 + 35 * j + 10 * k for j in range(5)]:
            a = math.radians(a)
            ctx.new_path()
            ctx.move_to(x + r[0] * math.cos(a), y + r[0] * math.sin(a))
            ctx.line_to(x + r[1] * math.cos(a), y + r[1] * math.sin(a))
            fx_line(ctx, 1.6)
    return fn


def fx_rumble(x: float, y: float) -> Callable:
    """Stomach rumble: three zigzags beside the belly."""
    def fn(ctx: cairo.Context) -> None:
        for dy in (-8.0, 0.0, 8.0):
            ctx.new_path()
            ctx.move_to(x, y + dy)
            for i in range(1, 6):
                ctx.line_to(x + 3.4 * i, y + dy + (3.0 if i % 2 else -3.0))
            fx_line(ctx, 1.7)
    return fn


def fx_puff(x: float, y: float, r: float = 9.0) -> Callable:
    """A cloud of steam: overlapping grey circles."""
    def fn(ctx: cairo.Context) -> None:
        ctx.set_source_rgb(*FXGREY)
        for dx, dy, rr in ((0, 0, 1.0), (-0.9, 0.3, 0.7), (0.9, 0.25, 0.75), (-0.4, -0.7, 0.65), (0.5, -0.65, 0.6)):
            ctx.new_path()
            ctx.arc(x + dx * r, y + dy * r, rr * r, 0, 2 * math.pi)
            ctx.fill()
    return fn


def fx_sparkle(x: float, y: float, s: float = 1.0) -> Callable:
    """A four-point twinkle (savouring)."""
    def fn(ctx: cairo.Context) -> None:
        ctx.new_path()
        ctx.move_to(x, y - 6 * s)
        for a in range(1, 8):
            r = 6 * s if a % 2 == 0 else 1.6 * s
            ang = -math.pi / 2 + a * math.pi / 4
            ctx.line_to(x + r * math.cos(ang), y + r * math.sin(ang))
        ctx.close_path()
        ctx.set_source_rgb(*FXGREY)
        ctx.fill()
    return fn


def fx_dots(x: float, y: float, n: int) -> Callable:
    """Thinking dots growing to the upper right."""
    def fn(ctx: cairo.Context) -> None:
        for i in range(n):
            ctx.new_path()
            ellipse(x + i * 6.5, y - i * 4.5, 1.6 + 0.5 * i, 1.6 + 0.5 * i)(ctx)
            ctx.set_source_rgb(*FXGREY)
            ctx.fill()
    return fn


# ---------------------------------------------------------------------------
# Rows (the model faces right; running-left is the mirrored run cycle)
# ---------------------------------------------------------------------------
NOSE_TIP = (990.0, 628.0)       # face space: where smells go in


def row_idle() -> List[Pose]:
    """Calm ambient loop, a relaxed Chowder at Codex holds (0.7-1.9 s a frame) and at Petdex Desktop speed
    (0.1-0.3 s): one slow breath lifting the belly and the jowl, a blink at the top of it, a small sniff with
    the nose up, the hat lobes swaying. 0 and 5 (held) are rest poses close to ref 01; neighbours change a
    little. The big gags live in the other rows."""
    p0 = Pose(pupil=(4, 2), tail_rot=-4)
    p1 = Pose(breath=0.5, belly=0.2, face_sy=1.01, pupil=(4, 0), hat_skew=-3, hat_sy=1.02, far_rot=2)
    p2 = Pose(breath=1.0, belly=0.4, face_sy=1.02, shut="blink", hat_skew=-5, hat_sy=1.04, near=Arm(angle=3),
              far_rot=3, tail_rot=4)
    p3 = Pose(breath=0.8, belly=0.3, face_sy=1.02, head_rot=-4, lid_b=0.25, lid_f=0.25, pupil=(8, -8),
              hat_skew=-1, hat_sy=1.03, near=Arm(angle=4), far_rot=2, tail_rot=6)
    p4 = Pose(breath=0.3, belly=0.1, head_rot=-1, pupil=(6, -2), hat_skew=3, hat_sy=1.01, near=Arm(angle=2),
              tail_rot=2)
    p5 = Pose(pupil=(2, 4), hat_skew=2, tail_rot=-7)
    p3.fx = [fx_sniff(*to_cell(p3, 1200, NOSE_TIP[1], head=True), 1)]     # in the air, clear of the face
    return [p0, p1, p2, p3, p4, p5]


def row_run_right() -> List[Pose]:
    """Frantic: the leg wheel, a hard lean, an 8 px bounce with squash and stretch; the hat lobes flap back
    and the hat and jowl lag the bounce (follow-through); gleeful open mouth; dust puffs fly off behind."""
    out = []
    for i in range(8):
        ph = 2 * math.pi * (i + 0.5) / 8    # half a frame off: at whole frames the swing repeats every 4 frames
        up = i % 2                  # every other frame at the top of the stride, stretched
        k = i % 4
        p = Pose(gx=-8, gy=-13 - 8 * up, sx=1.05 - 0.09 * up, sy=0.94 + 0.12 * up, lean=15,
                 head_rot=-3 + 3 * math.sin(2 * ph), hat_skew=16 + 8 * (1 - up), hat_dy=6 * up,
                 hat_sy=1.12 - 0.2 * up, face_sy=0.95 + 0.12 * up, face_sx=1.04 - 0.06 * up, mouth="open",
                 mouth_s=1.0, eye_s=1.15, pupil=(20, -6), wheel=i / 8, tail_rot=20 * math.sin(ph),
                 near=Arm(angle=98 + 34 * math.sin(ph), length=1.1, hand="open", hand_rot=-10),
                 far=Arm(angle=-96 + 28 * math.sin(ph), length=1.0, hand="open", mirror=True))
        p.fx = [fx_speed([(12 - k * 2, 86, 14 + k * 3), (8 + (k * 3) % 5, 112, 16), (12 - k, 138, 12 + k * 2)])]
        p.fx_back = [fx_wheel_arcs(p), fx_dust(30 - 5 * k, 194 - 2 * (k % 2), 0.7 + 0.1 * k),
                     fx_dust(52 - 5 * ((k + 2) % 4), 197, 0.55)]
        out.append(p)
    return out


def hand_point(p: Pose, a: Arm, far: bool, local: Pt) -> Pt:
    """Cell position of a point in a hand's frame."""
    return mul(pose_matrix(p), lean_matrix(p), hand_matrix(a, far)).transform_point(*local)


WAVE_ROOT = (1100.0, 1040.0)    # the raised far arm comes out from behind the robe's right edge


def row_waving() -> List[Pose]:
    """Over-eager: big arm swings, the whole body wiggles and hops, the hat follows a frame late."""
    rots = [-7.0, 6.0, -6.0, 7.0]
    hops = [0.0, -10.0, 0.0, -4.0]
    out = []
    for k, w in enumerate((1, -1, 1, -1)):
        lag = rots[k] - rots[k - 1]         # the hat keeps the previous frame's tilt for a beat
        p = Pose(gx=-6, gy=hops[k], rot=rots[k], sx=1.04 if hops[k] == 0 else 0.96,
                 sy=0.95 if hops[k] == 0 else 1.06, mouth="open", mouth_s=1.0 + 0.1 * (k in (2, 3)),
                 shut="happy" if k == 0 else "", eye_s=1.3 if k == 2 else 1.15, pupil=(8, -4), head_rot=-2 * w,
                 hat_dy=8 if hops[k] == 0 else 0, hat_sy=0.9 if hops[k] == 0 else 1.14, hat_skew=0.8 * lag,
                 tail_rot=14 * w,
                 face_sy=1.05 if hops[k] else 0.96, near=Arm(angle=-12 - 10 * w, hand="open", hand_rot=-10),
                 far=Arm(angle=-138 + 16 * w, length=1.85, hand="open", hand_rot=26 * w, mirror=True,
                         root=WAVE_ROOT))
        hx, hy = hand_point(p, p.far, True, (0.0, 250.0))
        p.fx = [fx_arcs(hx - 2, hy - 5, -150, 55, (5.0, 8.5)) if w > 0 else fx_arcs(hx + 3, hy + 2, -60, 60,
                                                                                  (5.0, 8.5))]
        out.append(p)
    return out


def row_jumping() -> List[Pose]:
    """Deep anticipation squash, launch stretch with the jowl and hat lagging, airborne with the hat lobes
    springing up, falling with them trailing up, splat landing."""
    crouch = Pose(sy=0.8, sx=1.14, face_sx=1.06, face_sy=0.92, hat_dy=8, hat_sy=0.9, hat_sx=1.04, shut="squeeze",
                  mouth="smile", near=Arm(angle=-22, hand="open", hand_rot=10), far=Arm(angle=-38, hand="open",
                                                                                     mirror=True),
                  shoe_n=(-30, 0, -8), shoe_f=(30, 0, 8), tail_rot=-14,
                  fx_back=[fx_dust(60, 198, 0.6), fx_dust(140, 198, 0.6)])
    launch = Pose(gy=-9, sy=1.12, sx=0.9, face_sx=0.94, face_sy=1.14, hat_dy=8, hat_sy=0.86, eye_s=1.3, pupil_s=0.6,
                  pupil=(-6, -10), mouth="o", near=Arm(angle=150, length=1.6, hand="open", hand_rot=30),
                  near_behind=True, far=Arm(angle=-150, length=1.6, hand="open", hand_rot=-25, mirror=True),
                  shoe_n=(10, 25, 25), shoe_f=(-10, 25, -25), tail_rot=-28)
    peak = Pose(gy=-20, sy=1.03, sx=0.98, hat_sy=1.16, eye_s=1.4, pupil_s=0.5, pupil=(-6, -12),
                mouth="scream", mouth_s=0.85, face_sy=1.02, near=Arm(angle=136, length=1.85, hand="open", hand_rot=40),
                near_behind=True, far=Arm(angle=-146, length=1.85, hand="open", hand_rot=-32, mirror=True),
                shoe_n=(-24, -10, 30), shoe_f=(24, -10, -30), tail_rot=-18,
                fx=[fx_drop(30, 50, -30), fx_drop(164, 48, 30)])
    fall = Pose(gy=-14, sy=1.04, sx=0.96, hat_sy=1.24, hat_skew=8, face_sx=1.04, face_sy=0.92, eye_s=1.3, pupil_s=0.6,
                pupil=(-4, 8), mouth="o", mouth_s=1.25, near=Arm(angle=112, length=1.4, hand="open", hand_rot=20),
                near_behind=True, far=Arm(angle=-118, length=1.4, hand="open", hand_rot=-15, mirror=True),
                shoe_n=(0, 20, 10), shoe_f=(0, 20, -10), tail_rot=16)
    splat = Pose(sy=0.72, sx=1.19, face_sx=1.12, face_sy=0.86, hat_dy=10, hat_sy=0.88, hat_sx=1.06, shut="squeeze",
                 mouth="o", mouth_s=0.8, near=Arm(angle=100, hand="open", hand_rot=-20),
                 far=Arm(angle=-46, hand="open", mirror=True), shoe_n=(-60, 0, -15), shoe_f=(60, 0, 15), tail_rot=-24,
                 fx_back=[fx_dust(44, 196, 1.2), fx_dust(150, 196, 1.2), fx_dust(28, 192, 0.7),
                          fx_dust(166, 192, 0.7)])
    return [crouch, launch, peak, fall, splat]


def row_failed() -> List[Pose]:
    """The take: the whole head pops up with the body, the hat lobes spring up and stretch and the eyes pop
    (0-1); then the scream with a full-body shake while the lobes land late and wobble (2-7)."""
    out = []
    rots = [0.0, 0.0, -5.0, 5.0, -5.0, 5.0, -4.0, 4.0]
    for k in range(8):
        w = 1 if k % 2 == 0 else -1
        take = k < 2
        p = Pose(gx=0.9 * rots[k] if not take else 0.0, gy=-3 - 2 * k if take else 0.0, rot=rots[k],
                 sx=0.96 - 0.01 * k if take else (0.97 if k % 2 else 1.03),
                 sy=1.06 + 0.01 * k if take else (1.03 if k % 2 else 0.97),
                 mouth="panic", mouth_s=0.85 + 0.12 * k if take else 0.98 + 0.04 * w,
                 eye_s=1.45 - 0.05 * k if take else 1.3, pupil_s=0.4 if take else 0.55, pupil=(-8, -12),
                 face_sx=0.96 if take else 1.0, face_sy=1.1,
                 hat_sy=(1.22 - 0.06 * k) if take else (0.86 if k == 2 else 1.0 + 0.05 * w),
                 hat_sx=(0.94 + 0.16 * k) if take else 1.0, hat_skew=-6 * k if take else 1.6 * rots[k],
                 hat_dy=8 if k == 2 else 0,
                 near=Arm(angle=152 if take else 133 + 3 * w, length=1.85, hand="open", hand_rot=40 + 8 * w),
                 far=Arm(angle=-160 if take else -145 + 3 * w, length=1.85, hand="open", hand_rot=-32 + 8 * w,
                         mirror=True),
                 near_behind=True, tail_rot=(30 if take else 12 * w))
        if take:                            # shock bursts beside the head on the take
            p.fx = [fx_clink(26, 74, 2), fx_clink(170, 70, -2)] if k == 0 else []
        else:
            p.fx = [fx_shake(18, 110, -1, 3, 0.85), fx_shake(176, 106, 1, 3, 0.85),
                    fx_drop(30 + (k % 4), 40 + k, -35) if k % 2 == 0 else fx_drop(164 - (k % 4), 36 + k, 35)]
        out.append(p)
    return out


FORK_ROOT = (1080.0, 1060.0)    # the fork arm's shoulder, at the robe's right edge
BOWL_GRIP = (-46.0, 112.0)      # ref-06 hand frame: where its fingers hold the bowl's rim


FORK_BUTT = (-150.0, 127.0)     # fist frame: the end of the fork's handle (see held_fork)


def row_waiting() -> List[Pose]:
    """Impatient: eyes dart between the fork and the empty bowl, the mouth chomps (grin, shut), he bangs the
    fork's butt on the bowl twice, his stomach rumbles, then pleading puppy eyes."""
    out = []
    plan = [  # fork: up/bang/mid; eyes; mouth; body squash; belly
        ("up", (16, -12), "open", 1.03, 0.0), ("bang", (-4, 16), "smile", 0.93, 0.0), ("mid", (20, -2), "open", 1.03, 0.0),
        ("bang", (-2, 18), "smile", 0.93, 0.0), ("low", (-8, 18), "o", 0.96, 1.3), ("up", (0, 2), "open", 1.02, 0.0)]
    for k, (fork, pupil, mouth, sy, belly) in enumerate(plan):
        bang = fork == "bang"
        near = Arm(angle=-60 if bang else -64, hand="bowl")
        bx, by = hand_matrix(near, False).transform_point(*BOWL_GRIP)
        bowl_c = (bx + BOWL_W - 30, by + 12 + (14 if bang else 0))
        far = Arm(hand="fist", held=held_fork(), root=FORK_ROOT)
        if bang:
            reach(far, True, FORK_BUTT, (bowl_c[0] + 70 - 20 * k, bowl_c[1] - 18), -90)
        else:
            far.angle, far.length = {"up": (-128, 1.45), "mid": (-108, 1.3), "low": (-84, 1.1)}[fork]
            far.hand_rot = -90 - far.angle          # the fork stays upright
        p = Pose(gx=-10, sy=sy, sx=2 - sy, belly=belly, mouth=mouth, mouth_s=0.95 if mouth == "open" else 0.8,
                 pupil=pupil, pupil_s=1.5 if k == 5 else 1.2, eye_s=1.25 if k == 5 else 1.08,
                 head_rot=6 if k == 5 else (-3 if bang else 0), rot=-3 if k == 5 else (2 if bang else 0),
                 hat_dy=8 if bang else 0, hat_sy=0.88 if bang else 1.08, hat_skew=-6 if k == 5 else 0,
                 face_sy=0.94 if bang else 1.04, tail_rot=14 if bang else -6, near=near, far=far, far_front=True,
                 mid=[bowl_at(bowl_c)])
        if bang:
            cx, cy = mul(pose_matrix(p), lean_matrix(p)).transform_point(bowl_c[0] + 70 - 20 * k, bowl_c[1] - 18)
            p.fx = [fx_clink(cx, cy - 7, rays=(-150, -30), r=(3, 6.5))]   # beside the handle, off the robe's lines
        if belly:
            rx, ry = to_cell(p, 1140, 1260)
            p.fx = [fx_rumble(rx + 10, ry + 12)]
        out.append(p)
    return out


def row_work() -> List[Pose]:
    """Stirs hard, leaning into it (0-1); a puff of steam rises past his face (2); a sneaky look (3) and a taste
    of the ladle (4); back to stirring with a guilty glance (5). The pot stays put on the ground."""
    out = []
    for k in range(6):
        a = 2 * math.pi * k / 6
        p = Pose(gx=-14, mouth="open" if k == 0 else "smile", mouth_s=0.7, pupil=(14, 18), far_front=True,
                 tail_rot=12 * math.sin(a))
        entry, arm = None, None
        if k in (0, 1, 5):                  # stirring: the fist above the rim, the ladle in the stew
            side = (-1, 1, 0)[(0, 1, 5).index(k)]
            p.rot, p.lean = 4 * side, 4 + 2 * side
            p.hat_skew, p.hat_dy, p.hat_sy = -10 * side, 0 if k == 5 else 8, 1.06 if k == 5 else 0.94
            p.sx, p.sy = (1.04, 0.96) if k == 1 else (0.98, 1.02)
            if k == 5:
                p.pupil = (24, -6)          # a guilty glance away
            entry = (POT_C + 10 + 95 * side, POT_TOP + 16 + 8 * side)
            # angles keep the fist clear of the rim against each frame's tilt (the fist is ~19 px tall)
            arm = Arm(angle={-1: -96.0, 1: -128.0, 0: -104.0}[side], length=1.0, hand="fist")
        elif k == 2:                        # steam puff in the face: head jerked back, eyes squeezed, lobes up
            p.gx, p.rot, p.lean, p.head_rot, p.head_dy = -10, -4, -5, -8, -10
            p.shut, p.mouth, p.mouth_s = "squeeze", "o", 0.8
            p.hat_sy, p.hat_skew, p.face_sy = 1.16, 6, 1.06
            entry = (POT_C, POT_TOP + 16)
            arm = Arm(angle=-104, length=1.0, hand="fist")
            x, y = to_cell(p, 1215, 610, head=True)     # in front of the nose, clear of the face: hides no line
            p.fx = [fx_puff(x + 12, y, 6.5)]
            p.fx_back = [fx_steam(*pot_cell(POT_C + 150, POT_TOP - 70), k), fx_steam(x + 10, y + 26, k + 3)]
        else:                               # sneak the ladle to the mouth, then the pursed lips on it
            p.sy, p.sx, p.head_dy, p.rot = 0.95, 1.03, 20, 2 * (k - 3)
            p.pupil = (-26, -2) if k == 3 else (0, 0)
            p.shut = "" if k == 3 else "happy"
            p.mouth = "o" if k == 3 else "pucker"
            p.face_sx = 1.0 if k == 3 else 1.06
            p.hat_dy, p.hat_sy = (8, 0.9) if k == 3 else (0, 1.05)
            target = mul(head_matrix(p), face_matrix(p)).transform_point(*PUCKER[:2])
            stop = 70.0 if k == 3 else 0.0      # the ladle's rim stops short of the lips, then touches them
            p.far = grip_to(aim(Arm(angle=-150, length=0.85, hand="fist"), True, target), target,
                            LADLE[0] + LADLE_LEN + stop)
            p.far.held = held_stick(LADLE[0], ladle_head, LADLE[1])
            p.mid = [pot(k, p)]
        if entry is not None:
            e = pot_space(p).transform_point(*entry)
            p.far = grip_to(aim(arm, True, e, side=-1), e, LADLE[0] + LADLE_LEN - LADLE_DIP, side=-1)
            p.mid = [stir(k, p, p.far, held_stick(LADLE[0], ladle_head, LADLE[1], side=-1), entry)]
        if k != 2:
            p.fx_back = [fx_steam(*pot_cell(POT_C + 150, POT_TOP - 70), k)]
        out.append(p)
    return out


def row_review() -> List[Pose]:
    """Tastes: the spoon comes up to the open mouth (0), the mouth closes on it with puffed cheeks and shut
    eyes (1), the spoon comes out clean (2), a closed-mouth "mmm" (3), then the thoughtful take with the spoon
    held up (4-5, ref 05: heavy lid, eyes up, pursed lips)."""
    out = []
    face = lambda p, x, y: mul(head_matrix(p), face_matrix(p)).transform_point(x, y)
    for k in range(6):
        p = Pose(gx=-14, far_front=True, tail_rot=10 * math.sin(2 * math.pi * k / 6))
        p.mid = [pot(k, p)]
        if k == 0:                      # the spoon's tip stops short of the open mouth: nothing is hidden
            p.mouth, p.mouth_s, p.pupil, p.lean, p.sy = "sip", 1.1, (16, 18), 5, 1.02
            target = face(p, SIP[0], SIP[1])
            p.far = grip_to(aim(Arm(angle=-128, length=0.82, hand="fist"), True, target), target,
                            SPOON[0] + SPOON_LEN + 60)
            p.far.held = held_stick(SPOON[0], spoon_head, SPOON[1])
        elif k == 1:                    # lips shut on it: the bowl is in the mouth, the handle ends at the lips
            p.mouth, p.shut, p.face_sx, p.face_sy, p.sy, p.sx = "pucker", "happy", 1.08, 0.96, 0.95, 1.04
            p.hat_dy, p.hat_sy = 8, 0.88
            target = face(p, PUCKER[0], PUCKER[1])
            p.far = grip_to(aim(Arm(angle=-128, length=0.82, hand="fist"), True, target), target, SPOON[0] + 5)
            p.far.held = held_stick(SPOON[0] + 5, None, SPOON[1])
        elif k in (2, 3):               # pulled out clean, then "mmm"
            p.shut, p.hat_dy, p.hat_sy = "happy", (0, 8)[k - 2], (1.12, 0.92)[k - 2]
            if k == 2:
                p.mouth, p.head_rot, p.sy, p.sx, p.face_sx = "pucker", -6, 1.05, 0.97, 1.04
            else:
                p.mouth, p.rot, p.hat_skew, p.belly = "smile", 5, 8, 0.6
                x, y = to_cell(p, 1060, 420, head=True)
                p.fx = [fx_sparkle(x + 4, y - 4, 1.0), fx_sparkle(x + 16, y + 8, 0.7), fx_sparkle(x + 24, y - 14, 0.6)]
            target = face(p, PUCKER[0] + 60, PUCKER[1] + 60 * (k - 1))
            p.far = grip_to(aim(Arm(angle=-120, length=0.85, hand="fist"), True, target), target,
                            SPOON[0] + SPOON_LEN + 150)
            p.far.held = held_stick(SPOON[0], clean_spoon_head, SPOON[1])
        else:                           # thinking it over, the spoon held up beside the jowl
            p.mouth, p.lid_b, p.pupil = "pucker", 0.5 + 0.05 * (k - 4), ((-6, -16) if k == 4 else (6, -18))
            p.head_rot, p.lean, p.rot, p.hat_skew = 5 + 2 * (k - 4), -4, -3 * (k - 3), -5
            p.far = Arm(angle=-100 + 4 * (k - 4), length=0.9, hand="fist", hand_rot=10 - 4 * (k - 4),
                        held=held_stick(SPOON[0], clean_spoon_head, SPOON[1]))
            x, y = to_cell(p, 1060, 380, head=True)
            p.fx = [fx_dots(x + 4, y - 3, 2 + (k - 4))]
        p.fx_back = [fx_steam(*pot_cell(POT_C + 68, POT_TOP - 80), k)]
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def paint(draw: Callable[[cairo.Context], None], antialias: int = cairo.ANTIALIAS_BEST) -> np.ndarray:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_W * SS, CELL_H * SS)
    ctx = cairo.Context(surf)
    ctx.scale(SS, SS)
    ctx.set_antialias(antialias)
    draw(ctx)
    surf.flush()
    # cairo ARGB32 = premultiplied BGRA (little endian); "BGRa" unpremultiplies.
    im = Image.frombuffer("RGBA", (CELL_W * SS, CELL_H * SS), bytes(surf.get_data()), "raw", "BGRa",
                          surf.get_stride(), 1)
    return np.asarray(im).astype(np.float32) / 255.0


def dilate(alpha: np.ndarray, r: float) -> np.ndarray:
    """Grey-scale dilation of an alpha mask with a disc of radius r (pixels), soft edge."""
    ri = int(math.ceil(r))
    h, w = alpha.shape
    pad = np.pad(alpha, ri)
    out = np.zeros_like(alpha)
    for dy in range(-ri, ri + 1):
        for dx in range(-ri, ri + 1):
            d = math.hypot(dx, dy)
            if d > r + 0.5:
                continue
            k = min(1.0, r + 0.5 - d)
            out = np.maximum(out, pad[ri + dy:ri + dy + h, ri + dx:ri + dx + w] * k)
    return out


def over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Straight-alpha 'over' of float RGBA arrays."""
    a = src[..., 3:4] + dst[..., 3:4] * (1 - src[..., 3:4])
    c = src[..., :3] * src[..., 3:4] + dst[..., :3] * dst[..., 3:4] * (1 - src[..., 3:4])
    return np.concatenate([np.where(a > 0, c / np.maximum(a, 1e-6), 0), a], axis=-1)


def solid(colour: Sequence[float], alpha: np.ndarray) -> np.ndarray:
    out = np.empty(alpha.shape + (4,), np.float32)
    out[..., :3] = colour
    out[..., 3] = alpha
    return out


def render(p: Pose, look: Look = LOOK, rim: bool = True) -> Image.Image:
    fig = paint(lambda c: draw_character(c, p, look))
    contour = dilate(fig[..., 3], OUTER * SS)
    halo = dilate(contour, RIM_R * SS) * RIM_ALPHA if rim else np.zeros_like(contour)
    out = over(over(solid([c / 255 for c in RIM_RGB], halo), solid(INK, contour)), fig)
    if p.fx_back:
        out = over(paint(lambda c: [fn(c) for fn in p.fx_back]), out)
    if p.fx:
        out = over(out, paint(lambda c: [fn(c) for fn in p.fx]))
    img = Image.fromarray(np.round(np.clip(out, 0, 1) * 255).astype(np.uint8), "RGBA")
    arr = np.array(img.resize((CELL_W, CELL_H), Image.LANCZOS))  # Pillow resamples RGBA premultiplied
    arr[arr[:, :, 3] == 0] = 0
    return Image.fromarray(arr)


def row_poses() -> List[List[Pose]]:
    """Every row's poses in atlas order; running-left reuses running-right (render mirrors it)."""
    run = row_run_right()
    return [row_idle(), run, run, row_waving(), row_jumping(), row_failed(), row_waiting(), row_work(),
            row_review()]


def build_frames(rows: List[List[Pose]]) -> List[List[Image.Image]]:
    out: List[List[Image.Image]] = []
    for (name, count), poses in zip(ROW_SPEC, rows):
        mirrored = name == "running-left"
        frames = [ImageOps.mirror(im) for im in out[1]] if mirrored else [render(p) for p in poses]
        assert len(frames) == count, (name, len(frames))
        out.append(frames)
    return out


# ---------------------------------------------------------------------------
# Frame checks (S7): measurable pose rules; any violation stops the build before the sheet is written
# ---------------------------------------------------------------------------
# Each frame is rendered once more through draw_character with a false colour per part or class (the part
# map, without antialiasing so no edge blends into another class's colour), once ink-only through render(),
# and its effect marks alone; the rules read 1x masks off those.
HALF = 128 / 255        # survives 8 bits exactly, so part-map colours compare exactly
PM: Dict[str, RGB] = {"face": (1, 0, 0), "hat": (0, 1, 0), "hand": (0, 0, 1), "sleeve": (1, 1, 0),
                      "robe": (1, 0, 1), "fang": (0, 1, 1), "teeth": (1, HALF, 0), "mouth": (HALF, 0, 0),
                      "prop": (0, HALF, 0), "pot": (0, 0, HALF), "other": (HALF, HALF, HALF)}
OPEN_MOUTHS = ("open", "o", "sip", "scream", "panic")
INK_DARK = 70           # a 1x pixel at or under this luma reads as ink (calibrated on the round-3 jowl gap)
EXPOSED_MAX = 1.0       # cell px^2 of head bared under the brim: brim notches give <= 0.5, a lifted hat 60+
PROP_TOL = 1.0          # cell px
PALM, THUMB = (0.0, 120.0), (-120.0, 115.0)    # in the open hand (hand_open): palm centre and thumb tip


def _look(colour: RGB, **classes: RGB) -> Look:
    return Look({k: classes.get(k, colour) for k in CLASS_RGB}, INK)


def part_look() -> Look:
    o = PM["other"]
    look = _look(o, mouth=PM["mouth"], tongue=PM["mouth"], wood=PM["prop"], pot=PM["pot"], pot_l=PM["pot"],
                 pot_in=PM["pot"], stew=PM["pot"])
    flat = {"face": "face", "body": "robe", "sleeve_n": "sleeve", "hat": "hat", "hand_n": "hand",
            "hand_open": "hand", "hand_fist": "hand", "hand_bowl": "hand"}
    look.parts = {n: _look(PM[k]) for n, k in flat.items()}
    look.parts["fang"] = _look(PM["face"], white=PM["fang"])          # the tooth; its trim of skin is face
    for n in ("mouth", "mouth_open", "mouth_scream", "mouth_scream04"):     # donor mouths: lip skin is face
        look.parts[n] = _look(o, mouth=PM["mouth"], tongue=PM["mouth"], white=PM["teeth"], skin=PM["face"])
    return look


def _down(a: np.ndarray) -> np.ndarray:
    """Hi-res (SS) array to 1x coverage."""
    return a.reshape(CELL_H, SS, CELL_W, SS).mean(axis=(1, 3))


def _grow(m: np.ndarray, r: int = 1) -> np.ndarray:
    """Binary dilation by a (2r+1)^2 square."""
    pad, out = np.pad(m, r), m.copy()
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= pad[dy:dy + m.shape[0], dx:dx + m.shape[1]]
    return out


def _fill_holes(m: np.ndarray) -> np.ndarray:
    """m plus every region of ~m that the cell border cannot reach (4-connected)."""
    out = np.zeros_like(m)
    out[0, :] = out[-1, :] = out[:, 0] = out[:, -1] = True
    out &= ~m
    while True:
        nxt = out.copy()
        nxt[1:] |= out[:-1]
        nxt[:-1] |= out[1:]
        nxt[:, 1:] |= out[:, :-1]
        nxt[:, :-1] |= out[:, 1:]
        nxt &= ~m
        if (nxt == out).all():
            return ~out
        out = nxt


def _blobs(m: np.ndarray, min_px: int = 1) -> List[np.ndarray]:
    """8-connected components of m with at least min_px pixels, as (n, 2) arrays of (y, x)."""
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
        if len(comp) >= min_px:
            out.append(np.array(comp))
    return out


@dataclass
class Frame:
    """What the rules read for one pose: hi-res part masks, their 1x coverage, the ink-only luma at 1x."""
    pose: Pose
    hi: Dict[str, np.ndarray]
    cov: Dict[str, np.ndarray]
    ink: np.ndarray

    def m(self, name: str) -> np.ndarray:
        return self.cov[name] >= 0.5


def part_map(draw: Callable[[cairo.Context], None]) -> Dict[str, np.ndarray]:
    """Hi-res mask per part-map colour of an unantialiased drawing."""
    img = paint(draw, cairo.ANTIALIAS_NONE)
    key = lambda rgb: rgb[..., 0] << 16 | rgb[..., 1] << 8 | rgb[..., 2]
    got = np.where(img[..., 3] > 0.99, key(np.round(img[..., :3] * 255).astype(np.int32)), -1)
    return {k: got == key(np.round(np.array(v) * 255).astype(np.int32)) for k, v in PM.items()}


INK_LOOK = Look({k: (1.0, 1.0, 1.0) for k in CLASS_RGB}, (0.0, 0.0, 0.0))     # white fills, black ink


def frame(p: Pose) -> Frame:
    q = replace(p, fx=[], fx_back=[])
    hi = part_map(lambda c: draw_character(c, q, PART_LOOK))
    ink = np.asarray(render(q, INK_LOOK, rim=False)).astype(float)
    a = ink[..., 3:] / 255
    luma = ((ink[..., :3] * a + 255 * (1 - a)) * (0.299, 0.587, 0.114)).sum(axis=-1)
    return Frame(p, hi, {k: _down(v) for k, v in hi.items()}, luma)


def _face_space_mask(draw: Callable[[cairo.Context], None]) -> np.ndarray:
    """Face space at 2 units per pixel (x -100..1300, y -300..1100), for the hat rule."""
    surf = cairo.ImageSurface(cairo.FORMAT_A8, 700, 700)
    ctx = cairo.Context(surf)
    ctx.scale(0.5, 0.5)
    ctx.translate(100, 300)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    draw(ctx)
    surf.flush()
    return np.ndarray((700, surf.get_stride()), np.uint8, surf.get_data())[:, :700] > 127


def _hat_mask(fn: Optional[Callable[[np.ndarray], np.ndarray]]) -> np.ndarray:
    def draw(ctx: cairo.Context) -> None:
        add_subs(ctx, [(cmds, fn(pts) if fn else pts) for cmds, pts in part("hat").sil])
        ctx.fill()
    return _face_space_mask(draw)


def _head_mask(ctx: cairo.Context) -> None:
    dome(ctx)
    ctx.fill()
    below_brim(ctx)
    ctx.clip()
    add_subs(ctx, part("face").sil)
    ctx.fill()


HEAD_AT_REST: Dict[str, np.ndarray] = {}


def exposed(p: Pose) -> float:
    """Head area (cell px^2) that the posed hat bares beyond 1 cell px from what shows at rest."""
    if not HEAD_AT_REST:
        head, hat = _face_space_mask(_head_mask), _hat_mask(None)
        HEAD_AT_REST.update(head=head, hat=hat, seen=_grow(head & ~hat, 5))
    r = HEAD_AT_REST
    return float((r["head"] & r["hat"] & ~_hat_mask(hat_warp(p)) & ~r["seen"]).sum()) * (2 * S) ** 2


def pot_mask(p: Pose) -> np.ndarray:
    """1x mask of the pot (the props drawn in p.mid, in the part map)."""
    def mid(ctx: cairo.Context) -> None:
        ctx.transform(mul(pose_matrix(p), lean_matrix(p)))
        for fn in p.mid:
            fn(ctx, PART_LOOK)
    return _down(part_map(mid)["pot"]) >= 0.5


def rule_fang(f: Frame) -> Optional[str]:
    # an open mouth keeps its fang and teeth inside the lip line (L15: the panic fang hung above the mouth)
    if f.pose.mouth not in OPEN_MOUTHS:
        return None
    out = (f.m("fang") | f.m("teeth")) & ~_fill_holes(_grow(f.m("mouth"), 3))     # 3 px: lip and fang lines
    return f"{out.sum()} px of fang or teeth outside the open mouth" if out.any() else None


def rule_hat(f: Frame) -> Optional[str]:
    # a lifted or bent-away hat bares the head under the brim, which reads as an empty head (L22)
    if f.pose.hat_dy < 0:
        return f"hat_dy={f.pose.hat_dy:g} lifts the hat off the head"
    area = exposed(f.pose)
    return f"{area:.1f} px^2 of head bared under the brim" if area > EXPOSED_MAX else None


def rule_outline(f: Frame) -> Optional[str]:
    # every edge of the visible face (jowl) and hat carries ink; a front part brings its own (L22: jowl gap)
    inked, bad = _grow(f.ink <= INK_DARK, 2), []
    for name in ("face", "hat"):
        v = _fill_holes(f.m(name))
        bad += [f"{name} at ({int(b[:, 1].mean())}, {int(b[:, 0].mean())})"
                for b in _blobs(v & _grow(~v) & ~inked, 3)]
    return "outline without ink: " + ", ".join(bad) if bad else None


def rule_wave(f: Frame) -> Optional[str]:
    # the waving hand is up above the shoulder, palm out, thumb toward the head (L15: it read upside down)
    p = f.pose
    (px, py), (tx, ty) = (hand_point(p, p.far, True, q) for q in (PALM, THUMB))
    if py >= to_cell(p, *(p.far.root or SH_F))[1]:
        return "waving hand below the shoulder"
    hx, hy = to_cell(p, 650.0, 700.0, head=True)
    if math.hypot(tx - hx, ty - hy) >= math.hypot(px - hx, py - hy):
        return "waving thumb points away from the head"
    return None


def rule_pot(f: Frame) -> Optional[str]:
    # only the hand and the ladle may cross the pot (L15: the robe and the sleeve cut through its rim)
    over_pot = pot_mask(f.pose) & (f.m("sleeve") | f.m("robe"))
    return f"{over_pot.sum()} px of sleeve or robe over the pot" if over_pot.any() else None


def rule_hands(f: Frame) -> Optional[str]:
    # every visible hand hangs from its sleeve (L15: the fork hand floated off a stub sleeve)
    p, held = f.pose, f.m("sleeve")
    if p.near_behind or (p.far is not None and not p.far_front):
        held = held | f.m("face") | f.m("hat") | f.m("robe")    # an arm behind them reaches its hand unseen
    held = _grow(held, 3)                                       # 3 px: the hand's and the sleeve's outlines
    fingers = f.m("hand")
    loose = [b for b in _blobs(_grow(fingers), 4) if not (held & fingers)[b[:, 0], b[:, 1]].any()]
    return (f"hand without a sleeve at ({int(loose[0][:, 1].mean())}, {int(loose[0][:, 0].mean())})"
            if loose else None)


def rule_fx(f: Frame) -> Optional[str]:
    # effect marks are unoutlined grey: over an ink line they erase it (L26: the steam puff hid the face)
    if not f.pose.fx:
        return None
    marks = _down(paint(lambda c: [fn(c) for fn in f.pose.fx])[..., 3]) >= 0.25
    n = int((marks & (f.ink <= INK_DARK)).sum())
    return f"effect marks cover {n} px of ink" if n else None


def prop_length(f: Frame) -> Optional[Tuple[float, bool, Pt, Pt]]:
    """Visible length (cell px) of the held spoon or ladle along its axis, whether its head shows, its far end
    and the axis direction (cell space), from the prop's pixels in the part map."""
    ys, xs = np.nonzero(f.hi["prop"])
    if len(xs) < 4 * SS * SS:
        return None
    pts = np.stack([xs, ys], 1).astype(float) / SS
    c = pts.mean(0)
    u = np.linalg.eigh(np.cov((pts - c).T))[1][:, -1]          # main axis of the prop's pixels
    t = (pts - c) @ u
    hands = np.stack(np.nonzero(f.m("hand"))[::-1], 1) + 0.5
    gap = [np.hypot(*(hands - (c + u * e)).T).min() if len(hands) else 0.0 for e in (t.max(), t.min())]
    if gap[1] > gap[0]:
        u, t = -u, -t                                           # the far end is the one away from the fist
    w = np.abs((pts - c) @ np.array([-u[1], u[0]]))
    head = 2 * w[t > t.max() - 6].max() > 3.5                   # a spoon or ladle head is wider than its handle
    far = c + u * t.max()
    return float(t.max() - t.min()), bool(head), (float(far[0]), float(far[1])), (float(u[0]), float(u[1]))


def rule_props(frames: List[Frame]) -> List[Tuple[int, str]]:
    # spoon and ladle are rigid (L26: the spoon grew and shrank); only lips, stew and the fist may hide them
    got = [(i, prop_length(f)) for i, f in enumerate(frames)]
    got = [(i, g) for i, g in got if g]
    whole = [g[0] for _, g in got if g[1]]
    if not whole:
        return []
    ref, bad = float(np.median(whole)), []
    for i, (length, head, far, u) in got:
        if head:
            if abs(length - ref) > PROP_TOL:
                bad.append((i, f"prop length {length:.1f} px, the row's prop is {ref:.1f} px"))
            continue
        x, y = (int(round(far[k] + u[k] * (ref - length))) for k in (0, 1))   # where the hidden tip is
        pot = pot_mask(frames[i].pose)
        in_stew = _grow(pot, 2)[int(far[1]), int(far[0])]
        holder = pot if in_stew else _fill_holes(frames[i].m("face"))
        if length > ref + PROP_TOL or not (0 <= y < CELL_H and 0 <= x < CELL_W and holder[y, x]):
            bad.append((i, f"prop shows {length:.1f} of {ref:.1f} px and its hidden tip is not in the "
                           f"{'pot' if in_stew else 'mouth'}"))
    return bad


PART_LOOK = part_look()
RULES = [rule_fang, rule_hat, rule_outline, rule_pot, rule_hands, rule_fx]


def check_frames(rows: List[List[Pose]]) -> None:
    """Run every rule on every distinct frame; raise SystemExit naming row, frame and rule on any violation."""
    errors = []
    for (name, _), poses in zip(ROW_SPEC, rows):
        if name == "running-left":
            continue                    # the mirrored running-right frames
        frames = [frame(p) for p in poses]
        for c, f in enumerate(frames):
            for rule in RULES + ([rule_wave] if name == "waving" else []):
                msg = rule(f)
                if msg:
                    errors.append(f"{name} frame {c}: {rule.__name__}: {msg}")
        errors += [f"{name} frame {c}: rule_props: {msg}" for c, msg in rule_props(frames)]
    if errors:
        raise SystemExit("frame checks failed:\n  " + "\n  ".join(errors))


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = row_poses()
    check_frames(rows)
    atlas = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))
    for r, frames in enumerate(build_frames(rows)):
        for c, im in enumerate(frames):
            atlas.paste(im, (c * CELL_W, r * CELL_H))
    out = os.path.join(OUT_DIR, "spritesheet.webp")
    atlas.save(out, "WEBP", lossless=True, quality=100, method=6, exact=True)
    shutil.copy2(out, os.path.join(HERE, "spritesheet.webp"))
    print("wrote", out, "and copied it next to pet.json")


if __name__ == "__main__":
    main()
