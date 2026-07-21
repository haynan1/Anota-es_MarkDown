"""Reading-comfort panel: text scale and bold emphasis.

The feature's promise is narrow and load-bearing: *only* typography moves.
Most of these tests exist to hold that line — a scale that dragged the
spacing scale with it would reflow every screen.
"""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED_SCALES = ["0.95", "1", "1.12", "1.25"]


def css(app, name="base.css"):
    return (Path(app.root_path) / "static" / "css" / name).read_text(encoding="utf-8")


def js(app, *parts):
    return (Path(app.root_path) / "static" / "js" / Path(*parts)).read_text(
        encoding="utf-8"
    )


class TestTypeScale:
    def test_every_font_size_comes_from_the_scale(self, app):
        """A literal size cannot follow the setting.

        The one exception is the panel's own `Aa` samples: they preview the
        steps, so they must stay fixed rather than move with the value they
        are demonstrating.
        """
        for name in ("base.css", "editor.css", "charts.css", "markdown.css"):
            source = css(app, name)
            source = re.sub(r"\.a11y-sample-\d\s*\{[^}]*\}", "", source)

            literals = re.findall(r"font-size:\s*([0-9.]+(?:rem|px))", source)
            assert not literals, f"{name} ainda tem tamanhos fixos: {literals}"

    def test_the_scale_multiplies_every_step(self, app):
        source = css(app)
        # --fs-chart-* is deliberately absent: chart text wears the shared
        # scale now, because a px size inside an SVG is multiplied by the
        # viewBox like everything else and so had no fixed size at all.
        for token in ("--fs-2xs", "--fs-xs", "--fs-sm", "--fs-base", "--fs-3xl"):
            # calc(...) contains a nested var(...), so match to end of line.
            match = re.search(rf"{token}:\s*calc\((.+)\);", source)
            assert match, f"{token} não definido"
            assert "var(--a11y-font-scale)" in match.group(1), token

    def test_spacing_never_follows_the_text_scale(self, app):
        """The guarantee the panel's subtitle makes to the user."""
        source = css(app)
        block = source[source.index("--sp-1:") : source.index("--font-ui")]
        assert "a11y-font-scale" not in block

    def test_the_scale_defaults_to_one(self, app):
        assert re.search(r"--a11y-font-scale:\s*1;", css(app))

    def test_small_text_got_larger(self, app):
        """The requested bump: the old 0.8125rem step is now 0.875rem."""
        match = re.search(r"--fs-sm:\s*calc\(([0-9.]+)rem", css(app))
        assert match
        assert float(match.group(1)) == 0.875


class TestAntiFlash:
    def test_the_initialiser_is_an_external_file(self, app):
        """The CSP issues no nonce, so an inline block would be blocked."""
        base = (
            Path(app.root_path) / "templates" / "base.html"
        ).read_text(encoding="utf-8")

        assert "js/theme-init.js" in base
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", base), (
            "nenhum script inline pode existir sob esta CSP"
        )

    def test_the_initialiser_runs_before_the_stylesheets(self, app):
        base = (
            Path(app.root_path) / "templates" / "base.html"
        ).read_text(encoding="utf-8")
        assert base.index("theme-init.js") < base.index("css/base.css")

    def test_the_initialiser_applies_the_saved_scale(self, app):
        source = js(app, "theme-init.js")
        assert "--a11y-font-scale" in source
        assert "a11y-text-size" in source

    def test_only_allowlisted_scales_are_applied(self, app):
        """Nothing arbitrary from storage may reach the stylesheet."""
        source = js(app, "theme-init.js")
        match = re.search(r"SCALES\s*=\s*\[([^\]]*)\]", source)
        assert match
        values = [v.strip() for v in match.group(1).split(",")]
        assert values == ALLOWED_SCALES

    def test_floats_are_compared_with_a_tolerance(self, app):
        """`===` on floats would silently drop a valid step."""
        for source in (js(app, "theme-init.js"), js(app, "modules", "a11y.js")):
            assert "Math.abs" in source
            assert "EPSILON" in source


class TestPanel:
    def test_the_dialog_is_present_on_every_page(self, client, document):
        for path in ["/", "/documentos/", "/configuracoes/"]:
            body = client.get(path).data.decode("utf-8")
            assert 'id="a11y-dialog"' in body, path

    def test_the_trigger_sits_beside_the_theme_toggle(self, client):
        body = client.get("/").data.decode("utf-8")
        trigger = body.index('data-action="open-a11y"')
        theme = body.index('data-action="toggle-theme"')
        assert abs(trigger - theme) < 500

    def test_the_trigger_is_named_and_declares_its_dialog(self, client):
        body = client.get("/").data.decode("utf-8")
        match = re.search(r'<button[^>]*data-action="open-a11y"[^>]*>', body)
        assert match
        assert 'aria-label=' in match.group(0)
        assert 'aria-haspopup="dialog"' in match.group(0)

    def test_all_four_steps_are_offered(self, client):
        body = client.get("/").data.decode("utf-8")
        offered = re.findall(r'data-a11y-scale="([^"]+)"', body)
        assert offered == ALLOWED_SCALES

    def test_each_step_is_a_pressable_button(self, client):
        body = client.get("/").data.decode("utf-8")
        for scale in ALLOWED_SCALES:
            match = re.search(rf'<button[^>]*data-a11y-scale="{scale}"[^>]*>', body)
            assert match, scale
            assert "aria-pressed" in match.group(0), scale

    def test_the_change_is_announced(self, client):
        body = client.get("/").data.decode("utf-8")
        assert re.search(r'data-a11y-status[^>]*aria-live="polite"', body) or re.search(
            r'aria-live="polite"[^>]*data-a11y-status', body
        )

    def test_the_panel_states_its_guarantee(self, client):
        """The subtitle is the contract; it must not drift from the CSS."""
        body = client.get("/").data.decode("utf-8")
        assert "mantendo menus, cartões e espaçamentos estáveis" in body

    def test_bold_emphasis_is_offered(self, client):
        body = client.get("/").data.decode("utf-8")
        assert "data-a11y-bold" in body
        assert "Realce em negrito" in body


class TestBoldEmphasis:
    def test_bold_raises_weight_and_darkens_faint_greys(self, app):
        source = css(app)
        assert "html.a11y-bold-text" in source
        # Extra weight on a near-invisible grey buys nothing.
        assert re.search(
            r"html\.a11y-bold-text\s*\{\s*--text-subtle:\s*var\(--text-muted\)", source
        )

    def test_bold_does_not_touch_layout(self, app):
        source = css(app)
        block = source[source.index("html.a11y-bold-text") :]
        block = block[: block.index("\n\n")] if "\n\n" in block else block
        for forbidden in ("padding", "margin", "width", "gap"):
            assert forbidden not in block, forbidden


class TestFormControlsFollow:
    def test_controls_inherit_the_scaled_font(self, app):
        """Inputs ignore the document size unless told to inherit."""
        source = css(app)
        assert re.search(
            r"button, input, select, textarea\s*\{\s*font: inherit", source
        )


class TestThemeWithoutJavaScript:
    """The saved theme must survive scripting being switched off.

    The server rendered `data-theme="dark"` unconditionally and left it to
    theme-init.js to correct. With JavaScript disabled that correction never
    ran, so a user who had chosen the light theme got a dark interface on
    every page, permanently, with no way to fix it.
    """

    def _theme_attr(self, client):
        html = client.get("/").get_data(as_text=True)
        match = re.search(r'<html[^>]*data-theme="([^"]+)"', html)
        assert match, "atributo data-theme ausente"
        return match.group(1)

    def _set_theme(self, app, value):
        from app.services.settings_service import SettingsService

        with app.app_context():
            SettingsService.update_many({"theme": value})

    def test_light_is_rendered_by_the_server(self, app, client):
        self._set_theme(app, "light")
        assert self._theme_attr(client) == "light"

    def test_dark_is_rendered_by_the_server(self, app, client):
        self._set_theme(app, "dark")
        assert self._theme_attr(client) == "dark"

    def test_auto_falls_back_to_dark_first(self, app, client):
        """Only the client knows the system preference, so auto stays dark."""
        self._set_theme(app, "auto")
        assert self._theme_attr(client) == "dark"

    def test_the_preference_meta_still_carries_the_real_value(self, app, client):
        """theme-init.js needs the untranslated choice, including "auto"."""
        self._set_theme(app, "auto")
        html = client.get("/").get_data(as_text=True)

        assert '<meta name="theme-preference" content="auto">' in html
