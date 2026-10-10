"""Explainer design system for Manim Community Edition v0.19.2.

Generated lesson scenes start with::

    from manim import *
    from visual_helpers import *

    class ExplainerVideo(ExplainerScene):
        def construct(self):
            with self.scene_block(index=0, concept="...", caption="...", duration=7.5):
                ...

This module provides

* a palette, typography and layout constants (16:9 frame with a reserved header
  band at the top and a caption band at the bottom),
* `ExplainerScene`, which draws the header/captions, enforces the storyboard
  timing (every `scene_block` lasts exactly its planned duration, whatever the
  code inside it does), keeps content out of the header/caption bands, and
  emits machine-readable timing lines for the production pipeline,
* forgiving high-level builders (labels, equations, chips/token rows, array
  cells, pointers, weighted arrows, graphs, axes/plots, bar charts, callouts)
  and animation shorthands.

It runs inside the render sandbox only; nothing here touches the network or
files outside Manim's media directory, except reading the storyboard manifest
named by $EXPLAINER_STORYBOARD (written by the pipeline).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import random
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from manim import *  # noqa: F401,F403 - the generated scene imports manim itself; see __all__
from manim import config
from manim.animation.animation import prepare_animation
from manim.animation.transform_matching_parts import TransformMatchingAbstractBase
from manim.mobject.mobject import _AnimationBuilder

# --------------------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------------------
# These intentionally shadow Manim's BLUE/PURPLE/YELLOW/GREEN/RED/ORANGE/PINK so that any
# code using those names gets the house palette (the scene imports this module after manim).

BG = ManimColor("#0B1020")  # near-black navy background
SURFACE = ManimColor("#141B30")  # panels / cards
SURFACE_2 = ManimColor("#1C2541")  # raised panels
GRID = ManimColor("#27314F")  # grid lines, faint structure
INK = ManimColor("#EEF2FF")  # primary text / strokes
MUTED = ManimColor("#9AA5C4")  # secondary text, axes
DIM = ManimColor("#5A6685")  # de-emphasised elements

BLUE = ManimColor("#5B9DFF")
PURPLE = ManimColor("#A78BFA")
CYAN = ManimColor("#2DD4E6")
YELLOW = ManimColor("#FFD166")
GREEN = ManimColor("#5EE3A1")
RED = ManimColor("#FF6B7F")
ORANGE = ManimColor("#FF9F5A")
PINK = ManimColor("#F67BC0")

GOOD = GREEN  # correct / positive / kept
BAD = RED  # wrong / negative / discarded
HIGHLIGHT = YELLOW  # "look here"
ACCENTS = [BLUE, PURPLE, CYAN, YELLOW, GREEN, ORANGE, PINK, RED]

# --------------------------------------------------------------------------------------
# Typography
# --------------------------------------------------------------------------------------

FONT = "Inter"
SIZE_TITLE = 48  # big statement in the content area
SIZE_HEADING = 36
SIZE_BODY = 30
SIZE_LABEL = 26  # default for labels, chips, cells
SIZE_SMALL = 22
SIZE_TINY = 18
SIZE_MATH = 44  # MathTex font size
CAPTION_SIZE = 25

# --------------------------------------------------------------------------------------
# Layout (Manim units; the 16:9 frame is 14.22 x 8, origin at the centre)
# --------------------------------------------------------------------------------------

FRAME_W = float(config.frame_width)
FRAME_H = float(config.frame_height)
FRAME_LEFT, FRAME_RIGHT = -FRAME_W / 2, FRAME_W / 2
FRAME_BOTTOM, FRAME_TOP = -FRAME_H / 2, FRAME_H / 2

SIDE_MARGIN = 0.6
CAPTION_BAND_TOP = -2.75  # nothing but the caption may go below this line
HEADER_BAND_BOTTOM = 2.95  # nothing but the header may go above this line
CAPTION_CENTER_Y = -3.28
CAPTION_MAX_WIDTH = 11.0

INTRO_TIME = 0.6  # caption/header swap at the start of each block (overlaps your first animation)
FADE_OUT_TIME = 0.5  # optional end-of-block fade (scene_block(..., fade_out=True))


@dataclass(frozen=True)
class Area:
    """An axis-aligned rectangle of the frame, in Manim units."""

    left: float
    right: float
    bottom: float
    top: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    @property
    def center(self) -> np.ndarray:
        return np.array([(self.left + self.right) / 2, (self.bottom + self.top) / 2, 0.0])

    def get_center(self) -> np.ndarray:
        return self.center

    @property
    def top_center(self) -> np.ndarray:
        return np.array([self.center[0], self.top, 0.0])

    @property
    def bottom_center(self) -> np.ndarray:
        return np.array([self.center[0], self.bottom, 0.0])

    @property
    def left_center(self) -> np.ndarray:
        return np.array([self.left, self.center[1], 0.0])

    @property
    def right_center(self) -> np.ndarray:
        return np.array([self.right, self.center[1], 0.0])

    def point(self, x: float = 0.5, y: float = 0.5) -> np.ndarray:
        """Point at fractional position (x, y) in [0, 1] from the bottom-left corner."""
        return np.array([self.left + x * self.width, self.bottom + y * self.height, 0.0])

    def inset(self, dx: float, dy: float | None = None) -> "Area":
        dy = dx if dy is None else dy
        return Area(self.left + dx, self.right - dx, self.bottom + dy, self.top - dy)

    def columns(self, n: int = 2, gap: float = 0.5, ratios: Sequence[float] | None = None) -> list["Area"]:
        """Split into n side-by-side areas (optionally with relative widths)."""
        n = max(1, int(n))
        ratios = list(ratios) if ratios else [1.0] * n
        total = sum(ratios)
        usable = self.width - gap * (len(ratios) - 1)
        out, x = [], self.left
        for r in ratios:
            w = usable * r / total
            out.append(Area(x, x + w, self.bottom, self.top))
            x += w + gap
        return out

    def rows(self, n: int = 2, gap: float = 0.3, ratios: Sequence[float] | None = None) -> list["Area"]:
        """Split into n stacked areas, top first (optionally with relative heights)."""
        n = max(1, int(n))
        ratios = list(ratios) if ratios else [1.0] * n
        total = sum(ratios)
        usable = self.height - gap * (len(ratios) - 1)
        out, y = [], self.top
        for r in ratios:
            h = usable * r / total
            out.append(Area(self.left, self.right, y - h, y))
            y -= h + gap
        return out

    def contains(self, mob: Mobject, tol: float = 0.02) -> bool:
        b = _bounds(mob)
        if b is None:
            return True
        l, r, bo, t = b
        return l >= self.left - tol and r <= self.right + tol and bo >= self.bottom - tol and t <= self.top + tol


CONTENT_AREA = Area(
    FRAME_LEFT + SIDE_MARGIN, FRAME_RIGHT - SIDE_MARGIN, CAPTION_BAND_TOP + 0.2, HEADER_BAND_BOTTOM - 0.15
)

# --------------------------------------------------------------------------------------
# Global defaults
# --------------------------------------------------------------------------------------

config.background_color = BG
Text.set_default(font=FONT, color=INK)
MarkupText.set_default(font=FONT, color=INK)
MathTex.set_default(color=INK)
Tex.set_default(color=INK)

_TEXT_OVERSAMPLE = 4.0


def _install_text_oversampling(cls: type) -> None:
    """Render Pango text at 4x and scale down: at small sizes Pango rounds glyph advances,
    which makes word spacing visibly uneven ("mi ddle"). `font_size` stays correct because
    Manim derives it from the current scale."""
    if getattr(cls, "_explainer_oversampled", False):
        return
    original = cls.__init__

    def __init__(self: Any, text: Any = "", *args: Any, **kwargs: Any) -> None:
        if kwargs.get("width") is not None or kwargs.get("height") is not None:
            original(self, text, *args, **kwargs)
            return
        fs = float(kwargs.pop("font_size", DEFAULT_FONT_SIZE) or DEFAULT_FONT_SIZE)
        original(self, text, *args, font_size=fs * _TEXT_OVERSAMPLE, **kwargs)
        self.scale(1.0 / _TEXT_OVERSAMPLE)

    cls.__init__ = __init__
    cls._explainer_oversampled = True


_install_text_oversampling(Text)
# MarkupText is deliberately NOT oversampled: Manim lays it out with a fixed Pango wrap
# width, so a 4x font would wrap lines 4x sooner. The design system never uses it.


def _emit(tag: str, payload: dict[str, Any]) -> None:
    """One machine-readable line on stdout for the production pipeline."""
    try:
        line = json.dumps(payload, separators=(",", ":"), default=str)
    except Exception:  # pragma: no cover - defensive
        line = json.dumps({"message": str(payload)})
    print(f"{tag} {line}", flush=True)


def _warn(message: str, **extra: Any) -> None:
    _emit("EXPLAINER_WARNING", {"message": message, **extra})


# --------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------


def _col(c: Any, default: Any = INK) -> ManimColor:
    """Parse a colour forgivingly (ManimColor, hex string, or a name like 'red')."""
    if c is None:
        return ManimColor(default)
    if isinstance(c, ManimColor):
        return c
    try:
        return ManimColor(c)
    except Exception:
        pass
    try:
        name = str(c).strip().upper()
        value = globals().get(name)
        if isinstance(value, ManimColor):
            return value
        return ManimColor(str(c).strip().lower())
    except Exception:
        return ManimColor(default)


def _has_points(mob: Mobject | None) -> bool:
    if mob is None:
        return False
    try:
        return any(len(m.points) > 0 for m in mob.get_family())
    except Exception:
        return False


def _bounds(mob: Mobject | None) -> tuple[float, float, float, float] | None:
    if not _has_points(mob):
        return None
    return (
        float(mob.get_left()[0]),
        float(mob.get_right()[0]),
        float(mob.get_bottom()[1]),
        float(mob.get_top()[1]),
    )


def _union_bounds(mobs: Iterable[Mobject]) -> tuple[float, float, float, float] | None:
    boxes = [b for b in (_bounds(m) for m in mobs) if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes))


def _as_point(x: Any) -> np.ndarray:
    if isinstance(x, Mobject):
        return x.get_center()
    arr = np.array(x, dtype=float).flatten()
    if arr.size == 2:
        arr = np.array([arr[0], arr[1], 0.0])
    return arr[:3]


def _flatten_mobjects(items: Iterable[Any]) -> list[Mobject]:
    out: list[Mobject] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, Mobject):
            out.append(item)
        elif isinstance(item, (list, tuple, set)):
            out.extend(_flatten_mobjects(item))
    return out


def _fmt_number(v: float, decimals: int | None = None) -> str:
    if decimals is not None:
        return f"{v:.{decimals}f}"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:g}"


def _text_str(value: Any) -> str:
    s = "" if value is None else str(value)
    return s.replace("\t", " ").strip()


# --------------------------------------------------------------------------------------
# Layout helpers
# --------------------------------------------------------------------------------------


def fit_in(
    mob: Mobject,
    area: Area | None = None,
    *,
    buff: float = 0.0,
    scale_up: bool = False,
    max_scale: float = 1.6,
    align: Any = None,
    keep_position: bool = False,
) -> Mobject:
    """Scale `mob` down (or up, with scale_up=True) to fit `area`, then position it.

    align: None = centre of the area; or a direction such as UP, DOWN, LEFT, UL to hug
    that side. keep_position=True only nudges the mobject inside the area.
    """
    area = area or CONTENT_AREA
    inner = area.inset(buff) if buff else area
    b = _bounds(mob)
    if b is None:
        return mob
    w, h = b[1] - b[0], b[3] - b[2]
    factors = []
    if w > 1e-6:
        factors.append(inner.width / w)
    if h > 1e-6:
        factors.append(inner.height / h)
    if factors:
        s = min(factors)
        if s < 1.0:
            mob.scale(s * 0.999)
        elif scale_up and s > 1.0:
            mob.scale(min(s, max_scale) * 0.999)
    if keep_position:
        return keep_inside(mob, inner)
    mob.move_to(inner.center)
    if align is not None:
        d = np.array(align, dtype=float)
        if d[0] < 0:
            mob.align_to(np.array([inner.left, 0, 0]), LEFT)
        elif d[0] > 0:
            mob.align_to(np.array([inner.right, 0, 0]), RIGHT)
        if d[1] > 0:
            mob.align_to(np.array([0, inner.top, 0]), UP)
        elif d[1] < 0:
            mob.align_to(np.array([0, inner.bottom, 0]), DOWN)
    return mob


def keep_inside(mob: Mobject, area: Area | None = None) -> Mobject:
    """Shift (and if needed shrink) `mob` so it lies inside `area` (default: content area)."""
    area = area or CONTENT_AREA
    b = _bounds(mob)
    if b is None:
        return mob
    w, h = b[1] - b[0], b[3] - b[2]
    s = 1.0
    if w > area.width:
        s = min(s, area.width / w)
    if h > area.height:
        s = min(s, area.height / h)
    if s < 1.0:
        mob.scale(s * 0.999)
        b = _bounds(mob)
    l, r, bo, t = b
    dx = area.left - l if l < area.left else (area.right - r if r > area.right else 0.0)
    dy = area.bottom - bo if bo < area.bottom else (area.top - t if t > area.top else 0.0)
    if dx or dy:
        mob.shift(np.array([dx, dy, 0.0]))
    return mob


def place_near(mob: Mobject, target: Any, direction: Any = UP, *, buff: float = 0.25, area: Area | None = None) -> Mobject:
    """`mob.next_to(target, direction, buff)`, then nudged inside the content area."""
    if isinstance(target, Mobject):
        mob.next_to(target, direction, buff=buff)
    else:
        mob.next_to(_as_point(target), direction, buff=buff)
    return keep_inside(mob, area)


def arrange_in_grid_fit(mobs: Sequence[Mobject], rows: int | None = None, cols: int | None = None, *, buff: float = 0.4, area: Area | None = None) -> VGroup:
    """Arrange mobjects in a grid and fit the result inside `area`."""
    g = VGroup(*mobs)
    g.arrange_in_grid(rows=rows, cols=cols, buff=buff)
    return fit_in(g, area)


# --------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------


def make_label(
    text: Any,
    *,
    size: float = SIZE_LABEL,
    color: Any = INK,
    weight: str = NORMAL,
    max_width: float | None = None,
    highlight: dict[str, Any] | None = None,
    slant: str = NORMAL,
    font_size: float | None = None,
) -> Text:
    """Plain text (Inter). `highlight` colours substrings, e.g. {"bank": YELLOW}.
    (`font_size=` is accepted as an alias for `size=`.)"""
    size = font_size or size
    s = _text_str(text)
    kwargs: dict[str, Any] = {"font": FONT, "font_size": size, "color": _col(color), "weight": weight, "slant": slant}
    if highlight:
        kwargs["t2c"] = {str(k): _col(v) for k, v in highlight.items() if str(k)}
    t = Text(s, **kwargs)
    if max_width and t.width > max_width:
        t.scale_to_fit_width(max_width)
    return t


def make_heading(text: Any, *, size: float = SIZE_HEADING, color: Any = INK, max_width: float | None = None,
                 font_size: float | None = None) -> Text:
    """A bold-ish heading for the content area (the persistent header is drawn for you)."""
    size = font_size or size
    return make_label(text, size=size, color=color, weight=SEMIBOLD, max_width=max_width or CONTENT_AREA.width)


def make_title(text: Any, *, size: float = SIZE_TITLE, color: Any = INK, max_width: float | None = None,
               font_size: float | None = None) -> Text:
    size = font_size or size
    return make_label(text, size=size, color=color, weight=BOLD, max_width=max_width or CONTENT_AREA.width)


def _measure_avg_char(text: str, size: float, weight: str) -> tuple[float, float]:
    t = Text(text, font=FONT, font_size=size, weight=weight)
    return t.width, t.width / max(len(text), 1)


def _wrap_lines(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _is_operator(word: str) -> bool:
    return len(word) <= 2 and not any(ch.isalnum() for ch in word)


def _balanced_two_lines(text: str) -> list[str]:
    words = text.split()
    if len(words) < 2:
        return [text]
    best, best_score = None, None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        score = max(len(a), len(b)) + (0.5 if len(a) < len(b) else 0)  # prefer the longer line on top
        # Never strand an operator or split "b = 4" across lines.
        if _is_operator(words[k - 1]) or _is_operator(words[k]):
            score += 12
        if k >= 2 and _is_operator(words[k - 2]):
            score += 6
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best or [text]


def make_paragraph(
    text: Any,
    *,
    max_width: float = 6.0,
    size: float = SIZE_BODY,
    color: Any = INK,
    align: str = "left",
    weight: str = NORMAL,
    line_spacing: float = -1,
    font_size: float | None = None,
) -> VMobject:
    """Multi-line text wrapped to `max_width` units. align: 'left' | 'center' | 'right'."""
    size = font_size or size
    s = " ".join(_text_str(text).split())
    if not s:
        return VGroup()
    full_w, avg = _measure_avg_char(s, size, weight)
    if full_w <= max_width:
        return make_label(s, size=size, color=color, weight=weight)
    max_chars = max(6, int(max_width / max(avg, 1e-6)))
    lines = _wrap_lines(s, max_chars)
    p = Paragraph(
        *lines,
        alignment=align if align in ("left", "center", "right") else "left",
        font=FONT,
        font_size=size,
        color=_col(color),
        weight=weight,
        line_spacing=line_spacing,
        disable_ligatures=True,
    )
    if p.width > max_width:
        p.scale_to_fit_width(max_width)
    return p


def make_bullets(
    items: Sequence[Any],
    *,
    size: float = SIZE_BODY,
    color: Any = INK,
    bullet_color: Any = BLUE,
    max_width: float = 7.0,
    buff: float = 0.32,
) -> VGroup:
    """A left-aligned bullet list; each row is VGroup(dot, text)."""
    rows = VGroup()
    for item in items:
        txt = make_paragraph(item, max_width=max_width - 0.5, size=size, color=color)
        dot = Dot(radius=0.06, color=_col(bullet_color))
        row = VGroup(dot, txt)
        txt.next_to(dot, RIGHT, buff=0.22)
        dot.align_to(txt, UP).shift(DOWN * min(0.14, txt.height / 2 - 0.02))
        rows.add(row)
    rows.arrange(DOWN, aligned_edge=LEFT, buff=buff)
    return rows


# --------------------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------------------

_LATEX_TO_UNICODE = {
    r"\cdot": "·", r"\times": "×", r"\div": "÷", r"\pm": "±", r"\leq": "≤", r"\le": "≤", r"\geq": "≥",
    r"\ge": "≥", r"\neq": "≠", r"\approx": "≈", r"\sim": "~", r"\infty": "∞", r"\to": "→", r"\rightarrow": "→",
    r"\leftarrow": "←", r"\Rightarrow": "⇒", r"\sum": "Σ", r"\prod": "Π", r"\int": "∫", r"\sqrt": "√",
    r"\partial": "∂", r"\nabla": "∇", r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\Delta": "Δ", r"\epsilon": "ε", r"\varepsilon": "ε", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
    r"\pi": "π", r"\sigma": "σ", r"\Sigma": "Σ", r"\phi": "φ", r"\omega": "ω", r"\eta": "η", r"\rho": "ρ",
    r"\tau": "τ", r"\in": "∈", r"\log": "log", r"\ln": "ln", r"\sin": "sin", r"\cos": "cos", r"\tan": "tan",
    r"\exp": "exp", r"\max": "max", r"\min": "min", r"\quad": "  ", r"\,": " ", r"\;": " ", r"\!": "",
    r"\left": "", r"\right": "", r"\langle": "⟨", r"\rangle": "⟩",
}
_SUPERSCRIPTS = str.maketrans("0123456789+-=()niT", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵀ")
_SUBSCRIPTS = str.maketrans("0123456789+-=()ijknx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ᵢⱼₖₙₓ")


def _latex_to_text(tex: str) -> str:
    """Best-effort readable fallback when LaTeX fails to compile."""
    import re

    s = tex
    s = re.sub(r"\\(?:text|mathrm|mathbf|operatorname|mathit)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    for k in sorted(_LATEX_TO_UNICODE, key=len, reverse=True):
        s = s.replace(k, _LATEX_TO_UNICODE[k])
    s = re.sub(r"\^\{([^{}]*)\}", lambda m: m.group(1).translate(_SUPERSCRIPTS), s)
    s = re.sub(r"\^(\w)", lambda m: m.group(1).translate(_SUPERSCRIPTS), s)
    s = re.sub(r"_\{([^{}]*)\}", lambda m: m.group(1).translate(_SUBSCRIPTS), s)
    s = re.sub(r"_(\w)", lambda m: m.group(1).translate(_SUBSCRIPTS), s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(s.split())


def make_equation(*parts: Any, size: float = SIZE_MATH, color: Any = INK, colors: dict[Any, Any] | None = None,
                  font_size: float | None = None) -> VMobject:
    """MathTex from one or more LaTeX parts (math mode, no $ delimiters).

    Pass separate parts to colour or animate pieces individually:
        eq = make_equation("a^2", "+", "b^2", "=", "c^2", colors={"a^2": BLUE, 2: GREEN})
        eq[0] is "a^2", eq[2] is "b^2" ...
    Each part must be valid LaTeX on its own (balanced braces). If LaTeX fails to
    compile, a readable plain-text version is returned instead of crashing the render.
    """
    size = font_size or size
    strs = [str(p) for p in parts if p is not None and str(p).strip()]
    if not strs:
        strs = ["?"]
    eq: VMobject
    try:
        eq = MathTex(*strs, font_size=size, color=_col(color))
    except Exception as exc:  # LaTeX failed: degrade gracefully
        try:
            if len(strs) == 1:
                raise
            eq = MathTex(" ".join(strs), font_size=size, color=_col(color))
            _warn("make_equation: parts did not compile separately; rendered as one piece", tex=" ".join(strs))
        except Exception:
            _warn("make_equation: LaTeX failed, using plain text", tex=" ".join(strs), error=str(exc)[:200])
            eq = make_label(_latex_to_text(" ".join(strs)), size=size * 0.8, color=color)
            return eq
    if colors:
        for key, c in colors.items():
            try:
                if isinstance(key, int):
                    if -len(eq) <= key < len(eq):
                        eq[key].set_color(_col(c))
                else:
                    for i, p in enumerate(strs):
                        if p.strip() == str(key).strip() and i < len(eq):
                            eq[i].set_color(_col(c))
            except Exception:
                pass
    return eq


def make_number(value: float, *, decimals: int | None = None, size: float = SIZE_LABEL, color: Any = INK,
                font_size: float | None = None) -> DecimalNumber:
    """A number that can be animated with `count_to(number, new_value)` (ChangeDecimalToValue)."""
    size = font_size or size
    v = float(value)
    if decimals is None:
        s = f"{v:g}"
        decimals = len(s.split(".")[1]) if "." in s and "e" not in s else 0
        decimals = min(decimals, 3)
    return DecimalNumber(v, num_decimal_places=decimals, font_size=size, color=_col(color), mob_class=Text)


# --------------------------------------------------------------------------------------
# Shapes: boxes, chips (token boxes), rows, array cells, pointers
# --------------------------------------------------------------------------------------


def make_box(
    width: float = 2.0,
    height: float = 1.0,
    *,
    color: Any = BLUE,
    fill_opacity: float = 0.14,
    stroke_width: float = 3,
    corner_radius: float = 0.14,
) -> RoundedRectangle:
    """A rounded rectangle with a translucent fill of its own colour."""
    c = _col(color)
    r = min(corner_radius, width / 2 - 1e-3, height / 2 - 1e-3)
    return RoundedRectangle(
        width=width, height=height, corner_radius=max(r, 0.0), stroke_color=c, stroke_width=stroke_width,
        fill_color=c, fill_opacity=fill_opacity,
    )


def make_panel(width: float = 4.0, height: float = 3.0, *, corner_radius: float = 0.2) -> RoundedRectangle:
    """A subtle dark card to group related content."""
    return RoundedRectangle(
        width=width, height=height, corner_radius=corner_radius, stroke_color=GRID, stroke_width=2,
        fill_color=SURFACE, fill_opacity=0.9,
    )


class Chip(VGroup):
    """A box with a centred label (token, tag, array cell). Parts: .box, .label (and .index for cells)."""

    def __init__(
        self,
        text: Any = "",
        *,
        color: Any = BLUE,
        size: float = SIZE_LABEL,
        width: float | None = None,
        height: float | None = None,
        min_width: float = 0.8,
        padding: float = 0.28,
        fill_opacity: float = 0.16,
        text_color: Any = INK,
        corner_radius: float = 0.12,
        weight: str = MEDIUM,
    ) -> None:
        super().__init__()
        self.color_value = _col(color)
        self.label_size = size
        self.text_color = _col(text_color)
        self.label_weight = weight
        s = _text_str(text)
        label = make_label(s, size=size, color=text_color, weight=weight) if s else VMobject()
        lh = Text("Hg", font=FONT, font_size=size).height
        h = height if height is not None else lh + 2 * padding * 0.75
        w = width if width is not None else max(min_width, label.width + 2 * padding)
        self.label_natural_width = label.width if s else 0.0
        if s and label.width > w - 0.12:
            label.scale_to_fit_width(max(w - 0.16, 0.1))
        box = make_box(w, h, color=self.color_value, fill_opacity=fill_opacity, corner_radius=corner_radius)
        label.move_to(box.get_center())
        self.box = box
        self.label = label
        self.add(box, label)

    @property
    def text(self) -> str:
        return getattr(self.label, "text", "")


def make_chip(text: Any = "", *, color: Any = BLUE, size: float = SIZE_LABEL, width: float | None = None,
              height: float | None = None, fill_opacity: float = 0.16, text_color: Any = INK,
              font_size: float | None = None) -> Chip:
    """A token / tag box with text. Animate with stagger_in, tint, pulse, set_chip_text."""
    size = font_size or size
    return Chip(text, color=color, size=size, width=width, height=height, fill_opacity=fill_opacity, text_color=text_color)


class ItemRow(VGroup):
    """A horizontal row of chips/cells. row[i] is the i-th item; see highlight/dim/set_value."""

    def item(self, i: int) -> Chip:
        return self.submobjects[i]

    @property
    def items(self) -> list[Chip]:
        return list(self.submobjects)

    def highlight(self, i: int | Sequence[int], color: Any = YELLOW) -> Animation:
        idx = [i] if isinstance(i, int) else list(i)
        return AnimationGroup(*[tint(self.submobjects[k], color) for k in idx])

    def dim_items(self, *indices: int, amount: float = 0.7) -> Animation:
        """Dim items by index (all if none given). (Mobject.dim is taken by Manim.)"""
        idx = list(indices) if indices else list(range(len(self.submobjects)))
        return dim(*[self.submobjects[k] for k in idx], amount=amount)

    def undim_items(self, *indices: int) -> Animation:
        idx = list(indices) if indices else list(range(len(self.submobjects)))
        return undim(*[self.submobjects[k] for k in idx])

    def set_value(self, i: int, value: Any, color: Any = None) -> Animation:
        return set_chip_text(self.submobjects[i], value, color=color)


class TokenRow(ItemRow):
    @property
    def tokens(self) -> list[Chip]:
        return self.items


class ArrayRow(ItemRow):
    @property
    def cells(self) -> list[Chip]:
        return self.items


def make_token_row(
    words: Sequence[Any],
    *,
    colors: Sequence[Any] | Any = None,
    size: float = SIZE_LABEL,
    buff: float = 0.22,
    height: float | None = None,
    equal_width: bool = False,
    max_width: float | None = None,
    font_size: float | None = None,
) -> TokenRow:
    """A row of token boxes, e.g. make_token_row(["the", "river", "bank"]). row[i] is a Chip."""
    size = font_size or size
    words = list(words)
    if colors is None:
        cols = [BLUE] * len(words)
    elif isinstance(colors, (list, tuple)):
        cols = [colors[i % len(colors)] for i in range(len(words))]
    else:
        cols = [colors] * len(words)
    chips = [Chip(w, color=c, size=size, height=height) for w, c in zip(words, cols)]
    if equal_width and chips:
        w = max(c.box.width for c in chips)
        chips = [Chip(wd, color=c, size=size, width=w, height=height) for wd, c in zip(words, cols)]
    row = TokenRow(*chips)
    row.arrange(RIGHT, buff=buff)
    limit = max_width or CONTENT_AREA.width
    if row.width > limit:
        row.scale_to_fit_width(limit)
    return row


def make_array(
    values: Sequence[Any],
    *,
    cell_size: float = 0.82,
    colors: Sequence[Any] | Any = None,
    size: float = SIZE_LABEL,
    show_indices: bool = True,
    index_start: int = 0,
    buff: float = 0.0,
    max_width: float | None = None,
    font_size: float | None = None,
) -> ArrayRow:
    """A row of square cells holding values (use "" for empty cells). arr[i] is a Chip with
    .box, .label and .index (the small index number under the cell, if show_indices)."""
    size = font_size or size
    values = list(values)
    if colors is None:
        cols = [BLUE] * len(values)
    elif isinstance(colors, (list, tuple)):
        cols = [colors[i % len(colors)] for i in range(len(values))]
    else:
        cols = [colors] * len(values)
    cells = []
    for i, (v, c) in enumerate(zip(values, cols)):
        cell = Chip(v, color=c, size=size, width=cell_size, height=cell_size, corner_radius=0.06, fill_opacity=0.12)
        if show_indices:
            idx = make_label(str(i + index_start), size=SIZE_TINY, color=MUTED)
            idx.next_to(cell.box, DOWN, buff=0.14)
            cell.index = idx
            cell.add(idx)
        else:
            cell.index = VMobject()
        cells.append(cell)
    # One label scale for every cell, so a long value doesn't make its cell look different.
    scales = [c.label.width / c.label_natural_width for c in cells
              if _has_points(c.label) and getattr(c, "label_natural_width", 0) > 0]
    if scales and min(scales) < 0.999:
        k = min(scales)
        for c in cells:
            if _has_points(c.label) and getattr(c, "label_natural_width", 0) > 0:
                c.label.scale(k * c.label_natural_width / c.label.width).move_to(c.box.get_center())
        for c in cells:
            c.label_size = c.label_size * k
    arr = ArrayRow(*cells)
    # Arrange by boxes so index labels don't affect spacing.
    for i in range(1, len(cells)):
        cells[i].shift(cells[i - 1].box.get_right() - cells[i].box.get_left() + RIGHT * buff)
    arr.move_to(ORIGIN)
    limit = max_width or CONTENT_AREA.width
    if arr.width > limit:
        arr.scale_to_fit_width(limit)
    return arr


def make_grid(
    values: Sequence[Sequence[Any]],
    *,
    cell_size: float = 0.7,
    colors: Any = None,
    size: float = SIZE_SMALL,
    show_values: bool = True,
    row_labels: Sequence[Any] | None = None,
    col_labels: Sequence[Any] | None = None,
    heat: bool = False,
    heat_color: Any = BLUE,
) -> VGroup:
    """A 2-D grid of cells (table / matrix / heatmap). grid.cells[r][c] is a Chip.
    heat=True shades each cell by its numeric value (0..max)."""
    rows = [list(r) for r in values]
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    nums = []
    for r in rows:
        for v in r:
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                pass
    vmax = max([abs(x) for x in nums] or [1.0]) or 1.0
    grid = VGroup()
    cells: list[list[Chip]] = []
    for ri in range(n_rows):
        row_cells = []
        for ci in range(n_cols):
            v = rows[ri][ci] if ci < len(rows[ri]) else ""
            if isinstance(colors, (list, tuple)) and colors and isinstance(colors[0], (list, tuple)):
                c = colors[ri][ci % len(colors[ri])] if ri < len(colors) and colors[ri] else BLUE
            elif isinstance(colors, (list, tuple)) and colors:
                c = colors[(ri * n_cols + ci) % len(colors)]
            else:
                c = colors if colors is not None else BLUE
            fill = 0.12
            if heat:
                try:
                    fill = 0.08 + 0.8 * abs(float(v)) / vmax
                    c = heat_color
                except (TypeError, ValueError):
                    pass
            label = _fmt_number(float(v)) if isinstance(v, (int, float)) else _text_str(v)
            chip = Chip(label if show_values else "", color=c, size=size, width=cell_size, height=cell_size,
                        corner_radius=0.05, fill_opacity=fill)
            chip.move_to(np.array([ci * cell_size, -ri * cell_size, 0.0]))
            row_cells.append(chip)
            grid.add(chip)
        cells.append(row_cells)
    grid.cells = cells
    if row_labels:
        rl = VGroup(*[make_label(t, size=size, color=MUTED) for t in row_labels])
        for ri, lab in enumerate(rl):
            if ri < n_rows:
                lab.next_to(cells[ri][0].box, LEFT, buff=0.2)
        grid.add(rl)
        grid.row_labels = rl
    if col_labels:
        cl = VGroup(*[make_label(t, size=size, color=MUTED) for t in col_labels])
        for ci, lab in enumerate(cl):
            if n_rows and ci < n_cols:
                lab.next_to(cells[0][ci].box, UP, buff=0.18)
        grid.add(cl)
        grid.col_labels = cl
    grid.move_to(ORIGIN)
    return grid


class Pointer(VGroup):
    """An arrow (+ optional label) pointing at a target from one side. Parts: .arrow, .label."""

    def __init__(self, target: Any, label: Any = "", *, direction: Any = DOWN, color: Any = YELLOW,
                 length: float = 0.55, size: float = SIZE_SMALL, buff: float = 0.12) -> None:
        super().__init__()
        self.direction = np.array(direction, dtype=float)
        n = np.linalg.norm(self.direction)
        self.direction = self.direction / n if n > 0 else DOWN
        self.length = length
        self.buff = buff
        c = _col(color)
        tip = self._tip_point(target)
        start = tip + self.direction * length
        self.arrow = Arrow(start, tip, buff=0, stroke_width=5, color=c, tip_length=0.16,
                           max_tip_length_to_length_ratio=0.5, max_stroke_width_to_length_ratio=100)
        self.add(self.arrow)
        s = _text_str(label)
        self.label = make_label(s, size=size, color=c, weight=SEMIBOLD) if s else VMobject()
        if s:
            self.label.next_to(start, self.direction, buff=0.08)
            self.add(self.label)

    def _tip_point(self, target: Any) -> np.ndarray:
        if isinstance(target, Mobject):
            box = target.box if isinstance(target, Chip) else target
            edge = target.get_critical_point(self.direction)
            center = box.get_center()
            # Use the edge along the pointer axis, centred on the target.
            if abs(self.direction[0]) < 1e-6:
                p = np.array([center[0], edge[1], 0.0])
            elif abs(self.direction[1]) < 1e-6:
                p = np.array([edge[0], center[1], 0.0])
            else:
                p = edge
        else:
            p = _as_point(target)
        return p + self.direction * self.buff

    def target_shift(self, target: Any) -> np.ndarray:
        return self._tip_point(target) - self.arrow.get_end()


def make_pointer(target: Any, label: Any = "", *, direction: Any = DOWN, color: Any = YELLOW,
                 length: float = 0.55, size: float = SIZE_SMALL) -> Pointer:
    """An arrow pointing at `target` from `direction` (DOWN = sits below it pointing up)."""
    return Pointer(target, label, direction=direction, color=color, length=length, size=size)


def move_pointer(pointer: Pointer, target: Any) -> Animation:
    """Animation sliding a pointer to point at a new target."""
    return pointer.animate.shift(pointer.target_shift(target))


# --------------------------------------------------------------------------------------
# Arrows, weighted edges, nodes and graphs
# --------------------------------------------------------------------------------------


def _edge_point(mob: Mobject, direction: np.ndarray) -> np.ndarray:
    """Where a ray from the mobject's centre in `direction` leaves its outline."""
    center = mob.get_center()
    shape = getattr(mob, "circle", None) or getattr(mob, "box", None) or mob
    if isinstance(shape, (Circle, Dot)):
        return shape.get_center() + direction * (shape.width / 2)
    hw, hh = max(shape.width / 2, 1e-3), max(shape.height / 2, 1e-3)
    dx, dy = abs(direction[0]), abs(direction[1])
    t = min(hw / dx if dx > 1e-9 else np.inf, hh / dy if dy > 1e-9 else np.inf)
    if not np.isfinite(t):
        t = 0.0
    return center + direction * t


def _endpoints(start: Any, end: Any, buff: float) -> tuple[np.ndarray, np.ndarray]:
    a, b = _as_point(start), _as_point(end)
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-6:
        return a, b + RIGHT * 0.01
    u = d / n
    p0 = _edge_point(start, u) if isinstance(start, Mobject) else a
    p1 = _edge_point(end, -u) if isinstance(end, Mobject) else b
    if np.linalg.norm(p1 - p0) > 2 * buff + 0.05:
        p0 = p0 + u * buff
        p1 = p1 - u * buff
    return p0, p1


def make_arrow(
    start: Any,
    end: Any,
    *,
    color: Any = MUTED,
    weight: float | None = None,
    directed: bool = True,
    buff: float = 0.1,
    stroke_width: float | None = None,
    curve: float = 0.0,
    dashed: bool = False,
    tip_size: float | None = None,
) -> VMobject:
    """Arrow (or plain line with directed=False) between two mobjects or points.

    Mobject endpoints attach to their outlines. weight in [0, 1] encodes strength as
    thickness + opacity (0 = faint hairline, 1 = bold). curve = bend angle in radians
    (e.g. 0.6). Use reweight(arrow, w) to animate a weight change.
    """
    c = _col(color)
    w = None if weight is None else float(np.clip(weight, 0.0, 1.0))
    sw = stroke_width if stroke_width is not None else (4.0 if w is None else 1.5 + 9.0 * w)
    opacity = 1.0 if w is None else 0.3 + 0.7 * w
    tip = tip_size if tip_size is not None else (0.2 if w is None else 0.14 + 0.14 * w)
    p0, p1 = _endpoints(start, end, buff)
    mob: VMobject
    if curve:
        if directed:
            mob = CurvedArrow(p0, p1, angle=curve, color=c, stroke_width=sw, tip_length=tip)
        else:
            mob = ArcBetweenPoints(p0, p1, angle=curve, color=c, stroke_width=sw)
    elif directed:
        mob = Arrow(p0, p1, buff=0, color=c, stroke_width=sw, tip_length=tip,
                    max_tip_length_to_length_ratio=0.45, max_stroke_width_to_length_ratio=100)
    elif dashed:
        mob = DashedLine(p0, p1, color=c, stroke_width=sw, dash_length=0.12)
    else:
        mob = Line(p0, p1, color=c, stroke_width=sw)
    if dashed and directed and not curve:
        mob = DashedVMobject(mob, num_dashes=max(6, int(np.linalg.norm(p1 - p0) / 0.2)))
    mob.set_opacity(opacity)
    mob.explainer_spec = dict(start=start, end=end, color=c, directed=directed, buff=buff, curve=curve,
                              dashed=dashed, stroke_width=stroke_width, tip_size=tip_size)
    mob.weight = w
    return mob


def make_edge(a: Any, b: Any, *, weight: float | None = None, color: Any = BLUE, directed: bool = False, **kw: Any) -> VMobject:
    """A connection between two nodes; thickness/opacity encode `weight` in [0, 1]."""
    return make_arrow(a, b, weight=weight, color=color, directed=directed, **kw)


def reweight(arrow: VMobject, weight: float, *, color: Any = None) -> Animation:
    """Animation: change an arrow/edge made by make_arrow/make_edge to a new weight (and colour)."""
    spec = dict(getattr(arrow, "explainer_spec", {}) or {})
    if not spec:
        w = float(np.clip(weight, 0, 1))
        return arrow.animate.set_stroke(width=1.5 + 9 * w, opacity=0.3 + 0.7 * w)
    if color is not None:
        spec["color"] = _col(color)
    start, end = spec.pop("start"), spec.pop("end")
    new = make_arrow(start, end, weight=weight, **spec)
    arrow.explainer_spec = new.explainer_spec
    arrow.weight = new.weight
    return Transform(arrow, new)


def make_edge_label(edge: VMobject, text: Any, *, size: float = SIZE_SMALL, color: Any = INK, offset: float = 0.25,
                    side: int = 1) -> Text:
    """A label beside the midpoint of an arrow/line (side=1 left of travel, -1 right)."""
    start, end = edge.get_start(), edge.get_end()
    d = end - start
    n = np.linalg.norm(d)
    normal = np.array([-d[1], d[0], 0.0]) / n if n > 1e-6 else UP
    lab = make_label(text, size=size, color=color)
    lab.move_to((start + end) / 2 + normal * side * (offset + max(lab.width, lab.height) / 2.5))
    return lab


class Node(VGroup):
    """A circle with a centred label. Parts: .circle, .label."""

    def __init__(self, label: Any = "", *, color: Any = BLUE, radius: float = 0.38, size: float = SIZE_LABEL,
                 fill_opacity: float = 0.18, text_color: Any = INK) -> None:
        super().__init__()
        c = _col(color)
        self.circle = Circle(radius=radius, stroke_color=c, stroke_width=3, fill_color=c, fill_opacity=fill_opacity)
        s = _text_str(label)
        self.label = make_label(s, size=size, color=text_color, weight=MEDIUM) if s else VMobject()
        if s:
            if self.label.width > radius * 1.6:
                self.label.scale_to_fit_width(radius * 1.6)
            self.label.move_to(self.circle.get_center())
        self.box = self.circle
        self.add(self.circle, self.label)


def make_node(label: Any = "", *, color: Any = BLUE, radius: float = 0.38, size: float = SIZE_LABEL,
              fill_opacity: float = 0.18) -> Node:
    return Node(label, color=color, radius=radius, size=size, fill_opacity=fill_opacity)


class NodeGraph(VGroup):
    """Nodes + edges. Parts: .nodes (list of Node), .edges (dict {(u, v): edge}),
    .node(i), .edge(u, v). Edges sit behind nodes."""

    def node(self, i: int) -> Node:
        return self.nodes[i]

    def edge(self, u: int, v: int) -> VMobject:
        return self.edges.get((u, v)) or self.edges[(v, u)]


def make_graph(
    labels: Sequence[Any] | int,
    edges: Sequence[Sequence[Any]] = (),
    *,
    layout: str | Sequence[Sequence[float]] = "circle",
    layers: Sequence[Sequence[int]] | None = None,
    width: float = 6.0,
    height: float = 3.6,
    node_color: Any = BLUE,
    node_colors: Sequence[Any] | None = None,
    edge_color: Any = MUTED,
    directed: bool = False,
    node_radius: float = 0.36,
    size: float = SIZE_SMALL,
) -> NodeGraph:
    """A node-link diagram.

    labels: list of node labels (or a count). edges: (u, v) or (u, v, weight in 0..1).
    layout: "circle" | "row" | "column" | "grid" | "layers" (with layers=[[0], [1, 2], ...])
            | "tree" (BFS levels from node 0) | an explicit list of (x, y) positions.
    """
    if isinstance(labels, int):
        labels = [str(i) for i in range(labels)]
    labels = list(labels)
    n = len(labels)
    pos: list[np.ndarray]
    if not isinstance(layout, str):
        pos = [np.array([float(p[0]), float(p[1]), 0.0]) for p in layout]
    elif layout == "row":
        pos = [np.array([(-width / 2 + width * i / max(n - 1, 1)) if n > 1 else 0.0, 0.0, 0.0]) for i in range(n)]
    elif layout == "column":
        pos = [np.array([0.0, (height / 2 - height * i / max(n - 1, 1)) if n > 1 else 0.0, 0.0]) for i in range(n)]
    elif layout == "grid":
        cols = max(1, math.ceil(math.sqrt(n)))
        rows_n = max(1, math.ceil(n / cols))
        pos = [np.array([(-width / 2 + width * (i % cols) / max(cols - 1, 1)) if cols > 1 else 0.0,
                         (height / 2 - height * (i // cols) / max(rows_n - 1, 1)) if rows_n > 1 else 0.0, 0.0])
               for i in range(n)]
    elif layout in ("layers", "tree"):
        if layout == "tree" or not layers:
            adj: dict[int, list[int]] = {i: [] for i in range(n)}
            for e in edges:
                u, v = int(e[0]), int(e[1])
                adj[u].append(v)
                if not directed:
                    adj[v].append(u)
            seen, frontier, layers_list = {0}, [0], []
            while frontier:
                layers_list.append(frontier)
                nxt = []
                for u in frontier:
                    for v in adj[u]:
                        if v not in seen:
                            seen.add(v)
                            nxt.append(v)
                frontier = nxt
            rest = [i for i in range(n) if i not in seen]
            if rest:
                layers_list.append(rest)
            layers = layers_list
        pos = [ORIGIN.copy() for _ in range(n)]
        L = len(layers)
        for li, layer in enumerate(layers):
            m = len(layer)
            for k, idx in enumerate(layer):
                if layout == "tree":
                    x = (-width / 2 + width * (k + 0.5) / m) if m > 1 else 0.0
                    y = (height / 2 - height * li / max(L - 1, 1)) if L > 1 else 0.0
                else:
                    x = (-width / 2 + width * li / max(L - 1, 1)) if L > 1 else 0.0
                    y = (height / 2 - height * (k + 0.5) / m) + 0.0 if m > 1 else 0.0
                    if m > 1:
                        y = height / 2 - height * k / (m - 1)
                pos[int(idx)] = np.array([x, y, 0.0])
    else:  # circle
        r = min(width, height) / 2
        pos = [np.array([r * math.cos(math.pi / 2 - 2 * math.pi * i / max(n, 1)),
                         r * math.sin(math.pi / 2 - 2 * math.pi * i / max(n, 1)), 0.0]) for i in range(n)]
    while len(pos) < n:
        pos.append(ORIGIN.copy())
    nodes = []
    for i, lab in enumerate(labels):
        c = node_colors[i % len(node_colors)] if node_colors else node_color
        nd = Node(lab, color=c, radius=node_radius, size=size)
        nd.move_to(pos[i])
        nodes.append(nd)
    edge_map: dict[tuple[int, int], VMobject] = {}
    edge_group = VGroup()
    for e in edges:
        u, v = int(e[0]), int(e[1])
        w = float(e[2]) if len(e) > 2 and e[2] is not None else None
        ed = make_arrow(nodes[u], nodes[v], weight=w, color=edge_color, directed=directed, buff=0.06)
        edge_map[(u, v)] = ed
        edge_group.add(ed)
    g = NodeGraph(edge_group, *nodes)
    g.nodes = nodes
    g.edges = edge_map
    g.edge_group = edge_group
    g.node_group = VGroup(*nodes)
    return g


# --------------------------------------------------------------------------------------
# Highlights, braces, callouts
# --------------------------------------------------------------------------------------


def make_highlight(mob: Mobject, *, color: Any = YELLOW, buff: float = 0.1, stroke_width: float = 4) -> SurroundingRectangle:
    """A rounded outline around a mobject (Create it to draw attention)."""
    return SurroundingRectangle(mob, color=_col(color), buff=buff, corner_radius=0.1, stroke_width=stroke_width)


def make_underline(mob: Mobject, *, color: Any = YELLOW, buff: float = 0.08, stroke_width: float = 4) -> Line:
    return Line(mob.get_corner(DL) + DOWN * buff, mob.get_corner(DR) + DOWN * buff, color=_col(color), stroke_width=stroke_width)


def make_brace(mob: Mobject, text: Any = "", *, direction: Any = DOWN, color: Any = MUTED, size: float = SIZE_SMALL) -> VGroup:
    """A brace along one side of `mob` with an optional label. Parts: .brace, .label."""
    br = Brace(mob, direction=direction, color=_col(color), buff=0.12)
    grp = VGroup(br)
    grp.brace = br
    s = _text_str(text)
    grp.label = make_label(s, size=size, color=color) if s else VMobject()
    if s:
        grp.label.next_to(br, direction, buff=0.1)
        grp.add(grp.label)
    return grp


def make_callout(text: Any, *, target: Mobject | None = None, direction: Any = UR, color: Any = YELLOW,
                 size: float = SIZE_SMALL, max_width: float = 4.0) -> VGroup:
    """A small note box (optionally connected to `target` by a thin line). Parts: .box, .label, .connector."""
    lab = make_paragraph(text, max_width=max_width, size=size, color=INK)
    box = make_box(lab.width + 0.5, lab.height + 0.36, color=color, fill_opacity=0.12, stroke_width=2.5, corner_radius=0.12)
    lab.move_to(box)
    grp = VGroup(box, lab)
    grp.box, grp.label = box, lab
    grp.connector = VMobject()
    if target is not None:
        grp.next_to(target, direction, buff=0.35)
        keep_inside(grp)
        p0, p1 = _endpoints(box, target, 0.06)
        conn = Line(p0, p1, color=_col(color), stroke_width=2.5)
        grp.connector = conn
        grp.add_to_back(conn)
    return grp


# --------------------------------------------------------------------------------------
# Axes, plots, number lines, bar charts
# --------------------------------------------------------------------------------------


def _norm_range(r: Sequence[float], default_step: float = 1.0) -> list[float]:
    r = [float(x) for x in r]
    if len(r) == 2:
        r.append(default_step)
    if r[1] <= r[0]:
        r[1] = r[0] + 1.0
    if r[2] <= 0:
        r[2] = default_step
    return r[:3]


def _tick_values(lo: float, hi: float, step: float, max_ticks: int = 12) -> list[float]:
    k0 = math.ceil(lo / step - 1e-9)
    k1 = math.floor(hi / step + 1e-9)
    vals = [k * step for k in range(k0, k1 + 1)]
    stride = 1
    while len(vals) / stride > max_ticks:
        stride += 1
    return [v for i, v in enumerate(vals) if (round(v / step)) % stride == 0]


def make_axes(
    x_range: Sequence[float] = (0, 5, 1),
    y_range: Sequence[float] = (0, 5, 1),
    *,
    width: float = 7.0,
    height: float = 4.2,
    x_label: Any = None,
    y_label: Any = None,
    numbers: bool = True,
    grid: bool = False,
    color: Any = MUTED,
    tips: bool = False,
    number_size: float = SIZE_TINY,
) -> Axes:
    """Styled Axes. Use axes.c2p(x, y) for points, plot_function(axes, f) for curves.
    Tick labels are Inter text at "nice" multiples of each step; axes.x_label / axes.y_label
    hold the axis titles."""
    xr, yr = _norm_range(x_range), _norm_range(y_range)
    c = _col(color)
    axes = Axes(
        x_range=xr, y_range=yr, x_length=width, y_length=height, tips=tips,
        axis_config={"color": c, "stroke_width": 2.5, "include_ticks": False, "include_numbers": False,
                     "tip_width": 0.16, "tip_height": 0.16},
    )
    ticks = VGroup()
    labels = VGroup()
    origin = axes.c2p(0 if xr[0] <= 0 <= xr[1] else xr[0], 0 if yr[0] <= 0 <= yr[1] else yr[0])
    for v in _tick_values(xr[0], xr[1], xr[2]):
        p = axes.c2p(v, 0 if yr[0] <= 0 <= yr[1] else yr[0])
        ticks.add(Line(p + DOWN * 0.06, p + UP * 0.06, color=c, stroke_width=2))
        if numbers and not (abs(v) < 1e-9 and xr[0] < 0 < xr[1] and yr[0] < 0 < yr[1]):
            lab = make_label(_fmt_number(v), size=number_size, color=MUTED)
            lab.next_to(p, DOWN, buff=0.14)
            labels.add(lab)
    for v in _tick_values(yr[0], yr[1], yr[2]):
        p = axes.c2p(0 if xr[0] <= 0 <= xr[1] else xr[0], v)
        ticks.add(Line(p + LEFT * 0.06, p + RIGHT * 0.06, color=c, stroke_width=2))
        if numbers and not (abs(v) < 1e-9 and (yr[0] < 0 < yr[1] or abs(origin[1] - p[1]) < 1e-6)):
            lab = make_label(_fmt_number(v), size=number_size, color=MUTED)
            lab.next_to(p, LEFT, buff=0.14)
            labels.add(lab)
    if grid:
        lines = VGroup()
        for v in _tick_values(xr[0], xr[1], xr[2]):
            lines.add(Line(axes.c2p(v, yr[0]), axes.c2p(v, yr[1]), color=GRID, stroke_width=1.5))
        for v in _tick_values(yr[0], yr[1], yr[2]):
            lines.add(Line(axes.c2p(xr[0], v), axes.c2p(xr[1], v), color=GRID, stroke_width=1.5))
        axes.add_to_back(lines)
        axes.grid_lines = lines
    axes.add(ticks, labels)
    axes.tick_marks, axes.tick_labels = ticks, labels
    axes.x_label = VMobject()
    axes.y_label = VMobject()
    if x_label:
        axes.x_label = make_label(x_label, size=SIZE_SMALL, color=MUTED)
        axes.x_label.next_to(axes.x_axis.get_end(), RIGHT, buff=0.3)
        axes.add(axes.x_label)
    if y_label:
        axes.y_label = make_label(y_label, size=SIZE_SMALL, color=MUTED)
        axes.y_label.next_to(axes.y_axis.get_end(), UP, buff=0.15)
        axes.add(axes.y_label)
    return axes


def _func_of(axes: Axes, func_or_graph: Any) -> Callable[[float], float]:
    if callable(func_or_graph) and not isinstance(func_or_graph, Mobject):
        return func_or_graph
    f = getattr(func_or_graph, "underlying_function", None)
    if f is not None:
        return f
    raise TypeError("expected a function f(x) or a graph returned by plot_function")


def plot_function(axes: Axes, func: Callable[[float], float], *, x_range: Sequence[float] | None = None,
                  color: Any = BLUE, stroke_width: float = 5, **kwargs: Any) -> ParametricFunction:
    """axes.plot(func) clipped to the visible y-range (curves never shoot into the header)."""
    xr = list(axes.x_range[:2])
    yr = list(axes.y_range[:2])
    if x_range is None:
        xs = np.linspace(xr[0], xr[1], 801)
        ok = []
        for x in xs:
            try:
                y = float(func(x))
                ok.append(np.isfinite(y) and yr[0] - 1e-6 <= y <= yr[1] + 1e-6)
            except Exception:
                ok.append(False)
        best, cur_start, best_len = None, None, 0
        for i, flag in enumerate(ok + [False]):
            if flag and cur_start is None:
                cur_start = i
            if not flag and cur_start is not None:
                if i - cur_start > best_len:
                    best, best_len = (cur_start, i - 1), i - cur_start
                cur_start = None
        if best is None:
            x_range = xr
        else:
            x_range = [xs[best[0]], xs[best[1]]]
    else:
        x_range = list(x_range)[:2]
    return axes.plot(func, x_range=x_range, color=_col(color), stroke_width=stroke_width, **kwargs)


def graph_point(axes: Axes, func_or_graph: Any, x: float) -> np.ndarray:
    """Scene point on the curve at input x."""
    f = _func_of(axes, func_or_graph)
    return axes.c2p(x, f(x))


def make_dot(point: Any = ORIGIN, *, color: Any = YELLOW, radius: float = 0.1) -> Dot:
    return Dot(_as_point(point), radius=radius, color=_col(color))


def dot_on_graph(axes: Axes, func_or_graph: Any, x: float, *, color: Any = YELLOW, radius: float = 0.1) -> Dot:
    return make_dot(graph_point(axes, func_or_graph, x), color=color, radius=radius)


def make_tangent(axes: Axes, func_or_graph: Any, x: float, *, length: float = 2.6, color: Any = ORANGE,
                 stroke_width: float = 4) -> Line:
    """Tangent line segment (in scene units `length`) to the curve at input x."""
    f = _func_of(axes, func_or_graph)
    h = 1e-4 * max(1.0, abs(x))
    p0, p1 = axes.c2p(x - h, f(x - h)), axes.c2p(x + h, f(x + h))
    d = p1 - p0
    n = np.linalg.norm(d)
    u = d / n if n > 1e-9 else RIGHT
    c = axes.c2p(x, f(x))
    return Line(c - u * length / 2, c + u * length / 2, color=_col(color), stroke_width=stroke_width)


def slope_at(func: Callable[[float], float], x: float) -> float:
    h = 1e-5 * max(1.0, abs(x))
    return (func(x + h) - func(x - h)) / (2 * h)


def make_number_line(x_range: Sequence[float] = (-5, 5, 1), *, length: float = 10.0, numbers: bool = True,
                     color: Any = MUTED, number_size: float = SIZE_TINY) -> NumberLine:
    """Styled NumberLine; use nl.n2p(x) for points."""
    xr = _norm_range(x_range)
    nl = NumberLine(x_range=xr, length=length, color=_col(color), stroke_width=2.5, include_numbers=False,
                    include_ticks=True, tick_size=0.07)
    if numbers:
        labs = VGroup()
        for v in _tick_values(xr[0], xr[1], xr[2], max_ticks=16):
            lab = make_label(_fmt_number(v), size=number_size, color=MUTED)
            lab.next_to(nl.n2p(v), DOWN, buff=0.16)
            labs.add(lab)
        nl.add(labs)
        nl.tick_labels = labs
    return nl


class BarChartView(VGroup):
    """Simple bar chart. Parts: .bars, .labels, .value_labels, .baseline. bar(i), set_values(...)."""

    def __init__(self, values: Sequence[float], labels: Sequence[Any] | None = None, *, colors: Any = None,
                 height: float = 3.2, bar_width: float = 0.7, gap: float = 0.4, max_value: float | None = None,
                 show_values: bool = True, decimals: int | None = None, label_size: float = SIZE_SMALL) -> None:
        super().__init__()
        vals = [float(v) for v in values]
        self.max_value = float(max_value) if max_value else (max([abs(v) for v in vals] or [1.0]) or 1.0)
        self.chart_height = height
        self.bar_width = bar_width
        self.decimals = decimals
        self.label_size = label_size
        self.show_values = show_values
        n = len(vals)
        if colors is None:
            cols = [ACCENTS[i % 4] for i in range(n)]
        elif isinstance(colors, (list, tuple)):
            cols = [colors[i % len(colors)] for i in range(n)]
        else:
            cols = [colors] * n
        self.colors = [_col(c) for c in cols]
        total_w = n * bar_width + (n - 1) * gap
        self.base_y = -height / 2
        self.xs = [-total_w / 2 + bar_width / 2 + i * (bar_width + gap) for i in range(n)]
        self.bars = VGroup(*[self._bar(i, v) for i, v in enumerate(vals)])
        self.baseline = Line(LEFT * (total_w / 2 + 0.3) + UP * self.base_y, RIGHT * (total_w / 2 + 0.3) + UP * self.base_y,
                             color=MUTED, stroke_width=2.5)
        self.labels = VGroup()
        for i in range(n):
            if labels is not None and i < len(labels) and _text_str(labels[i]):
                lab = make_label(labels[i], size=label_size, color=MUTED, max_width=bar_width + gap * 0.9)
                lab.move_to(np.array([self.xs[i], self.base_y - 0.3, 0]))
                self.labels.add(lab)
        self.value_labels = VGroup(*[self._value_label(i, v) for i, v in enumerate(vals)]) if show_values else VGroup()
        self.values = vals
        self._baseline_w0 = self.baseline.width
        self.add(self.baseline, self.bars, self.labels, self.value_labels)

    def _bar_height(self, v: float) -> float:
        return max(abs(v) / self.max_value * self.chart_height, 0.02)

    def _bar(self, i: int, v: float) -> Rectangle:
        h = self._bar_height(v)
        bar = Rectangle(width=self.bar_width, height=h, stroke_width=0, fill_color=self.colors[i], fill_opacity=0.9)
        bar.move_to(np.array([self.xs[i], self.base_y + h / 2, 0]))
        return bar

    def _value_label(self, i: int, v: float) -> Text:
        lab = make_label(_fmt_number(v, self.decimals), size=self.label_size, color=INK)
        lab.move_to(np.array([self.xs[i], self.base_y + self._bar_height(v) + 0.25, 0]))
        return lab

    def bar(self, i: int) -> Rectangle:
        return self.bars[i]

    def set_values(self, values: Sequence[float]) -> Animation:
        """Animation growing/shrinking bars (and value labels) to new values.
        Works after the chart has been moved or scaled."""
        k = self.baseline.width / self._baseline_w0 if self._baseline_w0 > 0 else 1.0
        base_y = self.baseline.get_y()
        anims = []
        for i, v in enumerate(values):
            if i >= len(self.bars):
                break
            old = self.bars[i]
            h = self._bar_height(float(v)) * k
            new_bar = Rectangle(width=old.width, height=h, stroke_width=0, fill_color=self.colors[i], fill_opacity=0.9)
            new_bar.move_to(np.array([old.get_x(), base_y + h / 2, 0]))
            anims.append(Transform(old, new_bar))
            if self.show_values and i < len(self.value_labels):
                nl = make_label(_fmt_number(float(v), self.decimals), size=self.label_size, color=INK).scale(k)
                nl.next_to(new_bar, UP, buff=0.12 * k)
                anims.append(Transform(self.value_labels[i], nl))
        self.values = [float(v) for v in values]
        return AnimationGroup(*anims)


def make_bar_chart(values: Sequence[float], labels: Sequence[Any] | None = None, *, colors: Any = None,
                   height: float = 3.2, bar_width: float = 0.7, gap: float = 0.4, max_value: float | None = None,
                   show_values: bool = True, decimals: int | None = None) -> BarChartView:
    """Bars rising from a baseline; chart.set_values([...]) animates new values."""
    return BarChartView(values, labels, colors=colors, height=height, bar_width=bar_width, gap=gap,
                        max_value=max_value, show_values=show_values, decimals=decimals)


def make_segment_bar(fractions: Sequence[float], *, colors: Any = None, width: float = 5.0, height: float = 0.55,
                     labels: Sequence[Any] | None = None, show_percent: bool = False) -> VGroup:
    """A horizontal bar split into proportional segments (shares that add up to a whole).
    Parts: .segments, .labels."""
    vals = [max(float(f), 0.0) for f in fractions]
    total = sum(vals) or 1.0
    n = len(vals)
    cols = (colors if isinstance(colors, (list, tuple)) else [colors] * n) if colors is not None else ACCENTS
    segs = VGroup()
    x = -width / 2
    for i, v in enumerate(vals):
        w = max(width * v / total, 0.01)
        c = _col(cols[i % len(cols)])
        r = Rectangle(width=w, height=height, stroke_color=BG, stroke_width=2, fill_color=c, fill_opacity=0.85)
        r.move_to(np.array([x + w / 2, 0, 0]))
        segs.add(r)
        x += w
    grp = VGroup(segs)
    grp.segments = segs
    labs = VGroup()
    for i, seg in enumerate(segs):
        text = None
        if labels is not None and i < len(labels):
            text = _text_str(labels[i])
        elif show_percent:
            text = f"{100 * vals[i] / total:.0f}%"
        if text:
            lab = make_label(text, size=SIZE_SMALL, color=INK)
            if lab.width < seg.width - 0.1:
                lab.move_to(seg)
            else:
                lab.next_to(seg, DOWN, buff=0.12)
            labs.add(lab)
    grp.labels = labs
    grp.add(labs)
    return grp


# --------------------------------------------------------------------------------------
# Animation shorthands (each returns an Animation for self.play)
# --------------------------------------------------------------------------------------


def _is_texty(mob: Mobject) -> bool:
    return isinstance(mob, (Text, MarkupText, MathTex, Tex, Paragraph, DecimalNumber, SingleStringMathTex))


def draw_in(mob: Mobject, **kwargs: Any) -> Animation:
    """Write text/equations, Create shapes; groups are drawn part by part."""
    if _is_texty(mob):
        return Write(mob, **kwargs)
    if isinstance(mob, (Chip, Node)):
        return AnimationGroup(Create(mob[0]), FadeIn(mob[1:]) if len(mob) > 1 else Wait(0.01), lag_ratio=0.4, **kwargs)
    if isinstance(mob, VGroup) and len(mob) and any(_is_texty(m) for m in mob):
        return LaggedStart(*[draw_in(m) for m in mob], lag_ratio=0.15, **kwargs)
    return Create(mob, **kwargs)


def rise_in(mob: Mobject, distance: float = 0.3, **kwargs: Any) -> Animation:
    """Fade in while drifting up slightly."""
    return FadeIn(mob, shift=UP * distance, **kwargs)


def pop_in(mob: Mobject, **kwargs: Any) -> Animation:
    """Grow from the centre."""
    return GrowFromCenter(mob, **kwargs)


def stagger_in(*mobs: Any, lag_ratio: float = 0.15, shift: Any = UP * 0.25, **kwargs: Any) -> Animation:
    """Fade items in one after another, e.g. stagger_in(token_row) or stagger_in(a, b, c)."""
    items: list[Mobject] = []
    for m in mobs:
        if isinstance(m, (list, tuple)):
            items.extend(_flatten_mobjects(m))
        elif isinstance(m, VGroup) and len(mobs) == 1 and len(m) > 0 and not isinstance(m, (Chip, Node)):
            items.extend(list(m))
        elif isinstance(m, Mobject):
            items.append(m)
    if not items:
        return Wait(0.1)
    return LaggedStart(*[FadeIn(m, shift=np.array(shift, dtype=float)) for m in items], lag_ratio=lag_ratio, **kwargs)


def fade_out(*mobs: Any, **kwargs: Any) -> Animation:
    items = _flatten_mobjects(mobs)
    if not items:
        return Wait(0.1)
    return AnimationGroup(*[FadeOut(m) for m in items], **kwargs)


def pulse(mob: Mobject, *, color: Any = None, scale: float = 1.12, **kwargs: Any) -> Animation:
    """Briefly enlarge + tint, then return to normal."""
    kw: dict[str, Any] = {"scale_factor": scale}
    kw["color"] = _col(color) if color is not None else YELLOW
    kw.update(kwargs)
    return Indicate(mob, **kw)


def flash_around(mob: Mobject, *, color: Any = YELLOW, **kwargs: Any) -> Animation:
    """A highlight stroke racing around the mobject (leaves nothing behind)."""
    return Circumscribe(mob, color=_col(color), fade_out=True, buff=0.1, **kwargs)


def tint(mob: Mobject, color: Any, *, fill_opacity: float = 0.2) -> Animation:
    """Recolour: chips/cells/nodes get a tinted box (text stays readable); anything else set_color."""
    c = _col(color)
    box = getattr(mob, "box", None)
    if box is not None:
        mob.color_value = c
        return box.animate.set_stroke(color=c).set_fill(color=c, opacity=fill_opacity)
    return mob.animate.set_color(c)


def set_chip_text(chip: Mobject, value: Any, *, color: Any = None) -> Animation:
    """Animation changing the text inside a chip / array cell (keeps the same objects)."""
    old = getattr(chip, "label", None)
    size = getattr(chip, "label_size", SIZE_LABEL)
    s = _text_str(value)
    new = make_label(s if s else " ", size=size, color=color if color is not None else getattr(chip, "text_color", INK),
                     weight=getattr(chip, "label_weight", MEDIUM))
    box = getattr(chip, "box", chip)
    if new.width > box.width - 0.12:
        new.scale_to_fit_width(max(box.width - 0.16, 0.1))
    new.move_to(box.get_center())
    if old is None or not _has_points(old):
        if old is not None and old in chip.submobjects:
            chip.remove(old)
        chip.label = new
        chip.add(new)
        return FadeIn(new, scale=0.8)
    return Transform(old, new)


def count_to(number: DecimalNumber, value: float, **kwargs: Any) -> Animation:
    """Animate a make_number(...) counting to a new value."""
    return ChangeDecimalToValue(number, float(value), **kwargs)


def swap(old: Mobject, new: Mobject, **kwargs: Any) -> Animation:
    """Cross-fade `old` into `new` (old is removed, new is added)."""
    return FadeTransform(old, new, **kwargs)


def _vm_family(mob: Mobject) -> list[VMobject]:
    return [m for m in mob.get_family() if isinstance(m, VMobject)]


def dim(*mobs: Any, amount: float = 0.7) -> Animation:
    """Fade mobjects to (1 - amount) of their current opacity; undim(...) restores them."""
    anims = []
    for m in _flatten_mobjects(mobs):
        if getattr(m, "_explainer_dim_snapshot", None) is None:
            m._explainer_dim_snapshot = [(sm.get_fill_opacity(), sm.get_stroke_opacity()) for sm in _vm_family(m)]
        target = m.copy()
        for sm, (fo, so) in zip(_vm_family(target), m._explainer_dim_snapshot):
            sm.set_fill(opacity=fo * (1 - amount), family=False)
            sm.set_stroke(opacity=so * (1 - amount), family=False)
        anims.append(Transform(m, target))
    return AnimationGroup(*anims) if anims else Wait(0.1)


def undim(*mobs: Any) -> Animation:
    anims = []
    for m in _flatten_mobjects(mobs):
        snap = getattr(m, "_explainer_dim_snapshot", None)
        if snap is None:
            continue
        target = m.copy()
        for sm, (fo, so) in zip(_vm_family(target), snap):
            sm.set_fill(opacity=fo, family=False)
            sm.set_stroke(opacity=so, family=False)
        m._explainer_dim_snapshot = None
        anims.append(Transform(m, target))
    return AnimationGroup(*anims) if anims else Wait(0.1)


# --------------------------------------------------------------------------------------
# Scene chrome: header + captions
# --------------------------------------------------------------------------------------


def _build_caption(text: str) -> VGroup:
    s = " ".join(_text_str(text).split()) or " "
    size = CAPTION_SIZE
    lines: list[str] = [s]
    for size in (CAPTION_SIZE, 24, 23, 22):
        full_w, avg = _measure_avg_char(s, size, MEDIUM)
        if full_w <= CAPTION_MAX_WIDTH:
            lines = [s]
            break
        lines = _balanced_two_lines(s)
        if max(len(l) for l in lines) * avg <= CAPTION_MAX_WIDTH * 1.02:
            break
    if len(lines) == 1:
        txt: VMobject = Text(lines[0], font=FONT, font_size=size, weight=MEDIUM, color=INK)
    else:
        txt = Paragraph(*lines, alignment="center", font=FONT, font_size=size, weight=MEDIUM, color=INK,
                        disable_ligatures=True, line_spacing=0.7)
    if txt.width > CAPTION_MAX_WIDTH:
        txt.scale_to_fit_width(CAPTION_MAX_WIDTH)
    max_h = 0.72
    if txt.height > max_h:
        txt.scale_to_fit_height(max_h)
    backing = RoundedRectangle(
        width=txt.width + 0.7, height=txt.height + 0.34, corner_radius=0.16, stroke_color=GRID, stroke_width=1.5,
        stroke_opacity=0.9, fill_color=ManimColor("#060A16"), fill_opacity=0.82,
    )
    backing.move_to(np.array([0.0, CAPTION_CENTER_Y, 0.0]))
    txt.move_to(backing.get_center())
    grp = VGroup(backing, txt)
    grp.backing, grp.text_mob = backing, txt
    grp.set_z_index(90)
    txt.set_z_index(91)
    return grp


def _build_progress(index: int, total: int) -> VGroup:
    dots = VGroup()
    for i in range(total):
        if i == index:
            d = RoundedRectangle(width=0.42, height=0.11, corner_radius=0.055, stroke_width=0, fill_color=BLUE, fill_opacity=1)
        else:
            d = Circle(radius=0.055, stroke_width=0, fill_color=MUTED if i < index else DIM, fill_opacity=0.9 if i < index else 0.7)
        dots.add(d)
    dots.arrange(RIGHT, buff=0.14)
    return dots


def _build_kicker(title: str) -> VMobject:
    s = " ".join(_text_str(title).upper().split())
    if not s:
        return VMobject()
    # Letter-spaced small caps look: hair spaces between letters (plain Text never wraps).
    tracked = "\u200a".join(s).replace("\u200a \u200a", "  ")
    try:
        k = Text(tracked, font=FONT, font_size=17, weight=SEMIBOLD, color=MUTED)
    except Exception:
        k = Text(s, font=FONT, font_size=17, weight=SEMIBOLD, color=MUTED)
    max_w = FRAME_W - 2 * SIDE_MARGIN - 2.6
    if k.width > max_w:
        k.scale_to_fit_width(max_w)
    return k


def _build_concept(concept: str) -> Text:
    t = Text(_text_str(concept) or " ", font=FONT, font_size=34, weight=SEMIBOLD, color=INK)
    max_w = FRAME_W - 2 * SIDE_MARGIN - 2.0
    if t.width > max_w:
        t.scale_to_fit_width(max_w)
    return t


# --------------------------------------------------------------------------------------
# The scene base class
# --------------------------------------------------------------------------------------


@dataclass
class _Block:
    index: int
    concept: str
    caption: str
    duration: float
    start_frame: int
    end_frame: int
    reserve: int
    is_last: bool
    fade_out: bool
    pending_intro: list[Animation] | None = None
    clamped: int = 0
    skipped: int = 0
    requested: float = 0.0
    layout_fixes: int = 0
    entry_frame: int = 0


def _load_manifest() -> dict[str, Any] | None:
    path = os.environ.get("EXPLAINER_STORYBOARD")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        scenes = sorted(data.get("scenes", []), key=lambda s: int(s.get("index", 0)))
        if not scenes:
            return None
        start = 0.0
        out = {}
        for s in scenes:
            idx = int(s["index"])
            dur = float(s["duration"])
            out[idx] = {"concept": str(s.get("concept", "")), "caption": str(s.get("caption", "")),
                        "duration": dur, "start": start}
            start += dur
        return {"title": str(data.get("title", "")), "scenes": out, "total": start,
                "last_index": max(out), "count": len(out)}
    except Exception as exc:
        _warn("could not read storyboard manifest", error=str(exc))
        return None


class ExplainerScene(Scene):
    """Base class for generated lessons. See the module docstring and `scene_block`."""

    LESSON_TITLE: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        random.seed(7)
        np.random.seed(7)
        try:
            self.camera.background_color = BG
        except Exception:
            pass
        self.fps = float(config.frame_rate)
        self._frames = 0
        self._internal_depth = 0
        self._chrome_refs: list[Mobject] = []
        self._chrome_ids: set[int] = set()
        self._block: _Block | None = None
        self._cursor_s = 0.0
        self._after_last = False
        self._blocks_done: list[int] = []
        self._manifest = _load_manifest()
        self._header_kicker: VMobject | None = None
        self._header_concept: VMobject | None = None
        self._header_progress: VMobject | None = None
        self._caption: VGroup | None = None
        self._outside_frames = 0
        self.content_area = CONTENT_AREA
        self._install_frame_counter()

    # ---------------------------------------------------------------- infrastructure

    def _install_frame_counter(self) -> None:
        renderer = self.renderer
        original = getattr(renderer, "add_frame", None)
        if original is None:  # pragma: no cover - OpenGL renderer is not used
            return

        def add_frame(frame: Any, num_frames: int = 1) -> None:
            if not renderer.skip_animations:
                self._frames += int(num_frames)
            return original(frame, num_frames)

        renderer.add_frame = add_frame

    @contextlib.contextmanager
    def _internal(self):
        self._internal_depth += 1
        try:
            yield
        finally:
            self._internal_depth -= 1

    def render(self, preview: bool = False) -> Any:
        try:
            return super().render(preview)
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                _emit("EXPLAINER_ERROR", {"type": type(exc).__name__, "message": str(exc)[:500]})
                print("EXPLAINER_TRACEBACK_BEGIN", flush=True)
                traceback.print_exc(file=sys.stdout)
                print("EXPLAINER_TRACEBACK_END", flush=True)
            raise

    def _frames_for(self, seconds: float) -> int:
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            s = 1.0
        if not np.isfinite(s) or s <= 0:
            return 0
        return int(round(s * self.fps))

    def _rt_for_frames(self, n: int) -> float:
        """A run_time that makes Manim render exactly n frames for an animation."""
        if n <= 1:
            return 1.0 / self.fps
        return (n - 0.5) / self.fps

    def _needs_updates(self) -> bool:
        try:
            return bool(
                self.always_update_mobjects
                or self.updaters
                or any(m.has_time_based_updater() for m in self.get_mobject_family_members())
            )
        except Exception:
            return True

    # ---------------------------------------------------------------- chrome bookkeeping

    def _mark_chrome(self, mob: Mobject) -> None:
        self._chrome_refs.append(mob)
        for m in mob.get_family():
            self._chrome_ids.add(id(m))

    def _is_chrome(self, mob: Mobject | None) -> bool:
        return mob is not None and id(mob) in self._chrome_ids

    def _remove_chrome_from(self, mob: Mobject) -> None:
        if self._is_chrome(mob):
            return
        family_ids = {id(m) for m in mob.get_family()}
        if not (family_ids & self._chrome_ids):
            return
        stack = [mob]
        while stack:
            m = stack.pop()
            chrome_children = [c for c in m.submobjects if self._is_chrome(c)]
            if chrome_children:
                m.remove(*chrome_children)
            stack.extend(c for c in m.submobjects if not self._is_chrome(c))

    def _strip_chrome(self, anim: Animation) -> Animation | None:
        if isinstance(anim, AnimationGroup) and not isinstance(anim, TransformMatchingAbstractBase):
            changed = False
            subs = []
            for sub in anim.animations:
                r = self._strip_chrome(sub)
                if r is None:
                    subs.append(Wait(run_time=max(sub.run_time, 1e-3)))
                    changed = True
                else:
                    subs.append(r)
            if changed:
                anim.animations = subs
                anim.build_animations_with_timings()
                self._remove_chrome_from(anim.group)
            return anim
        mob = getattr(anim, "mobject", None)
        if self._is_chrome(mob):
            return None
        if mob is not None:
            self._remove_chrome_from(mob)
        return anim

    @property
    def content_mobjects(self) -> list[Mobject]:
        """Everything on screen except the header and caption."""
        return [m for m in self.mobjects if not self._is_chrome(m)]

    # ---------------------------------------------------------------- layout guard

    def _guard(self, mobs: list[Mobject], reason: str) -> None:
        mobs = [m for m in mobs if m is not None and not self._is_chrome(m) and _has_points(m)]
        if not mobs:
            return
        b = _union_bounds(mobs)
        if b is None:
            return
        l, r, bo, t = b
        if r < FRAME_LEFT or l > FRAME_RIGHT or t < FRAME_BOTTOM or bo > FRAME_TOP:
            return  # entirely off-screen: an intentional slide in/out
        area = self.content_area
        tol = 0.1
        if l >= area.left - tol and r <= area.right + tol and bo >= area.bottom - tol and t <= area.top + tol:
            return
        w, h = r - l, t - bo
        s = 1.0
        if w > area.width:
            s = min(s, area.width / w)
        if h > area.height:
            s = min(s, area.height / h)
        center = np.array([(l + r) / 2, (bo + t) / 2, 0.0])
        if s < 1.0:
            for m in mobs:
                m.scale(s * 0.995, about_point=center)
            l, r, bo, t = _union_bounds(mobs)
        dx = area.left - l if l < area.left else (area.right - r if r > area.right else 0.0)
        dy = area.bottom - bo if bo < area.bottom else (area.top - t if t > area.top else 0.0)
        if dx or dy:
            for m in mobs:
                m.shift(np.array([dx, dy, 0.0]))
        if self._block is not None:
            self._block.layout_fixes += 1
        _emit("EXPLAINER_LAYOUT", {"index": self._block.index if self._block else None, "reason": reason,
                                   "scale": round(s, 3), "dx": round(dx, 3), "dy": round(dy, 3)})

    def _guard_candidates(self, anim: Animation, in_scene: set[int], out: list[Mobject]) -> None:
        if isinstance(anim, TransformMatchingAbstractBase):
            tgt = getattr(anim, "to_add", None)
            if tgt is not None and id(tgt) not in in_scene:
                out.append(tgt)
            return
        if isinstance(anim, AnimationGroup):
            for sub in anim.animations:
                self._guard_candidates(sub, in_scene, out)
            return
        # Anything this animation brings into the scene is a candidate: introducers (FadeIn,
        # Create, Write...), but also e.g. TransformFromCopy's target, which Manim adds as the
        # animation's own mobject. Treating them together keeps groups intact when nudged.
        mob = getattr(anim, "mobject", None)
        if mob is not None and id(mob) not in in_scene:
            out.append(mob)
        tgt = getattr(anim, "target_mobject", None)
        if isinstance(tgt, Mobject) and tgt is not mob and id(tgt) not in in_scene:
            out.append(tgt)

    def fit_to_content(self, mob: Mobject, area: Area | None = None, **kwargs: Any) -> Mobject:
        """Scale/position `mob` to fit the content area (or `area`). Returns mob."""
        return fit_in(mob, area or self.content_area, **kwargs)

    def columns(self, n: int = 2, gap: float = 0.5, ratios: Sequence[float] | None = None) -> list[Area]:
        return self.content_area.columns(n, gap=gap, ratios=ratios)

    def rows(self, n: int = 2, gap: float = 0.3, ratios: Sequence[float] | None = None) -> list[Area]:
        return self.content_area.rows(n, gap=gap, ratios=ratios)

    # ---------------------------------------------------------------- Scene overrides

    def add(self, *mobjects: Mobject) -> "ExplainerScene":
        if self._internal_depth == 0:
            in_scene = {id(m) for m in self.get_mobject_family_members()}
            self._guard([m for m in mobjects if isinstance(m, Mobject) and id(m) not in in_scene], "add")
        return super().add(*mobjects)

    def remove(self, *mobjects: Mobject) -> "ExplainerScene":
        if self._internal_depth == 0:
            mobjects = tuple(m for m in mobjects if not self._is_chrome(m))
            for m in mobjects:
                if isinstance(m, Mobject):
                    self._remove_chrome_from(m)
        return super().remove(*mobjects)

    def clear(self) -> "ExplainerScene":
        if self._internal_depth == 0:
            return self.remove(*self.content_mobjects)
        return super().clear()

    def _prepare(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Animation]:
        for k in ("subcaption", "subcaption_duration", "subcaption_offset"):
            kwargs.pop(k, None)
        anims: list[Animation] = []

        def visit(item: Any, depth: int = 0) -> None:
            if item is None:
                return
            if isinstance(item, (Animation, _AnimationBuilder)):
                anims.append(prepare_animation(item))
            elif isinstance(item, Mobject):
                _warn("a Mobject was passed to self.play(); fading it in", mobject=type(item).__name__)
                anims.append(FadeIn(item))
            elif depth == 0 and isinstance(item, Iterable) and not isinstance(item, (str, bytes)):
                for sub in item:
                    visit(sub, depth + 1)
            else:
                anims.append(prepare_animation(item))  # raises a clear TypeError

        for a in args:
            visit(a)
        run_time = kwargs.pop("run_time", None)
        for anim in anims:
            for k, v in kwargs.items():
                setattr(anim, k, v)
            if run_time is not None:
                try:
                    anim.run_time = max(float(run_time), 0.0)
                except (TypeError, ValueError):
                    pass
        out = []
        for anim in anims:
            r = self._strip_chrome(anim)
            if r is not None:
                out.append(r)
        return out

    def _run_play(self, anims: list[Animation], n: int, natural: float) -> None:
        target = self._rt_for_frames(n)
        scale = target / natural if natural > 1e-9 else 1.0
        for a in anims:
            a.run_time = max(a.run_time * scale, 1e-6)
        longest = max(anims, key=lambda a: a.run_time)
        longest.run_time = target
        with self._internal():
            Scene.play(self, *anims)

    def _fast_forward(self, anims: list[Animation]) -> None:
        """Apply animations instantly (no frames written), like Manim's -n skipping."""
        for a in anims:
            if a.run_time <= 0:
                a.run_time = 1.0 / self.fps
        r = self.renderer
        orig = r._original_skipping_status
        r._original_skipping_status = True
        try:
            with self._internal():
                Scene.play(self, *anims)
        finally:
            r._original_skipping_status = orig
            r.skip_animations = orig

    def _wait_frames(self, n: int, *, stop_condition: Callable[[], bool] | None = None,
                     frozen_frame: bool | None = None) -> None:
        if n <= 0:
            return
        with self._internal():
            if stop_condition is not None:
                Scene.wait(self, self._rt_for_frames(n), stop_condition=stop_condition, frozen_frame=False)
                return
            static = (not self._needs_updates()) if frozen_frame is None else bool(frozen_frame)
            rt = (n + 0.5) / self.fps if static else self._rt_for_frames(n)
            Scene.wait(self, rt, frozen_frame=static)

    def _budget(self) -> int | None:
        b = self._block
        if b is None:
            return None
        return b.end_frame - b.reserve - self._frames

    def play(self, *args: Any, **kwargs: Any) -> None:
        if self._internal_depth:
            return Scene.play(self, *args, **kwargs)
        anims = self._prepare(args, kwargs)
        if not anims:
            _warn("self.play() called with no animations; skipped")
            return
        if len(anims) == 1 and type(anims[0]) is Wait and anims[0].stop_condition is None:
            return self.wait(anims[0].run_time)  # same frame-exact path as self.wait
        in_scene = {id(m) for m in self.get_mobject_family_members()}
        cands: list[Mobject] = []
        for a in anims:
            self._guard_candidates(a, in_scene, cands)
        self._guard(cands, "play")
        natural = max(a.run_time for a in anims)
        n = self._frames_for(natural)
        b = self._block
        if b is None:
            if self._after_last:
                self._fast_forward(anims)
                return
            if n <= 0:
                self._fast_forward(anims)
                return
            before = self._frames
            self._run_play(anims, n, natural)
            self._outside_frames += self._frames - before
            return
        b.requested += max(natural, 0.0)
        budget = self._budget() or 0
        if b.pending_intro:
            intro = b.pending_intro
            if n >= int(0.35 * self.fps) and budget >= n:
                b.pending_intro = None
                for ia in intro:
                    ia.run_time = min(INTRO_TIME, natural)
                anims = anims + intro
            else:
                self._flush_intro()
                budget = self._budget() or 0
        if n <= 0:
            self._fast_forward(anims)
            return
        if n > budget:
            b.clamped += 1
            if budget <= 0:
                b.skipped += 1
                self._fast_forward(anims)
                return
            n = budget
        self._run_play(anims, n, natural)

    def wait(self, duration: float = DEFAULT_WAIT_TIME, stop_condition: Callable[[], bool] | None = None,
             frozen_frame: bool | None = None) -> None:
        if self._internal_depth:
            return Scene.wait(self, duration, stop_condition=stop_condition, frozen_frame=frozen_frame)
        n = self._frames_for(duration)
        b = self._block
        if b is None:
            if self._after_last or n <= 0:
                return
            before = self._frames
            self._wait_frames(n, stop_condition=stop_condition, frozen_frame=frozen_frame)
            self._outside_frames += self._frames - before
            return
        b.requested += max(n / self.fps, 0.0)
        if b.pending_intro:
            self._flush_intro()
            n -= int(round(INTRO_TIME * self.fps))
        budget = self._budget() or 0
        if n <= 0:
            return
        if n > budget:
            b.clamped += 1
            if budget <= 0:
                b.skipped += 1
                return
            n = budget
        self._wait_frames(n, stop_condition=stop_condition, frozen_frame=frozen_frame)

    def time_left(self) -> float:
        """Seconds left in the current scene_block (excluding any reserved fade-out)."""
        b = self._budget()
        return max(b, 0) / self.fps if b is not None else 0.0

    # ---------------------------------------------------------------- scene blocks

    @contextlib.contextmanager
    def scene_block(self, index: int, concept: str = "", caption: str = "", duration: float | None = None,
                    *, fade_out: bool = False, **_ignored: Any):
        """One storyboard scene. Lasts exactly `duration` seconds no matter what runs inside:
        animations that would overrun are shortened, and the end is padded with a hold.
        Shows the caption and concept header. Content stays on screen into the next block
        unless you remove it (self.clear_content(...)) or pass fade_out=True."""
        if self._block is not None:
            raise RuntimeError("scene_block() blocks cannot be nested")
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = len(self._blocks_done)
        m = self._manifest
        spec = m["scenes"].get(index) if m else None
        if spec:
            if concept and concept.strip() != spec["concept"].strip():
                _warn("scene_block concept differs from storyboard; using storyboard", index=index)
            if caption and " ".join(str(caption).split()) != " ".join(spec["caption"].split()):
                _warn("scene_block caption differs from storyboard; using storyboard", index=index)
            if duration is not None and abs(float(duration) - spec["duration"]) > 1e-6:
                _warn("scene_block duration differs from storyboard; using storyboard", index=index,
                      code=duration, storyboard=spec["duration"])
            concept, caption, dur, start_s = spec["concept"], spec["caption"], spec["duration"], spec["start"]
            is_last = index == m["last_index"]
        else:
            try:
                dur = float(duration) if duration is not None else 6.0
            except (TypeError, ValueError):
                dur = 6.0
            dur = min(max(dur, 1.0), 60.0)
            start_s = self._cursor_s
            is_last = False
        start_frame = int(round(start_s * self.fps))
        end_frame = int(round((start_s + dur) * self.fps))
        # Catch up if blocks were skipped (keeps every block at its storyboard time).
        if self._frames < start_frame:
            self._wait_frames(start_frame - self._frames)
        reserve = int(round(FADE_OUT_TIME * self.fps)) if (fade_out and not is_last) else 0
        state = _Block(index=index, concept=str(concept), caption=str(caption), duration=dur,
                       start_frame=start_frame, end_frame=end_frame, reserve=reserve, is_last=is_last,
                       fade_out=bool(fade_out), entry_frame=self._frames)
        _emit("EXPLAINER_BLOCK_START", {"index": index, "total": m["count"] if m else None})
        self._block = state
        state.pending_intro = self._make_intro(state)
        try:
            yield state
        except BaseException:
            self._block = None
            raise
        self._finish_block(state)
        self._block = None

    def _make_intro(self, b: _Block) -> list[Animation]:
        anims: list[Animation] = []
        total = self._manifest["count"] if self._manifest else 0
        title = (self._manifest["title"] if self._manifest else "") or self.LESSON_TITLE
        new_caption = _build_caption(b.caption)
        new_concept = _build_concept(b.concept)
        x0 = FRAME_LEFT + SIDE_MARGIN
        with self._internal():
            if self._header_kicker is None:
                kicker = _build_kicker(title)
                if _has_points(kicker):
                    kicker.move_to(np.array([0, 3.62, 0])).align_to(np.array([x0, 0, 0]), LEFT)
                    kicker.set_z_index(80)
                    self._mark_chrome(kicker)
                    anims.append(FadeIn(kicker, shift=RIGHT * 0.15))
                self._header_kicker = kicker
            new_concept.move_to(np.array([0, 3.2, 0])).align_to(np.array([x0, 0, 0]), LEFT)
            new_concept.set_z_index(80)
            self._mark_chrome(new_concept)
            if self._header_concept is not None:
                anims.append(FadeOut(self._header_concept, shift=UP * 0.18))
                anims.append(FadeIn(new_concept, shift=UP * 0.18))
            else:
                anims.append(FadeIn(new_concept, shift=UP * 0.12))
            self._header_concept = new_concept
            if total and total > 1:
                prog = _build_progress(min(max(b.index, 0), total - 1), total)
                prog.move_to(np.array([0, 3.62, 0])).align_to(np.array([FRAME_RIGHT - SIDE_MARGIN, 0, 0]), RIGHT)
                prog.set_z_index(80)
                self._mark_chrome(prog)
                if self._header_progress is not None:
                    anims.append(FadeOut(self._header_progress))
                anims.append(FadeIn(prog))
                self._header_progress = prog
            self._mark_chrome(new_caption)
            if self._caption is not None:
                old = self._caption
                anims.append(FadeOut(old.text_mob, shift=UP * 0.12))
                anims.append(ReplacementTransform(old.backing, new_caption.backing))
                anims.append(FadeIn(new_caption.text_mob, shift=UP * 0.12))
            else:
                anims.append(FadeIn(new_caption, shift=UP * 0.12))
            self._caption = new_caption
        for a in anims:
            a.run_time = INTRO_TIME
        return anims

    def _flush_intro(self) -> None:
        b = self._block
        if b is None or not b.pending_intro:
            return
        anims, b.pending_intro = b.pending_intro, None
        n = int(round(INTRO_TIME * self.fps))
        room = b.end_frame - self._frames
        n = max(1, min(n, room)) if room > 0 else 0
        for a in anims:
            a.run_time = INTRO_TIME
        if n <= 0:
            self._fast_forward(anims)
        else:
            self._run_play(anims, n, INTRO_TIME)

    def _finish_block(self, b: _Block) -> None:
        self._flush_intro()
        if b.fade_out and not b.is_last:
            hold = b.end_frame - b.reserve - self._frames
            if hold > 0:
                self._wait_frames(hold)
            content = [m for m in self.content_mobjects if _has_points(m)]
            remaining = b.end_frame - self._frames
            if content:
                fades = [FadeOut(m) for m in content]
                if remaining >= 1:
                    self._run_play(fades, remaining, FADE_OUT_TIME)
                else:
                    self._fast_forward(fades)
            leftovers = [m for m in self.content_mobjects]
            if leftovers:
                with self._internal():
                    Scene.remove(self, *leftovers)
        for _ in range(3):
            pad = b.end_frame - self._frames
            if pad <= 0:
                break
            self._wait_frames(pad)
        actual = (self._frames - b.entry_frame) / self.fps
        _emit("EXPLAINER_TIMELINE", {
            "index": b.index,
            "planned": round(b.duration, 4),
            "actual": round(actual, 4),
            "start": round(b.entry_frame / self.fps, 4),
            "end": round(self._frames / self.fps, 4),
            "end_target": round(b.end_frame / self.fps, 4),
            "requested": round(b.requested, 3),
            "clamped_calls": b.clamped,
            "skipped_calls": b.skipped,
            "layout_fixes": b.layout_fixes,
        })
        self._blocks_done.append(b.index)
        self._cursor_s = b.end_frame / self.fps
        if b.is_last:
            self._after_last = True

    def clear_content(self, *keep: Any, run_time: float = 0.6, keep_mobjects: Any = None, **kwargs: Any) -> None:
        """Fade out everything on screen except the header, caption and `keep` mobjects.
        Also accepts keep=[...] as a keyword."""
        extra = kwargs.pop("keep", None)
        keep = tuple(keep) + tuple(_flatten_mobjects([extra, keep_mobjects]))
        keep_ids: set[int] = set()
        for k in _flatten_mobjects(keep):
            keep_ids.update(id(x) for x in k.get_family())
        targets = []
        for m in self.content_mobjects:
            if id(m) in keep_ids:
                continue
            fam = m.get_family()
            if any(id(x) in keep_ids for x in fam):
                # A group containing something to keep: fade its other top-level parts only.
                for sub in m.submobjects:
                    if id(sub) not in keep_ids and not any(id(x) in keep_ids for x in sub.get_family()):
                        targets.append(sub)
                continue
            targets.append(m)
        targets = [t for t in targets if _has_points(t)]
        if not targets:
            return
        self.play(*[FadeOut(t) for t in targets], run_time=run_time)

    def tear_down(self) -> None:
        if self._block is not None:  # pragma: no cover - only on unusual control flow
            self._finish_block(self._block)
            self._block = None
        m = self._manifest
        if m:
            missing = [i for i in m["scenes"] if i not in self._blocks_done]
            if missing:
                _warn("storyboard scenes never ran", missing=missing)
            target = int(round(m["total"] * self.fps))
            if self._frames < target:
                self._wait_frames(target - self._frames)
        _emit("EXPLAINER_SUMMARY", {
            "frames": self._frames,
            "fps": self.fps,
            "duration": round(self._frames / self.fps, 4),
            "blocks": self._blocks_done,
            "expected_blocks": m["count"] if m else None,
            "outside_block_seconds": round(self._outside_frames / self.fps, 3),
        })
        super().tear_down()


__all__ = [
    # palette
    "BG", "SURFACE", "SURFACE_2", "GRID", "INK", "MUTED", "DIM", "BLUE", "PURPLE", "CYAN", "YELLOW", "GREEN",
    "RED", "ORANGE", "PINK", "GOOD", "BAD", "HIGHLIGHT", "ACCENTS",
    # typography
    "FONT", "SIZE_TITLE", "SIZE_HEADING", "SIZE_BODY", "SIZE_LABEL", "SIZE_SMALL", "SIZE_TINY", "SIZE_MATH",
    "CAPTION_SIZE",
    # layout
    "FRAME_W", "FRAME_H", "FRAME_LEFT", "FRAME_RIGHT", "FRAME_TOP", "FRAME_BOTTOM", "CAPTION_BAND_TOP",
    "HEADER_BAND_BOTTOM", "Area", "CONTENT_AREA", "fit_in", "keep_inside", "place_near", "arrange_in_grid_fit",
    # text & math
    "make_label", "make_heading", "make_title", "make_paragraph", "make_bullets", "make_equation", "make_number",
    # shapes
    "make_box", "make_panel", "Chip", "make_chip", "ItemRow", "TokenRow", "ArrayRow", "make_token_row",
    "make_array", "make_grid", "Pointer", "make_pointer", "move_pointer",
    # arrows & graphs
    "make_arrow", "make_edge", "reweight", "make_edge_label", "Node", "make_node", "NodeGraph", "make_graph",
    # highlights
    "make_highlight", "make_underline", "make_brace", "make_callout",
    # plots & charts
    "make_axes", "plot_function", "graph_point", "make_dot", "dot_on_graph", "make_tangent", "slope_at",
    "make_number_line", "BarChartView", "make_bar_chart", "make_segment_bar",
    # animations
    "draw_in", "rise_in", "pop_in", "stagger_in", "fade_out", "pulse", "flash_around", "tint", "set_chip_text",
    "count_to", "swap", "dim", "undim",
    # scene
    "ExplainerScene", "INTRO_TIME", "FADE_OUT_TIME",
]
