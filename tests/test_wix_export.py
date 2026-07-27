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

    def test_alignment_is_flattened_and_reported(self, app):
        """Wix keeps the words but not the class that centred them."""
        result = render_for_wix("::: centro\nTítulo da promoção\n:::\n")

        assert "Título da promoção" in result.html
        assert "class=" not in result.html
        assert any("alinhad" in note for note in result.notes)

    def test_left_alignment_is_not_worth_a_note(self, app):
        """It is what the paste already does; saying so is noise."""
        result = render_for_wix("::: esquerda\nTexto.\n:::\n")

        assert "Texto." in result.html
        assert not any("alinhad" in note for note in result.notes)

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


class TestCopyingInParts:
    """O documento cortado onde estão as imagens.

    Wix accepts one upload at a time, so a document with pictures is pasted in
    instalments. The split has exactly one rule that cannot bend: everything in
    the whole copy has to be in the parts, in the same order. A writer who
    follows the checklist to the end must arrive at the same description as one
    who pasted it all at once.
    """

    WITH_IMAGES = (
        "# Camiseta\n\nPrimeiro trecho.\n\n"
        "![frente.png](/midia/aaa)\n\n"
        "## Detalhes\n\nSegundo trecho.\n\n"
        "![costas.png](/midia/bbb)\n\n"
        "Trecho final.\n"
    )

    def test_the_document_is_cut_at_every_picture(self, app):
        result = render_for_wix(self.WITH_IMAGES)

        assert len(result.parts) == 3
        assert result.parts[0].media_after.description == "imagem: frente.png"
        assert result.parts[1].media_after.description == "imagem: costas.png"
        assert result.parts[2].media_after is None

    def test_nothing_is_lost_in_the_split(self, app):
        """The parts, joined back together, are the whole copy."""
        result = render_for_wix(self.WITH_IMAGES)

        assert "".join(part.html for part in result.parts) == result.html

    def test_each_part_carries_its_own_text_twin(self, app):
        result = render_for_wix(self.WITH_IMAGES)

        assert "Primeiro trecho." in result.parts[0].text
        assert "Segundo trecho." in result.parts[1].text
        # A part is a paste of its own: part 2 must not repeat part 1.
        assert "Primeiro trecho." not in result.parts[1].text

    def test_a_part_is_sanitized_like_the_whole(self, app):
        import re

        result = render_for_wix(self.WITH_IMAGES + "\n<script>alert(1)</script>\n")

        for part in result.parts:
            assert "class=" not in part.html
            assert "style=" not in part.html
            assert "script" not in part.html.lower()
            assert set(re.findall(r"<([a-zA-Z0-9]+)", part.html)) <= WIX_TAGS

    def test_a_document_without_media_is_a_single_part(self, app):
        """Nothing to interleave, so the dialog stays as it was."""
        result = render_for_wix("Só texto.\n\nMais texto.")

        assert len(result.parts) == 1
        assert result.parts[0].media_after is None
        assert result.parts[0].html == result.html

    def test_a_document_that_opens_with_a_picture(self, app):
        """The upload comes first; the part before it is empty on purpose."""
        result = render_for_wix("![capa.png](/midia/ccc)\n\nTexto depois.")

        assert len(result.parts) == 2
        assert result.parts[0].html == ""
        assert result.parts[0].media_after.description == "imagem: capa.png"
        assert "Texto depois." in result.parts[1].html

    def test_a_document_that_ends_with_a_picture_has_no_empty_tail(self, app):
        result = render_for_wix("Texto antes.\n\n![fim.png](/midia/ddd)")

        assert len(result.parts) == 1
        assert result.parts[0].media_after.description == "imagem: fim.png"

    def test_a_video_is_a_step_of_its_own(self, app):
        result = render_for_wix(
            'Antes.\n\n<video controls src="/midia/h" title="clipe.mp4"></video>\n\nDepois.'
        )

        assert len(result.parts) == 2
        assert result.parts[0].media_after.kind == "vídeo"
        assert result.parts[0].media_after.label == "clipe.mp4"

    def test_a_picture_inside_a_paragraph_breaks_after_it(self, app):
        """Half a paragraph is not a paste; the cut waits for the end of it."""
        result = render_for_wix("Texto com ![meio.png](/midia/g) no meio.\n\nDepois.")

        assert len(result.parts) == 2
        assert "Texto com" in result.parts[0].html
        assert "Depois." in result.parts[1].html

    def test_an_empty_document_has_no_parts(self, app):
        assert render_for_wix("").parts == []

    def test_the_split_marker_never_reaches_the_clipboard(self, app):
        """The sentinel that marks a cut is machinery, not content.

        It is created inside the converter and has to be gone before anything
        is serialised - including from inside a quotation, where a cut cannot
        happen and the marker would otherwise be carried along in the tree.
        """
        from app.services.wix_service import _BREAK_TAG

        result = render_for_wix(
            "> Citação com ![dentro.png](/midia/x) uma imagem\n\nDepois.\n"
        )

        assert _BREAK_TAG not in result.html
        assert all(_BREAK_TAG not in part.html for part in result.parts)
        assert _BREAK_TAG not in result.text
        # The picture still cuts the document, from outside the quotation.
        assert len(result.parts) == 2
        assert result.parts[0].media_after.label == "dentro.png"

    def test_the_endpoint_publishes_the_parts(self, client, app):
        response = client.post(
            "/api/texto-rico", json={"content_markdown": self.WITH_IMAGES}
        )
        payload = response.get_json()

        assert response.status_code == 200
        assert len(payload["parts"]) == 3
        assert payload["parts"][0]["media"]["description"] == "imagem: frente.png"
        assert payload["parts"][2]["media"] is None


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
