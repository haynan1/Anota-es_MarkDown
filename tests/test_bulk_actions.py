"""Bulk actions over a selection of documents from the listing screen."""

from __future__ import annotations

import pytest

from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError


class TestBulkService:
    def test_bulk_archive_marks_every_document(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(3)]
        affected, skipped = DocumentService.bulk_apply(docs, "archive")
        assert (affected, skipped) == (3, 0)
        assert all(doc.is_archived for doc in docs)

    def test_bulk_unarchive_reverses_it(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        DocumentService.bulk_apply(docs, "archive")
        DocumentService.bulk_apply(docs, "unarchive")
        assert not any(doc.is_archived for doc in docs)

    def test_bulk_lock_and_unlock(self, app, make_document):
        docs = [make_document(title=f"Doc {i}") for i in range(2)]
        DocumentService.bulk_apply(docs, "lock")
        assert all(doc.is_locked for doc in docs)
        DocumentService.bulk_apply(docs, "unlock")
        assert not any(doc.is_locked for doc in docs)

    def test_bulk_trash_skips_locked_documents(self, app, make_document):
        free = make_document(title="Livre")
        locked = make_document(title="Protegido")
        DocumentService.toggle_lock(locked)

        affected, skipped = DocumentService.bulk_apply([free, locked], "trash")

        assert (affected, skipped) == (1, 1)
        assert free.is_deleted is True
        # The protection held: a bulk action never bypasses the lock.
        assert locked.is_deleted is False

    def test_bulk_trash_removes_from_search_index(self, app, make_document):
        from app.services.search_service import search_index

        doc = make_document(title="Encontrável", content="palavra rara xyzzy")
        assert doc.id in (search_index.search_ids("xyzzy") or [])

        DocumentService.bulk_apply([doc], "trash")
        assert doc.id not in (search_index.search_ids("xyzzy") or [])

    def test_unknown_action_is_rejected(self, app, document):
        with pytest.raises(ValidationError):
            DocumentService.bulk_apply([document], "detonate")


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
