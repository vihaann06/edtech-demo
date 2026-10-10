"""Storyboard -> Manim code (LLM), plus a cheap static check and an LLM repair step.

The system prompt (prompts/codegen_system.md) is loaded once and sent byte-for-byte
identically on every call (generation and repair), so prompt caching applies.
The model writes every scene itself; `visual_helpers` only supplies primitives.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app import llm
from app.config import get_settings
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus, PipelineError, ProgressCallback, noop_progress

log = logging.getLogger(__name__)

SCENE_CLASS_NAME = "ExplainerVideo"
BASE_CLASS_NAME = "ExplainerScene"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CODEGEN_SYSTEM_PROMPT: str = (PROMPTS_DIR / "codegen_system.md").read_text(encoding="utf-8")

CODEGEN_MAX_TOKENS = 32000
MAX_CODE_CHARS = 60000


class CodeGenerationError(PipelineError):
    """The model's answer did not contain usable code."""


# --------------------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------------------


def _py_str(value: str) -> str:
    """A Python string literal (JSON string syntax is valid Python)."""
    return json.dumps(value, ensure_ascii=False)


def _fmt_duration(d: float) -> str:
    return repr(float(d))


def block_header(scene: Any) -> str:
    return (
        f"with self.scene_block(index={scene.index}, concept={_py_str(scene.concept)}, "
        f"caption={_py_str(scene.caption)}, duration={_fmt_duration(scene.duration)}):"
    )


def storyboard_for_prompt(storyboard: Storyboard) -> str:
    data = storyboard.model_dump(mode="json")
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_codegen_message(storyboard: Storyboard) -> str:
    headers = "\n".join(f"        {block_header(s)}" for s in storyboard.scenes)
    running = storyboard.running_example.strip() or "(none given - pick one concrete example and reuse it in every scene)"
    return (
        "Write the Manim scene for this storyboard.\n\n"
        f"<storyboard>\n{storyboard_for_prompt(storyboard)}\n</storyboard>\n\n"
        f"Running example (reuse its exact values in every scene): {running}\n\n"
        f"Total length: {storyboard.total_duration:g} s in {len(storyboard.scenes)} scenes. "
        "Use exactly these block headers, in this order, copied verbatim:\n\n"
        f"{headers}\n\n"
        "Follow each scene's visual_description, animation_instructions (in order, with roughly the stated "
        "timings), math_expressions (verbatim, via make_equation) and transition. Keep the objects that the "
        "transitions say stay on screen, and remove the ones that leave. Output exactly one ```python block."
    )


def build_repair_message(storyboard: Storyboard, code: str, error: str, attempt: int, stage: str) -> str:
    headers = "\n".join(f"        {block_header(s)}" for s in storyboard.scenes)
    stage_text = {
        "static_check": "It was rejected by the pre-render checker before rendering.",
        "render": "Rendering it failed with a Python error.",
        "timeout": "Rendering it took too long and was stopped.",
        "timing": "It rendered, but the timing did not match the storyboard.",
        "validation": "It rendered, but the video failed validation.",
    }.get(stage, "It failed.")
    return (
        f"Your previous code for this storyboard failed (repair attempt {attempt}). {stage_text}\n\n"
        f"<storyboard>\n{storyboard_for_prompt(storyboard)}\n</storyboard>\n\n"
        f"<failing_code>\n{code}\n</failing_code>\n\n"
        f"<error>\n{error.strip()}\n</error>\n\n"
        "Fix the root cause and return the complete corrected file in exactly one ```python block. "
        "Keep everything that already works; change as little as possible. If an API call is the problem and "
        "you are not certain of the correct signature, replace it with a simpler helper from the reference. "
        "The block headers must stay exactly:\n\n"
        f"{headers}"
    )


# --------------------------------------------------------------------------------------
# Code extraction
# --------------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```[ \t]*(?:python|py|python3)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str) -> str:
    """Pull the Python file out of the model's answer (a single ```python fenced block)."""
    if not text or not text.strip():
        raise CodeGenerationError("The AI returned no animation code.", detail="empty response")
    blocks = [b.strip("\n") for b in _FENCE_RE.findall(text)]
    blocks = [b for b in blocks if b.strip()]
    if blocks:
        with_class = [b for b in blocks if f"class {SCENE_CLASS_NAME}" in b]
        chosen = max(with_class or blocks, key=len)
        return chosen.strip() + "\n"
    stripped = text.strip()
    if stripped.startswith("```"):
        # An unterminated fence: the answer was cut off.
        raise CodeGenerationError("The AI's animation code was incomplete.", detail="unterminated code fence")
    if f"class {SCENE_CLASS_NAME}" in stripped and "import" in stripped:
        return stripped + "\n"
    raise CodeGenerationError("The AI did not return animation code.", detail=text[:2000])


# --------------------------------------------------------------------------------------
# Static check
# --------------------------------------------------------------------------------------

ALLOWED_IMPORTS = {
    "manim", "visual_helpers", "numpy", "math", "random", "typing", "itertools", "functools", "collections",
    "dataclasses", "colorsys", "__future__", "enum", "string", "fractions", "decimal", "statistics", "operator",
    "copy", "re",
}
BANNED_NAMES = {
    "os": "no filesystem/OS access", "sys": "no interpreter access", "subprocess": "no processes",
    "socket": "no network", "shutil": "no filesystem access", "pathlib": "no filesystem access",
    "Path": "no filesystem access", "requests": "no network", "urllib": "no network", "http": "no network",
    "ctypes": "not allowed", "importlib": "no dynamic imports", "builtins": "not allowed",
    "open": "no file access", "eval": "not allowed", "exec": "not allowed", "compile": "not allowed",
    "__import__": "no dynamic imports", "globals": "not allowed", "locals": "not allowed", "vars": "not allowed",
    "breakpoint": "not allowed", "input": "no interactive input", "exit": "not allowed", "quit": "not allowed",
    "memoryview": "not allowed", "tempconfig": "do not change the Manim config",
    "ImageMobject": "no image files - draw shapes instead", "SVGMobject": "no SVG files - draw shapes instead",
    "VideoMobject": "no video files", "ThreeDScene": "3D scenes are not supported",
    "MovingCameraScene": "camera moves are not supported", "ZoomedScene": "camera moves are not supported",
    "ShowCreation": "removed in Manim CE: use Create", "TextMobject": "removed in Manim CE: use make_label / Text",
    "TexMobject": "removed in Manim CE: use make_equation / MathTex",
    "FadeInFrom": "removed: use FadeIn(m, shift=DIR)", "FadeInFromDown": "removed: use FadeIn(m, shift=UP)",
    "FadeOutAndShift": "removed: use FadeOut(m, shift=DIR)", "FadeInFromLarge": "removed: use FadeIn(m, scale=1.5)",
    "ShowCreationThenDestruction": "removed: use ShowPassingFlash or flash_around",
    "ShowCreationThenFadeOut": "removed: use Create then FadeOut",
}
BANNED_ATTRS = {
    "get_graph": "removed in Manim CE: use plot_function(axes, f) or axes.plot(f)",
    "add_sound": "videos have no audio",
    "set_camera_orientation": "3D is not supported",
}
FORBIDDEN_OVERRIDES = {"setup", "tear_down", "render", "play", "wait", "add", "remove", "clear", "__init__",
                       "scene_block", "pause", "wait_until"}
ALLOWED_DUNDER_ATTRS = {"__init__", "__name__"}
CONFIG_READ_ATTRS = {"frame_width", "frame_height", "frame_x_radius", "frame_y_radius", "frame_rate",
                     "pixel_width", "pixel_height"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}"
    return ""


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.value if not isinstance(node, ast.Call) else node.func
    return node.id if isinstance(node, ast.Name) else ""


def _is_scene_block_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node.func) == "self.scene_block"


def _const_int(node: ast.AST | None) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _block_index(call: ast.Call) -> Optional[int]:
    for kw in call.keywords:
        if kw.arg == "index":
            return _const_int(kw.value)
    if call.args:
        return _const_int(call.args[0])
    return None


def static_check(code: str, storyboard: Storyboard | None = None) -> list[str]:
    """Cheap pre-render checks. Returns a list of problems (empty = OK).

    Defense in depth only - the container is the real security boundary - but it
    also catches common LLM mistakes before spending a render on them."""
    problems: list[str] = []
    if len(code) > MAX_CODE_CHARS:
        return [f"The file is {len(code)} characters long; keep it under {MAX_CODE_CHARS}."]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        line = (exc.text or "").rstrip()
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}" + (f"\n    {line}" if line else "")]

    lines = code.splitlines()

    def where(node: ast.AST) -> str:
        ln = getattr(node, "lineno", None)
        if not ln:
            return ""
        src = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
        return f"line {ln}: {src[:120]}"

    # ---- imports -------------------------------------------------------------------
    manim_star_line = vh_star_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    problems.append(f"Import of '{alias.name}' is not allowed ({where(node)}). Allowed: "
                                    "manim, visual_helpers, numpy, math, random, typing, itertools, functools, collections.")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level and node.level > 0:
                problems.append(f"Relative imports are not allowed ({where(node)}).")
            elif root not in ALLOWED_IMPORTS:
                problems.append(f"Import from '{node.module}' is not allowed ({where(node)}).")
            if node.module == "manim" and any(a.name == "*" for a in node.names) and manim_star_line is None:
                manim_star_line = node.lineno
            if node.module == "visual_helpers" and any(a.name == "*" for a in node.names) and vh_star_line is None:
                vh_star_line = node.lineno
    if manim_star_line is None:
        problems.append("The file must start with `from manim import *`.")
    if vh_star_line is None:
        problems.append("The file must contain `from visual_helpers import *` (after `from manim import *`).")
    elif manim_star_line is not None and vh_star_line < manim_star_line:
        problems.append("`from visual_helpers import *` must come after `from manim import *`.")

    # ---- banned names / attributes / calls -------------------------------------------
    seen: set[tuple[str, int]] = set()

    def flag(msg: str, node: ast.AST) -> None:
        key = (msg, getattr(node, "lineno", 0))
        if key not in seen:
            seen.add(key)
            problems.append(f"{msg} ({where(node)})")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            flag(f"`{node.id}` is not allowed: {BANNED_NAMES[node.id]}", node)
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                flag(f"`.{node.attr}` is not allowed: {BANNED_ATTRS[node.attr]}", node)
            elif node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in ALLOWED_DUNDER_ATTRS:
                flag(f"Dunder attribute access `.{node.attr}` is not allowed", node)
            elif node.attr == "frame" and _call_name(node.value) == "self.camera":
                flag("`self.camera.frame` does not exist here (ExplainerScene is not a MovingCameraScene); "
                     "do not move the camera", node)
            if isinstance(node.value, ast.Name) and node.value.id == "config" and node.attr not in CONFIG_READ_ATTRS:
                flag(f"Do not use `config.{node.attr}`; the renderer controls the configuration", node)
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in ("getattr", "setattr", "delattr", "hasattr"):
                attr = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)) or attr.value.startswith("__"):
                    flag(f"`{name}` is only allowed with a literal, non-dunder attribute name", node)
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("dim", "undim") and \
                    not (isinstance(node.func.value, ast.Name) and node.func.value.id in ("self",)):
                flag(f"`.{node.func.attr}(...)` is not a method (Mobject.dim is a number in Manim); use "
                     f"`{node.func.attr}(mob)` or `row.{node.func.attr}_items(...)`", node)
            if name == "Code" or name.endswith(".Code"):
                if any(kw.arg in ("file_name", "code_file") for kw in node.keywords):
                    flag("`Code(file_name=...)` reads files; pass code_string instead", node)
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "config":
                flag("Do not call methods on `config`", node)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if _root_name(t) == "config" and not isinstance(t, ast.Name):
                    flag("Do not change the Manim `config` (resolution, frame rate, etc. are fixed)", node)
        elif isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and node.test.value:
                if not any(isinstance(n, ast.Break) for n in ast.walk(node)):
                    flag("`while True` loop without a break would never finish", node)
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONFIG" for t in stmt.targets):
                    flag("`CONFIG = {...}` class dicts were removed in Manim CE; use keyword arguments", stmt)

    # ---- the scene class ---------------------------------------------------------------
    scene_cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == SCENE_CLASS_NAME:
            scene_cls = node
    if scene_cls is None:
        problems.append(f"Define `class {SCENE_CLASS_NAME}({BASE_CLASS_NAME}):` at the top level of the file.")
        return problems
    base_names = {_call_name(b).split(".")[-1] for b in scene_cls.bases}
    if BASE_CLASS_NAME not in base_names:
        problems.append(f"`{SCENE_CLASS_NAME}` must inherit from `{BASE_CLASS_NAME}` (not Scene).")
    methods = {n.name: n for n in scene_cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in sorted(FORBIDDEN_OVERRIDES & set(methods)):
        problems.append(f"Do not override `{name}` in {SCENE_CLASS_NAME}; ExplainerScene relies on it "
                        f"(line {methods[name].lineno}).")
    construct = methods.get("construct")
    if construct is None:
        problems.append(f"`{SCENE_CLASS_NAME}` must define `def construct(self):`.")
        return problems

    # ---- scene blocks ------------------------------------------------------------------
    indices: list[int] = []
    block_calls: list[ast.Call] = []
    for node in ast.walk(construct):
        if isinstance(node, ast.Call) and _is_scene_block_call(node):
            block_calls.append(node)
    with_blocks: set[int] = set()
    for node in ast.walk(construct):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if _is_scene_block_call(item.context_expr):
                    with_blocks.add(id(item.context_expr))
                    for inner in ast.walk(node):
                        if inner is not node and isinstance(inner, (ast.With, ast.AsyncWith)) and any(
                            _is_scene_block_call(i.context_expr) for i in inner.items
                        ):
                            flag("scene_block blocks cannot be nested", inner)
    for call in block_calls:
        if id(call) not in with_blocks:
            flag("Use `with self.scene_block(...):` (a with-statement), not a plain call", call)
        idx = _block_index(call)
        if idx is None:
            flag("scene_block index must be a literal integer", call)
        else:
            indices.append(idx)
    dupes = sorted({i for i in indices if indices.count(i) > 1})
    if dupes:
        problems.append(f"scene_block index used more than once: {dupes}")
    if storyboard is not None:
        expected = [s.index for s in storyboard.scenes]
        missing = [i for i in expected if i not in indices]
        extra = [i for i in indices if i not in expected]
        if missing:
            problems.append(f"Missing scene_block for storyboard scene index {missing}; write exactly one "
                            f"`with self.scene_block(...)` per scene ({len(expected)} total).")
        if extra:
            problems.append(f"scene_block index {extra} is not in the storyboard (valid: {expected}).")
        if not missing and not extra and indices != sorted(indices):
            problems.append("scene_blocks must appear in storyboard order (index 0, 1, 2, ...).")
    elif not indices:
        problems.append("No `with self.scene_block(...)` blocks found in construct().")

    # ---- play/wait outside blocks in construct ---------------------------------------------
    def visit(node: ast.AST, inside: bool) -> None:
        for child in ast.iter_child_nodes(node):
            child_inside = inside
            if isinstance(child, (ast.With, ast.AsyncWith)) and any(_is_scene_block_call(i.context_expr) for i in child.items):
                child_inside = True
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call) and not child_inside and _call_name(child.func) in (
                "self.play", "self.wait", "self.clear_content", "self.pause", "self.wait_until"
            ):
                flag("Every self.play/self.wait in construct() must be inside a `with self.scene_block(...)` block", child)
            visit(child, child_inside)

    visit(construct, False)
    return problems


# --------------------------------------------------------------------------------------
# LLM calls
# --------------------------------------------------------------------------------------


class _ProgressTracker:
    """Turns streamed code into "Animating scene k of n" progress updates."""

    def __init__(self, total: int, on_progress: ProgressCallback, status: JobStatus, verb: str) -> None:
        self.total = max(total, 1)
        self.on_progress = on_progress
        self.status = status
        self.verb = verb
        self.last: Optional[str] = None

    def emit(self, detail: str) -> None:
        if detail != self.last:
            self.last = detail
            try:
                self.on_progress(self.status, detail)
            except Exception:  # pragma: no cover - progress must never break generation
                log.exception("progress callback failed")

    def on_output_start(self) -> None:
        self.emit("Writing the animation code")

    def on_text(self, snapshot: str) -> None:
        count = snapshot.count("self.scene_block(")
        if count <= 0:
            return
        k = min(count, self.total)
        self.emit(f"{self.verb} scene {k} of {self.total}")


def generate_manim_code(storyboard: Storyboard, *, on_progress: ProgressCallback = noop_progress) -> str:
    """Ask the model for the full Manim file. Raises LLMError / CodeGenerationError."""
    s = get_settings()
    tracker = _ProgressTracker(len(storyboard.scenes), on_progress, JobStatus.GENERATING_CODE, "Animating")
    tracker.emit("Planning the animations")
    result = llm.generate_text(
        system=CODEGEN_SYSTEM_PROMPT,
        messages=build_codegen_message(storyboard),
        purpose="animation code",
        model=s.codegen_model,
        effort=s.codegen_effort,
        max_tokens=CODEGEN_MAX_TOKENS,
        on_output_start=tracker.on_output_start,
        on_text=tracker.on_text,
    )
    return extract_code(result.text)


def repair_manim_code(
    storyboard: Storyboard,
    code: str,
    error: str,
    attempt: int,
    *,
    stage: str = "render",
    on_progress: ProgressCallback = noop_progress,
    max_attempts: int | None = None,
) -> str:
    """Ask the model to fix `code` given a focused error excerpt. Returns the full corrected file."""
    s = get_settings()
    total = max_attempts or (1 + s.max_repair_attempts)
    detail = f"Fixing an animation issue (attempt {attempt + 1} of {total})"

    def progress(status: JobStatus, _detail: Optional[str] = None) -> None:
        on_progress(JobStatus.REPAIRING, detail)

    progress(JobStatus.REPAIRING)
    result = llm.generate_text(
        system=CODEGEN_SYSTEM_PROMPT,
        messages=build_repair_message(storyboard, code, error, attempt, stage),
        purpose="animation code repair",
        model=s.codegen_model,
        effort=s.repair_effort,
        max_tokens=CODEGEN_MAX_TOKENS,
    )
    return extract_code(result.text)
