"""The formatted PDF is the Markdown, and nothing else.

No title block, no metadata line, no running header, no footer, no page
number. Whatever a reader sees on the page is something the document itself
says - and everything asserted here is asserted against both engines, because
"we removed it from one template" is how this feature grows back.

The Markdown *source* listing is a different export with a different job, and
this file guards that it was left alone.
"""

from __future__ import annotations

import pytest

from app.services.pdf_service import (
    VARIANT_RENDERED,
    VARIANT_SOURCE,
    WeasyPrintEngine,
    Xhtml2PdfEngine,
    build_context,
    build_html,
    render_document_pdf,
)

BOTH_ENGINES = pytest.mark.parametrize(
    "engine", [WeasyPrintEngine, Xhtml2PdfEngine], ids=["weasyprint", "xhtml2pdf"]
)

SAMPLE = """# Título dentro do documento

Parágrafo com **negrito**.

| A | B |
|---|---|
| 1 | 2 |
"""


def rendered_html(app, document, engine, overrides=None) -> str:
    with app.test_request_context():
        return build_html(
            document,
            build_context(document, overrides),
            engine,
            variant=VARIANT_RENDERED,
        )


class TestNothingIsAdded:
    @BOTH_ENGINES
    def test_there_is_no_document_header(self, app, make_document, engine):
        document = make_document(title="Nome no banco", content=SAMPLE)
        html = rendered_html(app, document, engine)

        for chrome in ("doc-header", "doc-title", "doc-meta"):
            assert chrome not in html

    @BOTH_ENGINES
    def test_the_stored_title_never_reaches_the_page(self, app, make_document, engine):
        """The title lives in the database; only the Markdown reaches paper.

        It stays in <title> - that is the PDF's metadata, not its first page.
        """
        document = make_document(title="Nome no banco", content=SAMPLE)
        html = rendered_html(app, document, engine)
        body = html.split("<body>", 1)[1]

        assert "Nome no banco" in html
        assert "Nome no banco" not in body
        assert "Título dentro do documento" in body

    @BOTH_ENGINES
    def test_there_is_no_metadata_line(self, app, make_document, engine):
        document = make_document(
            title="Com metadados", content=SAMPLE, tag_names=["etiqueta-inconfundivel"]
        )
        html = rendered_html(app, document, engine)

        assert "palavras" not in html
        assert "Gerado em" not in html
        assert "etiqueta-inconfundivel" not in html

    @BOTH_ENGINES
    def test_there_is_no_running_header_or_footer(self, app, make_document, engine):
        document = make_document(title="Sem cabeçalho", content=SAMPLE)
        html = rendered_html(app, document, engine)

        for chrome in (
            "@top-center",
            "@bottom-left",
            "@bottom-right",
            "@frame",
            "page-header",
            "page-footer",
        ):
            assert chrome not in html

    @BOTH_ENGINES
    def test_there_is_no_page_number(self, app, make_document, engine):
        document = make_document(title="Sem numeração", content=SAMPLE)
        html = rendered_html(app, document, engine)

        assert "counter(page)" not in html
        assert "pdf:pagenumber" not in html
        assert "pdf:pagecount" not in html

    @BOTH_ENGINES
    def test_the_body_is_the_rendered_markdown(self, app, make_document, engine):
        document = make_document(title="Conteúdo", content=SAMPLE)
        html = rendered_html(app, document, engine)
        body = html.split("<body>", 1)[1].split("</body>", 1)[0].strip()

        assert body.startswith("<h1")
        assert "<strong>negrito</strong>" in body
        assert "<table>" in body


class TestTypographySurvives:
    """Page size, margins, font and theme are how the Markdown is set."""

    @BOTH_ENGINES
    @pytest.mark.parametrize("theme", ["classic", "minimal", "academic", "modern"])
    def test_every_theme_still_changes_the_typography(
        self, app, make_document, engine, theme
    ):
        document = make_document(title="Temas", content=SAMPLE)
        html = rendered_html(app, document, engine, {"theme": theme})
        assert "<style>" in html

    @BOTH_ENGINES
    def test_themes_differ_from_each_other(self, app, make_document, engine):
        document = make_document(title="Comparação", content=SAMPLE)
        classic = rendered_html(app, document, engine, {"theme": "classic"})
        modern = rendered_html(app, document, engine, {"theme": "modern"})
        assert classic != modern

    @BOTH_ENGINES
    @pytest.mark.parametrize("page_size", ["A4", "Letter"])
    def test_the_page_size_reaches_the_stylesheet(
        self, app, make_document, engine, page_size
    ):
        document = make_document(title="Tamanho", content=SAMPLE)
        html = rendered_html(app, document, engine, {"page_size": page_size})
        assert page_size.lower() in html.lower()

    @BOTH_ENGINES
    def test_the_margin_setting_reaches_the_page_rule(self, app, make_document, engine):
        document = make_document(title="Margens", content=SAMPLE)
        html = rendered_html(app, document, engine)
        assert "@page" in html
        assert "margin:" in html


class TestStillProducesAPdf:
    def test_a_document_with_a_heading(self, app, make_document):
        document = make_document(title="Com título", content=SAMPLE)
        pdf_bytes, filename = render_document_pdf(document)

        assert pdf_bytes.startswith(b"%PDF")
        assert filename == "com-titulo.pdf"

    def test_a_document_without_any_heading(self, app, make_document):
        """Nothing is injected, so a document with no heading has none."""
        document = make_document(title="Sem título interno", content="Apenas um parágrafo.")
        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")

    def test_an_empty_document(self, app, make_document):
        document = make_document(title="Vazio", content="")
        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")

    def test_the_route_still_serves_it(self, client, make_document):
        document = make_document(title="Pela rota", content=SAMPLE)
        response = client.get(f"/exportar/{document.uuid}/pdf")

        assert response.status_code == 200
        assert response.data.startswith(b"%PDF")


class TestSourceVariantIsUntouched:
    """The other export keeps its own header - it is a listing, not a document."""

    def test_the_source_listing_still_identifies_itself(self, app, make_document):
        document = make_document(title="Código-fonte", content=SAMPLE)
        with app.test_request_context():
            html = build_html(
                document,
                build_context(document),
                Xhtml2PdfEngine,
                variant=VARIANT_SOURCE,
            )

        assert "doc-header" in html
        assert "Código-fonte" in html
        assert "linhas" in html

    def test_the_source_pdf_still_generates(self, app, make_document):
        document = make_document(title="Fonte", content=SAMPLE)
        pdf_bytes, filename = render_document_pdf(document, variant=VARIANT_SOURCE)

        assert pdf_bytes.startswith(b"%PDF")
        assert filename == "fonte-markdown.pdf"


class TestSettingsMatchReality:
    def test_the_render_context_carries_no_chrome(self, app, document):
        context = build_context(document)

        for retired in ("header", "footer", "show_page_numbers", "app_name"):
            assert not hasattr(context, retired)

    def test_the_settings_screen_no_longer_offers_chrome(self, client, app):
        body = client.get("/configuracoes/").data.decode("utf-8")

        assert "pdf_header" not in body
        assert "pdf_footer" not in body
        assert "pdf_show_page_numbers" not in body
        # The source listing still stamps itself, so this one stays.
        assert "pdf_show_generated_date" in body
