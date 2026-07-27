"""Ritmo visual: espaçamento, alinhamento e os limites da tela.

Spacing is the part of a design system that decays first, because every
individual deviation is defensible and invisible on its own. These tests hold
the three decisions that were being contradicted in practice: one spacing
scale, one place where the product stops being a desktop, and one owner for the
distance between two elements.

Read as design documentation that fails the build when the design changes by
accident rather than on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLESHEETS = ("base.css", "editor.css", "charts.css", "markdown.css")

# The 4px scale, in the two units it is written in.
SPACING_TOKENS = {
    "0.25rem": 4, "0.5rem": 8, "0.75rem": 12, "1rem": 16,
    "1.5rem": 24, "2rem": 32, "3rem": 48, "4rem": 64,
}


def css(app, name="base.css") -> str:
    return (Path(app.root_path) / "static" / "css" / name).read_text(encoding="utf-8")


def rule_for(source: str, selector: str) -> str:
    """The declaration block of ``selector``, or "" when it is absent."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source)
    return match.group(1) if match else ""


class TestOneBreakpoint:
    """Where the product stops being a desktop is one decision, not two."""

    def test_the_editor_and_the_shell_narrow_at_the_same_width(self, app):
        """The shell goes off-canvas and the editor drops its second pane.

        With those on different thresholds the product had a 60px-wide window
        where it was half phone and half desktop - the answer to "is this
        screen narrow?" depended on which part of the screen was asking.
        """
        widths = set()
        for name in STYLESHEETS:
            widths.update(re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", css(app, name)))

        ordered = sorted(int(width) for width in widths)
        for smaller, larger in zip(ordered, ordered[1:]):
            assert larger - smaller > 100, (
                f"breakpoints {smaller}px e {larger}px estão perto demais para "
                "serem decisões diferentes"
            )


class TestTheEditorPanesAgree:
    """Split view shows one document twice; it must look like one document."""

    def test_both_panes_share_the_same_horizontal_inset(self, app):
        source = css(app, "editor.css")

        writing = rule_for(source, ".editor-textarea")
        reading = rule_for(source, ".editor-preview-inner")

        def sides(block: str) -> str:
            padding = re.search(r"padding:\s*([^;]+)", block)
            assert padding, block
            return padding.group(1).split()[1]

        assert sides(writing) == sides(reading), (
            "o mesmo parágrafo começa a distâncias diferentes nos dois painéis"
        )


class TestOneOwnerForEachGap:
    """A gap is set by the container or by the child - never by both."""

    def test_a_field_inside_a_stack_does_not_add_its_own_margin(self, app):
        source = css(app)

        assert re.search(r"\.stack\s*>\s*\.field\s*\{[^}]*margin-bottom:\s*0", source), (
            "campo dentro de um stack soma a própria margem ao gap do container"
        )

    def test_the_form_grid_still_owns_its_rows(self, app):
        """The exception, stated out loud: here the child is the owner.

        `.form-grid` zeroes its own row gap precisely so the field margin
        provides the vertical rhythm; a future cleanup that removes the field
        margin has to fix this rule in the same commit.
        """
        assert "gap: 0 var(--sp-4)" in rule_for(css(app), ".form-grid")


class TestSpacingComesFromTheScale:
    """Padding, margin and gap are spelled with tokens, not with numbers."""

    # Optical corrections that are not spacing: hairlines, off-screen parking,
    # icon-sized nudges and the pill paddings that predate the scale.
    ALLOWED_LITERALS = {
        "-1px", "-2px", "-4px", "2px", "3px", "5px", "6px", "7px", "8px", "10px",
        "-100px", "0.1rem", "0.15rem", "0.3rem", "0.4rem", "0.55rem",
        "2.375rem", "5rem", "5.5rem",
    }

    def test_no_new_spacing_literals_creep_in(self, app):
        """`em` is exempt: rendered markdown scales with the reading size."""
        pattern = re.compile(
            r"(?<![-\w])(?:margin|padding|gap|row-gap|column-gap)"
            r"(?:-(?:top|right|bottom|left|inline|block))?\s*:\s*([^;{}]+)"
        )
        offenders: list[str] = []

        for name in STYLESHEETS:
            for declaration in pattern.findall(css(app, name)):
                if "var(--sp" in declaration or "var(--radius" in declaration:
                    continue
                for amount, unit in re.findall(r"(-?\d*\.?\d+)(px|rem)", declaration):
                    literal = f"{amount}{unit}"
                    if literal in self.ALLOWED_LITERALS or amount == "0":
                        continue
                    offenders.append(f"{name}: {literal} em “{declaration.strip()}”")

        assert not offenders, "espaçamento fora da escala:\n  " + "\n  ".join(offenders)
