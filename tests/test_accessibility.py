"""Accessibility checks against the rendered HTML (WCAG 2.1 AA targets).

These are structural checks, not a substitute for a screen-reader pass, but
they catch the regressions that silently break keyboard and assistive-tech
users: unlabelled controls, icon-only buttons with no name, missing landmarks.
"""

from __future__ import annotations

import re

import pytest

from app.services import palette as palettes
from tests import contrast

PAGES = [
    "/",
    "/documentos/",
    "/lixeira/",
    "/configuracoes/",
    "/documentos/categorias",
    "/documentos/importar",
    "/grupos/",
    "/mapas/",
    "/metas/",
    "/metas/esteira",
    "/metas/plano",
    "/metas/historico",
    "/metas/conquistas",
    "/metas/predefinidas",
    "/metas/nova",
    "/metas/frases",
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

    def test_the_alignment_menu_announces_which_option_is_active(self, client, document):
        """Four exclusive options, each declaring its own state."""
        html = body_of(client, f"/editor/{document.uuid}")

        assert 'aria-label="Alinhamento do texto"' in html
        options = re.findall(r'<button[^>]*data-align="[^"]*"[^>]*>', html)
        assert len(options) == 4, "opções de alinhamento incompletas"
        for option in options:
            assert "aria-pressed=" in option, f"opção sem estado: {option[:70]}"


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
        # Drawn for a pointer, sized for a finger: 34px with a mouse, 44px
        # wherever the primary input is coarse.
        assert "min-height: 34px" in css
        assert "@media (pointer: coarse)" in css
        coarse = css[css.index("@media (pointer: coarse)") :]
        coarse = coarse[: coarse.index("\n}")]
        assert ".btn-icon, .btn-icon.btn-sm { width: 44px; min-height: 44px; }" in coarse
        assert "min-height: 32px" in css


class TestTheCanvasIsReachableWithoutAMouse:
    """The canvas is a picture, and a picture is unreachable with a screen
    reader. These pin the three things that make it usable anyway: the board
    announces itself, every icon-only tool is named, and the outline exists as
    the same map in text.

    Its URL carries a UUID, so it cannot join PAGES - which is exactly why it
    was never checked by any of the tests above.
    """

    @pytest.fixture()
    def canvas(self, client, app):
        from app.services.mind_map_service import MindMapService

        mind_map = MindMapService.create("Mapa acessível", "")
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.data.decode("utf-8")

    def test_the_board_announces_what_it_is(self, canvas):
        assert 'role="application"' in canvas
        board = re.search(r'<div class="mm-stage"[^>]*>', canvas).group(0)
        assert "aria-label=" in board

    def test_the_board_is_a_tab_stop(self, canvas):
        """Without it the keyboard shortcuts have nothing to be pressed into."""
        board = re.search(r'<div class="mm-stage"[^>]*>', canvas).group(0)
        assert "tabindex=" in board

    def test_every_tool_is_named(self, canvas):
        for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", canvas, re.S):
            attrs, inner = match.group(1), match.group(2)
            text_content = re.sub(r"<[^>]+>", "", inner).strip()
            assert (
                "aria-label=" in attrs or "aria-labelledby=" in attrs or text_content
            ), f"botão sem nome acessível no canvas: {attrs[:80]}"

    def test_the_toolbar_declares_itself_a_toolbar(self, canvas):
        toolbar = re.search(r'<div class="mm-toolbar"[^>]*>', canvas).group(0)
        assert 'role="toolbar"' in toolbar
        assert "aria-label=" in toolbar

    def test_the_active_tool_is_announced(self, canvas):
        """Which tool is selected must not be carried by colour alone."""
        assert 'aria-pressed="true"' in canvas

    def test_the_outline_is_the_texts_version_of_the_picture(self, canvas):
        panel = re.search(r"<aside[^>]*data-outline-panel[^>]*>", canvas).group(0)
        assert "aria-label=" in panel

    def test_decorative_svgs_are_hidden_from_assistive_tech(self, canvas):
        for svg in re.findall(r"<svg\b[^>]*>", canvas):
            if 'width="0"' in svg:  # the sprite itself
                continue
            assert 'aria-hidden="true"' in svg or 'role="img"' in svg, (
                f"svg sem tratamento no canvas: {svg[:80]}"
            )

    def test_the_page_has_one_h1(self, canvas):
        """The map's own name. The canvas had none at all, so its two h2
        panels opened a heading hierarchy that started nowhere."""
        assert len(re.findall(r"<h1\b", canvas)) == 1

    def test_the_board_is_a_main_landmark(self, canvas):
        """This screen replaces base.html's shell, so it brings its own."""
        assert re.search(r'<main[^>]*id="main-content"', canvas)

class TestColourContrast:
    """O contraste, lido dos tokens em vez de confiado ao olho — e agora de
    *todas* as paletas.

    Um par de cores que reprova o AA não aparece numa revisão de tela: ele
    aparece quando alguém tenta ler a etiqueta num notebook ao sol. Estes
    pares foram medidos uma vez, e agora são medidos toda vez.

    O que mudou com as paletas é a origem dos valores. As superfícies saem do
    registro em ``app/services/palette.py``, o destaque sai do solver medido
    contra as superfícies daquela paleta, e as cores semânticas continuam em
    ``base.css`` porque valem para todas. Acrescentar uma paleta é acrescentar
    uma entrada naquele arquivo — e, a partir daqui, é também submetê-la a
    esta bateria inteira, nos dois temas, antes de ela existir para alguém.

    Referência: WCAG 2.1 AA - 4.5:1 para texto, 3:1 para texto grande e para
    elementos gráficos que identificam um controle (1.4.11).
    """

    TEXT_MIN = 4.5
    LARGE_MIN = 3.0
    NON_TEXT_MIN = 3.0

    # (o que é, tinta, fundo da tinta, fundo desse fundo)
    TEXT_PAIRS = [
        ("texto de apoio sobre o fundo da página", "text-muted", "bg", "bg"),
        ("texto de apoio sobre um cartão", "text-muted", "surface", "bg"),
        ("texto de apoio sobre a superfície rebaixada", "text-muted", "surface-sunken", "bg"),
        ("texto tênue sobre o fundo da página", "text-subtle", "bg", "bg"),
        ("texto tênue sobre a superfície rebaixada", "text-subtle", "surface-sunken", "bg"),
        ("texto tênue sobre um painel levantado", "text-subtle", "surface-2", "bg"),
        ("etiqueta de prioridade alta", "danger-ink", "danger-soft", "surface"),
        ("etiqueta de prioridade média", "warning-ink", "warning-soft", "surface"),
        ("etiqueta de situação concluída", "success-ink", "success-soft", "surface"),
        ("aba ativa da jornada", "accent", "accent-soft", "bg"),
        ("data atrasada", "danger", "surface", "bg"),
        ("link para um documento ligado", "accent", "surface", "bg"),
        ("o rótulo de um botão primário", "accent-contrast", "accent", "accent"),
        ("frase motivacional", "text", "accent-soft", "bg"),
    ]

    # Elementos gráficos que identificam um controle.
    NON_TEXT_PAIRS = [
        ("o círculo de concluir, em repouso", "text-muted", "surface"),
        ("o alvo do arrastar, iluminado", "accent", "surface-sunken"),
        ("o preenchimento do medidor de nível", "accent", "surface-sunken"),
        ("o visto sobre o círculo concluído", "accent-contrast", "success"),
    ]

    @pytest.mark.parametrize("palette", palettes.PALETTES, ids=lambda p: p.key)
    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("label,ink,fill,under", TEXT_PAIRS)
    def test_text_meets_aa(self, palette, theme, label, ink, fill, under):
        tokens = contrast.tokens_for(palette, theme)
        value = contrast.measure(tokens, ink, fill, under)
        assert value >= self.TEXT_MIN, (
            f"{label} na paleta {palette.name}, tema {theme}: "
            f"{value:.2f}:1, abaixo de {self.TEXT_MIN}:1"
        )

    @pytest.mark.parametrize("palette", palettes.PALETTES, ids=lambda p: p.key)
    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("label,ink,fill", NON_TEXT_PAIRS)
    def test_controls_are_perceivable(self, palette, theme, label, ink, fill):
        tokens = contrast.tokens_for(palette, theme)
        value = contrast.measure(tokens, ink, fill, fill)
        assert value >= self.NON_TEXT_MIN, (
            f"{label} na paleta {palette.name}, tema {theme}: "
            f"{value:.2f}:1, abaixo de {self.NON_TEXT_MIN}:1"
        )

    def test_the_semantic_inks_exist_in_both_themes(self):
        """A tinta é o que torna a etiqueta legível; sem ela o par volta a 2.8:1."""
        for theme in ("light", "dark"):
            tokens = contrast.css_tokens(theme)
            for token in ("success-ink", "warning-ink", "danger-ink"):
                assert token in tokens, f"--{token} sumiu do tema {theme}"
