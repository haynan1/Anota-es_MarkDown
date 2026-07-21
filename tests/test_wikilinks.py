"""Obsidian-style [[links]] between documents."""

from __future__ import annotations

import re

import pytest

from app.services.markdown_service import render_markdown
from app.services.wikilink_service import collect_targets


class TestResolution:
    def test_link_to_an_existing_document(self, app, make_document):
        target = make_document(title="Guia de vendas", content="conteúdo")
        html = render_markdown("Veja o [[Guia de vendas]] para detalhes.")

        assert f'href="/editor/{target.uuid}"' in html
        assert 'class="wikilink"' in html
        assert ">Guia de vendas</a>" in html

    def test_links_point_at_the_uuid_not_the_slug(self, app, make_document):
        """Renaming must not break links that already exist."""
        from app.services.document_service import DocumentService

        target = make_document(title="Nome original")
        html_before = render_markdown("[[Nome original]]")
        assert target.uuid in html_before

        DocumentService.rename(target, "Nome completamente diferente")
        # The old text no longer resolves, but any link already rendered by
        # UUID keeps working - that is the point of not using the slug.
        assert f"/editor/{target.uuid}" in html_before

    def test_custom_label(self, app, make_document):
        target = make_document(title="Guia de vendas")
        html = render_markdown("Consulte [[Guia de vendas|o nosso guia]].")

        assert f'href="/editor/{target.uuid}"' in html
        assert ">o nosso guia</a>" in html
        assert "Guia de vendas" in html  # kept as the title attribute

    def test_matching_ignores_case_and_accents(self, app, make_document):
        target = make_document(title="Relatório Mensal")

        for written in ["[[Relatório Mensal]]", "[[relatorio mensal]]", "[[RELATORIO MENSAL]]"]:
            assert target.uuid in render_markdown(written), written

    def test_matching_ignores_punctuation_drift(self, app, make_document):
        target = make_document(title="Guia: vendas 2026")
        assert target.uuid in render_markdown("[[guia vendas 2026]]")

    def test_missing_target_renders_as_a_creation_link(self, app):
        html = render_markdown("Veja [[Documento que não existe]].")

        assert "wikilink-missing" in html
        assert "novo-por-titulo" in html
        assert ">Documento que não existe</a>" in html

    def test_trashed_documents_are_not_link_targets(self, app, make_document):
        from app.services.document_service import DocumentService

        target = make_document(title="Vai para a lixeira")
        DocumentService.move_to_trash(target)

        html = render_markdown("[[Vai para a lixeira]]")
        assert "wikilink-missing" in html
        assert target.uuid not in html

    def test_multiple_links_in_one_document(self, app, make_document):
        first = make_document(title="Primeiro")
        second = make_document(title="Segundo")

        html = render_markdown("[[Primeiro]] e depois [[Segundo]] e [[Inexistente]].")

        assert first.uuid in html
        assert second.uuid in html
        assert html.count("wikilink") >= 3
        assert "wikilink-missing" in html


class TestParsing:
    def test_collect_targets_normalises(self, app):
        found = collect_targets("[[Um Título]] e [[outro-titulo]] e [[Um Titulo]]")
        assert found == {"um-titulo", "outro-titulo"}

    def test_empty_link_is_left_alone(self, app):
        html = render_markdown("Isto [[]] não é um link.")
        assert "wikilink" not in html

    def test_does_not_swallow_normal_markdown_links(self, app):
        html = render_markdown("[texto normal](https://exemplo.com)")
        assert 'href="https://exemplo.com"' in html
        assert "wikilink" not in html

    def test_does_not_break_reference_style_brackets(self, app):
        html = render_markdown("Uma lista [1] e [2] entre colchetes.")
        assert "wikilink" not in html

    def test_link_inside_code_is_not_converted(self, app, make_document):
        make_document(title="Alvo")
        html = render_markdown("Use `[[Alvo]]` na sintaxe.")
        assert "wikilink" not in html

    def test_link_inside_a_fenced_block_is_not_converted(self, app, make_document):
        make_document(title="Alvo")
        html = render_markdown("```\n[[Alvo]]\n```")
        assert "wikilink" not in html

    def test_newlines_do_not_span_links(self, app):
        html = render_markdown("[[quebra\nde linha]]")
        assert "wikilink" not in html


class TestSecurity:
    @pytest.mark.parametrize(
        "payload",
        [
            "[[javascript:alert(1)]]",
            "[[<script>alert(1)</script>]]",
            '[[x"onmouseover="alert(1)]]',
            "[[../../etc/passwd]]",
            "[[Alvo|<img src=x onerror=alert(1)>]]",
        ],
    )
    def test_hostile_link_targets_are_neutralised(self, app, payload):
        """Assert on structure, not on substrings.

        A naive search fails here for the wrong reason: "javascript:" appears
        inside the *query string* of an internal href
        (``/documentos/novo-por-titulo?titulo=javascript:alert(1)``), which is
        inert. What matters is that no tag carries an event handler and no
        href resolves to an executable scheme.
        """
        html = render_markdown(payload)

        assert "<script" not in html.lower()

        # No element may carry an on* attribute.
        assert not re.search(r"<[a-z]+[^>]*\son\w+\s*=", html, re.I), html

        # Every href must be a relative, same-origin path.
        for href in re.findall(r'href="([^"]*)"', html):
            scheme = href.split(":", 1)[0].lower() if ":" in href.split("/")[0] else ""
            assert scheme not in {"javascript", "vbscript", "data", "file"}, href
            assert href.startswith("/") and not href.startswith("//"), href

        # Quotes inside attribute values must stay escaped.
        for tag in re.findall(r"<a\b[^>]*>", html):
            assert tag.count('"') % 2 == 0, tag

    def test_target_never_escapes_the_application(self, app):
        """A wiki link may only ever produce an internal href."""
        html = render_markdown("[[https://evil.test/phish]]")

        for href in re.findall(r'href="([^"]*)"', html):
            assert href.startswith("/"), href
            assert not href.startswith("//"), href


class TestPerformance:
    def test_resolution_is_a_single_query(self, app, make_document):
        """Twenty links must not mean twenty queries."""
        from tests.test_performance import QueryCounter

        for index in range(10):
            make_document(title=f"Alvo {index}")

        body = " ".join(f"[[Alvo {i}]]" for i in range(10)) + " [[Inexistente]]"

        with QueryCounter() as counter:
            html = render_markdown(body)

        assert html.count("wikilink") >= 11
        assert counter.count <= 2, f"{counter.count} consultas para 11 links"


class TestRoutes:
    def test_following_a_broken_link_offers_to_create_the_document(self, client, app, db):
        """The GET asks; it must not write."""
        from app.models import Document

        response = client.get("/documentos/novo-por-titulo?titulo=Documento%20novo")

        assert response.status_code == 200
        assert "Documento novo" in response.get_data(as_text=True)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

    def test_confirming_creates_the_document(self, client, app, db):
        from app.models import Document

        response = client.post(
            "/documentos/novo-por-titulo",
            data={"titulo": "Documento novo"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        created = db.session.scalars(
            db.select(Document).where(Document.title == "Documento novo")
        ).unique().one_or_none()
        assert created is not None
        assert f"/editor/{created.uuid}" in response.headers["Location"]

    def test_creation_link_sanitises_the_title(self, client, app, db):
        from app.models import Document

        client.get("/documentos/novo-por-titulo?titulo=<script>alert(1)</script>")

        titles = [d.title for d in db.session.scalars(db.select(Document)).unique().all()]
        assert not any("<script" in title for title in titles)

    def test_rendered_link_is_clickable_in_the_editor(self, client, app, make_document):
        target = make_document(title="Alvo do link")
        source = make_document(title="Origem", content="Veja [[Alvo do link]].")

        body = client.get(f"/editor/{source.uuid}").data.decode("utf-8")
        assert f"/editor/{target.uuid}" in body
