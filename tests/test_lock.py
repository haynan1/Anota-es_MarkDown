"""The lock that protects a document from accidental deletion.

The lock guards *existence*, not content: editing a locked document is fine,
removing it is not. Every path that can destroy a document has to honour it,
including the bulk one.
"""

from __future__ import annotations

import pytest

from app.models import Document
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError


@pytest.fixture()
def locked_document(app, make_document):
    document = make_document(title="Documento protegido", content="Não pode sumir.")
    DocumentService.toggle_lock(document)
    return document


class TestToggling:
    def test_documents_start_unlocked(self, app, document):
        assert document.is_locked is False

    def test_toggle_locks_and_unlocks(self, app, document):
        assert DocumentService.toggle_lock(document) is True
        assert document.is_locked is True
        assert DocumentService.toggle_lock(document) is False
        assert document.is_locked is False

    def test_lock_survives_a_reload(self, app, db, locked_document):
        document_id = locked_document.id
        db.session.expire_all()
        assert db.session.get(Document, document_id).is_locked is True


class TestDeletionIsBlocked:
    def test_locked_document_cannot_go_to_the_trash(self, app, locked_document):
        with pytest.raises(ValidationError) as excinfo:
            DocumentService.move_to_trash(locked_document)

        assert "cadeado" in excinfo.value.message
        assert locked_document.is_deleted is False

    def test_locked_document_cannot_be_purged(self, app, make_document):
        document = make_document(title="Vai para a lixeira")
        DocumentService.move_to_trash(document)
        DocumentService.toggle_lock(document)

        with pytest.raises(ValidationError):
            DocumentService.purge(document)

        assert DocumentService.require(document.uuid, include_deleted=True) is not None

    def test_emptying_the_trash_skips_locked_documents(self, app, db, make_document):
        """The bulk path must not be the loophole."""
        for index in range(3):
            document = make_document(title=f"Descartável {index}")
            DocumentService.move_to_trash(document)

        protegido = make_document(title="Protegido na lixeira")
        DocumentService.move_to_trash(protegido)
        DocumentService.toggle_lock(protegido)

        removed = DocumentService.empty_trash()

        assert removed == 3
        db.session.expire_all()
        remaining = db.session.scalars(db.select(Document)).unique().all()
        assert [d.title for d in remaining] == ["Protegido na lixeira"]

    def test_unlocking_restores_the_ability_to_delete(self, app, locked_document):
        DocumentService.toggle_lock(locked_document)
        DocumentService.move_to_trash(locked_document)
        assert locked_document.is_deleted is True


class TestEditingStaysAllowed:
    def test_a_locked_document_can_still_be_edited(self, app, locked_document):
        result = DocumentService.save(
            locked_document, "Protegido", "Conteúdo totalmente novo."
        )
        assert result.content_changed is True
        assert locked_document.is_locked is True

    def test_a_locked_document_can_be_archived(self, app, locked_document):
        DocumentService.set_archived(locked_document, True)
        assert locked_document.is_archived is True

    def test_a_locked_document_can_be_duplicated(self, app, locked_document):
        copy = DocumentService.duplicate(locked_document)
        # The copy is a new document; protection is not inherited.
        assert copy.is_locked is False


class TestRoutes:
    def test_toggle_route_locks_the_document(self, client, db, document):
        response = client.post(
            f"/documentos/{document.uuid}/cadeado", follow_redirects=True
        )
        assert response.status_code == 200
        db.session.refresh(document)
        assert document.is_locked is True

    def test_trash_route_refuses_a_locked_document(self, client, db, locked_document):
        response = client.post(
            f"/documentos/{locked_document.uuid}/lixeira", follow_redirects=True
        )
        assert response.status_code == 200
        assert "cadeado".encode() in response.data
        db.session.refresh(locked_document)
        assert locked_document.is_deleted is False

    def test_purge_route_refuses_a_locked_document(self, client, db, make_document):
        document = make_document(title="Na lixeira e protegido")
        DocumentService.move_to_trash(document)
        DocumentService.toggle_lock(document)

        response = client.post(
            f"/lixeira/{document.uuid}/excluir", follow_redirects=True
        )

        assert response.status_code == 200
        assert db.session.get(Document, document.id) is not None

    def test_empty_trash_route_preserves_locked_documents(self, client, db, make_document):
        descartavel = make_document(title="Some")
        DocumentService.move_to_trash(descartavel)
        protegido = make_document(title="Fica")
        DocumentService.move_to_trash(protegido)
        DocumentService.toggle_lock(protegido)

        client.post("/lixeira/esvaziar", data={"confirmation": "EXCLUIR"}, follow_redirects=True)

        db.session.expire_all()
        titles = [d.title for d in db.session.scalars(db.select(Document)).unique().all()]
        assert titles == ["Fica"]

    def test_listing_shows_the_lock(self, client, locked_document):
        response = client.get("/documentos/")
        assert response.status_code == 200
        assert "Protegido contra exclus".encode() in response.data

    def test_menu_hides_delete_while_locked(self, client, locked_document):
        """Offering an action that always fails is worse than omitting it."""
        body = client.get("/documentos/").data.decode("utf-8")
        assert f"/documentos/{locked_document.uuid}/lixeira" not in body
        assert f"/documentos/{locked_document.uuid}/cadeado" in body

    def test_editor_exposes_the_toggle(self, client, document):
        body = client.get(f"/editor/{document.uuid}").data.decode("utf-8")
        assert f"/documentos/{document.uuid}/cadeado" in body


class TestBackupRoundTrip:
    def test_lock_state_survives_backup_and_restore(self, app, db, make_document):
        from app.services.backup_service import create_backup, restore_backup

        document = make_document(title="Protegido no backup")
        DocumentService.toggle_lock(document)
        source = create_backup()

        restore_backup(source.path, mode="replace")

        restored = db.session.scalars(db.select(Document)).unique().one()
        assert restored.is_locked is True
