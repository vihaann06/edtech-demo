"""Docker-rendered tests for the design system (visual_helpers runs only inside the sandbox).

Each test renders a small probe scene at low resolution and checks the machine-readable
EXPLAINER_* lines (and PROBE lines the scene prints itself).
"""

from __future__ import annotations

import ast
import json
import shutil

import pytest

from app.config import BACKEND_DIR
from app.pipeline import renderer

HELPERS = BACKEND_DIR / "manim" / "visual_helpers.py"
HEADER = "from manim import *\nfrom visual_helpers import *\nimport json\n\n"


def test_helpers_module_is_valid_python_and_exports_documented_api():
    """Host-side check (manim is not installed on the host, so parse instead of import)."""
    tree = ast.parse(HELPERS.read_text())
    exported = None
    defined = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            exported = [e.value for e in node.value.elts]
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                elts = t.elts if isinstance(t, ast.Tuple) else [t]
                defined.update(e.id for e in elts if isinstance(e, ast.Name))
    assert exported, "__all__ missing"
    missing = [name for name in exported if name not in defined]
    assert not missing, f"exported but not defined: {missing}"
    prompt = (BACKEND_DIR / "app" / "pipeline" / "prompts" / "codegen_system.md").read_text()
    for name in ["make_label", "make_equation", "make_token_row", "make_array", "make_pointer", "move_pointer",
                 "make_arrow", "reweight", "make_graph", "make_axes", "plot_function", "dot_on_graph", "make_tangent",
                 "make_bar_chart", "make_segment_bar", "make_callout", "stagger_in", "pulse", "dim", "undim",
                 "count_to", "set_chip_text", "fit_in", "clear_content"]:
        assert name in exported or name == "clear_content"
        assert f"`{name}(" in prompt or f".{name}(" in prompt, f"{name} is not documented in the codegen prompt"


@pytest.fixture(scope="module")
def sandbox():
    renderer._status_cache = None
    st = renderer.sandbox_status(refresh=True)
    if not (st["docker_available"] and st["image_present"]):
        pytest.skip("Docker or the renderer image is not available")


@pytest.fixture
def work_dir(request, sandbox):
    d = BACKEND_DIR / ".jobs" / f"pytest-agent3-vh-{request.node.name}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def manifest(*durations: float, title: str = "Probe Lesson") -> str:
    scenes = [{"index": i, "concept": f"Concept {i}", "caption": f"Caption number {i} for the probe.", "duration": d}
              for i, d in enumerate(durations)]
    return json.dumps({"title": title, "scenes": scenes})


def render(code: str, work_dir, sb: str | None, fps: int = 30):
    r = renderer.render(code, work_dir, storyboard_json=sb, width=640, height=360, fps=fps, timeout_s=240)
    assert r.success, r.log_text[-4000:]
    probes = {}
    for line in r.log_text.splitlines():
        if line.startswith("PROBE "):
            probes.update(json.loads(line[6:]))
    return r, probes


@pytest.mark.docker
def test_timing_is_exact_whatever_the_code_does(work_dir):
    code = HEADER + '''
class ExplainerVideo(ExplainerScene):
    def construct(self):
        with self.scene_block(index=0, concept="Concept 0", caption="Caption number 0 for the probe.", duration=2.0):
            for i in range(3):  # 4.5 s requested in a 2 s block
                self.play(FadeIn(Square().shift(RIGHT * (i - 1) * 2.5)), run_time=1.5)
            self.wait(5)
        self.play(FadeIn(Circle()), run_time=0.5)  # outside any block: counts toward the next one
        with self.scene_block(index=1, concept="Concept 1", caption="A caption that differs from the storyboard.", duration=9.0):
            self.play(Create(Triangle()), run_time=0.01)  # sub-frame
            self.play(FadeIn(Dot()), run_time=0)          # zero run time
            self.wait(0.37)                               # not a whole number of frames
        # scene 2 is missing on purpose: the video must still be padded to the storyboard total
'''
    r, _ = render(code, work_dir, manifest(2.0, 3.0, 2.5))
    tl = {e["index"]: e for e in r.timeline}
    assert tl[0]["actual"] == 2.0 and tl[0]["end"] == 2.0
    assert tl[0]["clamped_calls"] >= 2 and tl[0]["skipped_calls"] >= 1
    assert tl[1]["planned"] == 3.0  # storyboard duration wins over the code's 9.0
    assert tl[1]["end"] == 5.0      # absolute end on target despite the out-of-block play
    assert r.summary["frames"] == int(7.5 * 30)
    assert r.summary["outside_block_seconds"] == 0.5
    messages = " ".join(w["message"] for w in r.warnings)
    assert "caption differs" in messages and "duration differs" in messages and "never ran" in messages


@pytest.mark.docker
def test_plays_after_the_last_block_add_no_frames(work_dir):
    code = HEADER + '''
class ExplainerVideo(ExplainerScene):
    def construct(self):
        with self.scene_block(index=0, concept="Concept 0", caption="Caption number 0 for the probe.", duration=1.0):
            self.play(FadeIn(Square()))
        self.play(FadeOut(Square()), run_time=3)
        self.wait(4)
'''
    r, _ = render(code, work_dir, manifest(1.0))
    assert r.summary["frames"] == 30 and r.timeline[0]["end"] == 1.0


@pytest.mark.docker
def test_chrome_layout_guard_and_forgiving_calls(work_dir):
    code = HEADER + '''
class ExplainerVideo(ExplainerScene):
    def construct(self):
        with self.scene_block(index=0, concept="Concept 0", caption="Caption number 0 for the probe.", duration=4.0):
            low = make_label("I was put in the caption band").to_edge(DOWN)
            high = make_label("I was put in the header").to_edge(UP)
            wide = VGroup(*[Square(side_length=1.5) for _ in range(12)]).arrange(RIGHT)
            self.play(FadeIn(low), FadeIn(high))
            self.play(Create(wide))
            moved = Dot().move_to(ORIGIN)
            self.add(moved)
            self.play(moved.animate.shift(DOWN * 3.2))  # target in the caption band -> pulled back
            area = self.content_area
            print("PROBE " + json.dumps({
                "low_bottom_ok": bool(low.get_bottom()[1] >= area.bottom - 0.11),
                "high_top_ok": bool(high.get_top()[1] <= area.top + 0.11),
                "wide_fits": bool(wide.width <= area.width + 0.11),
                "dot_ok": bool(moved.get_bottom()[1] >= area.bottom - 0.11),
            }), flush=True)
            bad = make_equation(r"\\frac{1}{", r"\\notacommand{x}")
            self.play(bad)  # a bare mobject: faded in instead of crashing
            self.play(*[FadeOut(m) for m in self.mobjects])  # LLM habit: must not remove header/caption
            print("PROBE " + json.dumps({"chrome_left": len(self.mobjects), "content_left": len(self.content_mobjects)}), flush=True)
            self.play(*[])  # empty play: skipped
            t = make_label("Sized text", size=26)
            alias = make_label("Alias", font_size=30)  # Manim's kwarg name works too
            keep_me, drop_me = make_chip("keep").shift(LEFT * 2), make_chip("drop").shift(RIGHT * 2)
            self.play(FadeIn(keep_me), FadeIn(drop_me), run_time=0.3)
            self.play(Wait(0.5))
            self.clear_content(keep=[keep_me])
            print("PROBE " + json.dumps({"font_size": round(t.font_size, 2), "alias_size": round(alias.font_size, 2),
                                         "kept": len(self.content_mobjects)}), flush=True)
'''
    r, probes = render(code, work_dir, manifest(4.0))
    assert probes["low_bottom_ok"] and probes["high_top_ok"] and probes["wide_fits"] and probes["dot_ok"]
    assert probes["content_left"] == 0 and probes["chrome_left"] >= 3  # kicker, concept, caption (+ progress)
    assert probes["font_size"] == 26.0  # oversampled text still reports the requested size
    assert probes["alias_size"] == 30.0 and probes["kept"] == 1
    assert len(r.layout_fixes) >= 3
    messages = " ".join(w["message"] for w in r.warnings)
    assert "make_equation" in messages and "Mobject was passed" in messages and "no animations" in messages
    assert r.timeline[0]["actual"] == 4.0


@pytest.mark.docker
def test_every_helper_builds_and_animates(work_dir):
    code = HEADER + '''
class ExplainerVideo(ExplainerScene):
    def construct(self):
        with self.scene_block(index=0, concept="Concept 0", caption="Caption number 0 for the probe.", duration=8.0):
            left, right = self.columns(2)
            top, bottom = self.rows(2)
            title = make_title("Title"); heading = make_heading("Heading")
            para = make_paragraph("A fairly long paragraph that should wrap onto several lines nicely.", max_width=3.0)
            bullets = make_bullets(["first point", "second point"])
            fit_in(VGroup(title, heading, para, bullets).arrange(DOWN), left)
            row = make_token_row(["the", "river", "bank"], colors=[BLUE, PURPLE, CYAN])
            arr = make_array([2, 5, 8, 12, 16], cell_size=0.6)
            grid = make_grid([[0.1, 0.7], [0.4, 0.2]], heat=True, row_labels=["a", "b"], col_labels=["x", "y"])
            fit_in(VGroup(row, arr, grid).arrange(DOWN, buff=0.4), right)
            self.play(stagger_in(row), draw_in(title), draw_in(arr[0]), draw_in(Square()), run_time=1.0)
            self.play(FadeIn(arr), FadeIn(grid), FadeIn(para), FadeIn(bullets), FadeIn(heading), run_time=0.5)
            ptr = make_pointer(arr[1], "mid", direction=UP)
            self.play(GrowArrow(ptr.arrow), FadeIn(ptr.label), run_time=0.3)
            self.play(move_pointer(ptr, arr[3]), arr.highlight(3, GREEN), arr.dim_items(0, 1), row.set_value(0, "a"), run_time=0.3)
            self.play(arr.undim_items(0), undim(arr[1]), tint(row[1], YELLOW), pulse(row[2]), run_time=0.3)
            a1 = make_arrow(row[0], row[2], weight=0.2, curve=0.6)
            a2 = make_arrow(row[0].get_center(), row[1].get_center() + UP, directed=False, dashed=True)
            lbl = make_edge_label(a1, "0.2")
            self.play(Create(a1), Create(a2), FadeIn(lbl), run_time=0.3)
            self.play(reweight(a1, 0.9, color=YELLOW), run_time=0.3)
            self.clear_content(row)
            g1 = make_graph(["A", "B", "C", "D"], [(0, 1, 0.9), (0, 2), (2, 3, 0.1)], layout="tree", directed=True)
            g2 = make_graph(4, [(0, 1), (1, 2)], layout="layers", layers=[[0], [1, 2], [3]])
            g3 = make_graph(3, [(0, 1)], layout="row"); g4 = make_graph(5, [], layout="grid")
            fit_in(VGroup(g1, g2, g3, g4).arrange_in_grid(2, 2), bottom)
            self.play(FadeIn(g1), FadeIn(g2), FadeIn(g3), FadeIn(g4), run_time=0.3)
            self.play(Indicate(g1.edge(0, 1)), Indicate(g1.node(3)), run_time=0.3)
            self.clear_content()
            axes = make_axes((-3.5, 3.5, 1), (0, 10, 2), x_label="x", y_label="y", grid=True)
            f = lambda x: x ** 2
            curve = plot_function(axes, f)
            t = ValueTracker(3)
            dot = always_redraw(lambda: dot_on_graph(axes, f, t.get_value()))
            tan = always_redraw(lambda: make_tangent(axes, curve, t.get_value()))
            fit_in(VGroup(axes, curve), left)
            self.play(Create(axes), Create(curve), FadeIn(dot), Create(tan), run_time=0.5)
            self.play(t.animate.set_value(1), run_time=0.5)
            num = make_number(1.0).next_to(axes, UP)
            chart = make_bar_chart([1, 3, 2], ["a", "b", "c"])
            seg = make_segment_bar([0.1, 0.7, 0.2], labels=["10%", "70%", "20%"])
            nl = make_number_line((0, 10, 2), length=5)
            fit_in(VGroup(chart, seg, nl).arrange(DOWN), right)
            self.play(FadeIn(num), FadeIn(chart), FadeIn(seg), Create(nl), run_time=0.4)
            self.play(count_to(num, 2.5), chart.set_values([3, 1, 2]), run_time=0.4)
            eq = make_equation("a^2", "+", "b^2", "=", "c^2", colors={"a^2": BLUE, 2: PURPLE})
            eq2 = make_equation("x = 1")
            place_near(eq, chart, UP)
            hl, ul, br = make_highlight(eq), make_underline(eq), make_brace(chart, "bars")
            call = make_callout("a note", target=chart.bar(1))
            self.play(Write(eq), Create(hl), Create(ul), FadeIn(br), FadeIn(call), run_time=0.4)
            self.play(swap(eq, eq2), flash_around(chart), fade_out(hl, ul), run_time=0.4)
            box, panel, chip, node = make_box(), make_panel(), make_chip("chip"), make_node("n")
            arrange_in_grid_fit([box, panel, chip, node], 2, 2, area=self.content_area)
            self.play(pop_in(chip), rise_in(node), set_chip_text(chip, "new"), run_time=0.3)
            print("PROBE " + json.dumps({"time_left": round(self.time_left(), 2), "inside": bool(CONTENT_AREA.contains(axes))}), flush=True)
'''
    r, probes = render(code, work_dir, manifest(8.0))
    assert probes["inside"] is True and probes["time_left"] >= 0
    assert r.timeline[0]["actual"] == 8.0
    assert not [w for w in r.warnings if "make_equation" in w["message"]]  # all LaTeX in this scene is valid
