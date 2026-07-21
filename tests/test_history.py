"""Versioning: creation, deduplication, comparison and restoration."""

from __future__ import annotations

import pytest

from app.repositories.version_repository import VersionRepository
from app.services.document_service import DocumentService
from app.services.history_service import HistoryService


class TestSnapshots:
    def test_edit_creates_a_version(self, app, document):
        before = VersionRepository.count(document.id)
        DocumentService.save(document, document.title, "Conteúdo alterado de verdade.")
        assert VersionRepository.count(document.id) == before + 1

    def test_repeated_identical_saves_create_one_version(self, app, document):
        DocumentService.save(document, document.title, "Mesmo texto.")
        after_first = VersionRepository.count(document.id)

        for _ in range(5):
            DocumentService.save(document, document.title, "Mesmo texto.")

        assert VersionRepository.count(document.id) == after_first

    def test_versions_are_numbered_sequentially(self, app, document):
        for index in range(3):
            DocumentService.save(document, document.title, f"Versão número {index}.")

        numbers = [
            version.version_number
            for version in VersionRepository.paginate(document.id, per_page=50).items
        ]
        assert numbers == sorted(numbers, reverse=True)
        assert numbers[0] == VersionRepository.latest_number(document.id)

    def test_snapshot_returns_none_when_unchanged(self, app, document):
        assert HistoryService.snapshot(document) is None

    def test_forced_snapshot_always_writes(self, app, document):
        before = VersionRepository.count(document.id)
        assert HistoryService.snapshot(document, force=True) is not None
        assert VersionRepository.count(document.id) == before + 1

    def test_change_summary_describes_growth(self, app):
        assert "adicionada" in HistoryService.describe_change("uma", "uma duas tres")
        assert "removida" in HistoryService.describe_change("uma duas tres", "uma")
        assert HistoryService.describe_change("igual", "trocado") == "Conteúdo revisado"


class TestDiff:
    def test_identical_texts_report_no_changes(self, app):
        diff = HistoryService.build_diff("linha a\nlinha b", "linha a\nlinha b")
        assert diff.is_identical

    def test_insertion_is_detected(self, app):
        diff = HistoryService.build_diff("a", "a\nb")
        assert diff.added == 1
        assert any(row.tag == "insert" for row in diff.rows)

    def test_deletion_is_detected(self, app):
        diff = HistoryService.build_diff("a\nb", "a")
        assert diff.removed == 1
        assert any(row.tag == "delete" for row in diff.rows)

    def test_replacement_is_detected(self, app):
        diff = HistoryService.build_diff("antigo", "novo")
        assert diff.changed == 1

    def test_long_unchanged_regions_are_collapsed(self, app):
        body = "\n".join(f"linha {index}" for index in range(60))
        diff = HistoryService.build_diff(body, body + "\nfinal")
        assert any(row.tag == "skip" for row in diff.rows)
        assert len(diff.rows) < 60


class TestRestore:
    def test_restore_brings_back_old_content(self, app, document, db):
        original = document.content_markdown
        DocumentService.save(document, document.title, "Texto totalmente novo.")

        target = VersionRepository.paginate(document.id, per_page=50).items[-1]
        HistoryService.restore(document, target.version_number)

        assert document.content_markdown == original

    def test_restore_never_loses_the_pre_restore_content(self, app, document, db):
        """The state being replaced must remain retrievable from history.

        It is already stored as the newest version, so deduplication correctly
        declines to write a second identical snapshot - what matters is that
        the content survives.
        """
        DocumentService.save(document, document.title, "Estado que deve sobreviver.")

        HistoryService.restore(document, 1)
        db.session.commit()

        bodies = [
            version.content_markdown
            for version in VersionRepository.paginate(document.id, per_page=50).items
        ]
        assert "Estado que deve sobreviver." in bodies

    def test_restore_of_an_edited_state_snapshots_it_first(self, app, document, db):
        """When the live content is not yet in history, restoring snapshots it."""
        DocumentService.save(document, document.title, "Primeira alteração.")
        # Change the body without going through save(), so it is unversioned.
        document.content_markdown = "Trabalho ainda não versionado."
        before = VersionRepository.count(document.id)

        HistoryService.restore(document, 1)
        db.session.commit()

        assert VersionRepository.count(document.id) == before + 1
        bodies = [
            version.content_markdown
            for version in VersionRepository.paginate(document.id, per_page=50).items
        ]
        assert "Trabalho ainda não versionado." in bodies

    def test_restoring_an_unknown_version_raises(self, app, document):
        with pytest.raises(LookupError):
            HistoryService.restore(document, 999)


class TestRoutes:
    def test_history_page_renders(self, client, document):
        DocumentService.save(document, document.title, "Algo diferente.")
        response = client.get(f"/documentos/{document.uuid}/historico")
        assert response.status_code == 200
        assert "Histórico".encode() in response.data

    def test_version_view_renders(self, client, document):
        response = client.get(f"/documentos/{document.uuid}/historico/1")
        assert response.status_code == 200

    def test_missing_version_returns_404(self, client, document):
        response = client.get(f"/documentos/{document.uuid}/historico/999")
        assert response.status_code == 404

    def test_compare_page_renders(self, client, document):
        DocumentService.save(document, document.title, "Mudou bastante aqui.")
        response = client.get(f"/documentos/{document.uuid}/historico/1/comparar")
        assert response.status_code == 200

    def test_restore_route_updates_document(self, client, document, db):
        original = document.content_markdown
        DocumentService.save(document, document.title, "Conteúdo substituto.")

        response = client.post(
            f"/documentos/{document.uuid}/historico/1/restaurar", follow_redirects=True
        )

        assert response.status_code == 200
        db.session.refresh(document)
        assert document.content_markdown == original
        # Derived fields must be recomputed, not left stale.
        assert document.word_count > 0
        assert document.rendered_html
