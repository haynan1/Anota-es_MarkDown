"""Bulk actions over a selection of documents from the listing screen."""

from __future__ import annotations

import pytest

from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError


def _ids(documents) -> list[int]:
    return [document.id for document in documents]


class TestBulkService:
    def test_bulk_archive_marks_every_document(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(3)]
        affected, skipped = DocumentService.bulk_apply_ids(_ids(docs), "archive")
        assert (affected, skipped) == (3, 0)
        assert all(doc.is_archived for doc in docs)

    def test_bulk_unarchive_reverses_it(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        DocumentService.bulk_apply_ids(_ids(docs), "archive")
        DocumentService.bulk_apply_ids(_ids(docs), "unarchive")
        assert not any(doc.is_archived for doc in docs)

    def test_bulk_lock_and_unlock(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        DocumentService.bulk_apply_ids(_ids(docs), "lock")
        assert all(doc.is_locked for doc in docs)
        DocumentService.bulk_apply_ids(_ids(docs), "unlock")
        assert not any(doc.is_locked for doc in docs)

    def test_bulk_trash_skips_locked_documents(self, app, make_document):
        free = make_document(title="Livre")
        locked = make_document(title="Protegido")
        DocumentService.toggle_lock(locked)

        affected, skipped = DocumentService.bulk_apply_ids(_ids([free, locked]), "trash")

        assert (affected, skipped) == (1, 1)
        assert free.is_deleted is True
        # The protection held: a bulk action never bypasses the lock.
        assert locked.is_deleted is False

    def test_bulk_trash_removes_from_search_index(self, app, make_document):
        from app.services.search_service import search_index

        doc = make_document(title="Encontrável", content="palavra rara xyzzy")
        assert doc.id in (search_index.search_ids("xyzzy") or [])

        DocumentService.bulk_apply_ids([doc.id], "trash")
        assert doc.id not in (search_index.search_ids("xyzzy") or [])

    def test_unknown_action_is_rejected(self, app, document):
        with pytest.raises(ValidationError):
            DocumentService.bulk_apply_ids([document.id], "detonate")


class TestGetManyByUuids:
    def test_resolves_the_selection(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(3)]
        uuids = [doc.uuid for doc in docs]
        found = DocumentRepository.get_many_by_uuids(uuids)
        assert {doc.id for doc in found} == {doc.id for doc in docs}

    def test_drops_unknown_uuids_instead_of_failing(self, app, document):
        found = DocumentRepository.get_many_by_uuids(
            [document.uuid, "00000000-0000-0000-0000-000000000000"]
        )
        assert [doc.id for doc in found] == [document.id]

    def test_excludes_trashed_unless_asked(self, app, document):
        DocumentService.move_to_trash(document)
        assert DocumentRepository.get_many_by_uuids([document.uuid]) == []
        assert DocumentRepository.get_many_by_uuids(
            [document.uuid], include_deleted=True
        )[0].id == document.id

    def test_empty_input_returns_empty(self, app):
        assert DocumentRepository.get_many_by_uuids([]) == []


class TestBulkRoute:
    def test_archive_selection(self, client, make_document, db):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "archive", "uuids": [doc.uuid for doc in docs]},
            follow_redirects=True,
        )
        assert response.status_code == 200
        for doc in docs:
            db.session.refresh(doc)
            assert doc.is_archived is True

    def test_trash_selection_redirects_to_clean_listing(self, client, make_document, db):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "trash", "uuids": [doc.uuid for doc in docs]},
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/documentos/")
        for doc in docs:
            db.session.refresh(doc)
            assert doc.is_deleted is True

    def test_locked_document_is_reported_as_skipped(self, client, make_document, db):
        locked = make_document(title="Protegido")
        DocumentService.toggle_lock(locked)

        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "trash", "uuids": [locked.uuid]},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "cadeado".encode() in response.data
        db.session.refresh(locked)
        assert locked.is_deleted is False

    def test_invalid_action_is_refused(self, client, document):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "detonate", "uuids": [document.uuid]},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "inválida".encode() in response.data

    def test_empty_selection_is_refused(self, client):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "archive"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Selecione ao menos um".encode() in response.data

    def test_listing_exposes_selection_hooks(self, client, document):
        """The checkbox column and bar ship in the markup for JS to activate."""
        response = client.get("/documentos/")
        assert b"doc-select" in response.data
        assert b"data-bulk-bar" in response.data


@pytest.fixture()
def two_categories(app):
    from app.extensions import db
    from app.repositories.taxonomy_repository import CategoryRepository

    first = CategoryRepository.get_or_create("Ensaios", "#4F46E5")
    second = CategoryRepository.get_or_create("Notas", "#0EA5E9")
    db.session.commit()
    return first, second


class TestBulkCategoryService:
    def test_a_selection_moves_into_one_category(self, app, make_document, two_categories):
        ensaios, _ = two_categories
        docs = [make_document(title=f"Doc {index}") for index in range(3)]

        changed = DocumentService.bulk_set_category(
            [doc.id for doc in docs], ensaios.id
        )

        assert changed == 3
        assert all(doc.category_id == ensaios.id for doc in docs)

    def test_it_moves_documents_that_already_had_a_category(
        self, app, make_document, two_categories
    ):
        ensaios, notas = two_categories
        doc = make_document(title="Mudança", category_id=ensaios.id)

        assert DocumentService.bulk_set_category([doc.id], notas.id) == 1
        assert doc.category_id == notas.id

    def test_none_clears_the_category(self, app, make_document, two_categories):
        ensaios, _ = two_categories
        doc = make_document(title="Solto", category_id=ensaios.id)

        assert DocumentService.bulk_set_category([doc.id], None) == 1
        assert doc.category_id is None

    def test_a_document_already_there_is_not_counted(
        self, app, make_document, two_categories
    ):
        ensaios, _ = two_categories
        doc = make_document(title="Parado", category_id=ensaios.id)

        assert DocumentService.bulk_set_category([doc.id], ensaios.id) == 0

    def test_an_uncategorised_document_is_not_cleared_twice(self, app, document):
        assert DocumentService.bulk_set_category([document.id], None) == 0

    def test_the_trash_is_left_alone(self, app, make_document, two_categories):
        ensaios, _ = two_categories
        doc = make_document(title="Na lixeira")
        DocumentService.move_to_trash(doc)

        assert DocumentService.bulk_set_category([doc.id], ensaios.id) == 0

    def test_an_empty_selection_touches_nothing(self, app, two_categories):
        ensaios, _ = two_categories
        assert DocumentService.bulk_set_category([], ensaios.id) == 0


class TestBulkCategoryRoute:
    def test_the_selection_is_moved(self, client, make_document, two_categories, db):
        ensaios, _ = two_categories
        docs = [make_document(title=f"Doc {index}") for index in range(2)]

        response = client.post(
            "/documentos/acoes-em-massa",
            data={
                "acao": "category",
                "categoria": str(ensaios.id),
                "uuids": [doc.uuid for doc in docs],
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "2 documentos movidos".encode() in response.data
        for doc in docs:
            db.session.refresh(doc)
            assert doc.category_id == ensaios.id

    def test_the_sentinel_clears_the_category(
        self, client, make_document, two_categories, db
    ):
        """The same "@sem" the filter above the listing uses."""
        from app.repositories.document_repository import WITHOUT

        ensaios, _ = two_categories
        doc = make_document(title="Solto", category_id=ensaios.id)

        client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "category", "categoria": WITHOUT, "uuids": [doc.uuid]},
            follow_redirects=True,
        )

        db.session.refresh(doc)
        assert doc.category_id is None

    def test_no_destination_is_refused(self, client, document):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "category", "categoria": "", "uuids": [document.uuid]},
            follow_redirects=True,
        )
        assert "Escolha uma categoria".encode() in response.data

    def test_an_unknown_category_is_refused(self, client, document, db):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "category", "categoria": "4242", "uuids": [document.uuid]},
            follow_redirects=True,
        )
        assert "Categoria não encontrada".encode() in response.data
        db.session.refresh(document)
        assert document.category_id is None

    def test_a_nonsense_category_is_refused_without_a_500(self, client, document):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={
                "acao": "category",
                "categoria": "9" * 5000,
                "uuids": [document.uuid],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Categoria não encontrada".encode() in response.data

    def test_a_selection_already_there_says_so(
        self, client, make_document, two_categories
    ):
        ensaios, _ = two_categories
        doc = make_document(title="Parado", category_id=ensaios.id)

        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "category", "categoria": str(ensaios.id), "uuids": [doc.uuid]},
            follow_redirects=True,
        )
        assert "já estavam em".encode() in response.data

    def test_the_bar_offers_the_control(self, client, document, two_categories):
        body = client.get("/documentos/").data.decode("utf-8")
        assert 'id="bulk-category"' in body
        assert 'value="category"' in body
