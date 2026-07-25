"""Copiar para a Wix: o documento reduzido ao que um campo de texto rico aceita.

The contract is narrow on purpose. Whatever comes out of here is pasted into
somebody else's editor, so it may contain only the handful of tags that
survive that paste - and everything it had to change must be reported, never
dropped in silence.
"""

from __future__ import annotations

import pytest

from app.services.wix_service import WIX_TAGS, render_for_wix

PRODUCT = """# Camiseta Premium

Feita em **algodão egípcio** com *acabamento* manual.

## Especificações

| Material | Gramatura |
|:---------|:----------|
| Algodão  | 180g      |
| Elastano | 20g       |

- Item com [link externo](https://exemplo.com)
- Item com [link interno](/documentos/abc)

> Uma citação.

Fim.
"""


class TestFormattingThatSurvives:
    def test_headings_bold_italic_and_lists_are_kept(self, app):
        result = render_for_wix(PRODUCT)

        assert "<h1>Camiseta Premium</h1>" in result.html
        assert "<strong>algodão egípcio</strong>" in result.html
        assert "<em>acabamento</em>" in result.html
        assert "<ul>" in result.html and "<li>" in result.html
        assert "<blockquote>" in result.html

    def test_external_links_keep_their_address(self, app):
        result = render_for_wix(PRODUCT)
        assert '<a href="https://exemplo.com">link externo</a>' in result.html

    def test_only_wix_safe_tags_come_out(self, app):
        import re

        result = render_for_wix(PRODUCT + "\n\n```py\nprint(1)\n```\n")
        tags = set(re.findall(r"<([a-zA-Z0-9]+)", result.html))

        assert tags <= WIX_TAGS

    def test_nothing_carries_a_class_or_a_style(self, app):
        """Wix strips them anyway; sending them is noise the paste can trip on."""
        result = render_for_wix(PRODUCT)

        assert "class=" not in result.html
        assert "style=" not in result.html


class TestConversions:
    def test_a_table_becomes_one_paragraph_per_row(self, app):
        result = render_for_wix(PRODUCT)

        assert "<table" not in result.html
        assert "<strong>Material: </strong>Algodão" in result.html
        assert "<strong>Gramatura: </strong>180g" in result.html
        assert any("tabela" in note for note in result.notes)

    def test_a_table_without_a_header_still_reads(self, app):
        result = render_for_wix("| a | b |\n| c | d |\n")
        assert "<table" not in result.html

    def test_a_code_block_becomes_plain_paragraphs(self, app):
        result = render_for_wix("```python\nprint('oi')\n```\n")

        assert "<pre" not in result.html and "<code" not in result.html
        assert "print('oi')" in result.html
        assert any("código" in note for note in result.notes)

    def test_a_checklist_keeps_its_boxes_as_characters(self, app):
        result = render_for_wix("- [ ] pendente\n- [x] pronto\n")

        assert "☐ pendente" in result.html
        assert "☑ pronto" in result.html
        assert "<input" not in result.html

    def test_a_horizontal_rule_becomes_a_line_of_dashes(self, app):
        result = render_for_wix("antes\n\n---\n\ndepois\n")

        assert "<hr" not in result.html
        assert "———" in result.html

    def test_a_definition_list_becomes_labelled_paragraphs(self, app):
        result = render_for_wix("Termo\n:   Explicação do termo\n")

        assert "<dl" not in result.html
        assert "<strong>Termo</strong>" in result.html


class TestWhatCannotCross:
    def test_images_are_removed_and_reported(self, app):
        result = render_for_wix("Antes\n\n![foto](/midia/abc)\n\nDepois\n")

        assert "<img" not in result.html
        assert "Antes" in result.html and "Depois" in result.html
        assert any("imagem" in note for note in result.notes)

    def test_video_is_removed_and_reported(self, app):
        result = render_for_wix('<video controls src="/midia/abc"></video>\n')

        assert "<video" not in result.html
        assert any("vídeo" in note for note in result.notes)

    def test_internal_links_become_text(self, app):
        result = render_for_wix(PRODUCT)

        assert "/documentos/abc" not in result.html
        assert "link interno" in result.html
        assert any("link" in note for note in result.notes)

    def test_an_attachment_card_becomes_its_filename(self, app):
        import io

        from werkzeug.datastructures import FileStorage

        from app.services.media_service import store_upload

        asset = store_upload(
            FileStorage(io.BytesIO(b"%PDF-1.7\n" + b"x" * 200), filename="manual.pdf")
        )
        result = render_for_wix(f"Veja o [manual.pdf](/midia/{asset.uuid}).")

        assert "manual.pdf" in result.html
        assert "attachment" not in result.html
        assert "4,9 KB" not in result.html  # the card's metadata does not travel
        assert any("anexo" in note.lower() for note in result.notes)

    def test_a_wikilink_becomes_text(self, app, make_document):
        make_document(title="Outro documento")
        result = render_for_wix("Veja [[Outro documento]].")

        assert "/editor/" not in result.html
        assert "Outro documento" in result.html


class TestSafety:
    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script>",
            '<img src=x onerror="alert(1)">',
            "[clique](javascript:alert(1))",
            '<a href="javascript:alert(1)">x</a>',
            '<iframe src="https://evil.test"></iframe>',
        ],
    )
    def test_nothing_executable_reaches_the_clipboard(self, app, payload):
        result = render_for_wix(payload)

        lowered = result.html.lower()
        assert "script" not in lowered
        assert "javascript:" not in lowered
        assert "onerror" not in lowered
        assert "<iframe" not in lowered


class TestPlainTextTwin:
    def test_the_plain_text_mirrors_the_document(self, app):
        result = render_for_wix(PRODUCT)

        assert "Camiseta Premium" in result.text
        assert "**" not in result.text  # no markdown syntax leaks through
        assert "<" not in result.text
        assert "• Item com link externo" in result.text

    def test_an_empty_document_produces_nothing_at_all(self, app):
        result = render_for_wix("   \n\n  ")

        assert result.html == ""
        assert result.text == ""
        assert result.notes == []


class TestEndpoint:
    def test_the_editor_can_ask_for_the_pasteable_version(self, client, app):
        response = client.post(
            "/api/texto-rico", json={"content_markdown": "# Oi\n\nTexto **forte**."}
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert "<h1>Oi</h1>" in payload["html"]
        assert "Texto forte." in payload["text"]
        assert payload["notes"] == []

    def test_the_conversion_reports_what_changed(self, client, app):
        response = client.post(
            "/api/texto-rico", json={"content_markdown": "![foto](/midia/abc)"}
        )

        assert response.get_json()["notes"]

    def test_invalid_input_is_refused(self, client, app):
        response = client.post("/api/texto-rico", json={"content_markdown": 42})
        assert response.status_code == 400

    def test_an_oversized_document_is_refused(self, client, app):
        response = client.post(
            "/api/texto-rico", json={"content_markdown": "x" * (2 * 1024 * 1024 + 10)}
        )
        assert response.status_code == 400

    def test_the_endpoint_requires_csrf(self, csrf_app):
        client = csrf_app.test_client()
        response = client.post("/api/texto-rico", json={"content_markdown": "oi"})
        assert response.status_code == 400
