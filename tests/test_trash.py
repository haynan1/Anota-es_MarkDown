"""Soft delete, restoration and permanent deletion."""

from __future__ import annotations

import pytest

from app.models import Document, DocumentVersion
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError


class TestSoftDelete:
    def test_move_to_trash_is_reversible(self, app, document, db):
        DocumentService.move_to_trash(document)
        assert document.is_deleted is True
        assert document.deleted_at is not None
        # The row is still there - nothing was destroyed.
        assert db.session.get(Document, document.id) is not None

    def test_trashed_documents_disappear_from_listings(self, app, document):
        DocumentService.move_to_trash(document)
        assert DocumentRepository.get_by_uuid(document.uuid) is None
        assert DocumentRepository.get_by_uuid(document.uuid, include_deleted=True) is not None

    def test_restore_returns_the_document(self, app, document):
        DocumentService.move_to_trash(document)
        DocumentService.restore_from_trash(document)
        assert document.is_deleted is False
        assert document.deleted_at is None
        assert DocumentRepository.get_by_uuid(document.uuid) is not None

    def test_slugs_stay_unique_across_the_trash(self, app, make_document):
        """A trashed document keeps reserving its slug, so restore never collides."""
        first = make_document(title="Relatório mensal")
        original_slug = first.slug
        DocumentService.move_to_trash(first)

        second = make_document(title="Relatório mensal")
        assert second.slug != original_slug

        DocumentService.restore_from_trash(first)
        assert first.is_deleted is False
        assert first.slug == original_slug


class TestPermanentDelete:
    def test_purge_removes_the_document_and_its_versions(self, app, document, db):
        document_id = document.id
        DocumentService.move_to_trash(document)
        DocumentService.purge(document)

        assert db.session.get(Document, document_id) is None
        remaining = db.session.scalars(
            db.select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        ).all()
        assert remaining == []

    def test_purge_refuses_documents_not_in_the_trash(self, app, document):
        with pytest.raises(ValidationError):
            DocumentService.purge(document)

    def test_empty_trash_removes_everything_trashed(self, app, make_document, db):
        kept = make_document(title="Fica")
        for index in range(3):
            trashed = make_document(title=f"Sai {index}")
            DocumentService.move_to_trash(trashed)

        assert DocumentService.empty_trash() == 3
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1
        assert db.session.get(Document, kept.id) is not None


class TestRoutes:
    def test_trash_page_renders(self, client, document):
        DocumentService.move_to_trash(document)
        response = client.get("/lixeira/")
        assert response.status_code == 200
        assert document.title.encode() in response.data

    def test_empty_trash_page_shows_empty_state(self, client):
        response = client.get("/lixeira/")
        assert response.status_code == 200
        assert "lixeira está vazia".encode() in response.data

    def test_move_to_trash_route(self, client, document, db):
        response = client.post(
            f"/documentos/{document.uuid}/lixeira", follow_redirects=True
        )
        assert response.status_code == 200
        db.session.refresh(document)
        assert document.is_deleted is True

    def test_restore_route(self, client, document, db):
        DocumentService.move_to_trash(document)
        response = client.post(
            f"/lixeira/{document.uuid}/restaurar", follow_redirects=True
        )
        assert response.status_code == 200
        db.session.refresh(document)
        assert document.is_deleted is False

    def test_purge_route(self, client, document, db):
        DocumentService.move_to_trash(document)
        document_id = document.id

        response = client.post(f"/lixeira/{document.uuid}/excluir", follow_redirects=True)

        assert response.status_code == 200
        assert db.session.get(Document, document_id) is None

    def test_empty_trash_requires_the_confirmation_word(self, client, document, db):
        DocumentService.move_to_trash(document)

        rejected = client.post(
            "/lixeira/esvaziar", data={"confirmation": "sim"}, follow_redirects=True
        )
        assert rejected.status_code == 200
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

        accepted = client.post(
            "/lixeira/esvaziar", data={"confirmation": "EXCLUIR"}, follow_redirects=True
        )
        assert accepted.status_code == 200
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0
