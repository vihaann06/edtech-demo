"""Tests for the Storyboard contract shared by the lesson planner and the Manim generator."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.pipeline.schema import Storyboard, StoryboardScene


def scene(i: int, start: float, duration: float, **overrides: Any) -> dict:
    data = {
        "index": i,
        "concept": f"Idea {i + 1}",
        "start_time": start,
        "duration": duration,
        "caption": "Each step builds on the picture already on screen.",
        "visual_description": "Boxes and arrows.",
        "animation_instructions": ["Fade in the boxes", "Draw arrows"],
        "math_expressions": [],
        "visual_objects": ["token boxes"],
        "transition": "fade",
    }
    data.update(overrides)
    return data


def storyboard(durations=(7.0, 8.0, 8.0, 7.0), **overrides: Any) -> dict:
    scenes, start = [], 0.0
    for i, d in enumerate(durations):
        scenes.append(scene(i, start, d))
        start += d
    data = {
        "topic": "self-organizing lists",
        "title": "A Sample Lesson",
        "target_duration": 30.0,
        "learning_objective": "Understand the one idea of this lesson.",
        "scenes": scenes,
    }
    data.update(overrides)
    return data


def test_valid_storyboard_and_seeded_shape_still_validate() -> None:
    sb = Storyboard.model_validate(storyboard())
    assert sb.total_duration == 30.0
    assert sb.scenes[1].end_time == 15.0
    # The originally seeded shape (no running_example, terse fields) must keep validating.
    seeded = storyboard(durations=(10.0, 10.0, 10.0))
    assert Storyboard.model_validate(seeded).running_example == ""


def test_round_trips_through_json() -> None:
    sb = Storyboard.model_validate(storyboard(running_example="The list [3, 1, 2]."))
    again = Storyboard.model_validate_json(sb.model_dump_json())
    assert again == sb


def test_unknown_fields_are_ignored_for_forward_compatibility() -> None:
    data = storyboard()
    data["future_field"] = 1
    data["scenes"][0]["end_time"] = 7.0
    Storyboard.model_validate(data)


@pytest.mark.parametrize("count", [2, 5])
def test_scene_count_is_3_to_4(count: int) -> None:
    with pytest.raises(ValidationError):
        Storyboard.model_validate(storyboard(durations=[30.0 / count] * count))


def test_timeline_must_be_contiguous() -> None:
    data = storyboard()
    data["scenes"][2]["start_time"] = 16.0
    with pytest.raises(ValidationError, match=r"scenes\[2\] starts at 16.00s"):
        Storyboard.model_validate(data)


def test_indexes_must_be_sequential() -> None:
    data = storyboard()
    data["scenes"][1]["index"] = 5
    with pytest.raises(ValidationError, match="indexes must be"):
        Storyboard.model_validate(data)


def test_total_duration_must_match_target() -> None:
    with pytest.raises(ValidationError, match="add up to 36.0s"):
        Storyboard.model_validate(storyboard(durations=(9.0, 9.0, 9.0, 9.0)))


def test_caption_must_be_readable_in_time() -> None:
    data = storyboard(durations=(5.0, 9.0, 8.0, 8.0))
    data["scenes"][0]["caption"] = "a" * 90  # 18 chars/s in a 5 s scene
    with pytest.raises(ValidationError, match=r"scenes\[0\] caption has 90 characters"):
        Storyboard.model_validate(data)


def test_caption_whitespace_is_collapsed_before_length_checks() -> None:
    data = storyboard()
    data["scenes"][0]["caption"] = "  Each   step\nbuilds on the picture.  " + " " * 200
    sb = Storyboard.model_validate(data)
    assert sb.scenes[0].caption == "Each step builds on the picture."


def test_caption_rejects_latex() -> None:
    data = storyboard()
    data["scenes"][0]["caption"] = "The weight \\alpha grows each step."
    with pytest.raises(ValidationError, match="plain text"):
        Storyboard.model_validate(data)


def test_caption_allows_symbols_like_currency() -> None:
    data = storyboard()
    data["scenes"][0]["caption"] = "At 10% a year, $100 grows to $110 & keeps going."
    Storyboard.model_validate(data)


@pytest.mark.parametrize(
    "expr, ok",
    [
        ("\\frac{a}{b}", True),
        ("\\{x \\mid x > 0\\}", True),
        ("\\$100 \\times 1.1^{n}", True),
        ("x^2 \\\\ y", True),
        ("$x^2$", False),
        ("\\frac{a}{b", False),
        ("a}{b", False),
        ("", False),
        ("x" * 101, False),
    ],
)
def test_math_expressions_are_mathtex_ready(expr: str, ok: bool) -> None:
    data = storyboard()
    data["scenes"][0]["math_expressions"] = [expr]
    if ok:
        Storyboard.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            Storyboard.model_validate(data)


def test_list_limits() -> None:
    base = storyboard()
    too_many_beats = copy.deepcopy(base)
    too_many_beats["scenes"][0]["animation_instructions"] = [f"Beat {i}" for i in range(7)]
    blank_beat = copy.deepcopy(base)
    blank_beat["scenes"][0]["animation_instructions"] = ["Draw", "   "]
    too_many_math = copy.deepcopy(base)
    too_many_math["scenes"][0]["math_expressions"] = ["a", "b", "c", "d"]
    too_many_objects = copy.deepcopy(base)
    too_many_objects["scenes"][0]["visual_objects"] = [f"obj {i}" for i in range(9)]
    for data in (too_many_beats, blank_beat, too_many_math, too_many_objects):
        with pytest.raises(ValidationError):
            Storyboard.model_validate(data)


def test_required_text_fields_must_not_be_blank() -> None:
    for field, value in (("topic", " "), ("learning_objective", "short"), ("title", "  ")):
        with pytest.raises(ValidationError):
            Storyboard.model_validate(storyboard(**{field: value}))
    data = storyboard()
    data["scenes"][0]["visual_description"] = "  "
    with pytest.raises(ValidationError):
        Storyboard.model_validate(data)


def test_scene_model_is_usable_on_its_own() -> None:
    s = StoryboardScene.model_validate(scene(0, 0.0, 7.5))
    assert s.end_time == 7.5
