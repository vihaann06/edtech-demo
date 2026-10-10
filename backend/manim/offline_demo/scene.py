"""Offline-demo lesson: the Pythagorean theorem with a 3-4-5 triangle.

Hand-written against the visual_helpers design system. Used only by the explicitly
labelled offline demo mode (no API key needed) to exercise the real renderer.
"""

from manim import *
from visual_helpers import *

A_LEG, B_LEG, C_HYP = 3, 4, 5


def cell_grid(centers, angle, color, fill_opacity=0.38):
    """Unit squares (side 1 in figure units) centred on `centers`, rotated by `angle`."""
    cells = VGroup()
    for center in centers:
        cell = Square(side_length=1.0, stroke_color=color, stroke_width=1.6, fill_color=color,
                      fill_opacity=fill_opacity)
        cell.rotate(angle).move_to(center)
        cells.add(cell)
    return cells


def side_row(text, note, color):
    chip = make_chip(text, color=color, size=SIZE_LABEL, width=1.9)
    label = make_label(note, size=SIZE_SMALL, color=MUTED)
    label.next_to(chip, RIGHT, buff=0.3)
    row = VGroup(chip, label)
    row.chip, row.note = chip, label
    return row


class ExplainerVideo(ExplainerScene):
    def construct(self):
        a, b, c = A_LEG, B_LEG, C_HYP
        assert a * a + b * b == c * c

        # ---- geometry in "cell" units: right angle at the origin -------------------------
        corner = np.array([0.0, 0.0, 0.0])
        top = np.array([0.0, float(a), 0.0])  # end of leg a
        right = np.array([float(b), 0.0, 0.0])  # end of leg b
        along = (top - right) / c  # unit vector along the hypotenuse
        outward = np.array([a, b, 0.0]) / c  # unit normal pointing away from the triangle
        tilt = float(np.arctan2(outward[1], outward[0]))

        a_centers = [np.array([-i - 0.5, j + 0.5, 0.0]) for j in range(a) for i in range(a)]
        b_centers = [np.array([i + 0.5, -j - 0.5, 0.0]) for j in range(b) for i in range(b)]
        c_index = [(i, j) for j in range(c) for i in range(c)]
        c_centers = {ij: right + (ij[0] + 0.5) * along + (ij[1] + 0.5) * outward for ij in c_index}

        fill = Polygon(corner, right, top, stroke_width=0, fill_color=INK, fill_opacity=0.06)
        side_a = Line(corner, top, color=BLUE, stroke_width=6)
        side_b = Line(corner, right, color=PURPLE, stroke_width=6)
        side_c = Line(right, top, color=CYAN, stroke_width=6)
        mark = VMobject(stroke_color=YELLOW, stroke_width=3)
        mark.set_points_as_corners([RIGHT * 0.42, RIGHT * 0.42 + UP * 0.42, UP * 0.42])

        cells_a = cell_grid(a_centers, 0.0, BLUE)
        cells_b = cell_grid(b_centers, 0.0, PURPLE)
        cells_c = cell_grid([c_centers[ij] for ij in c_index], tilt, CYAN, fill_opacity=0.22)
        border_a = Square(side_length=a, stroke_color=BLUE, stroke_width=3).move_to([-a / 2, a / 2, 0])
        border_b = Square(side_length=b, stroke_color=PURPLE, stroke_width=3).move_to([b / 2, -b / 2, 0])
        border_c = Square(side_length=c, stroke_color=CYAN, stroke_width=3).rotate(tilt)
        border_c.move_to(right + (along + outward) * c / 2)

        figure = VGroup(fill, cells_a, cells_b, cells_c, border_a, border_b, border_c, side_a, side_b, side_c, mark)
        left, right_col = self.columns(2, gap=0.6, ratios=[1.15, 1.0])
        fit_in(figure, left.inset(0.1, 0.05))
        unit = border_a.width / a  # scene units per cell after fitting

        # ---- scene 0 ---------------------------------------------------------------------
        with self.scene_block(index=0, concept="A right triangle", caption="A right triangle has two legs, a = 3 and b = 4, and a longest side, the hypotenuse c = 5.", duration=7.0):
            lab_a = make_label("a = 3", color=BLUE, weight=SEMIBOLD).next_to(side_a, LEFT, buff=0.25)
            lab_b = make_label("b = 4", color=PURPLE, weight=SEMIBOLD).next_to(side_b, DOWN, buff=0.25)
            lab_c = make_label("c = 5", color=CYAN, weight=SEMIBOLD)
            lab_c.move_to(side_c.get_center() + outward * 0.45)
            rows = VGroup(
                side_row("a = 3", "leg", BLUE),
                side_row("b = 4", "leg", PURPLE),
                side_row("c = 5", "hypotenuse", CYAN),
            ).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
            fit_in(rows, right_col.rows(2, ratios=[1.3, 1.0])[0])

            self.play(LaggedStart(Create(side_b), Create(side_a), Create(side_c), lag_ratio=0.55), run_time=1.5)
            self.play(FadeIn(fill), Create(mark), run_time=0.8)
            self.play(stagger_in(lab_a, lab_b, lab_c, lag_ratio=0.25), run_time=1.2)
            self.play(stagger_in(rows, lag_ratio=0.25), run_time=1.3)
            self.play(pulse(side_c, color=CYAN, scale=1.04), pulse(rows[2].chip, color=CYAN), run_time=1.0)
            self.wait(1.0)

        # ---- scene 1 ---------------------------------------------------------------------
        with self.scene_block(index=1, concept="A square on every side", caption="Build a square on each side. Counting unit cells gives areas 3² = 9, 4² = 16 and 5² = 25.", duration=8.0):
            areas = VGroup(
                make_label("9", size=SIZE_HEADING, weight=SEMIBOLD).move_to(border_a),
                make_label("16", size=SIZE_HEADING, weight=SEMIBOLD).move_to(border_b),
                make_label("25", size=SIZE_HEADING, weight=SEMIBOLD).move_to(border_c),
            )
            new_notes = VGroup(*[
                make_label(text, size=SIZE_SMALL, color=MUTED).move_to(row.note, aligned_edge=LEFT)
                for text, row in zip(["3 × 3 cells", "4 × 4 cells", "5 × 5 cells"], rows)
            ])

            self.play(FadeOut(lab_a, lab_b, lab_c), Create(border_a), Create(border_b), Create(border_c), run_time=1.0)
            self.play(LaggedStart(*[FadeIn(cell, scale=0.6) for cell in cells_a], lag_ratio=0.12), run_time=1.1)
            self.play(LaggedStart(*[FadeIn(cell, scale=0.6) for cell in cells_b], lag_ratio=0.08), run_time=1.3)
            self.play(LaggedStart(*[FadeIn(cell, scale=0.6) for cell in cells_c], lag_ratio=0.05), run_time=1.5)
            self.play(
                stagger_in(areas, lag_ratio=0.2),
                *[set_chip_text(row.chip, text) for row, text in zip(rows, ["a² = 9", "b² = 16", "c² = 25"])],
                *[swap(row.note, note) for row, note in zip(rows, new_notes)],
                run_time=1.2,
            )
            for row, note in zip(rows, new_notes):
                row.note = note
            self.wait(1.4)

        # ---- scene 2 ---------------------------------------------------------------------
        with self.scene_block(index=2, concept="Small squares fill the big one", caption="Move the 9 blue and 16 purple cells into the big square: they fill it exactly, so 9 + 16 = 25.", duration=8.0):
            # Blue 3x3 block goes into the corner of the big square; purple fills the L around it.
            corner_cells = [(i, j) for j in range(a) for i in range(a)]
            l_cells = [ij for ij in c_index if ij not in corner_cells]
            l_cells.sort(key=lambda ij: (max(ij), ij[0] - ij[1]))
            blue_targets = VGroup(*[
                Square(side_length=unit, stroke_color=BLUE, stroke_width=1.6, fill_color=BLUE, fill_opacity=0.5)
                .rotate(tilt)
                for ij in corner_cells
            ])
            purple_targets = [
                Square(side_length=unit, stroke_color=PURPLE, stroke_width=1.6, fill_color=PURPLE, fill_opacity=0.5)
                .rotate(tilt)
                for _ in l_cells
            ]
            # Target centres in scene coordinates: read them off the fitted big-square cells.
            centre_of = {ij: cells_c[k].get_center() for k, ij in enumerate(c_index)}
            for sq, ij in zip(blue_targets, corner_cells):
                sq.move_to(centre_of[ij])
            for sq, ij in zip(purple_targets, l_cells):
                sq.move_to(centre_of[ij])
            blue_copy = cells_a.copy()
            purple_copies = [cell.copy() for cell in cells_b]
            # Fly the cells nearest the big square first.
            order = sorted(range(len(purple_copies)), key=lambda k: -purple_copies[k].get_center()[0])
            total = make_equation("9", "+", "16", "=", "25", colors={0: BLUE, 2: PURPLE, 4: CYAN})
            fit_in(total, right_col.rows(2, ratios=[1.3, 1.0])[1].inset(0.4))

            self.play(cells_c.animate.set_fill(opacity=0.0), areas[2].animate.set_opacity(0.0), run_time=0.7)
            self.play(Transform(blue_copy, blue_targets), run_time=1.6)
            self.play(
                LaggedStart(*[Transform(purple_copies[k], purple_targets[n]) for n, k in enumerate(order)], lag_ratio=0.08),
                run_time=2.4,
            )
            self.play(Write(total), run_time=1.1)
            self.play(pulse(border_c, color=CYAN, scale=1.03), flash_around(total, color=CYAN), run_time=1.0)
            self.wait(1.0)

        # ---- scene 3 ---------------------------------------------------------------------
        with self.scene_block(index=3, concept="a² + b² = c²", caption="This works for every right triangle: the two leg squares together equal the hypotenuse square.", duration=7.0):
            self.clear_content(figure, blue_copy, *purple_copies, areas)
            formula = make_equation("a^2", "+", "b^2", "=", "c^2", size=64, colors={0: BLUE, 2: PURPLE, 4: CYAN})
            check = make_equation("3^2", "+", "4^2", "=", "5^2", size=SIZE_MATH, colors={0: BLUE, 2: PURPLE, 4: CYAN})
            stack = VGroup(formula, check).arrange(DOWN, buff=0.55)
            fit_in(stack, right_col.inset(0.3, 0.6))
            stack.shift(UP * 0.35)
            note = make_label("true for every right triangle", size=SIZE_SMALL, color=MUTED)
            note.next_to(stack, DOWN, buff=0.55)

            self.play(Write(formula), run_time=1.4)
            self.play(FadeIn(check, shift=UP * 0.2), run_time=1.0)
            self.play(flash_around(formula, color=YELLOW), rise_in(note), run_time=1.2)
            self.wait(2.6)
