"""Ritmo visual: espaçamento, alinhamento, empilhamento e os limites da tela.

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

STYLESHEETS = (
    "base.css", "editor.css", "charts.css", "markdown.css", "mindmap.css"
)

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


class TestTheCanvasStacksInOneOrder:
    """On the canvas, what is in front of what is one decision.

    The bug: at narrow widths the side panels stop being columns and become
    sheets over the board, but kept the z-index they had as columns. The
    toolbar floating on the board was drawn on top of the sheet and clipped
    the first characters of every topic in the outline.
    """

    def layers(self, app) -> dict[str, int]:
        source = css(app, "mindmap.css")
        return {
            name: int(value)
            for name, value in re.findall(r"--mm-z-([a-z]+):\s*(\d+)", source)
        }

    def test_every_layer_is_named(self, app):
        """A bare number is how two layers end up disagreeing about the order."""
        assert set(self.layers(app)) == {
            "panel", "controls", "hint", "sheet", "header"
        }

        source = css(app, "mindmap.css")
        # The board's own internals stack against the node they belong to, not
        # against the page, so they are outside this scale on purpose.
        page_level = re.findall(r"z-index:\s*(\d+)", source)
        assert page_level == ["2", "3", "5"], (
            f"z-index solto fora da escala de camadas: {page_level}"
        )

    def test_a_sheet_covers_what_floats_on_the_board(self, app):
        layers = self.layers(app)
        assert layers["sheet"] > layers["hint"] > layers["controls"] > layers["panel"]

        source = css(app, "mindmap.css")
        narrow = source[source.index("@media (max-width: 960px)"):]
        assert "var(--mm-z-sheet)" in rule_for(narrow, ".mm-panel"), (
            "o painel vira folha sobre o quadro e continua abaixo da barra de "
            "ferramentas"
        )


class TestTheBoardDrawsItsLinks:
    """The world is 0x0 on purpose; what draws inside it has to survive that.

    The bug: the shell resets `img, svg { max-width: 100% }`. The links are a
    single SVG positioned against the zero-size world, so that reset resolved
    to `max-width: 0` - and a zero width on an outermost `svg` does not shrink
    it, it disables rendering of the element. Nodes are divs and were
    unaffected, so the board came up with every topic in place and not one
    line between them.
    """

    def test_the_world_has_no_size_of_its_own(self, app):
        """The precondition. Coordinates are the world's, not the box's."""
        world = rule_for(css(app, "mindmap.css"), ".mm-world")
        assert "width: 0" in world and "height: 0" in world

    def test_the_shell_still_clamps_images(self, app):
        """If this reset goes, the opt-out below stops being necessary - and
        stops being *obviously* necessary, which is the worse half."""
        assert "max-width: 100%" in rule_for(css(app), "img, svg")

    def test_the_links_opt_out_of_the_clamp(self, app):
        links = rule_for(css(app, "mindmap.css"), ".mm-links")
        assert "max-width: none" in links, (
            "o SVG das ligações volta a ser reduzido a zero pelo reset do shell"
        )
        # Stated in the stylesheet rather than as a presentation attribute,
        # which any rule in the shell outranks.
        assert "overflow: visible" in links


class TestHiddenStillHides:
    """`hidden` is a UA rule, so any `display` an author writes defeats it.

    The bug: `.mm-panel` sets `display: flex`, and nothing restored what the
    attribute means. Both side panels were permanently on screen - closing one
    only stopped it refreshing, and the outline rendered empty because the
    code correctly believed it was hidden. The shell already carries this
    companion rule for five other components; the canvas was missing it.
    """

    def hideable(self, app) -> set[str]:
        """Classes on an element that the markup can hide."""
        markup = (
            Path(app.root_path) / "templates" / "mindmaps" / "canvas.html"
        ).read_text(encoding="utf-8")
        names: set[str] = set()
        for tag in re.findall(r"<[^>]+>", markup):
            if not re.search(r"(?<!aria-)(?<!-)\bhidden\b", tag):
                continue
            found = re.search(r'class="([^"]+)"', tag)
            if found:
                names.update(found.group(1).split())

        # The canvas builds its nodes in JavaScript, so half of what can be
        # hidden never reaches the markup at all. Leaving those out is how a
        # dead collapse button rode along on every leaf of every board.
        source = Path(app.root_path) / "static" / "js" / "modules" / "mindmap"
        script = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(source.glob("*.js"))
        )
        for variable in set(re.findall(r"\b(\w+)\.hidden\s*=", script)):
            for assigned in re.finditer(
                rf"\b{variable}\.className\s*=\s*['\"]([^'\"]+)", script
            ):
                names.update(assigned.group(1).split())
        return names

    def test_nothing_hideable_is_forced_to_display(self, app):
        source = css(app, "mindmap.css")
        offenders = []

        for name in sorted(self.hideable(app)):
            block = rule_for(source, f".{name}")
            display = re.search(r"(?<!-)display:\s*([^;]+)", block)
            if not display or display.group(1).strip() == "none":
                continue
            if re.search(rf"\.{re.escape(name)}\[hidden\]", source):
                continue
            offenders.append(f".{name} (display: {display.group(1).strip()})")

        assert not offenders, (
            "o atributo hidden não esconde: " + ", ".join(offenders)
        )

    def test_a_closed_panel_does_not_take_the_board_with_it(self, app):
        """The columns are assigned, not auto-placed.

        With the panels hidden for the first time, auto-placement slid the
        board into the first `auto` track and collapsed it to zero width: the
        canvas came up blank.
        """
        source = css(app, "mindmap.css")
        assert "grid-column: 2" in rule_for(source, ".mm-stage")
        assert "grid-column: 1" in rule_for(source, ".mm-panel-left")
        assert "grid-column: 3" in rule_for(source, ".mm-panel-right")


class TestTheControlsBelongToTheBoard:
    """The toolbar floats over the board, so the board has to contain it.

    As a child of the page it was positioned against the whole window and sat
    on top of the outline panel, clipping the first characters of every topic.
    """

    def test_the_controls_are_inside_the_stage(self, app):
        markup = (
            Path(app.root_path) / "templates" / "mindmaps" / "canvas.html"
        ).read_text(encoding="utf-8")
        stage = markup.index('class="mm-stage"')
        board = markup[stage:markup.index("mm-panel mm-panel-right", stage)]

        for control in ("mm-toolbar", "mm-zoom", "mm-minimap"):
            assert f'class="{control}"' in board, (
                f"{control} está fora do quadro e flutua sobre os painéis"
            )

    def test_the_toolbar_no_longer_re_adds_the_header(self, app):
        """Measured from the board, which already starts below the header."""
        assert "var(--mm-header)" not in rule_for(css(app, "mindmap.css"), ".mm-toolbar")


class TestSpacingComesFromTheScale:
    """Padding, margin and gap are spelled with tokens, not with numbers."""

    # Optical corrections that are not spacing: hairlines, off-screen parking,
    # icon-sized nudges and the pill paddings that predate the scale.
    ALLOWED_LITERALS = {
        "1px", "-1px", "-2px", "-4px", "2px", "3px", "5px", "6px", "7px", "8px", "10px",
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
