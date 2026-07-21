"""PDF export of the raw Markdown source, alongside the rendered document."""

from __future__ import annotations

import pytest

from app.services.pdf_service import (
    MAX_SOURCE_LINES,
    VARIANT_RENDERED,
    VARIANT_SOURCE,
    render_document_pdf,
)

SAMPLE = """# Título do documento

Parágrafo com **negrito** e `código`.

- item um
- item dois

```python
def exemplo():
    return 42
```

| A | B |
|---|---|
| 1 | 2 |
"""


class TestSourceVariant:
    def test_source_pdf_is_generated(self, app, make_document):
        document = make_document(title="Guia técnico", content=SAMPLE)
        pdf_bytes, filename = render_document_pdf(document, variant=VARIANT_SOURCE)

        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 800
        assert filename == "guia-tecnico-markdown.pdf"

    def test_rendered_and_source_produce_different_files(self, app, make_document):
        document = make_document(title="Comparação", content=SAMPLE)

        rendered, rendered_name = render_document_pdf(document, variant=VARIANT_RENDERED)
        source, source_name = render_document_pdf(document, variant=VARIANT_SOURCE)

        assert rendered_name != source_name
        assert rendered != source

    def test_default_variant_is_the_rendered_document(self, app, make_document):
        document = make_document(title="Padrão", content=SAMPLE)
        _, filename = render_document_pdf(document)
        assert filename == "padrao.pdf"

    def test_empty_document_still_exports(self, app, make_document):
        document = make_document(title="Vazio", content="")
        pdf_bytes, _ = render_document_pdf(document, variant=VARIANT_SOURCE)
        assert pdf_bytes.startswith(b"%PDF")

    def test_accented_source_exports(self, app, make_document):
        document = make_document(
            title="Acentuação", content="# Ação\n\nÓrgão, coração e ímã."
        )
        pdf_bytes, filename = render_document_pdf(document, variant=VARIANT_SOURCE)
        assert pdf_bytes.startswith(b"%PDF")
        assert filename == "acentuacao-markdown.pdf"

    def test_very_long_source_is_capped(self, app, make_document):
        """A runaway document must not produce an unbounded listing."""
        body = "\n".join(f"linha {i}" for i in range(MAX_SOURCE_LINES + 500))
        document = make_document(title="Enorme", content=body)

        pdf_bytes, _ = render_document_pdf(document, variant=VARIANT_SOURCE)
        assert pdf_bytes.startswith(b"%PDF")

    @pytest.mark.parametrize("page_size", ["A4", "Letter"])
    def test_both_page_sizes(self, app, make_document, page_size):
        document = make_document(title="Tamanhos", content=SAMPLE)
        pdf_bytes, _ = render_document_pdf(
            document, {"page_size": page_size}, variant=VARIANT_SOURCE
        )
        assert pdf_bytes.startswith(b"%PDF")


class TestRoutes:
    def test_route_serves_the_source_variant(self, client, make_document):
        document = make_document(title="Rota fonte", content=SAMPLE)
        response = client.get(f"/exportar/{document.uuid}/pdf?formato=source")

        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF")
        assert "rota-fonte-markdown.pdf" in response.headers["Content-Disposition"]

    def test_route_defaults_to_rendered(self, client, make_document):
        document = make_document(title="Rota padrão", content=SAMPLE)
        response = client.get(f"/exportar/{document.uuid}/pdf")

        assert "rota-padrao.pdf" in response.headers["Content-Disposition"]

    def test_unknown_variant_falls_back_to_rendered(self, client, make_document):
        document = make_document(title="Variante inválida", content=SAMPLE)
        response = client.get(f"/exportar/{document.uuid}/pdf?formato=../../etc/passwd")

        assert response.status_code == 200
        assert "variante-invalida.pdf" in response.headers["Content-Disposition"]

    def test_post_form_carries_the_variant(self, client, make_document):
        document = make_document(title="Via formulário", content=SAMPLE)
        response = client.post(
            f"/exportar/{document.uuid}/pdf",
            data={"formato": "source", "tema": "modern", "tamanho": "Letter"},
        )

        assert response.status_code == 200
        assert "via-formulario-markdown.pdf" in response.headers["Content-Disposition"]

    def test_editor_offers_both_formats(self, client, document):
        body = client.get(f"/editor/{document.uuid}").data.decode("utf-8")
        assert 'value="rendered"' in body
        assert 'value="source"' in body
