"""Selecting every result of a filter, rather than only the page in front of you.

The listing paginates at five documents in the test configuration, so anything
here that creates eight is deliberately creating a selection that cannot be
expressed with the checkboxes on screen.
"""

from __future__ import annotations

import re

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Document
from app.repositories.taxonomy_repository import CategoryRepository
from app.services import selection_service
from app.services.document_service import DocumentService


@pytest.fixture()
def category(app):
    created = CategoryRepository.get_or_create("Ensaios", "#4F46E5")
    db.session.commit()
    return created


@pytest.fixture()
def library(app, make_document, category):
    """Eight documents in one category - more than a page holds."""
    return [
        make_document(title=f"Ensaio {index}", category_id=category.id)
        for index in range(8)
    ]


_WHITESPACE = re.compile(r"\s+")


def form(**fields) -> MultiDict:
    return MultiDict(fields)


def filtered(filters: str) -> MultiDict:
    return form(selecao=selection_service.MODE_FILTER, filtros=filters)


class TestResolveTickedBoxes:
    def test_the_uuids_the_client_sent(self, app, library):
        chosen = library[:3]
        selection = selection_service.resolve(
            MultiDict([("uuids", document.uuid) for document in chosen])
        )

        assert selection.from_filters is False
        assert set(selection.ids) == {document.id for document in chosen}

    def test_an_unknown_uuid_is_dropped_instead_of_failing(self, app, document):
        selection = selection_service.resolve(
            MultiDict([("uuids", document.uuid), ("uuids", "nao-existe")])
        )
        assert selection.ids == [document.id]

    def test_the_ticked_ceiling_still_applies(self, app, library):
        selection = selection_service.resolve(
            MultiDict([("uuids", document.uuid) for document in library]), limit=2
        )
        assert len(selection) == 2


class TestResolveEveryResult:
    def test_it_reaches_past_the_first_page(self, app, library, category):
        selection = selection_service.resolve(filtered(f"categoria={category.id}"))

        assert selection.from_filters is True
        assert len(selection) == 8
        assert set(selection.ids) == {document.id for document in library}

    def test_it_honours_the_filter_it_was_given(self, app, library, make_document):
        make_document(title="Fora da categoria")
        selection = selection_service.resolve(filtered("categoria=@sem"))

        titles = _titles_of(selection.ids)
        assert titles == ["Fora da categoria"]

    def test_it_honours_the_scope(self, app, library):
        DocumentService.set_archived(library[0], True)

        active = selection_service.resolve(filtered(""))
        archived = selection_service.resolve(filtered("escopo=archived"))

        assert len(active) == 7
        assert archived.ids == [library[0].id]

    def test_it_honours_a_search(self, app, library, make_document):
        make_document(title="Agulha", content="palavra rara xyzzy")

        selection = selection_service.resolve(filtered("q=xyzzy"))

        assert _titles_of(selection.ids) == ["Agulha"]

    def test_the_trash_stays_out_of_reach(self, app, library):
        DocumentService.move_to_trash(library[0])

        # There is no scope a listing URL can name that reaches deleted
        # documents, so no filter posted back can select them either.
        for filters in ("", "escopo=trash", "escopo=all"):
            selection = selection_service.resolve(filtered(filters))
            assert library[0].id not in selection.ids

    def test_a_garbled_filter_degrades_to_the_default_listing(self, app, library):
        selection = selection_service.resolve(
            filtered("categoria=abacaxi&ordem=&pagina=-3&escopo=inventado")
        )
        assert len(selection) == 8

    def test_a_page_number_is_ignored_because_a_selection_has_no_page(
        self, app, library
    ):
        selection = selection_service.resolve(filtered("pagina=2"))
        assert len(selection) == 8

    def test_the_ceiling_is_reported_rather_than_applied_in_silence(
        self, app, library, monkeypatch
    ):
        monkeypatch.setattr(selection_service, "MAX_FILTER_SELECTION", 3)

        selection = selection_service.resolve(filtered(""))

        assert len(selection) == 3
        assert selection.truncated is True

    def test_a_set_that_fits_is_not_reported_as_truncated(self, app, library):
        assert selection_service.resolve(filtered("")).truncated is False


class TestBulkActionsOverEveryResult:
    def test_archiving_reaches_every_page(self, client, library, category, db):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={
                "acao": "archive",
                "selecao": "filtro",
                "filtros": f"categoria={category.id}",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        for document in library:
            db.session.refresh(document)
            assert document.is_archived is True

    def test_the_ticked_boxes_still_win_when_no_mode_is_sent(
        self, client, library, db
    ):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "archive", "uuids": [library[0].uuid]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        db.session.refresh(library[0])
        db.session.refresh(library[1])
        assert library[0].is_archived is True
        assert library[1].is_archived is False

    def test_a_truncated_selection_says_so(self, client, library, monkeypatch):
        monkeypatch.setattr(selection_service, "MAX_FILTER_SELECTION", 3)

        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "archive", "selecao": "filtro", "filtros": ""},
            follow_redirects=True,
        )

        assert "limitada aos".encode() in response.data

    def test_a_filter_matching_nothing_is_refused(self, client, library):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "archive", "selecao": "filtro", "filtros": "q=inexistente"},
            follow_redirects=True,
        )
        assert "Selecione ao menos um".encode() in response.data

    def test_grouping_reaches_every_page(self, client, library, app):
        from app.repositories.group_repository import GroupRepository
        from app.services.group_service import GroupService

        group = GroupService.create("Tudo")

        client.post(
            "/documentos/acoes-em-massa",
            data={
                "acao": "group",
                "grupo": group.uuid,
                "selecao": "filtro",
                "filtros": "",
            },
            follow_redirects=True,
        )

        assert len(GroupRepository.documents_of(group)) == 8

    def test_exporting_packs_every_result(self, client, library, category, make_document):
        make_document(title="Fora da categoria")

        response = client.post(
            "/exportar/markdown/selecao",
            data={
                "selecao": "filtro",
                "filtros": f"categoria={category.id}",
            },
        )

        from tests.test_bulk_markdown import members

        entries = members(response.data)
        assert len(entries) == 8
        assert not any("fora-da-categoria" in name for name in entries)


class TestListingMarkup:
    def test_the_widening_offer_ships_when_there_is_more_than_one_page(
        self, client, library
    ):
        body = _text(client.get("/documentos/"))

        assert "data-bulk-select-everything" in body
        assert "Selecionar todos os 8 resultados" in body

    def test_it_stays_away_when_everything_already_fits(self, client, document):
        assert "data-bulk-select-everything" not in _text(client.get("/documentos/"))

    def test_the_offer_never_promises_more_than_the_ceiling_allows(
        self, client, library, monkeypatch
    ):
        from app.blueprints.documents import routes

        monkeypatch.setattr(routes, "MAX_FILTER_SELECTION", 3)
        body = _text(client.get("/documentos/"))

        assert "Selecionar os 3 primeiros de 8 resultados" in body

    def test_the_bar_carries_the_filters_it_was_rendered_with(self, client, library):
        body = _text(client.get("/documentos/?ordem=title_asc"))
        assert 'name="filtros"' in body
        assert "ordem=title_asc" in body


def _text(response) -> str:
    """The page with its markup whitespace collapsed.

    A sentence a template wraps across two lines is still one sentence to the
    reader, and a test that only passes while it fits on one line is a test
    about indentation.
    """
    return _WHITESPACE.sub(" ", response.data.decode("utf-8"))


def _titles_of(document_ids: list[int]) -> list[str]:
    return sorted(
        db.session.scalars(
            db.select(Document.title).where(Document.id.in_(document_ids))
        ).all()
    )
