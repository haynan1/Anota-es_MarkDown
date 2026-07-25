"""Accessibility checks against the rendered HTML (WCAG 2.1 AA targets).

These are structural checks, not a substitute for a screen-reader pass, but
they catch the regressions that silently break keyboard and assistive-tech
users: unlabelled controls, icon-only buttons with no name, missing landmarks.
"""

from __future__ import annotations

import re

import pytest

PAGES = [
    "/",
    "/documentos/",
    "/lixeira/",
    "/configuracoes/",
    "/documentos/categorias",
    "/documentos/importar",
    "/grupos/",
]


def body_of(client, path):
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> HTTP {response.status_code}"
    return response.data.decode("utf-8")


class TestDocumentStructure:
    @pytest.mark.parametrize("path", PAGES)
    def test_language_and_charset(self, client, document, path):
        html = body_of(client, path)
        assert 'lang="pt-BR"' in html
        assert '<meta charset="utf-8">' in html

    @pytest.mark.parametrize("path", PAGES)
    def test_has_a_single_h1(self, client, document, path):
        html = body_of(client, path)
        assert len(re.findall(r"<h1\b", html)) == 1, "deve haver exatamente um <h1>"

    @pytest.mark.parametrize("path", PAGES)
    def test_landmarks_are_present(self, client, document, path):
        html = body_of(client, path)
        assert "<main" in html
        assert "<nav" in html
        assert 'class="skip-link"' in html, "link de pular para o conteúdo ausente"

    @pytest.mark.parametrize("path", PAGES)
    def test_viewport_supports_zoom(self, client, document, path):
        """Blocking zoom fails WCAG 1.4.4."""
        html = body_of(client, path)
        viewport = re.search(r'name="viewport" content="([^"]+)"', html).group(1)
        assert "user-scalable=no" not in viewport
        assert "maximum-scale=1" not in viewport


class TestControlsHaveNames:
    @pytest.mark.parametrize("path", PAGES)
    def test_every_button_has_an_accessible_name(self, client, document, path):
        html = body_of(client, path)

        for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S):
            attrs, inner = match.group(1), match.group(2)
            text_content = re.sub(r"<[^>]+>", "", inner).strip()
            has_label = (
                "aria-label=" in attrs
                or "aria-labelledby=" in attrs
                or bool(text_content)
            )
            assert has_label, f"botão sem nome acessível em {path}: {attrs[:80]}"

    @pytest.mark.parametrize("path", PAGES)
    def test_icon_only_links_have_names(self, client, document, path):
        html = body_of(client, path)

        for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S):
            attrs, inner = match.group(1), match.group(2)
            text_content = re.sub(r"<[^>]+>", "", inner).strip()
            if text_content:
                continue
            assert "aria-label=" in attrs or "aria-labelledby=" in attrs or "<title" in inner, (
                f"link só com ícone e sem nome em {path}: {attrs[:80]}"
            )

    @pytest.mark.parametrize("path", PAGES)
    def test_inputs_are_labelled(self, client, document, path):
        """Explicit (for=), implicit (nested in a label) or aria-label."""
        html = body_of(client, path)
        label_targets = set(re.findall(r'<label[^>]*\sfor="([^"]+)"', html))

        # Fields nested inside a <label> are labelled implicitly - valid HTML
        # and valid WCAG, so collect those ranges before checking.
        implicit_spans = [
            match.span() for match in re.finditer(r"<label\b.*?</label>", html, re.S)
        ]

        def is_nested_in_label(position: int) -> bool:
            return any(start < position < end for start, end in implicit_spans)

        for match in re.finditer(r"<(input|select|textarea)\b([^>]*)>", html):
            attrs = match.group(2)
            if re.search(r'type="(hidden|submit|button)"', attrs):
                continue

            id_match = re.search(r'\sid="([^"]+)"', attrs)
            labelled = (
                "aria-label=" in attrs
                or "aria-labelledby=" in attrs
                or (id_match and id_match.group(1) in label_targets)
                or is_nested_in_label(match.start())
            )
            assert labelled, f"campo sem label em {path}: {attrs[:90]}"

    def test_the_import_field_has_exactly_one_tab_stop(self, client):
        """The dropzone is a <label>, so it must not also be focusable itself."""
        html = body_of(client, "/documentos/importar")
        dropzone = re.search(r"<label[^>]*class=\"dropzone\"[^>]*>", html).group(0)
        assert 'tabindex="0"' not in dropzone
        assert 'role="button"' not in dropzone
        assert 'for="import-file"' in dropzone

    def test_editor_controls_are_labelled(self, client, document):
        html = body_of(client, f"/editor/{document.uuid}")

        toolbar_buttons = re.findall(r'<button[^>]*data-md="[^"]*"[^>]*>', html)
        assert len(toolbar_buttons) >= 11, "barra de ferramentas incompleta"
        for button in toolbar_buttons:
            assert "aria-label=" in button, f"ferramenta sem rótulo: {button[:70]}"


class TestIconsAreHidden:
    @pytest.mark.parametrize("path", PAGES)
    def test_decorative_svgs_are_hidden_from_assistive_tech(self, client, document, path):
        html = body_of(client, path)

        for svg in re.findall(r"<svg\b[^>]*>", html):
            if 'width="0"' in svg:  # the sprite itself
                continue
            assert 'aria-hidden="true"' in svg or 'role="img"' in svg, (
                f"svg sem tratamento de acessibilidade em {path}: {svg[:80]}"
            )


class TestStateIsNotColourOnly:
    def test_save_status_carries_text(self, client, document):
        """WCAG 1.4.1: state must not be communicated by colour alone."""
        html = body_of(client, f"/editor/{document.uuid}")
        assert "data-save-label" in html
        assert "Salvo" in html

    def test_save_status_is_announced(self, client, document):
        html = body_of(client, f"/editor/{document.uuid}")
        status = re.search(r"<output[^>]*data-save-status[^>]*>", html).group(0)
        assert 'aria-live="polite"' in status

    def test_toasts_are_announced(self, client, document):
        html = body_of(client, "/")
        toasts = re.search(r'<div class="toasts"[^>]*>', html).group(0)
        assert 'role="status"' in toasts
        assert 'aria-live="polite"' in toasts

    def test_diff_rows_are_not_colour_only(self, app):
        """The diff uses +/-/~ prefixes in CSS, not just background colour."""
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "editor.css"
        ).read_text(encoding="utf-8")
        assert 'content: "+"' in css
        assert 'content: "−"' in css
        assert 'content: "~"' in css

    def test_favourite_state_has_a_text_label(self, client, document):
        html = body_of(client, "/documentos/")
        assert "aria-pressed=" in html
        assert "favoritos" in html.lower()


class TestNavigationState:
    def test_current_page_is_marked(self, client, document):
        html = body_of(client, "/documentos/")
        assert 'aria-current="page"' in html

    def test_expandable_controls_declare_state(self, client, document):
        html = body_of(client, "/")
        assert 'aria-expanded="false"' in html

    def test_editor_drawer_declares_its_relationship(self, client, document):
        html = body_of(client, f"/editor/{document.uuid}")
        assert 'aria-controls="meta-drawer"' in html


class TestSidebarToggle:
    """The brand mark is the collapse control.

    It is the only element left on screen once the rail is collapsed, so it
    has to be a real button, keep an honest name, and never be the thing that
    disappears along with the labels.
    """

    def test_the_mark_is_a_button_that_toggles_the_sidebar(self, client, document):
        html = body_of(client, "/documentos/")
        mark = re.search(r"<button[^>]*class=\"brand-mark\"[^>]*>", html)

        assert mark, "a marca deve ser um <button>"
        assert 'data-action="toggle-sidebar"' in mark.group(0)
        assert 'aria-expanded=' in mark.group(0)
        assert 'aria-controls="app-shell"' in mark.group(0)

    def test_the_mark_carries_an_accessible_name(self, client, document):
        """Named by aria-label/title only.

        A rendered tooltip overflowed the 72px rail once collapsed, so the
        name is carried by attributes the browser positions itself.
        """
        html = body_of(client, "/documentos/")
        mark = re.search(r"<button[^>]*class=\"brand-mark\"[^>]*>", html).group(0)

        assert 'aria-label="Recolher barra lateral"' in mark
        assert 'title="Recolher barra lateral"' in mark
        assert "data-tooltip=" not in mark, "a tooltip renderizada não cabe no rail"

    def test_the_mark_renders_no_visible_label(self, client, document):
        """No text node inside the button - it is icon-only by design."""
        html = body_of(client, "/documentos/")
        match = re.search(
            r"<button[^>]*class=\"brand-mark\".*?</button>", html, re.S
        )
        inner = re.sub(r"<[^>]+>", "", match.group(0)).strip()
        assert inner == "", f"texto visível dentro da marca: {inner!r}"

    def test_the_brand_name_stays_a_link_to_the_dashboard(self, client, document):
        """Turning the mark into a button must not cost the way home."""
        html = body_of(client, "/documentos/")
        link = re.search(r'<a class="brand-name" href="([^"]+)"', html)

        assert link, "o nome deve continuar sendo um link"
        assert link.group(1) == "/"

    def test_the_mark_survives_the_collapsed_rail(self, app):
        """Everything else in the head is hidden when collapsed - not this."""
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "base.css"
        ).read_text(encoding="utf-8")

        hidden = re.search(
            r'\.app\[data-sidebar="collapsed"\] \.brand-name,(.*?)\{([^}]*)\}',
            css,
            re.S,
        )
        assert hidden, "regra do estado recolhido não encontrada"
        assert ".brand-mark" not in hidden.group(1)

    def test_only_one_collapse_control_exists(self, client, document):
        """The separate toggle button was folded into the mark."""
        html = body_of(client, "/documentos/")
        assert html.count('data-action="toggle-sidebar"') == 1


class TestMotionAndContrast:
    def test_reduced_motion_is_respected(self, app):
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "base.css"
        ).read_text(encoding="utf-8")
        assert "prefers-reduced-motion: reduce" in css

    def test_focus_is_always_visible(self, app):
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "base.css"
        ).read_text(encoding="utf-8")
        assert ":focus-visible" in css
        assert "outline:" in css.replace("outline: ", "outline:")

    def test_scrollbars_are_standardised(self, app):
        """One themed treatment, not the platform default.

        The Windows default is a wide light-grey bar with stepper arrows; it
        ignores the theme and reads as a foreign object on a dark surface.
        """
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "base.css"
        ).read_text(encoding="utf-8")

        assert "::-webkit-scrollbar-thumb" in css
        assert "scrollbar-width: thin" in css, "Firefox ficaria com a barra padrão"
        assert "scrollbar-color:" in css
        # The stepper arrows must be gone.
        assert "::-webkit-scrollbar-button" in css
        # Thumb colour derives from the theme tokens, not a literal.
        assert "--scrollbar-thumb" in css

    def test_scrolling_panels_declare_a_single_axis(self, app):
        """A panel that scrolls sideways as well is almost always a bug."""
        import re
        from pathlib import Path

        editor_css = (
            Path(app.root_path) / "static" / "css" / "editor.css"
        ).read_text(encoding="utf-8")

        # Match the rule that actually declares the scroll, not the first
        # selector that merely mentions the class.
        for selector in (r"\.editor-source, \.editor-preview", r"\.meta-drawer-body"):
            match = re.search(selector + r"\s*\{([^}]*)\}", editor_css)
            assert match, selector

            rule = match.group(1)
            assert "overflow-y: auto" in rule, selector
            assert "overflow-x: hidden" in rule, selector
            assert "overscroll-behavior: contain" in rule, selector
            # No reserved gutter: the space belongs to the content.
            assert "scrollbar-gutter" not in rule, selector

    def test_click_targets_meet_the_minimum_size(self, app):
        """WCAG 2.5.5 / 2.5.8: icon-only buttons keep a 32px+ hit area."""
        from pathlib import Path

        css = (
            Path(app.root_path) / "static" / "css" / "base.css"
        ).read_text(encoding="utf-8")
        assert ".btn-icon" in css
        assert "min-height: 38px" in css
        assert "min-height: 32px" in css
