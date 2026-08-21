"""What comes back first, and what comes back at all.

Three separate ways the search could know about a document and still fail to
show it: it never matched a fragment inside a word, it ranked a passing
mention in a body level with the title of the document itself, and the listing
screen sorted every result by date before any of that reached the reader.
"""

from __future__ import annotations

import pytest

from app.repositories.document_repository import (
    SORT_RELEVANCE,
    DocumentQuery,
    DocumentRepository,
)
from app.services.listing_service import list_documents
from app.services.search_service import search_index


def _titles(term, **kwargs):
    kwargs.setdefault("sort", SORT_RELEVANCE)
    pagination = list_documents(
        DocumentQuery(search=term, per_page=50, **kwargs)
    ).pagination
    return [item.title for item in pagination.items]


@pytest.fixture()
def library(make_document):
    """A body that mentions a word the way a hundred documents do."""
    make_document(title="Hipertrofia Muscular", content="Treino e volume.")
    make_document(title="Proteína no dia a dia", content="Quanto consumir.")
    make_document(
        title="Notas soltas",
        content="Falamos de hipertrofia, de proteína e de mais uma dúzia de assuntos.",
    )
    return None


class TestFragmentsOfATitle:
    """The complaint in one sentence: part of a title must be enough."""

    def test_finds_a_fragment_from_the_middle_of_a_word(self, app, library):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        assert _titles("trofia")[0] == "Hipertrofia Muscular"

    def test_a_fragment_ignores_accents(self, app, library):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        assert "Proteína no dia a dia" in _titles("roteina")

    def test_a_fragment_ignores_case(self, app, library):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        assert _titles("TROFIA")[0] == "Hipertrofia Muscular"

    def test_a_fragment_may_span_a_space(self, app, make_document):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        make_document(title="Manual Blog", content="qualquer coisa")
        assert _titles("anual b") == ["Manual Blog"]

    def test_two_characters_are_left_to_the_prefix_search(self, app, library):
        """Below one trigram there is nothing to look up, and no error either."""
        assert search_index.build_substring_expression("ab") == ""
        assert _titles("hi")  # the word index still answers

    def test_quotes_in_the_term_cannot_break_the_query(self, app, make_document):
        make_document(title='Aspas "no" título', content="corpo")
        assert _titles('"no"') is not None  # no exception, whatever it matches


class TestRanking:
    def test_the_document_named_after_the_term_comes_first(self, app, library):
        """A body mentioning the word must not outrank the title carrying it."""
        assert _titles("hipertrofia")[0] == "Hipertrofia Muscular"

    def test_an_exact_title_wins_over_a_document_that_merely_mentions_it(
        self, app, make_document
    ):
        make_document(title="Sveltia CMS", content="Notas curtas.")
        for index in range(5):
            make_document(
                title=f"Artigo {index}",
                content="Publicado com Sveltia CMS, como todos os outros.",
            )
        assert _titles("Sveltia")[0] == "Sveltia CMS"

    def test_an_empty_result_stays_empty(self, app, library):
        """The -1 sentinel: no match must mean no rows, not "no filter"."""
        assert _titles("assuntoquenaoexisteemlugarnenhum") == []


class TestListingDefaults:
    def test_a_search_is_ranked_and_not_dated(self, client, make_document):
        """The toolbar used to pin `ordem=updated_desc` onto every search."""
        make_document(title="Alvo exato", content="curto")
        make_document(title="Depois", content="Cita alvo exato de passagem.")

        html = client.get(
            "/documentos/", query_string={"q": "Alvo exato"}
        ).get_data(as_text=True)
        assert html.index("Alvo exato") < html.index("Depois")

    def test_relevance_is_offered_only_while_searching(self, client, make_document):
        make_document(title="Qualquer um")

        assert "Mais relevantes" not in client.get("/documentos/").get_data(as_text=True)
        assert "Mais relevantes" in client.get(
            "/documentos/", query_string={"q": "qualquer"}
        ).get_data(as_text=True)

    def test_an_explicit_order_is_still_honoured(self, client, make_document):
        make_document(title="Zebra", content="assunto comum")
        make_document(title="Abelha", content="assunto comum")

        html = client.get(
            "/documentos/", query_string={"q": "assunto", "ordem": "title_asc"}
        ).get_data(as_text=True)
        assert html.index("Abelha") < html.index("Zebra")

    def test_relevance_never_leaks_into_a_listing_with_no_search(self, app):
        query = DocumentQuery(search="", sort=SORT_RELEVANCE)
        # Nothing to rank against, so the CASE ordering must not be built.
        assert query.matched_ids is None
        DocumentRepository.paginate(query)  # would raise on an empty CASE


class TestIndexLifecycle:
    def test_a_renamed_document_is_findable_by_its_new_title(self, app, make_document):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        from app.services.document_service import DocumentService

        document = make_document(title="Nome antigo", content="corpo")
        DocumentService.rename(document, "Hipertrofia revisada")

        assert _titles("trofia") == ["Hipertrofia revisada"]
        assert _titles("antigo") == []

    def test_a_trashed_document_leaves_both_indexes(self, app, make_document):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        from app.services.document_service import DocumentService

        document = make_document(title="Hipertrofia passageira", content="corpo")
        DocumentService.move_to_trash(document)

        assert _titles("trofia") == []
        assert _titles("hipertrofia") == []

    def test_rebuild_restores_both_indexes(self, app, make_document):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        make_document(title="Hipertrofia Muscular", content="corpo")

        assert search_index.rebuild() == 1
        assert _titles("trofia") == ["Hipertrofia Muscular"]
        assert _titles("muscular") == ["Hipertrofia Muscular"]

    def test_an_existing_library_is_backfilled_when_the_index_appears(
        self, app, make_document, db
    ):
        """The upgrade path: documents indexed before the title index existed.

        ``CREATE ... IF NOT EXISTS`` is silent about which of the two it did,
        so without the backfill an installation that already had documents
        would come back from the upgrade with mid-word search quietly dead.
        """
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        from sqlalchemy import text

        from app.services.search_service import TITLES_TABLE

        make_document(title="Hipertrofia Muscular", content="corpo")
        db.session.execute(text(f"DROP TABLE {TITLES_TABLE}"))
        db.session.commit()

        search_index.ensure()
        assert _titles("trofia") == ["Hipertrofia Muscular"]


class TestHostileInput:
    """The search box takes whatever is typed into it.

    Nothing here may reach SQLite as syntax, and nothing here may leave the
    index in a worse state than it found it.
    """

    PAYLOADS = [
        '"', '""', '"""', "*", "^abc", "abc*", "a OR b", "a AND b", "NOT a",
        "(a)", "a:b", "NEAR(a b)", "'; DROP TABLE documents;--", "' OR '1'='1",
        "<script>alert(1)</script>", "../../../etc/passwd", "%", "_", "\\",
        "\x00abc", "\x1f\x7f trofia", "trofia" * 40,
    ]

    def test_no_payload_raises_or_empties_the_library(self, app, library, db):
        from app.models import Document

        for payload in self.PAYLOADS:
            _titles(payload)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 3

    def test_no_payload_disables_the_index(self, app, library):
        """A query the engine refuses says nothing about whether it works.

        A NUL byte in the term used to mark the title index dead for the whole
        database, so one odd search cost every later one its mid-word matches.
        """
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        for payload in self.PAYLOADS:
            _titles(payload)
            assert search_index.titles_available, payload
        assert _titles("trofia") == ["Hipertrofia Muscular"]

    def test_the_expression_is_always_one_quoted_literal(self, app):
        for payload in self.PAYLOADS:
            expression = search_index.build_substring_expression(payload)
            if not expression:
                continue
            assert expression.startswith('"') and expression.endswith('"')
            # Every inner quote doubled: the FTS5 escape, so the string can
            # never close early and let the rest be read as query syntax.
            assert expression.count('"') % 2 == 0

    def test_control_characters_are_dropped_not_forwarded(self, app):
        assert search_index.build_substring_expression("\x00tro\x1ffia") == '"trofia"'


class TestRebuildBatching:
    """The rebuild streams in batches, and both indexes ride the same batch.

    A small ``batch_size`` exercises the same branch a 500-document library
    would, without paying for 500 documents.
    """

    def test_every_batch_reaches_both_indexes(self, app, make_document):
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        for index in range(5):
            make_document(title=f"Hipertrofia parte {index}", content=f"corpo {index}")

        assert search_index.rebuild(batch_size=2) == 5
        # Five documents across three batches, the last one short.
        assert len(_titles("trofia")) == 5
        assert len(_titles("corpo")) == 5

    def test_a_populated_index_is_not_refilled(self, app, make_document, db):
        """``ensure`` runs on every boot; the backfill must be a one-off."""
        if not search_index.titles_available:
            pytest.skip("SQLite sem o tokenizador trigram")
        from sqlalchemy import text

        from app.services.search_service import TITLES_TABLE

        make_document(title="Hipertrofia Muscular", content="corpo")
        before = db.session.scalar(text(f"SELECT count(*) FROM {TITLES_TABLE}"))

        search_index.ensure()
        search_index.ensure()

        assert db.session.scalar(text(f"SELECT count(*) FROM {TITLES_TABLE}")) == before
        assert _titles("trofia") == ["Hipertrofia Muscular"]
