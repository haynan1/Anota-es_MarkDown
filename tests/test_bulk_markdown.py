"""Bulk Markdown export and import - including the round trip between them.

The pair only earns its keep if what leaves comes back the same. Most of what
follows exports a library, wipes it, imports the archive and compares.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.extensions import db
from app.models import Document
from app.services import front_matter
from app.services.bulk_export_service import build_archive, export_all, to_markdown
from app.services.bulk_import_service import BulkImportReport, import_files
from app.services.exceptions import ValidationError
from app.services.group_service import GroupService
from app.repositories.document_repository import DocumentRepository


def zip_bytes(response_or_archive) -> bytes:
    stream = response_or_archive.stream
    stream.seek(0)
    return stream.read()


def members(payload: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {
            info.filename: archive.read(info).decode("utf-8")
            for info in archive.infolist()
        }


def upload(payload: bytes, filename: str):
    from werkzeug.datastructures import FileStorage

    return FileStorage(stream=io.BytesIO(payload), filename=filename)


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ── Export ──────────────────────────────────────────────────────────────────


class TestExport:
    def test_every_document_becomes_a_file(self, app, make_document):
        make_document(title="Primeiro", content="# Primeiro\n\nUm.")
        make_document(title="Segundo", content="# Segundo\n\nDois.")

        archive = export_all()
        assert archive.document_count == 2

        entries = members(zip_bytes(archive))
        assert set(entries) == {"primeiro.md", "segundo.md"}
        assert "Um." in entries["primeiro.md"]

    def test_categories_become_folders(self, app, make_document):
        from app.repositories.taxonomy_repository import CategoryRepository

        category = CategoryRepository.get_or_create("Marketing Digital")
        db.session.commit()
        make_document(title="Com categoria", category_id=category.id)
        make_document(title="Sem categoria")

        entries = members(zip_bytes(export_all()))

        assert "marketing-digital/com-categoria.md" in entries
        assert "sem-categoria.md" in entries

    def test_documents_sharing_a_title_never_collide(self, app, make_document):
        for _ in range(3):
            make_document(title="Mesmo nome", content="corpo")

        entries = members(zip_bytes(export_all()))

        assert len(entries) == 3
        assert "mesmo-nome.md" in entries
        assert "mesmo-nome-2.md" in entries

    def test_the_front_matter_carries_what_markdown_cannot(self, app, make_document):
        document = make_document(title="Completo", content="Corpo.", tag_names=["ads", "seo"])
        GroupService.attach_by_names(document, ["Campanhas"])
        document.is_favorite = True
        db.session.commit()

        fields, body = front_matter.parse(to_markdown(document))

        assert fields["title"] == "Completo"
        assert fields["uuid"] == document.uuid
        assert fields["tags"] == ["ads", "seo"]
        assert fields["groups"] == ["Campanhas"]
        assert front_matter.flag_of(fields, "favorite") is True
        assert body.strip() == "Corpo."

    def test_false_flags_are_left_out(self, app, document):
        written = to_markdown(document)
        assert "favorite" not in written
        assert "archived" not in written

    def test_archived_documents_travel_and_the_trash_does_not(self, app, make_document):
        from app.services.document_service import DocumentService

        archived = make_document(title="Arquivado")
        DocumentService.set_archived(archived, True)
        DocumentService.move_to_trash(make_document(title="Na lixeira"))

        entries = members(zip_bytes(export_all()))

        assert "arquivado.md" in entries
        assert "na-lixeira.md" not in entries
        assert front_matter.flag_of(front_matter.parse(entries["arquivado.md"])[0], "archived")

    def test_an_empty_library_produces_an_empty_archive(self, app):
        archive = export_all()
        assert archive.document_count == 0
        assert archive.truncated is False

    def test_reaching_the_ceiling_is_declared_not_hidden(self, app, make_document):
        """An archive that stops short must say so, not call itself "tudo"."""
        for index in range(3):
            make_document(title=f"Documento {index}")

        archive = build_archive(
            DocumentRepository.iter_for_export(limit=2), limit=2
        )

        assert archive.document_count == 2
        assert archive.truncated is True
        archive.stream.close()

    def test_the_ceiling_is_a_sql_limit_not_a_python_break(self, app, make_document):
        """The rows must not be loaded only to be discarded."""
        for index in range(4):
            make_document(title=f"Documento {index}")

        loaded = list(DocumentRepository.iter_for_export(limit=2))
        assert len(loaded) == 2

    def test_an_empty_selection_does_not_reach_the_database(self, app, make_document):
        from app.services.bulk_export_service import export_selection

        make_document(title="Existe")
        archive = export_selection([])

        assert archive.document_count == 0

    def test_accents_survive(self, app, make_document):
        make_document(title="Ação e coração", content="Órgão, ímã.")
        entries = members(zip_bytes(export_all()))
        assert "Órgão, ímã." in entries["acao-e-coracao.md"]

    def test_a_document_with_no_body_still_exports(self, app, make_document):
        make_document(title="Vazio", content="")
        entries = members(zip_bytes(export_all()))
        assert entries["vazio.md"].startswith("---")


class TestExportRoutes:
    def test_the_route_returns_a_zip(self, client, make_document):
        make_document(title="Para baixar")
        response = client.get("/exportar/markdown/tudo")

        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert "documentos-markdown-" in response.headers["Content-Disposition"]
        assert "para-baixar.md" in members(response.data)

    def test_an_empty_library_redirects_instead_of_downloading(self, client, app):
        response = client.get("/exportar/markdown/tudo", follow_redirects=True)
        assert response.status_code == 200
        assert "Não há documentos para exportar.".encode() in response.data

    def test_a_selection_exports_only_what_was_chosen(self, client, make_document):
        wanted = make_document(title="Escolhido")
        make_document(title="Ignorado")

        response = client.post(
            "/exportar/markdown/selecao", data={"uuids": [wanted.uuid]}
        )

        entries = members(response.data)
        assert "escolhido.md" in entries
        assert "ignorado.md" not in entries

    def test_the_selection_shares_one_ceiling_with_the_other_bulk_actions(self):
        """Ticking N boxes must mean the same N for every button on the bar."""
        from app.blueprints.documents.routes import MAX_BULK_SELECTION
        from app.services.bulk_export_service import MAX_SELECTION

        assert MAX_SELECTION == MAX_BULK_SELECTION

    def test_an_empty_selection_is_refused(self, client, document):
        response = client.post(
            "/exportar/markdown/selecao", data={"uuids": []}, follow_redirects=True
        )
        assert "Selecione ao menos um documento.".encode() in response.data

    def test_the_listing_offers_both_bulk_exports(self, client, document):
        body = client.get("/documentos/").data.decode("utf-8")
        assert "/exportar/markdown/tudo" in body
        assert "/exportar/markdown/selecao" in body


# ── Import ──────────────────────────────────────────────────────────────────


class TestImportFiles:
    def test_several_files_arrive_at_once(self, app):
        report = import_files(
            [
                upload("# Um\n\nA.".encode("utf-8"), "um.md"),
                upload("# Dois\n\nB.".encode("utf-8"), "dois.md"),
                upload("# Três\n\nC.".encode("utf-8"), "tres.md"),
            ]
        )

        assert report.created == 3
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 3

    def test_one_broken_file_does_not_cost_the_batch(self, app):
        report = import_files(
            [
                upload("# Bom\n\nA.".encode("utf-8"), "bom.md"),
                upload("acentuação".encode("latin-1"), "quebrado.md"),
                upload("# Outro\n\nB.".encode("utf-8"), "outro.md"),
            ]
        )

        assert report.created == 2
        assert report.failed == 1
        assert any("quebrado.md" in warning for warning in report.warnings)

    def test_an_unsupported_file_is_counted_not_imported(self, app):
        report = import_files([upload(b"binario", "imagem.png")])
        assert report.created == 0
        assert report.ignored == 1

    def test_nothing_selected_is_an_error(self, app):
        with pytest.raises(ValidationError):
            import_files([])

    def test_the_chosen_category_wins_over_the_file(self, app):
        from app.repositories.taxonomy_repository import CategoryRepository

        chosen = CategoryRepository.get_or_create("Escolhida")
        db.session.commit()

        payload = '---\ntitle: X\ncategory: "Do arquivo"\n---\n\nCorpo.'
        report = import_files([upload(payload.encode("utf-8"), "x.md")], category_id=chosen.id)

        assert report.first.category.name == "Escolhida"

    def test_the_file_category_is_used_when_none_was_chosen(self, app):
        payload = '---\ntitle: X\ncategory: "Do arquivo"\n---\n\nCorpo.'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.first.category.name == "Do arquivo"


class TestImportArchive:
    def test_a_zip_of_markdown_is_expanded(self, app):
        payload = make_zip(
            {
                "um.md": "# Um\n\nA.".encode("utf-8"),
                "categoria/dois.md": "# Dois\n\nB.".encode("utf-8"),
            }
        )
        report = import_files([upload(payload, "pacote.zip")])

        assert report.created == 2

    def test_entries_that_are_not_markdown_are_skipped_silently(self, app):
        payload = make_zip(
            {
                "um.md": "# Um\n\nA.".encode("utf-8"),
                "imagem.png": b"\x89PNG\r\n",
                ".DS_Store": b"lixo",
                "__MACOSX/._um.md": b"lixo",
            }
        )
        report = import_files([upload(payload, "pacote.zip")])

        assert report.created == 1
        assert report.failed == 0

    def test_a_traversing_member_never_becomes_a_document(self, app):
        payload = make_zip({"../fora.md": "# Fora\n\nX.".encode("utf-8")})
        report = import_files([upload(payload, "malicioso.zip")])

        assert report.created == 0
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

    def test_a_file_that_is_not_a_zip_is_reported(self, app):
        report = import_files([upload(b"nao sou um zip", "falso.zip")])

        assert report.failed == 1
        assert report.created == 0

    def test_a_corrupt_member_is_reported_and_the_rest_still_lands(self, app):
        """A failed CRC inside an otherwise valid archive."""
        buffer = io.BytesIO()
        # Stored, not deflated, so the payload can be corrupted in place.
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("bom.md", b"# Bom\n\ncorpo")
            archive.writestr("ruim.md", b"# Ruim\n\ncorpo")

        payload = bytearray(buffer.getvalue())
        marker = payload.rfind(b"# Ruim")
        assert marker != -1
        payload[marker : marker + 6] = b"######"

        report = import_files([upload(bytes(payload), "misto.zip")])

        assert report.created == 1
        assert report.failed == 1
        assert any("ruim.md" in warning for warning in report.warnings)

    def test_too_many_members_are_refused_before_reading(self, app, monkeypatch):
        from app.services import bulk_import_service

        monkeypatch.setattr(bulk_import_service, "MAX_ARCHIVE_MEMBERS", 2)
        payload = make_zip({f"doc{index}.md": b"# X" for index in range(5)})

        report = import_files([upload(payload, "grande.zip")])

        assert report.created == 0
        assert report.failed == 1

    def test_one_oversized_member_is_refused_before_it_is_read(self, app):
        """The aggregate ceiling would let a single huge entry through.

        `read()` allocates the whole member, so the per-document limit has to
        be applied to the declared size, not to the bytes that come back.
        """
        app.config["MAX_MARKDOWN_BYTES"] = 4096
        payload = make_zip({"bom.md": b"# Bom", "enorme.md": b"x" * 20000})

        report = import_files([upload(payload, "misto.zip")])

        assert report.created == 1
        assert report.failed == 1
        assert any("enorme.md" in warning for warning in report.warnings)

    def test_an_oversized_expansion_is_refused_before_reading(self, app, monkeypatch):
        from app.services import bulk_import_service

        monkeypatch.setattr(
            bulk_import_service, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 128
        )
        payload = make_zip({"grande.md": b"x" * 4096})

        report = import_files([upload(payload, "bomba.zip")])

        assert report.created == 0
        assert report.failed == 1


class TestPartialFailure:
    """The promise: whatever goes wrong with one file, the rest still lands."""

    def test_the_file_count_is_capped(self, app, monkeypatch):
        from app.services import bulk_import_service

        monkeypatch.setattr(bulk_import_service, "MAX_FILES_PER_REQUEST", 3)
        report = import_files(
            [upload(f"# Doc {i}".encode("utf-8"), f"d{i}.md") for i in range(10)]
        )

        assert report.created == 3
        assert any("3 arquivos" in warning for warning in report.warnings)

    def test_a_database_failure_on_one_file_spares_the_others(self, app, monkeypatch):
        from sqlalchemy.exc import SQLAlchemyError

        from app.services import bulk_import_service
        from app.services.document_service import DocumentService

        real = DocumentService.create

        def flaky(**kwargs):
            if "Falha" in kwargs.get("title", ""):
                raise SQLAlchemyError("boom")
            return real(**kwargs)

        monkeypatch.setattr(bulk_import_service.DocumentService, "create", flaky)

        report = import_files(
            [
                upload(b"# Bom um", "um.md"),
                upload("# Falha aqui".encode("utf-8"), "dois.md"),
                upload(b"# Bom tres", "tres.md"),
            ]
        )

        assert report.created == 2
        assert report.failed == 1
        assert any("dois.md" in warning for warning in report.warnings)

    def test_a_full_group_costs_only_the_membership(self, app, monkeypatch):
        """The document is worth more than the grouping it asked for."""
        from app.services import group_service

        monkeypatch.setattr(group_service, "MAX_DOCUMENTS_PER_GROUP", 0)
        payload = '---\ntitle: Sem grupo possível\ngroups: ["Cheio"]\n---\n\ncorpo'

        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.created == 1
        assert report.first.group_names == []
        assert any("grupos não aplicados" in warning for warning in report.warnings)


class TestHostileArchives:
    """A `.zip` of documents is something a person can be sent by someone else.

    Everything a header declares becomes a record this application renders, so
    the header is treated as attacker-controlled text throughout.
    """

    def test_markup_never_survives_into_a_title(self, app):
        payload = '---\ntitle: "<script>alert(1)</script>"\n---\n\ncorpo'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert "<script>" not in report.first.title
        assert "alert(1)" not in report.first.title

    def test_markup_never_survives_into_a_category(self, app):
        payload = '---\ntitle: X\ncategory: "<img src=x onerror=alert(1)>Marketing"\n---\n\nc'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.first.category.name == "Marketing"

    def test_markup_never_survives_into_a_tag(self, app):
        payload = '---\ntitle: X\ntags: ["<b>negrito</b>", "<script>mau</script>", "ok"]\n---\n\nc'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.first.tag_names == ["negrito", "ok"]

    def test_a_hostile_category_cannot_escape_its_folder_on_the_way_back(
        self, app, make_document
    ):
        from app.repositories.taxonomy_repository import CategoryRepository

        category = CategoryRepository.get_or_create("../../etc")
        db.session.commit()
        make_document(title="Dentro", category_id=category.id)

        for name in members(zip_bytes(export_all())):
            assert ".." not in name
            assert not name.startswith("/")

    def test_an_absolute_member_never_becomes_a_document(self, app):
        payload = make_zip({"/etc/passwd.md": b"# raiz"})
        report = import_files([upload(payload, "malicioso.zip")])
        assert report.created == 0

    def test_a_uuid_from_a_file_must_look_like_one(self, app):
        """A forged identifier must not become the stored one."""
        payload = '---\ntitle: X\nuuid: "../../etc/passwd"\n---\n\ncorpo'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.first.uuid != "../../etc/passwd"
        assert len(report.first.uuid) == 36

    def test_a_file_cannot_claim_to_be_in_the_trash(self, app):
        """`is_deleted` is not importable: nothing arrives already deleted."""
        payload = '---\ntitle: X\ndeleted: true\nis_deleted: true\n---\n\ncorpo'
        report = import_files([upload(payload.encode("utf-8"), "x.md")])

        assert report.first.is_deleted is False


class TestRoundTrip:
    """Export a library, wipe it, import the archive: nothing may be lost."""

    def _library(self, make_document):
        from app.repositories.taxonomy_repository import CategoryRepository
        from app.services.document_service import DocumentService

        category = CategoryRepository.get_or_create("Marketing")
        db.session.commit()

        first = make_document(
            title="Guia de Ads",
            content="# Guia de Ads\n\nCorpo com **negrito** e acentuação.",
            category_id=category.id,
            tag_names=["ads", "google"],
        )
        first.is_favorite = True
        GroupService.attach_by_names(first, ["Campanhas 2026"])

        second = make_document(title="Rascunho", content="Só um rascunho.")
        DocumentService.set_archived(second, True)
        db.session.commit()
        return first, second

    def test_documents_come_back_identical(self, app, make_document):
        original, _ = self._library(make_document)
        original_uuid = original.uuid
        original_body = original.content_markdown
        payload = zip_bytes(export_all())

        _wipe(app)
        report = import_files([upload(payload, "pacote.zip")])

        assert report.created == 2
        restored = DocumentRepository.get_by_uuid(original_uuid)
        assert restored is not None
        assert restored.title == "Guia de Ads"
        assert restored.content_markdown == original_body
        assert restored.category.name == "Marketing"
        assert restored.tag_names == ["ads", "google"]
        assert restored.group_names == ["Campanhas 2026"]
        assert restored.is_favorite is True

    def test_archived_state_survives(self, app, make_document):
        self._library(make_document)
        payload = zip_bytes(export_all())

        _wipe(app)
        import_files([upload(payload, "pacote.zip")])

        restored = db.session.scalars(
            db.select(Document).where(Document.title == "Rascunho")
        ).one()
        assert restored.is_archived is True

    def test_dates_survive(self, app, make_document):
        original, _ = self._library(make_document)
        original_uuid, created_at = original.uuid, original.created_at
        payload = zip_bytes(export_all())

        _wipe(app)
        import_files([upload(payload, "pacote.zip")])

        restored = DocumentRepository.get_by_uuid(original_uuid)
        assert restored.created_at.replace(tzinfo=None) == created_at.replace(tzinfo=None)

    def test_importing_the_same_archive_twice_changes_nothing(self, app, make_document):
        self._library(make_document)
        payload = zip_bytes(export_all())

        second = import_files([upload(payload, "pacote.zip")])

        assert second.created == 0
        assert second.skipped == 2
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 2

    def test_the_lock_survives(self, app, make_document):
        """A document protected against deletion must come back protected."""
        from app.services.document_service import DocumentService

        protected = make_document(title="Protegido")
        DocumentService.toggle_lock(protected)
        payload = zip_bytes(export_all())

        assert "locked" in members(payload)["protegido.md"]

        _wipe(app)
        import_files([upload(payload, "pacote.zip")])

        restored = db.session.scalars(db.select(Document)).one()
        assert restored.is_locked is True

    def test_a_rendered_body_is_rebuilt_not_carried(self, app, make_document):
        """The HTML is derived, so it must be produced again on import."""
        make_document(title="Com markup", content="# Título\n\nTexto **forte**.")
        payload = zip_bytes(export_all())

        _wipe(app)
        import_files([upload(payload, "pacote.zip")])

        restored = db.session.scalars(db.select(Document)).one()
        assert "<strong>forte</strong>" in restored.rendered_html
        assert restored.word_count > 0


class TestImportRoutes:
    def test_several_files_through_the_route(self, client, app):
        response = client.post(
            "/documentos/importar",
            data={
                "files": [
                    (io.BytesIO("# Um\n\nA.".encode("utf-8")), "um.md"),
                    (io.BytesIO("# Dois\n\nB.".encode("utf-8")), "dois.md"),
                ],
                "action": "import",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 2
        assert "2 documento(s) importado(s)".encode() in response.data

    def test_a_single_file_still_lands_in_the_editor(self, client, app):
        response = client.post(
            "/documentos/importar",
            data={
                "files": (io.BytesIO("# Sozinho\n\nA.".encode("utf-8")), "sozinho.md"),
                "action": "import",
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 302
        assert "/editor/" in response.headers["Location"]

    def test_a_zip_through_the_route(self, client, app):
        payload = make_zip({"um.md": "# Um\n\nA.".encode("utf-8")})

        client.post(
            "/documentos/importar",
            data={"files": (io.BytesIO(payload), "pacote.zip"), "action": "import"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_previewing_a_zip_explains_itself(self, client, app):
        payload = make_zip({"um.md": b"# Um"})

        response = client.post(
            "/documentos/importar",
            data={"files": (io.BytesIO(payload), "pacote.zip"), "action": "preview"},
            content_type="multipart/form-data",
        )

        assert "não se aplica a arquivos ZIP".encode() in response.data
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

    def test_the_page_announces_bulk_import(self, client, app):
        body = client.get("/documentos/importar").data.decode("utf-8")
        assert "multiple" in body
        assert ".zip" in body


class TestReport:
    def test_touched_counts_everything_seen(self):
        report = BulkImportReport(created=2, skipped=1, ignored=3, failed=1)
        assert report.touched == 7

    def test_warnings_are_bounded(self):
        report = BulkImportReport()
        for index in range(500):
            report.note(f"aviso {index}")
        assert len(report.warnings) <= 20


def _wipe(app) -> None:
    """Empty the library without touching categories, tags or groups."""
    from app.models import DocumentVersion
    from app.services.search_service import search_index

    db.session.execute(db.delete(DocumentVersion))
    db.session.execute(db.delete(Document))
    db.session.commit()
    db.session.expunge_all()
    search_index.rebuild()
