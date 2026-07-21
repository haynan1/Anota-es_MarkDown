"""Data survival guarantees.

The promise this app makes is "your text is safe". These tests hold it to
that: nothing is lost across restarts, migrations, restores or destructive
operations that were not explicitly confirmed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import Document, DocumentVersion
from app.services.document_service import DocumentService


class TestPersistenceAcrossRestarts:
    def test_documents_survive_an_application_restart(self, tmp_path):
        """A new app instance against the same file must see the same data."""
        database = tmp_path / "persist.db"
        uri = f"sqlite:///{database.as_posix()}"

        first = create_app(
            "testing",
            {
                "SQLALCHEMY_DATABASE_URI": uri,
                "BACKUP_DIR": tmp_path / "b",
                "EXPORT_DIR": tmp_path / "e",
            },
        )
        with first.app_context():
            db.create_all()
            document = DocumentService.create(
                title="Persistente", content_markdown="Conteúdo com acentuação: ação."
            )
            uuid = document.uuid
            DocumentService.save(document, "Persistente", "Segunda versão do texto.")
            db.session.remove()

        second = create_app(
            "testing",
            {
                "SQLALCHEMY_DATABASE_URI": uri,
                "BACKUP_DIR": tmp_path / "b",
                "EXPORT_DIR": tmp_path / "e",
            },
        )
        with second.app_context():
            restored = db.session.scalars(
                db.select(Document).where(Document.uuid == uuid)
            ).unique().one()

            assert restored.title == "Persistente"
            assert "Segunda versão" in restored.content_markdown
            assert len(restored.versions) >= 1
            db.session.remove()

    def test_settings_survive_a_restart(self, tmp_path):
        from app.services.settings_service import SettingsService

        database = tmp_path / "settings.db"
        uri = f"sqlite:///{database.as_posix()}"
        config = {
            "SQLALCHEMY_DATABASE_URI": uri,
            "BACKUP_DIR": tmp_path / "b",
            "EXPORT_DIR": tmp_path / "e",
        }

        first = create_app("testing", config)
        with first.app_context():
            db.create_all()
            SettingsService.update_many({"app_name": "Meu Estúdio", "autosave_seconds": 9})
            db.session.remove()

        second = create_app("testing", config)
        with second.app_context():
            assert SettingsService.get("app_name") == "Meu Estúdio"
            assert SettingsService.get("autosave_seconds") == 9
            db.session.remove()


class TestSchemaGuarantees:
    def test_foreign_keys_are_enforced(self, app):
        """Without this pragma, ON DELETE CASCADE silently does nothing."""
        enabled = db.session.execute(text("PRAGMA foreign_keys")).scalar()
        assert enabled == 1

    def test_deleting_a_document_cascades_to_its_versions(self, app, document, db):
        document_id = document.id
        assert db.session.scalar(
            db.select(db.func.count(DocumentVersion.id)).where(
                DocumentVersion.document_id == document_id
            )
        ) >= 1

        DocumentService.move_to_trash(document)
        DocumentService.purge(document)

        assert db.session.scalar(
            db.select(db.func.count(DocumentVersion.id)).where(
                DocumentVersion.document_id == document_id
            )
        ) == 0

    def test_slug_uniqueness_is_enforced_by_the_database(self, app, make_document, db):
        from sqlalchemy.exc import IntegrityError

        first = make_document(title="Único")
        duplicate = Document(
            title="Outro", slug=first.slug, content_markdown="", content_hash="x"
        )
        db.session.add(duplicate)

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_version_numbers_are_unique_per_document(self, app, document, db):
        from sqlalchemy.exc import IntegrityError

        clash = DocumentVersion(
            document_id=document.id,
            version_number=1,
            title="Duplicada",
            content_markdown="x",
            content_hash="y",
        )
        db.session.add(clash)

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_expected_indexes_exist(self, app):
        """Filtering and sorting columns must be indexed."""
        indexes = {
            index["name"] for index in inspect(db.engine).get_indexes("documents")
        }
        for expected in (
            "ix_documents_uuid",
            "ix_documents_slug",
            "ix_documents_updated_at",
            "ix_documents_is_deleted",
            "ix_documents_state_updated",
        ):
            assert expected in indexes, f"índice ausente: {expected}"


class TestDestructiveOperationsAreGuarded:
    def test_purge_requires_the_trash(self, app, document):
        from app.services.exceptions import ValidationError

        with pytest.raises(ValidationError):
            DocumentService.purge(document)

    def test_emptying_the_trash_requires_a_typed_confirmation(self, client, document, db):
        DocumentService.move_to_trash(document)

        client.post("/lixeira/esvaziar", data={"confirmation": ""}, follow_redirects=True)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

        client.post("/lixeira/esvaziar", data={"confirmation": "sim"}, follow_redirects=True)
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_replacing_data_writes_a_safety_backup_first(self, app, make_document):
        from app.services.backup_service import create_backup, list_backups, restore_backup

        make_document(title="Dados atuais")
        source = create_backup()
        before = len(list_backups())

        report = restore_backup(source.path, mode="replace")

        assert report.safety_backup is not None
        assert len(list_backups()) > before

    def test_a_failed_restore_leaves_data_untouched(self, app, make_document, db):
        import io

        from app.services.backup_service import restore_backup
        from app.services.exceptions import ValidationError

        make_document(title="Não deve sumir")
        before = db.session.scalar(db.select(db.func.count(Document.id)))

        with pytest.raises(ValidationError):
            restore_backup(io.BytesIO(b"isto nao e um zip"), mode="replace")

        assert db.session.scalar(db.select(db.func.count(Document.id))) == before


class TestSearchIndexNeverCostsData:
    """The index is an optimisation; it must never be able to lose a document.

    Found during the QA pass: the index writer called ``session.rollback()``
    on failure, which discarded the very save that triggered the indexing.
    """

    @staticmethod
    def _break_the_index(db):
        """Realistic failure mode: the FTS table is gone but still believed in.

        Simulated by dropping the table rather than by replacing the method,
        so the savepoint guard inside the indexer is actually exercised.
        """
        db.session.execute(text("DROP TABLE IF EXISTS documents_fts"))
        db.session.commit()

    def test_a_broken_index_does_not_prevent_creating(self, app, db):
        self._break_the_index(db)

        document = DocumentService.create(
            title="Salvo apesar do índice", content_markdown="Texto que não pode sumir."
        )

        db.session.expire_all()
        reloaded = db.session.get(Document, document.id)
        assert reloaded is not None
        assert reloaded.content_markdown == "Texto que não pode sumir."

    def test_index_failure_leaves_the_transaction_usable(self, app, db):
        """A savepoint rollback must not poison the outer transaction."""
        from sqlalchemy import text as sql_text

        from app.services.search_service import search_index

        document = DocumentService.create(title="Antes", content_markdown="corpo")

        # Break the index underneath the service, then save again.
        db.session.execute(sql_text("DROP TABLE IF EXISTS documents_fts"))
        db.session.commit()

        result = DocumentService.save(document, "Depois", "corpo alterado")

        assert result.content_changed is True
        db.session.expire_all()
        assert db.session.get(Document, document.id).title == "Depois"

        # And the app recovers once the index is rebuilt.
        assert search_index.rebuild() >= 1

    def test_versions_are_written_even_when_indexing_fails(self, app, db):
        from app.repositories.version_repository import VersionRepository

        document = DocumentService.create(title="Com histórico", content_markdown="v1")
        before = VersionRepository.count(document.id)

        self._break_the_index(db)
        DocumentService.save(document, "Com histórico", "v2 conteúdo diferente")

        assert VersionRepository.count(document.id) == before + 1

    def test_trashing_works_with_a_broken_index(self, app, db, make_document):
        document = make_document(title="Para a lixeira")
        self._break_the_index(db)

        DocumentService.move_to_trash(document)

        db.session.expire_all()
        assert db.session.get(Document, document.id).is_deleted is True


class TestEncodingIntegrity:
    @pytest.mark.parametrize(
        "content",
        [
            "Acentuação: ação, órgão, coração, ímã, você",
            "Emoji: 🎉 ✅ 🇧🇷 👨‍👩‍👧‍👦",
            "CJK: 日本語 中文 한국어",
            "RTL: مرحبا שלום",
            "Matemática: ∑ ∫ √ π ≠ ≤ ∞",
            "Zero-width: a​b  e NBSP: a b",
        ],
    )
    def test_unicode_survives_the_full_round_trip(self, app, make_document, db, content):
        document = make_document(title="Unicode", content=content)
        document_id = document.id

        db.session.expire_all()
        reloaded = db.session.get(Document, document_id)
        assert reloaded.content_markdown == content

    def test_unicode_survives_backup_and_restore(self, app, make_document, db):
        from app.services.backup_service import create_backup, restore_backup

        content = "Acentuação 🎉 日本語 ∑"
        make_document(title="Unicode completo", content=content)
        source = create_backup()

        restore_backup(source.path, mode="replace")

        restored = db.session.scalars(db.select(Document)).unique().one()
        assert restored.content_markdown == content

    def test_unicode_survives_markdown_export(self, client, make_document):
        content = "Acentuação 🎉 日本語 ∑"
        document = make_document(title="Export unicode", content=content)

        response = client.get(f"/exportar/{document.uuid}/markdown")
        assert response.data.decode("utf-8") == content
