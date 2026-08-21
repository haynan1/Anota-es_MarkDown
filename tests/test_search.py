"""Search, filtering, categories and tags."""

from __future__ import annotations

import re

from app.extensions import db
from app.repositories.document_repository import (
    SCOPE_ARCHIVED,
    SCOPE_TRASH,
    DocumentQuery,
    DocumentRepository,
)
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.services.document_service import DocumentService
from app.services.listing_service import list_documents
from app.services.search_service import search_index


def _search(term, **kwargs):
    """Search through the real entry point.

    ``DocumentRepository.paginate`` alone never consults the full-text index -
    resolving it is the listing service's job, so calling the repository
    directly would silently exercise only the LIKE fallback.
    """
    return list_documents(DocumentQuery(search=term, per_page=50, **kwargs)).pagination


class TestSearch:
    def test_finds_by_title(self, app, make_document):
        make_document(title="Guia de Google Ads", content="conteúdo qualquer")
        make_document(title="Outro assunto", content="nada a ver")

        results = _search("google")
        assert [item.title for item in results.items] == ["Guia de Google Ads"]

    def test_finds_by_body(self, app, make_document):
        make_document(title="Sem pistas no título", content="Falamos sobre orçamento aqui.")
        results = _search("orçamento")
        assert results.total == 1

    def test_is_case_insensitive(self, app, make_document):
        make_document(title="Markdown Studio", content="texto")
        assert _search("MARKDOWN").total == 1
        assert _search("markdown").total == 1

    def test_ignores_accents(self, app, make_document):
        """FTS5 with remove_diacritics makes "codigo" match "código"."""
        make_document(title="Relatório", content="Sobre código e histórico.")
        if search_index.available:
            assert _search("codigo").total == 1
            assert _search("relatorio").total == 1

    def test_multiple_terms_are_combined_with_and(self, app, make_document):
        make_document(title="Alpha", content="contém beta e gama")
        make_document(title="Delta", content="contém apenas beta")

        assert _search("beta gama").total == 1

    def test_no_results_returns_empty(self, app, make_document):
        make_document(title="Alguma coisa")
        assert _search("termoinexistentexyz").total == 0

    def test_trashed_documents_are_excluded(self, app, make_document):
        document = make_document(title="Documento secreto", content="palavra rara xyzzy")
        assert _search("xyzzy").total == 1

        DocumentService.move_to_trash(document)
        assert _search("xyzzy").total == 0

    def test_index_updates_when_content_changes(self, app, document):
        assert _search("palavraespecial").total == 0
        DocumentService.save(document, document.title, "Agora contém palavraespecial aqui.")
        assert _search("palavraespecial").total == 1

    def test_snippets_highlight_the_term(self, app, make_document):
        document = make_document(title="Guia", content="Um texto longo sobre marketing digital.")
        if not search_index.available:
            return
        snippets = search_index.snippets("marketing", [document.id])
        assert document.id in snippets
        assert "<mark>" in str(snippets[document.id])

    def test_snippet_escapes_html_before_highlighting(self, app, make_document):
        document = make_document(title="Perigo", content="Contém <b>tags</b> e xyzzy.")
        if not search_index.available:
            return
        snippets = search_index.snippets("xyzzy", [document.id])
        rendered = str(snippets.get(document.id, ""))
        assert "<b>" not in rendered
        assert "&lt;b&gt;" in rendered or "xyzzy" in rendered

    def test_match_expression_neutralises_fts_syntax(self, app):
        # Operators must never be interpreted - they are stripped to tokens.
        expression = search_index.build_match_expression('foo" OR bar NEAR(')
        assert expression.count('"') % 2 == 0
        assert "NEAR(" not in expression

    def test_search_route_renders_results(self, client, make_document):
        make_document(title="Encontrável", content="palavra procurada")
        response = client.get("/documentos/?q=procurada")
        assert response.status_code == 200
        assert "Encontrável".encode() in response.data

    def test_pressing_enter_in_the_box_only_searches(self, client, make_document):
        """The form's default button must be the nameless one next to the box.

        Implicit submission clicks the *first* submit button in the markup and
        sends its name and value along. With the "Favoritos" chip holding that
        position, pressing Enter after typing a term also turned favourites on
        and the reader got the two documents they had starred instead of the
        search they asked for.
        """
        make_document(title="Encontrável", content="palavra procurada")
        html = client.get("/documentos/?q=procurada").get_data(as_text=True)

        form = html[html.index('id="filters-form"') :]
        form = form[: form.index("</form>")]

        buttons = re.findall(r"<button[^>]*type=\"submit\"[^>]*>", form)
        assert buttons, "o formulário de filtros perdeu seus botões"
        assert "name=" not in buttons[0], (
            f"o botão padrão do formulário carrega um filtro: {buttons[0]}"
        )

    def test_suggestions_endpoint(self, client, make_document):
        make_document(title="Sugestão de teste", content="conteúdo")
        response = client.get("/api/busca?q=sugest")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert len(payload["results"]) == 1

    def test_suggestions_ignore_short_terms(self, client):
        payload = client.get("/api/busca?q=a").get_json()
        assert payload["results"] == []


class TestFilters:
    def test_filter_by_category(self, app, make_document):
        category = CategoryRepository.get_or_create("Guias")
        db.session.commit()
        make_document(title="Com categoria", category_id=category.id)
        make_document(title="Sem categoria")

        results = DocumentRepository.paginate(
            DocumentQuery(category_id=category.id, per_page=50)
        )
        assert results.total == 1

    def test_filter_by_tag(self, app, make_document):
        make_document(title="Etiquetado", tag_names=["projeto"])
        make_document(title="Simples")

        results = DocumentRepository.paginate(
            DocumentQuery(tag_slugs=("projeto",), per_page=50)
        )
        assert results.total == 1

    def test_filter_by_favorite(self, app, make_document):
        favorite = make_document(title="Favorito")
        make_document(title="Comum")
        DocumentService.toggle_favorite(favorite)

        results = DocumentRepository.paginate(
            DocumentQuery(only_favorites=True, per_page=50)
        )
        assert results.total == 1

    def test_archived_scope(self, app, make_document):
        archived = make_document(title="Arquivado")
        make_document(title="Ativo")
        DocumentService.set_archived(archived, True)

        assert DocumentRepository.paginate(DocumentQuery(per_page=50)).total == 1
        assert (
            DocumentRepository.paginate(
                DocumentQuery(scope=SCOPE_ARCHIVED, per_page=50)
            ).total
            == 1
        )

    def test_trash_scope(self, app, make_document):
        document = make_document(title="Na lixeira")
        DocumentService.move_to_trash(document)
        assert (
            DocumentRepository.paginate(DocumentQuery(scope=SCOPE_TRASH, per_page=50)).total
            == 1
        )

    def test_sorting_by_title(self, app, make_document):
        make_document(title="Zebra")
        make_document(title="Abacate")

        ascending = DocumentRepository.paginate(
            DocumentQuery(sort="title_asc", per_page=50)
        )
        assert ascending.items[0].title == "Abacate"

    def test_pagination_splits_results(self, app, make_document):
        for index in range(7):
            make_document(title=f"Documento {index}")

        first = DocumentRepository.paginate(DocumentQuery(per_page=3, page=1))
        assert len(first.items) == 3
        assert first.pages == 3
        assert first.has_next is True


class TestTaxonomy:
    def test_category_is_created_once(self, app):
        first = CategoryRepository.get_or_create("Guias", "#4F46E5")
        db.session.commit()
        second = CategoryRepository.get_or_create("guias")
        db.session.commit()
        assert first.id == second.id

    def test_category_gets_a_slug(self, app):
        category = CategoryRepository.get_or_create("Relatórios Mensais")
        db.session.commit()
        assert category.slug == "relatorios-mensais"

    def test_tags_are_deduplicated_by_slug(self, app):
        tags = TagRepository.resolve_many(["Projeto", "projeto", "PROJETO"])
        db.session.commit()
        assert len(tags) == 1

    def test_tags_attach_to_documents(self, app, make_document):
        document = make_document(title="Com etiquetas", tag_names=["a", "b", "c"])
        assert sorted(document.tag_names) == ["a", "b", "c"]

    def test_orphan_tags_are_cleaned_up(self, app, make_document):
        document = make_document(title="Temporário", tag_names=["efemera"])
        DocumentService.move_to_trash(document)
        DocumentService.purge(document)
        assert TagRepository.get_by_slug("efemera") is None

    def test_deleting_a_category_keeps_its_documents(self, app, make_document, db):
        category = CategoryRepository.get_or_create("Descartável")
        db.session.commit()
        document = make_document(title="Sobrevivente", category_id=category.id)

        db.session.delete(category)
        db.session.commit()
        db.session.refresh(document)

        assert document.id is not None
        assert document.category_id is None

    def test_categories_page_renders(self, client, app):
        CategoryRepository.get_or_create("Visível")
        db.session.commit()
        response = client.get("/documentos/categorias")
        assert response.status_code == 200
        assert "Visível".encode() in response.data

    def test_create_category_via_route(self, client, app):
        response = client.post(
            "/documentos/categorias",
            data={"name": "Nova categoria", "color": "#16A34A"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert CategoryRepository.get_by_name("Nova categoria") is not None

    def test_invalid_category_color_is_rejected(self, client, app):
        client.post(
            "/documentos/categorias",
            data={"name": "Cor inválida", "color": "vermelho"},
            follow_redirects=True,
        )
        assert CategoryRepository.get_by_name("Cor inválida") is None
