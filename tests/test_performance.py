"""Performance characteristics that must not regress.

Query counts matter more than wall-clock here: a listing that issues one query
per document still feels fast with 10 documents and falls over at 500.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import event

from app.extensions import db
from app.repositories.document_repository import DocumentQuery, DocumentRepository
from app.repositories.taxonomy_repository import CategoryRepository


class QueryCounter:
    """Counts SQL statements issued inside the ``with`` block."""

    def __init__(self):
        self.statements: list[str] = []

    def __enter__(self):
        self._listener = lambda conn, cursor, stmt, params, ctx, many: (
            self.statements.append(stmt)
        )
        event.listen(db.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, "before_cursor_execute", self._listener)
        return False

    @property
    def count(self) -> int:
        return len(self.statements)


@pytest.fixture()
def many_documents(app, make_document):
    """A corpus large enough for N+1 problems to show up."""
    category = CategoryRepository.get_or_create("Categoria", "#4F46E5")
    db.session.commit()
    for index in range(30):
        make_document(
            title=f"Documento {index:03d}",
            content=f"# Documento {index}\n\n" + ("palavra " * 200),
            category_id=category.id,
            tag_names=["alpha", "beta", f"tag{index % 5}"],
        )
    db.session.expire_all()
    return 30


class TestQueryCounts:
    def test_listing_does_not_scale_queries_with_documents(self, app, many_documents):
        """The count must be flat regardless of page size."""
        with QueryCounter() as small:
            DocumentRepository.paginate(DocumentQuery(per_page=5))
        with QueryCounter() as large:
            DocumentRepository.paginate(DocumentQuery(per_page=30))

        assert large.count == small.count, (
            f"N+1: {small.count} consultas para 5 documentos, "
            f"{large.count} para 30."
        )
        assert large.count <= 6, f"{large.count} consultas para uma listagem"

    def test_listing_page_query_count_is_bounded(self, client, many_documents):
        with QueryCounter() as counter:
            response = client.get("/documentos/?visual=cards")
        assert response.status_code == 200
        assert counter.count <= 12, f"{counter.count} consultas na listagem"

    def test_dashboard_query_count_is_bounded(self, client, many_documents):
        with QueryCounter() as counter:
            response = client.get("/")
        assert response.status_code == 200
        assert counter.count <= 15, f"{counter.count} consultas no painel"

    def test_editor_query_count_is_bounded(self, client, document):
        with QueryCounter() as counter:
            response = client.get(f"/editor/{document.uuid}")
        assert response.status_code == 200
        assert counter.count <= 15, f"{counter.count} consultas no editor"

    def test_listing_does_not_load_document_bodies(self, app, many_documents):
        """Bodies are deferred; loading 30 of them would move megabytes.

        Only the row-fetching statement is inspected. The paginator's COUNT
        wraps a subquery that names every column, but SQLite's planner drops
        the unreferenced ones — measured at 0.0 ms overhead against a 400
        document / 41 MB corpus, resolved by a covering index.
        """
        with QueryCounter() as counter:
            DocumentRepository.paginate(DocumentQuery(per_page=30))

        fetches = [
            s for s in counter.statements
            if "FROM documents" in s and "count(" not in s.lower()
        ]
        assert fetches, "nenhuma consulta de dados a documents"
        assert not any("content_markdown" in s for s in fetches), (
            "a listagem está carregando content_markdown"
        )
        assert not any("rendered_html" in s for s in fetches)

    def test_listing_does_not_use_distinct(self, app, many_documents):
        """DISTINCT would be dedup work the planner cannot skip.

        Tag filters are EXISTS subqueries and the category join is
        many-to-one, so no row is ever duplicated and DISTINCT buys nothing.
        """
        with QueryCounter() as counter:
            DocumentRepository.paginate(
                DocumentQuery(per_page=30, tag_slugs=("alpha", "beta"))
            )

        fetches = [
            s for s in counter.statements
            if "FROM documents" in s and "count(" not in s.lower()
        ]
        assert not any("DISTINCT" in s.upper() for s in fetches)

    def test_tag_filtering_returns_each_document_once(self, app, many_documents):
        """The guarantee that makes dropping DISTINCT safe."""
        page = DocumentRepository.paginate(
            DocumentQuery(per_page=50, tag_slugs=("alpha", "beta"))
        )
        ids = [document.id for document in page.items]
        assert len(ids) == len(set(ids)), "documento duplicado na listagem"
        assert page.total == len(ids)

    def test_history_listing_does_not_load_version_bodies(self, app, document):
        from app.repositories.version_repository import VersionRepository
        from app.services.document_service import DocumentService

        for index in range(10):
            DocumentService.save(document, document.title, f"Versão {index}.")

        with QueryCounter() as counter:
            VersionRepository.paginate(document.id, per_page=10)

        fetches = [
            s for s in counter.statements
            if "document_versions" in s and "count(" not in s.lower()
        ]
        assert fetches, "nenhuma consulta de dados a document_versions"
        assert not any("content_markdown" in s for s in fetches)


class TestLargeCorpus:
    """Behaviour with documents big enough for a bad query to hurt."""

    @pytest.fixture()
    def heavy_documents(self, app):
        from app.models import Document

        body = "palavra " * 6000  # ~48 KB each
        for index in range(60):
            db.session.add(
                Document(
                    title=f"Pesado {index:03d}",
                    slug=f"pesado-{index:03d}",
                    content_markdown=body,
                    rendered_html=f"<p>{body}</p>",
                    excerpt=body[:300],
                    content_hash=f"hash-{index}",
                    word_count=6000,
                    character_count=len(body),
                )
            )
        db.session.commit()
        db.session.expire_all()
        return 60

    def test_listing_a_heavy_corpus_stays_fast(self, app, heavy_documents):
        started = time.perf_counter()
        page = DocumentRepository.paginate(DocumentQuery(per_page=12))
        elapsed = time.perf_counter() - started

        assert page.total == 60
        assert elapsed < 1.0, f"listagem de corpus pesado levou {elapsed:.2f}s"

    def test_listing_page_of_a_heavy_corpus_stays_small(self, client, heavy_documents):
        """~2.9 MB of bodies exist; the page must ship only excerpts."""
        response = client.get("/documentos/?visual=cards")
        assert response.status_code == 200
        assert len(response.data) < 300_000, f"{len(response.data)} bytes"


class TestBulkOperationsDoNotScaleQueries:
    """Backup and reindex must issue a constant number of queries.

    Both were N+1 before the engineering pass: the export ran one query per
    document to load its history (65 queries for 60 documents), and the
    reindex opened a SAVEPOINT plus two statements per document (244).
    """

    @pytest.fixture()
    def corpus_with_history(self, app, make_document):
        from app.services.document_service import DocumentService

        for index in range(25):
            document = make_document(title=f"Doc {index:03d}", content="corpo inicial")
            DocumentService.save(document, document.title, f"revisão A {index}")
            DocumentService.save(document, document.title, f"revisão B {index}")
        db.session.expire_all()
        return 25

    def test_backup_export_is_not_n_plus_one(self, app, corpus_with_history):
        from app.services.backup_service import build_export_payload

        with QueryCounter() as counter:
            payload = build_export_payload()

        assert len(payload["documents"]) == corpus_with_history
        assert all(entry["versions"] for entry in payload["documents"])

        version_queries = [
            s for s in counter.statements
            if "document_versions" in s and "SELECT" in s
        ]
        assert len(version_queries) <= 2, (
            f"N+1 no backup: {len(version_queries)} consultas de versões "
            f"para {corpus_with_history} documentos"
        )
        assert counter.count <= 10, f"{counter.count} consultas no export"

    def test_backup_query_count_is_flat(self, app, make_document):
        from app.services.backup_service import build_export_payload

        for index in range(5):
            make_document(title=f"Pequeno {index}")
        db.session.expire_all()
        with QueryCounter() as small:
            build_export_payload()

        for index in range(25):
            make_document(title=f"Extra {index}")
        db.session.expire_all()
        with QueryCounter() as large:
            build_export_payload()

        assert large.count == small.count, (
            f"consultas cresceram de {small.count} para {large.count} "
            "ao adicionar documentos"
        )

    def test_reindex_is_batched(self, app, corpus_with_history):
        from app.services.search_service import search_index

        db.session.expire_all()
        with QueryCounter() as counter:
            total = search_index.rebuild()

        assert total == corpus_with_history
        assert counter.count <= 8, (
            f"{counter.count} consultas para reindexar {corpus_with_history} documentos"
        )

    def test_reindex_does_not_load_rendered_html(self, app, corpus_with_history):
        """Only id/title/body are needed; rendered_html would double the read."""
        from app.services.search_service import search_index

        db.session.expire_all()
        with QueryCounter() as counter:
            search_index.rebuild()

        selects = [s for s in counter.statements if "SELECT" in s and "documents" in s]
        assert not any("rendered_html" in s for s in selects)

    def test_emptying_the_trash_does_not_load_bodies(self, app, make_document):
        from app.services.document_service import DocumentService

        for index in range(10):
            document = make_document(title=f"Lixo {index}", content="corpo " * 500)
            DocumentService.move_to_trash(document)
        db.session.expire_all()

        with QueryCounter() as counter:
            removed = DocumentService.empty_trash()

        assert removed == 10
        selects = [
            s for s in counter.statements
            if "SELECT" in s and "FROM documents" in s and "count(" not in s.lower()
        ]
        assert not any("content_markdown" in s for s in selects), (
            "esvaziar a lixeira está carregando o corpo dos documentos"
        )


class TestResponseTimes:
    BUDGET = 2.0

    @pytest.mark.parametrize(
        "path", ["/", "/documentos/", "/lixeira/", "/configuracoes/"]
    )
    def test_pages_render_within_budget(self, client, many_documents, path):
        started = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert elapsed < self.BUDGET, f"{path} levou {elapsed:.2f}s"

    def test_search_is_fast_on_a_full_corpus(self, client, many_documents):
        started = time.perf_counter()
        response = client.get("/documentos/?q=documento")
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert elapsed < self.BUDGET, f"busca levou {elapsed:.2f}s"

    def test_preview_round_trip_is_fast(self, client, app):
        """The preview runs on every keystroke pause; it must stay snappy."""
        body = "\n\n".join(f"## Seção {i}\n\nTexto com **negrito**." for i in range(50))

        started = time.perf_counter()
        response = client.post("/api/preview", json={"content_markdown": body})
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert elapsed < 1.0, f"preview levou {elapsed:.2f}s"


class TestPayloadSizes:
    def test_listing_response_stays_small(self, client, many_documents):
        """Excerpts are capped so a listing never ships full document bodies."""
        response = client.get("/documentos/?visual=cards")
        assert len(response.data) < 400_000, f"{len(response.data)} bytes"

    def test_excerpts_are_capped(self, app, make_document):
        document = make_document(title="Longo", content="palavra " * 5000)
        assert len(document.excerpt) <= 320
