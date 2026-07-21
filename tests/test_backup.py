"""Backup creation, validation and restoration."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.models import Document
from app.repositories.taxonomy_repository import CategoryRepository
from app.services.backup_service import (
    DATA_NAME,
    MANIFEST_NAME,
    create_backup,
    list_backups,
    read_archive,
    resolve_backup,
    restore_backup,
)
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.settings_service import SettingsService


def make_archive(manifest=None, payload=None, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if manifest is not None:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        if payload is not None:
            archive.writestr(DATA_NAME, json.dumps(payload))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    buffer.seek(0)
    return buffer


class TestCreation:
    def test_backup_contains_the_expected_members(self, app, document):
        info = create_backup()
        assert info.path.exists()

        with zipfile.ZipFile(info.path) as archive:
            names = archive.namelist()
            assert MANIFEST_NAME in names
            assert DATA_NAME in names
            assert any(name.startswith("documents/") for name in names)

    def test_manifest_records_version_and_counts(self, app, document):
        info = create_backup()
        with zipfile.ZipFile(info.path) as archive:
            manifest = json.loads(archive.read(MANIFEST_NAME))

        assert manifest["backup_format"] == app.config["BACKUP_FORMAT_VERSION"]
        assert manifest["app_version"] == app.config["APP_VERSION"]
        assert manifest["counts"]["documents"] == 1
        assert manifest["created_at"]

    def test_payload_includes_content_and_versions(self, app, document):
        info = create_backup()
        with zipfile.ZipFile(info.path) as archive:
            payload = json.loads(archive.read(DATA_NAME))

        entry = payload["documents"][0]
        assert entry["title"] == document.title
        assert entry["content_markdown"] == document.content_markdown
        assert len(entry["versions"]) >= 1

    def test_backups_are_listed_newest_first(self, app, document):
        create_backup(label="um")
        create_backup(label="dois")
        backups = list_backups()
        assert len(backups) == 2
        assert backups[0].created_at >= backups[1].created_at

    def test_old_backups_are_pruned(self, app, document):
        SettingsService.update_many({"backup_keep_last": 2})
        for index in range(4):
            create_backup(label=f"n{index}")
        assert len(list_backups()) == 2

    def test_documents_are_written_as_readable_markdown(self, app, make_document):
        make_document(title="Guia de Vendas", content="# Vendas\n\nTexto.")
        info = create_backup()
        with zipfile.ZipFile(info.path) as archive:
            name = next(n for n in archive.namelist() if n.startswith("documents/"))
            assert name == "documents/guia-de-vendas.md"
            assert "# Vendas" in archive.read(name).decode("utf-8")


class TestValidation:
    def test_a_non_zip_is_rejected(self, app):
        with pytest.raises(ValidationError):
            read_archive(io.BytesIO(b"nao sou um zip"))

    def test_a_zip_without_a_manifest_is_rejected(self, app):
        archive = make_archive(payload={"documents": []})
        with pytest.raises(ValidationError) as excinfo:
            read_archive(archive)
        assert "manifest" in excinfo.value.message

    def test_a_future_format_version_is_rejected(self, app):
        archive = make_archive(
            manifest={"backup_format": 999}, payload={"documents": []}
        )
        with pytest.raises(ValidationError) as excinfo:
            read_archive(archive)
        assert "não suportado" in excinfo.value.message

    def test_a_malformed_payload_is_rejected(self, app):
        archive = make_archive(manifest={"backup_format": 1}, payload={"nope": True})
        with pytest.raises(ValidationError):
            read_archive(archive)

    def test_path_traversal_in_the_archive_is_rejected(self, app):
        archive = make_archive(
            manifest={"backup_format": 1},
            payload={"documents": []},
            extra={"../escapou.md": "conteudo"},
        )
        with pytest.raises(ValidationError) as excinfo:
            read_archive(archive)
        assert "inseguro" in excinfo.value.message

    def test_resolve_backup_refuses_traversal(self, app):
        for name in ["../secret.zip", "/etc/passwd", "sub/dir.zip", ""]:
            with pytest.raises(ValidationError):
                resolve_backup(name)

    def test_resolve_backup_refuses_a_missing_file(self, app):
        with pytest.raises(ValidationError):
            resolve_backup("nao-existe.zip")


class TestRestore:
    def test_merge_adds_missing_documents(self, app, make_document, db):
        make_document(title="Original")
        info = create_backup()

        DocumentService.empty_trash()
        for existing in db.session.scalars(db.select(Document)).all():
            DocumentService.move_to_trash(existing)
            DocumentService.purge(existing)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

        report = restore_backup(info.path, mode="merge")

        assert report.documents_created == 1
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_merge_skips_documents_that_already_exist(self, app, document):
        info = create_backup()
        report = restore_backup(info.path, mode="merge")

        assert report.documents_created == 0
        assert report.documents_skipped == 1

    def test_replace_creates_a_safety_backup_first(self, app, make_document, db):
        make_document(title="Antes da substituição")
        info = create_backup()

        report = restore_backup(info.path, mode="replace")

        assert report.safety_backup is not None
        assert any(b.name == report.safety_backup for b in list_backups())
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_replace_discards_documents_absent_from_the_backup(self, app, make_document, db):
        make_document(title="No backup")
        info = create_backup()
        make_document(title="Criado depois do backup")

        restore_backup(info.path, mode="replace")

        titles = [d.title for d in db.session.scalars(db.select(Document)).all()]
        assert titles == ["No backup"]

    def test_restore_preserves_content_categories_tags_and_history(self, app, make_document, db):
        category = CategoryRepository.get_or_create("Guias", "#16A34A")
        db.session.commit()
        source = make_document(
            title="Completo",
            content="# Título\n\nCorpo com acentuação: ação.",
            category_id=category.id,
            tag_names=["alpha", "beta"],
        )
        DocumentService.save(source, source.title, "# Título\n\nSegunda versão do corpo.")
        info = create_backup()

        restore_backup(info.path, mode="replace")

        restored = db.session.scalars(db.select(Document)).unique().one()
        assert restored.title == "Completo"
        assert "Segunda versão" in restored.content_markdown
        assert restored.category.name == "Guias"
        assert sorted(restored.tag_names) == ["alpha", "beta"]
        assert len(restored.versions) >= 2
        assert restored.rendered_html

    def test_restore_reindexes_search(self, app, make_document, db):
        from app.repositories.document_repository import DocumentQuery, DocumentRepository

        make_document(title="Indexável", content="conteudo com xyzzy dentro")
        info = create_backup()
        restore_backup(info.path, mode="replace")

        results = DocumentRepository.paginate(DocumentQuery(search="xyzzy", per_page=10))
        assert results.total == 1

    def test_an_invalid_mode_is_rejected(self, app, document):
        info = create_backup()
        with pytest.raises(ValidationError):
            restore_backup(info.path, mode="obliterar")

    def test_restore_survives_a_malformed_document_entry(self, app):
        archive = make_archive(
            manifest={"backup_format": 1},
            payload={
                "documents": [
                    "isto não é um objeto",
                    {"title": "Válido", "content_markdown": "ok"},
                ]
            },
        )
        report = restore_backup(archive, mode="merge")
        assert report.documents_created == 1
        assert report.warnings


class TestRoutes:
    def test_settings_page_renders(self, client, app):
        response = client.get("/configuracoes/")
        assert response.status_code == 200
        assert "Configurações".encode() in response.data

    def test_create_backup_route(self, client, app, document):
        response = client.post("/configuracoes/backups/criar", follow_redirects=True)
        assert response.status_code == 200
        assert len(list_backups()) == 1

    def test_download_backup_route(self, client, app, document):
        info = create_backup()
        response = client.get(f"/configuracoes/backups/{info.name}/baixar")
        assert response.status_code == 200
        assert response.mimetype == "application/zip"

    def test_download_refuses_traversal(self, client, app):
        response = client.get(
            "/configuracoes/backups/..%2F..%2Fapp%2Fconfig.py/baixar",
            follow_redirects=True,
        )
        assert b"config.py" not in response.data or response.status_code == 404

    def test_settings_are_saved(self, client, app):
        response = client.post(
            "/configuracoes/",
            data={
                "app_name": "Meu Estúdio",
                "theme": "dark",
                "accent_color": "#16A34A",
                "timezone": "America/Sao_Paulo",
                "autosave_seconds": "5",
                "pdf_page_size": "Letter",
                "pdf_theme": "modern",
                "pdf_font": "sans",
                "pdf_margin": "wide",
                "pdf_header": "Cabeçalho",
                "pdf_footer": "Rodapé",
                "backup_keep_last": "5",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        SettingsService.invalidate_cache()
        assert SettingsService.get("app_name") == "Meu Estúdio"
        assert SettingsService.get("autosave_seconds") == 5
        assert SettingsService.get("pdf_show_page_numbers") is False

    def test_invalid_settings_are_rejected(self, client, app):
        client.post(
            "/configuracoes/",
            data={
                "app_name": "X",
                "theme": "dark",
                "accent_color": "nao-e-cor",
                "timezone": "America/Sao_Paulo",
                "autosave_seconds": "5",
                "pdf_page_size": "A4",
                "pdf_theme": "classic",
                "pdf_font": "serif",
                "pdf_margin": "normal",
                "backup_keep_last": "5",
            },
            follow_redirects=True,
        )
        SettingsService.invalidate_cache()
        assert SettingsService.get("accent_color") == "#4F46E5"

    def test_reset_to_defaults(self, client, app):
        SettingsService.update_many({"app_name": "Alterado"})
        client.post("/configuracoes/restaurar-padroes", follow_redirects=True)
        SettingsService.invalidate_cache()
        assert SettingsService.get("app_name") == "Markdown Studio"

    def test_reindex_route(self, client, app, document):
        response = client.post("/configuracoes/reindexar", follow_redirects=True)
        assert response.status_code == 200
