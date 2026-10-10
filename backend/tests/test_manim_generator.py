"""Tests for manim_generator: code extraction, static checks, prompt building, LLM calls (mocked)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import llm
from app.config import BACKEND_DIR, get_settings
from app.pipeline import manim_generator as mg
from app.pipeline.schema import Storyboard
from app.pipeline.types import JobStatus

FIXTURE_DIR = BACKEND_DIR / "manim" / "offline_demo"

# A real lesson-planner output (self-attention demo prompt), used to check prompt building
# with quotes/apostrophes in captions. Not used to generate any scene code here.
LIVE_STORYBOARD = {
    "topic": "Self-attention in Transformers",
    "title": "How Self-Attention Lets Words Use Context",
    "target_duration": 30.0,
    "learning_objective": "In self-attention, each word scores how well every word matches its query, turns those scores into weights, and becomes a weighted mix of all the words.",
    "running_example": "The 3-word sentence \"the river bank\", where bank's match scores 1.0, 3.0, 1.7 become weights 0.1, 0.7, 0.2.",
    "scenes": [
        {"index": 0, "concept": "Context changes meaning", "start_time": 0.0, "duration": 7.0,
         "caption": "Bank can mean money or a riverside, so how does the word bank use its neighbors to decide?",
         "visual_description": "Three token boxes.", "animation_instructions": ["Write the token row (2 s)."],
         "math_expressions": [], "visual_objects": ["token row"], "transition": "The token row stays."},
        {"index": 1, "concept": "Query meets keys", "start_time": 7.0, "duration": 8.0,
         "caption": "Bank sends a query, and each word's key is compared with it to give a match score.",
         "visual_description": "Arrows from the query.", "animation_instructions": ["Grow arrows (2 s)."],
         "math_expressions": [], "visual_objects": ["attention arrows"], "transition": "Arrows stay."},
        {"index": 2, "concept": "Softmax makes weights", "start_time": 15.0, "duration": 8.0,
         "caption": "Softmax turns the scores into weights that add to 1, so river gets 70% of bank's attention.",
         "visual_description": "Scores become weights.", "animation_instructions": ["Transform scores (2 s)."],
         "math_expressions": [], "visual_objects": ["weights"], "transition": "Weights stay."},
        {"index": 3, "concept": "A weighted mix", "start_time": 23.0, "duration": 7.0,
         "caption": "Bank's new meaning is a weighted mix of all words, and every word does this at once.",
         "visual_description": "A mixed box.", "animation_instructions": ["Fill the box (2.5 s)."],
         "math_expressions": ["z = \\sum_{i} w_{i} v_{i}"], "visual_objects": ["mix equation"],
         "transition": "The video ends."},
    ],
}


@pytest.fixture
def fixture_storyboard() -> Storyboard:
    return Storyboard.model_validate_json((FIXTURE_DIR / "storyboard.json").read_text())


@pytest.fixture
def fixture_code() -> str:
    return (FIXTURE_DIR / "scene.py").read_text()


def minimal_code(storyboard: Storyboard, body: str = "self.wait(1)", extra_imports: str = "", header: str | None = None) -> str:
    blocks = "\n".join(f"        {mg.block_header(s)}\n            {body}" for s in storyboard.scenes)
    head = header if header is not None else "from manim import *\nfrom visual_helpers import *\n"
    return f"{head}{extra_imports}\n\nclass ExplainerVideo(ExplainerScene):\n    def construct(self):\n{blocks}\n"


# --------------------------------------------------------------------------------------
# extract_code
# --------------------------------------------------------------------------------------


def test_extract_single_block_with_prose_around():
    text = "Here you go:\n```python\nfrom manim import *\nclass ExplainerVideo(ExplainerScene):\n    pass\n```\nEnjoy!"
    code = mg.extract_code(text)
    assert code.startswith("from manim import *")
    assert "Enjoy" not in code and "```" not in code


def test_extract_prefers_block_with_scene_class():
    text = "```python\nprint('helper')\n```\n\n```python\nfrom manim import *\nclass ExplainerVideo(ExplainerScene):\n    x = 1\n```"
    assert "class ExplainerVideo" in mg.extract_code(text)


def test_extract_accepts_plain_fence_and_raw_code():
    assert "class ExplainerVideo" in mg.extract_code("```\nimport x\nclass ExplainerVideo(ExplainerScene): pass\n```")
    raw = "from manim import *\nclass ExplainerVideo(ExplainerScene):\n    pass"
    assert mg.extract_code(raw).strip() == raw


@pytest.mark.parametrize("text", ["", "   ", "I cannot help with that.", "```python\nfrom manim import *\nclass Explainer"])
def test_extract_errors(text):
    with pytest.raises(mg.CodeGenerationError):
        mg.extract_code(text)


# --------------------------------------------------------------------------------------
# static_check: accept
# --------------------------------------------------------------------------------------


def test_offline_fixture_passes_static_check(fixture_storyboard, fixture_code):
    assert mg.static_check(fixture_code, fixture_storyboard) == []


def test_prompt_example_passes_static_check():
    prompt = mg.CODEGEN_SYSTEM_PROMPT
    m = re.search(r"# Worked example.*?```python\n(.*?)```", prompt, re.DOTALL)
    assert m, "worked example missing from the codegen prompt"
    code = m.group(1)
    assert mg.static_check(code) == []
    assert code.count("self.scene_block(") == 3


def test_minimal_code_passes(fixture_storyboard):
    assert mg.static_check(minimal_code(fixture_storyboard), fixture_storyboard) == []


def test_helpers_and_allowed_imports_pass(fixture_storyboard):
    code = minimal_code(fixture_storyboard, extra_imports="import numpy as np\nimport math, random\nfrom typing import List\n")
    code += "\n\ndef helper(x):\n    return x * 2\n"
    assert mg.static_check(code, fixture_storyboard) == []


# --------------------------------------------------------------------------------------
# static_check: reject
# --------------------------------------------------------------------------------------


def _problems(storyboard: Storyboard, **kw) -> str:
    return "\n".join(mg.static_check(minimal_code(storyboard, **kw), storyboard))


@pytest.mark.parametrize(
    "extra, needle",
    [
        ("import os", "os"),
        ("import subprocess", "subprocess"),
        ("from os import path", "os"),
        ("import socket", "socket"),
        ("import requests", "requests"),
        ("from pathlib import Path", "pathlib"),
        ("import importlib", "importlib"),
        ("import ctypes", "ctypes"),
    ],
)
def test_rejects_disallowed_imports(fixture_storyboard, extra, needle):
    out = _problems(fixture_storyboard, extra_imports=extra + "\n")
    assert needle in out and "not allowed" in out


@pytest.mark.parametrize(
    "body, needle",
    [
        ("open('/etc/passwd').read()", "open"),
        ("eval('1+1')", "eval"),
        ("exec('x=1')", "exec"),
        ("__import__('os')", "__import__"),
        ("x = ().__class__.__bases__[0].__subclasses__()", "Dunder"),
        ("config.frame_rate = 60", "config"),
        ("config['pixel_width'] = 100", "config"),
        ("config.update({})", "config"),
        ("self.add(ImageMobject('cat.png'))", "ImageMobject"),
        ("self.add(SVGMobject('logo.svg'))", "SVGMobject"),
        ("self.play(ShowCreation(Square()))", "Create"),
        ("t = TextMobject('hi')", "TextMobject"),
        ("g = Axes().get_graph(lambda x: x)", "get_graph"),
        ("self.add_sound('x.wav')", "audio"),
        ("getattr(self, '__dict__')", "getattr"),
        ("self.camera.frame.animate.scale(2)", "camera"),
        ("os.system('ls')", "os"),
        ("row = make_token_row(['a']); self.play(row.dim(0))", "dim_items"),
    ],
)
def test_rejects_dangerous_or_deprecated_code(fixture_storyboard, body, needle):
    out = _problems(fixture_storyboard, body=body)
    assert out, f"expected a problem for {body!r}"
    assert needle.lower() in out.lower()


def test_rejects_syntax_error(fixture_storyboard):
    probs = mg.static_check("from manim import *\nclass ExplainerVideo(ExplainerScene:\n  pass", fixture_storyboard)
    assert len(probs) == 1 and probs[0].startswith("SyntaxError on line 2")


def test_rejects_missing_class_and_wrong_base(fixture_storyboard):
    assert any("Define `class ExplainerVideo" in p for p in mg.static_check("from manim import *\nfrom visual_helpers import *\nx = 1\n"))
    code = minimal_code(fixture_storyboard).replace("ExplainerVideo(ExplainerScene)", "ExplainerVideo(Scene)")
    assert any("must inherit from `ExplainerScene`" in p for p in mg.static_check(code, fixture_storyboard))


def test_rejects_import_order_and_missing_helpers(fixture_storyboard):
    swapped = minimal_code(fixture_storyboard, header="from visual_helpers import *\nfrom manim import *\n")
    assert any("must come after" in p for p in mg.static_check(swapped, fixture_storyboard))
    missing = minimal_code(fixture_storyboard, header="from manim import *\n")
    assert any("from visual_helpers import *" in p for p in mg.static_check(missing, fixture_storyboard))


def test_rejects_block_count_and_order_problems(fixture_storyboard):
    code = minimal_code(fixture_storyboard)
    # Drop the last block.
    last = mg.block_header(fixture_storyboard.scenes[-1])
    dropped = code.replace(f"        {last}\n            self.wait(1)\n", "")
    assert any("Missing scene_block" in p and "[3]" in p for p in mg.static_check(dropped, fixture_storyboard))
    # Duplicate an index.
    dup = code.replace("index=3,", "index=2,")
    out = "\n".join(mg.static_check(dup, fixture_storyboard))
    assert "more than once" in out
    # Out of order.
    h0, h1 = mg.block_header(fixture_storyboard.scenes[0]), mg.block_header(fixture_storyboard.scenes[1])
    swapped = code.replace(h0, "@@H0@@").replace(h1, h0).replace("@@H0@@", h1)
    assert any("storyboard order" in p for p in mg.static_check(swapped, fixture_storyboard))
    # Non-literal index.
    assert any("literal integer" in p for p in mg.static_check(code.replace("index=0,", "index=i,"), fixture_storyboard))


def test_rejects_play_outside_block_nested_blocks_and_overrides(fixture_storyboard):
    code = minimal_code(fixture_storyboard).replace("    def construct(self):\n", "    def construct(self):\n        self.play(FadeIn(Dot()))\n")
    assert any("inside a `with self.scene_block" in p for p in mg.static_check(code, fixture_storyboard))
    nested = minimal_code(fixture_storyboard, body="with self.scene_block(index=9, concept='x', caption='y', duration=5):\n                self.wait(1)")
    assert any("nested" in p for p in mg.static_check(nested, fixture_storyboard))
    override = minimal_code(fixture_storyboard) + "\n    def setup(self):\n        pass\n"
    override = override.replace("\n\n    def setup", "\n    def setup")
    assert any("Do not override `setup`" in p for p in mg.static_check(override, fixture_storyboard))


def test_rejects_infinite_loop(fixture_storyboard):
    assert any("while True" in p for p in mg.static_check(minimal_code(fixture_storyboard, body="while True:\n                pass"), fixture_storyboard))
    ok = minimal_code(fixture_storyboard, body="while True:\n                break")
    assert mg.static_check(ok, fixture_storyboard) == []


# --------------------------------------------------------------------------------------
# Prompt building
# --------------------------------------------------------------------------------------


def test_block_headers_are_valid_python_with_verbatim_values():
    sb = Storyboard.model_validate(LIVE_STORYBOARD)
    msg = mg.build_codegen_message(sb)
    for scene in sb.scenes:
        header = mg.block_header(scene)
        assert header in msg
        call = compile(header.removeprefix("with ").removesuffix(":"), "<h>", "eval")
        # Evaluate the header's arguments to check they round-trip exactly.
        captured = {}
        eval(call, {"self": SimpleNamespace(scene_block=lambda **kw: captured.update(kw))})
        assert captured == {"index": scene.index, "concept": scene.concept, "caption": scene.caption, "duration": scene.duration}
    assert sb.running_example in msg
    assert '"math_expressions"' in msg


def test_system_prompt_is_stable_and_neutral():
    p1 = (BACKEND_DIR / "app" / "pipeline" / "prompts" / "codegen_system.md").read_text()
    assert p1 == mg.CODEGEN_SYSTEM_PROMPT
    lowered = p1.lower()
    for topic in ("self-attention", "gradient descent", "binary search", "neural network", "transformer"):
        assert topic not in lowered, f"prompt example must not bias toward {topic!r}"
    assert "v0.19.2" in p1 and "```python" in p1


# --------------------------------------------------------------------------------------
# LLM calls (mocked)
# --------------------------------------------------------------------------------------


def _fake_result(text: str) -> llm.LLMResult:
    return llm.LLMResult(text=text, model="fake", stop_reason="end_turn", duration_s=0.1)


def test_generate_reports_real_progress_and_uses_cached_prompt(monkeypatch, fixture_storyboard, fixture_code):
    calls = []

    def fake_generate_text(**kw):
        calls.append(kw)
        kw["on_output_start"]()
        answer = f"```python\n{fixture_code}```"
        # Stream the answer in chunks, as the real client does (accumulated snapshots).
        for end in range(200, len(answer) + 200, 200):
            kw["on_text"](answer[:end])
        return _fake_result(answer)

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)
    events = []
    code = mg.generate_manim_code(fixture_storyboard, on_progress=lambda s, d=None: events.append((s, d)))
    assert code.strip() == fixture_code.strip()
    kw = calls[0]
    assert kw["system"] is mg.CODEGEN_SYSTEM_PROMPT
    assert kw["purpose"] == "animation code"
    assert kw["max_tokens"] >= 30000
    assert kw["effort"] == get_settings().codegen_effort
    details = [d for s, d in events if s == JobStatus.GENERATING_CODE]
    assert details[0] == "Planning the animations"
    assert "Writing the animation code" in details
    scene_msgs = [d for d in details if d.startswith("Animating scene")]
    assert scene_msgs == [f"Animating scene {k} of 4" for k in range(1, 5)]


def test_generate_raises_when_no_code(monkeypatch, fixture_storyboard):
    monkeypatch.setattr(llm, "generate_text", lambda **kw: _fake_result("Sorry, here is an explanation instead."))
    with pytest.raises(mg.CodeGenerationError):
        mg.generate_manim_code(fixture_storyboard)


def test_repair_sends_code_and_error_and_uses_repair_effort(monkeypatch, fixture_storyboard, fixture_code):
    calls = []

    def fake_generate_text(**kw):
        calls.append(kw)
        return _fake_result("```python\nfrom manim import *\nclass ExplainerVideo(ExplainerScene):\n    pass\n```")

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)
    events = []
    out = mg.repair_manim_code(fixture_storyboard, fixture_code, "NameError: name 'foo' is not defined", 1,
                               stage="render", on_progress=lambda s, d=None: events.append((s, d)))
    assert "class ExplainerVideo" in out
    kw = calls[0]
    assert kw["system"] is mg.CODEGEN_SYSTEM_PROMPT  # same cached prefix as generation
    assert kw["effort"] == "medium"
    assert kw["purpose"] == "animation code repair"
    msg = kw["messages"]
    assert "NameError: name 'foo' is not defined" in msg
    assert fixture_code.strip()[:200] in msg
    assert "repair attempt 1" in msg
    assert events and events[0] == (JobStatus.REPAIRING, "Fixing an animation issue (attempt 2 of 3)")
