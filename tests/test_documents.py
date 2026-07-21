"""Document lifecycle: create, edit, autosave, duplicate, archive, rename."""

from __future__ import annotations

import pytest

from app.models import Document
from app.repositories.version_repository import VersionRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ConflictError, ValidationError


class TestCreation:
    def test_create_persists_and_derives_fields(self, app, db):
        document = DocumentService.create(
            title="Meu guia", content_markdown="# Olá\n\nUm dois três quatro."
        )
        assert document.id is not None
        assert document.uuid
        assert document.slug == "meu-guia"
        assert document.word_count == 5
        assert document.character_count == len("# Olá\n\nUm dois três quatro.")
        assert document.estimated_reading_time == 1
        assert "<h1" in document.rendered_html
        assert document.excerpt

    def test_empty_title_falls_back(self, app):
        document = DocumentService.create(title="", content_markdown="")
        assert document.title == "Documento sem título"
        assert document.slug

    def test_slugs_stay_unique(self, app):
        first = DocumentService.create(title="Relatório")
        second = DocumentService.create(title="Relatório")
        third = DocumentService.create(title="Relatório")
        assert len({first.slug, second.slug, third.slug}) == 3
        assert second.slug == "relatorio-2"

    def test_oversized_content_is_rejected(self, app):
        app.config["MAX_MARKDOWN_BYTES"] = 100
        with pytest.raises(ValidationError):
            DocumentService.create(title="Grande", content_markdown="x" * 200)

    def test_creation_records_first_version(self, app):
        document = DocumentService.create(title="Com corpo", content_markdown="Texto real.")
        assert VersionRepository.count(document.id) == 1

    def test_empty_document_gets_no_version(self, app):
        document = DocumentService.create(title="Vazio", content_markdown="")
        assert VersionRepository.count(document.id) == 0


class TestSaving:
    def test_save_updates_content_and_bumps_revision(self, app, document):
        before = document.revision
        result = DocumentService.save(document, "Novo título", "Conteúdo novo aqui.")
        assert result.content_changed is True
        assert document.revision == before + 1
        assert document.title == "Novo título"
        assert "Conteúdo novo" in document.rendered_html

    def test_identical_save_is_a_no_op(self, app, document):
        before_revision = document.revision
        before_versions = VersionRepository.count(document.id)

        result = DocumentService.save(
            document, document.title, document.content_markdown
        )

        assert result.content_changed is False
        assert result.version_created is False
        assert document.revision == before_revision
        assert VersionRepository.count(document.id) == before_versions

    def test_stale_revision_is_rejected(self, app, document):
        DocumentService.save(document, document.title, "Primeira alteração.")

        with pytest.raises(ConflictError) as excinfo:
            DocumentService.save(
                document, document.title, "Alteração concorrente.", expected_revision=1
            )

        assert excinfo.value.status_code == 409
        assert excinfo.value.server_state["revision"] == document.revision

    def test_matching_revision_is_accepted(self, app, document):
        result = DocumentService.save(
            document, document.title, "Texto novo.", expected_revision=document.revision
        )
        assert result.content_changed is True

    def test_rename_updates_slug_and_history(self, app, document):
        before = VersionRepository.count(document.id)
        DocumentService.rename(document, "Título completamente novo")
        assert document.slug == "titulo-completamente-novo"
        assert VersionRepository.count(document.id) == before + 1


class TestStateTransitions:
    def test_toggle_favorite(self, app, document):
        assert DocumentService.toggle_favorite(document) is True
        assert DocumentService.toggle_favorite(document) is False

    def test_archive_and_unarchive(self, app, document):
        DocumentService.set_archived(document, True)
        assert document.is_archived is True
        DocumentService.set_archived(document, False)
        assert document.is_archived is False

    def test_duplicate_copies_content_not_identity(self, app, document, db):
        document.tags = []
        copy = DocumentService.duplicate(document)

        assert copy.id != document.id
        assert copy.uuid != document.uuid
        assert copy.slug != document.slug
        assert copy.content_markdown == document.content_markdown
        assert copy.title.endswith("(cópia)")
        assert VersionRepository.count(copy.id) == 1

    def test_touch_opened_does_not_change_updated_at(self, app, document, db):
        original = document.updated_at
        DocumentService.touch_opened(document)
        db.session.refresh(document)
        assert document.updated_at == original
        assert document.last_opened_at is not None


class TestRoutes:
    def test_dashboard_renders(self, client, document):
        response = client.get("/")
        assert response.status_code == 200
        assert "Painel".encode() in response.data

    def test_document_list_renders(self, client, document):
        response = client.get("/documentos/")
        assert response.status_code == 200
        assert document.title.encode() in response.data

    def test_editor_renders(self, client, document):
        response = client.get(f"/editor/{document.uuid}")
        assert response.status_code == 200
        assert b'data-editor-content' in response.data

    def test_editor_new_redirects_to_a_real_document(self, client, app, db):
        response = client.post("/editor/novo")
        assert response.status_code == 302
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_creating_a_document_is_not_reachable_by_get(self, client, app, db):
        """A GET must be safe. While this was a GET, a prefetched link, a
        restored tab or a back-navigation each wrote a document — and CSRF
        protection exempts GET, so any page could trigger it with an <img>."""
        response = client.get("/editor/novo")

        # 404 rather than 405: with the GET rule gone, "/editor/novo" falls
        # through to the "/editor/<public_uuid>" rule and no such document
        # exists. What matters is the write, and there is none.
        assert response.status_code in (404, 405)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

    def test_unknown_document_returns_404(self, client):
        response = client.get("/editor/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_unknown_route_returns_404_page(self, client):
        response = client.get("/rota-que-nao-existe")
        assert response.status_code == 404
        assert "não encontrada".encode() in response.data

    def test_editor_form_save_without_javascript(self, client, document):
        response = client.post(
            f"/editor/{document.uuid}",
            data={
                "title": "Salvo pelo formulário",
                "content_markdown": "Conteúdo enviado sem JavaScript.",
                "expected_revision": str(document.revision),
                "category_id": "",
                "tags": "",
                "page_size": "A4",
                "pdf_theme": "classic",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert document.title == "Salvo pelo formulário"


class TestAutosaveEndpoint:
    def test_autosave_saves_and_returns_new_revision(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": document.title,
                "content_markdown": "Texto vindo do autosave.",
                "revision": document.revision,
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["saved"] is True
        assert payload["revision"] == document.revision

    def test_autosave_with_stale_revision_returns_409(self, client, document):
        client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": document.title,
                "content_markdown": "Primeira.",
                "revision": document.revision,
            },
        )

        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": document.title,
                "content_markdown": "Concorrente.",
                "revision": 1,
            },
        )

        assert response.status_code == 409
        body = response.get_json()
        assert body["ok"] is False
        assert "server_state" in body

    def test_autosave_rejects_malformed_payload(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={"title": 123, "content_markdown": None},
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    def test_autosave_of_unchanged_content_creates_no_version(self, client, document):
        before = VersionRepository.count(document.id)
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": document.title,
                "content_markdown": document.content_markdown,
                "revision": document.revision,
            },
        )
        assert response.get_json()["saved"] is False
        assert VersionRepository.count(document.id) == before

    def test_preview_endpoint_renders_and_sanitizes(self, client, app):
        response = client.post(
            "/api/preview",
            json={"content_markdown": "# Oi\n\n<script>alert(1)</script>"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert "<h1" in payload["html"]
        assert "<script" not in payload["html"].lower()
        assert "alert" not in payload["html"]

    def test_preview_reports_metrics(self, client, app):
        response = client.post(
            "/api/preview", json={"content_markdown": "uma duas tres quatro"}
        )
        payload = response.get_json()
        assert payload["word_count"] == 4
        assert payload["character_count"] == 20
        assert payload["reading_time"] == 1

    def test_metadata_endpoint_updates_tags_and_favorite(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/metadados",
            json={"tags": ["projeto", "ideias"], "is_favorite": True},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert sorted(payload["tags"]) == ["ideias", "projeto"]
        assert payload["is_favorite"] is True
