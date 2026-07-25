"""Envio de arquivos do computador (PDF, Office, compactados, texto).

Two properties are worth more than all the others here and most of this file
exists to pin them down:

* an attachment is **never** interpreted by the browser in our origin - it is
  always delivered as a download;
* the *content* decides what a file is, so nothing dangerous gets in by
  wearing an accepted extension.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from app.services.exceptions import ValidationError
from app.services.markdown_service import render_markdown
from app.services.media_service import (
    KIND_FILE,
    PICKER_ACCEPT,
    badge_for,
    detect,
    label_for,
    markdown_for,
    max_bytes_for,
    store_upload,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"conteudo do relatorio " * 40
RTF = b"{\\rtf1\\ansi Documento de teste}"
SEVEN_ZIP = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
MP3 = b"ID3\x03\x00\x00\x00" + b"\x00" * 64


def upload(payload: bytes, filename: str = "arquivo.pdf") -> FileStorage:
    return FileStorage(stream=io.BytesIO(payload), filename=filename)


def zip_with(members: dict[str, str], first: str | None = None) -> bytes:
    """Build a ZIP; ``first`` is written before the rest (ODF's `mimetype`)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if first is not None:
            archive.writestr("mimetype", first)
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def ooxml(prefix: str) -> bytes:
    return zip_with({"[Content_Types].xml": "<Types/>", f"{prefix}/document.xml": "<x/>"})


class TestAcceptedFormats:
    @pytest.mark.parametrize(
        "payload,filename,expected_mime",
        [
            (PDF, "relatorio.pdf", "application/pdf"),
            (RTF, "carta.rtf", "application/rtf"),
            (SEVEN_ZIP, "pacote.7z", "application/x-7z-compressed"),
            (OLE, "planilha.xls", "application/x-ole-storage"),
            (MP3, "audio.mp3", "audio/mpeg"),
        ],
    )
    def test_binary_documents_are_stored_as_attachments(
        self, app, payload, filename, expected_mime
    ):
        asset = store_upload(upload(payload, filename))

        assert asset.kind == KIND_FILE
        assert asset.mime_type == expected_mime
        assert asset.original_name == filename

    @pytest.mark.parametrize(
        "prefix,expected_mime,expected_extension",
        [
            ("word", "application/vnd.openxmlformats-officedocument."
                     "wordprocessingml.document", ".docx"),
            ("xl", "application/vnd.openxmlformats-officedocument."
                   "spreadsheetml.sheet", ".xlsx"),
            ("ppt", "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation", ".pptx"),
        ],
    )
    def test_office_files_are_told_apart_from_a_plain_zip(
        self, app, prefix, expected_mime, expected_extension
    ):
        """Every OOXML file is a ZIP; only its contents say which one."""
        asset = store_upload(upload(ooxml(prefix), f"documento{expected_extension}"))

        assert asset.mime_type == expected_mime
        assert asset.stored_path.endswith(expected_extension)

    def test_opendocument_declares_itself(self, app):
        payload = zip_with(
            {"content.xml": "<x/>"}, first="application/vnd.oasis.opendocument.text"
        )
        asset = store_upload(upload(payload, "texto.odt"))

        assert asset.mime_type == "application/vnd.oasis.opendocument.text"

    def test_an_unrecognised_archive_is_still_a_zip(self, app):
        asset = store_upload(upload(zip_with({"leiame.txt": "oi"}), "pacote.zip"))

        assert asset.kind == KIND_FILE
        assert asset.mime_type == "application/zip"

    @pytest.mark.parametrize(
        "filename", ["dados.csv", "notas.md", "config.json", "registro.log", "a.yaml"]
    )
    def test_utf8_text_is_accepted_by_extension(self, app, filename):
        asset = store_upload(upload("coluna;valor\nAção;10\n".encode(), filename))

        assert asset.kind == KIND_FILE
        assert asset.mime_type == "text/plain"

    def test_the_picker_offers_the_formats_it_accepts(self, app):
        for extension in (".pdf", ".docx", ".xlsx", ".zip", ".csv", ".png", ".mp4"):
            assert extension in PICKER_ACCEPT


class TestWhatMustBeRefused:
    @pytest.mark.parametrize(
        "payload,filename",
        [
            (b"<html><script>alert(1)</script></html>", "pagina.html"),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "arte.svg"),
            (b"MZ\x90\x00" + b"\x00" * 64, "instalador.exe"),
            (b"\x7fELF\x02\x01\x01" + b"\x00" * 64, "binario.bin"),
            (b"@echo off\r\ndel /f /q *", "limpar.bat"),
            (b"#!/bin/sh\nrm -rf /", "script.sh"),
            (b"console.log(1)", "app.js"),
        ],
    )
    def test_executable_and_markup_formats_are_rejected(self, app, payload, filename):
        with pytest.raises(ValidationError):
            store_upload(upload(payload, filename))

    def test_an_html_file_renamed_to_txt_is_stored_as_inert_text(self, app):
        """Accepted, and harmless: text is only ever delivered as a download."""
        asset = store_upload(upload(b"<script>alert(1)</script>", "nota.txt"))

        assert asset.mime_type == "text/plain"
        assert asset.kind == KIND_FILE

    def test_binary_content_with_a_text_extension_is_rejected(self, app):
        with pytest.raises(ValidationError):
            store_upload(upload(b"dados\x00binarios", "planilha.csv"))

    def test_text_that_is_not_utf8_says_so(self, app):
        with pytest.raises(ValidationError) as excinfo:
            store_upload(upload("acentuação".encode("latin-1"), "nota.txt"))

        assert "UTF-8" in excinfo.value.message

    def test_an_extension_alone_does_not_make_a_pdf(self, app):
        with pytest.raises(ValidationError):
            store_upload(upload(b"nao sou um pdf de verdade" * 4, "falso.pdf"))

    def test_a_real_pdf_named_exe_is_still_accepted(self, app):
        """The extension carries no authority in either direction."""
        asset = store_upload(upload(PDF, "estranho.exe"))
        assert asset.mime_type == "application/pdf"

    def test_detection_needs_no_filename_for_binary_formats(self, app):
        assert detect(PDF, "").mime == "application/pdf"
        assert detect(b"texto qualquer", "") is None


class TestLimits:
    def test_attachments_have_their_own_ceiling(self, app):
        app.config["MEDIA_MAX_FILE_BYTES"] = 1024

        with pytest.raises(ValidationError) as excinfo:
            store_upload(upload(PDF + b"\x00" * 4096, "grande.pdf"))

        assert "limite" in excinfo.value.message

    def test_the_global_ceiling_does_not_shadow_the_attachment_limit(self, app):
        assert app.config["MAX_CONTENT_LENGTH"] > max_bytes_for(KIND_FILE)


class TestDelivery:
    def test_an_attachment_is_always_downloaded_never_rendered(self, client, app):
        asset = store_upload(upload(PDF, "relatório final.pdf"))

        response = client.get(f"/midia/{asset.uuid}")

        assert response.status_code == 200
        assert response.headers["Content-Disposition"].startswith("attachment")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in response.headers["Content-Security-Policy"]

    def test_the_download_keeps_the_original_filename(self, client, app):
        asset = store_upload(upload(PDF, "relatorio.pdf"))
        response = client.get(f"/midia/{asset.uuid}")

        assert "relatorio.pdf" in response.headers["Content-Disposition"]

    def test_images_are_still_served_inline(self, client, app):
        from tests.test_media import REAL_PNG

        asset = store_upload(upload(REAL_PNG, "foto.png"))
        response = client.get(f"/midia/{asset.uuid}")

        assert "attachment" not in response.headers.get("Content-Disposition", "")

    def test_a_header_cannot_be_injected_through_the_filename(self, app):
        asset = store_upload(upload(PDF, 'rel"atorio\r\nX-Injected: 1.pdf'))

        assert '"' not in asset.original_name
        assert "\r" not in asset.original_name and "\n" not in asset.original_name


class TestUploadEndpoint:
    def test_upload_returns_a_link_snippet_for_an_attachment(self, client, app):
        response = client.post(
            "/api/midia",
            data={"file": (io.BytesIO(PDF), "relatorio.pdf")},
            content_type="multipart/form-data",
        )

        payload = response.get_json()
        assert payload["kind"] == "file"
        assert payload["markdown"] == f"[relatorio.pdf]({payload['url']})"
        assert payload["badge"] == "PDF"
        assert payload["type_label"] == "PDF"
        assert payload["size_readable"]

    def test_a_bracket_in_the_name_cannot_break_the_link(self, app):
        asset = store_upload(upload(PDF, "Relatório [v2].pdf"))
        snippet = markdown_for(asset, f"/midia/{asset.uuid}")

        assert snippet == f"[Relatório \\[v2\\].pdf](/midia/{asset.uuid})"

        html = render_markdown(snippet)
        assert "attachment" in html
        assert "Relatório [v2].pdf" in html


class TestRendering:
    def test_a_link_to_an_upload_becomes_a_card(self, app):
        asset = store_upload(upload(PDF, "relatorio.pdf"))

        html = render_markdown(f"[relatorio.pdf](/midia/{asset.uuid})")

        assert 'class="attachment"' in html
        assert '<span class="attachment-badge">PDF</span>' in html
        assert "relatorio.pdf" in html
        assert "PDF ·" in html  # type and size in the meta line

    def test_a_renamed_link_keeps_the_real_filename_in_sight(self, app):
        asset = store_upload(upload(PDF, "contrato-2026.pdf"))

        html = render_markdown(f"[o contrato](/midia/{asset.uuid})")

        assert ">o contrato<" in html
        assert "contrato-2026.pdf" in html

    def test_a_missing_file_is_reported_instead_of_offering_a_dead_link(self, app):
        html = render_markdown("[sumiu.pdf](/midia/11111111-2222-3333-4444-555555555555)")

        assert "attachment-missing" in html
        assert "não está mais disponível" in html

    def test_ordinary_links_are_left_alone(self, app):
        html = render_markdown("[site](https://exemplo.com) e [x](/midia/nao-e-uuid)")

        assert "attachment" not in html
        assert 'href="https://exemplo.com"' in html

    def test_an_image_inside_a_link_is_not_replaced(self, app):
        asset = store_upload(upload(PDF, "relatorio.pdf"))

        html = render_markdown(f"[![capa](/midia/x)](/midia/{asset.uuid})")

        assert "attachment" not in html
        assert "<img" in html

    def test_the_card_survives_the_sanitizer(self, app):
        """The card is built after rendering; the allowlist must keep it."""
        asset = store_upload(upload(PDF, "relatorio.pdf"))
        html = render_markdown(f"[relatorio.pdf](/midia/{asset.uuid})")

        assert html.count("<span") == 4

    def test_links_past_the_resolution_cap_stay_plain_links(self, app):
        """A capped link is not a broken one.

        Past MAX_ATTACHMENTS_RESOLVED nothing is looked up, so the renderer
        cannot know whether those files exist - and must not claim they are
        gone. They render as the links the writer typed.
        """
        from app.services.attachment_service import MAX_ATTACHMENTS_RESOLVED

        import uuid as uuid_module

        extra = 5
        uuids = [
            str(uuid_module.uuid4())
            for _ in range(MAX_ATTACHMENTS_RESOLVED + extra)
        ]
        body = "\n\n".join(f"[arquivo](/midia/{value})" for value in uuids)

        html = render_markdown(body)

        # Every link still points where it did; only the resolved ones became
        # cards, and the five past the cap were left untouched.
        assert html.count('href="/midia/') == len(uuids)
        assert html.count("attachment-missing") == MAX_ATTACHMENTS_RESOLVED
        assert html.count('<a href="/midia/') == extra

    def test_a_database_failure_leaves_the_links_alone(self, app, monkeypatch):
        """Rendering survives a database hiccup without inventing bad news."""
        from app.services import attachment_service

        def explode(_uuids):
            raise RuntimeError("banco indisponível")

        monkeypatch.setattr(attachment_service, "resolve_assets", explode)

        html = render_markdown("[x](/midia/11111111-2222-3333-4444-555555555555)")

        assert "attachment-missing" not in html
        assert 'href="/midia/11111111-2222-3333-4444-555555555555"' in html

    def test_many_attachments_cost_one_query(self, app, db):
        assets = [store_upload(upload(PDF, f"doc-{index}.pdf")) for index in range(5)]
        body = "\n\n".join(f"[doc](/midia/{asset.uuid})" for asset in assets)

        from sqlalchemy import event

        statements: list[str] = []

        def record(_conn, _cursor, statement, *_args):
            if "media_assets" in statement:
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            html = render_markdown(body)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

        assert html.count('class="attachment"') == 5
        assert len(statements) == 1


class TestPdfExport:
    def test_a_card_becomes_a_printable_line(self, app):
        from app.services.pdf_service import prepare_for_print

        asset = store_upload(upload(PDF, "relatorio.pdf"))
        printed = prepare_for_print(render_markdown(f"[relatorio.pdf](/midia/{asset.uuid})"))

        assert "attachment-print" in printed
        assert "Anexo:" in printed
        assert "relatorio.pdf" in printed
        assert "<span" not in printed

    def test_a_missing_attachment_prints_as_unavailable(self, app):
        from app.services.pdf_service import prepare_for_print

        printed = prepare_for_print(
            render_markdown("[sumiu.pdf](/midia/11111111-2222-3333-4444-555555555555)")
        )

        assert "Anexo indisponível:" in printed

    def test_an_attachment_is_not_embedded_in_the_pdf(self, app):
        """Only images are read from disk during rendering."""
        from app.services.pdf_service import _resolve_local_asset

        asset = store_upload(upload(PDF, "relatorio.pdf"))
        with app.test_request_context():
            assert _resolve_local_asset(f"/midia/{asset.uuid}") is None

    def test_a_document_with_an_attachment_exports(self, app, make_document):
        from app.services.pdf_service import render_document_pdf

        asset = store_upload(upload(PDF, "relatorio.pdf"))
        document = make_document(
            title="Com anexo",
            content=f"# Título\n\n[relatorio.pdf](/midia/{asset.uuid})\n",
        )

        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")


class TestEditorWiring:
    def test_the_editor_publishes_the_limits_as_parseable_json(self, client, document):
        """Found in QA: `|tojson` in a double-quoted attribute ends it early.

        The value silently became "{", the browser scattered the rest as bogus
        attributes, and the client-side size check fell back to its defaults -
        so a custom MEDIA_MAX_*_MB was never honoured in the browser.
        """
        import json
        import re

        html = client.get(f"/editor/{document.uuid}").data.decode()
        match = re.search(r"data-upload-limits='([^']*)'", html)

        assert match, "o atributo data-upload-limits sumiu do editor"
        limits = json.loads(match.group(1))
        assert set(limits) == {"image", "video", "file"}
        assert all(isinstance(value, int) and value > 0 for value in limits.values())

    def test_the_picker_accepts_every_format_the_server_takes(self, client, document):
        from app.services.media_service import PICKER_ACCEPT

        html = client.get(f"/editor/{document.uuid}").data.decode()
        assert f'accept="{PICKER_ACCEPT}"' in html


class TestPresentation:
    def test_the_badge_comes_from_the_name_the_user_knows(self, app):
        """A legacy .xls and .doc share one container; the name tells them apart."""
        spreadsheet = store_upload(upload(OLE, "orcamento.xls"))
        document = store_upload(upload(OLE, "carta.doc"))

        assert badge_for(spreadsheet) == "XLS"
        assert badge_for(document) == "DOC"

    def test_a_hostile_name_cannot_reach_the_badge(self, app):
        asset = store_upload(upload(PDF, "arquivo.<script>"))
        assert badge_for(asset) == "PDF"

    def test_labels_are_written_for_people(self, app):
        asset = store_upload(upload(ooxml("xl"), "planilha.xlsx"))
        assert label_for(asset) == "Planilha do Excel"


class TestLifecycle:
    def test_an_attachment_is_pruned_with_its_document(self, app, make_document, db):
        from app.models import MediaAsset
        from app.services.media_service import asset_exists, delete_for_documents

        document = make_document(title="Temporário")
        asset = store_upload(upload(PDF, "relatorio.pdf"), document_id=document.id)
        assert asset_exists(asset)

        delete_for_documents([document.id])
        db.session.commit()

        assert db.session.scalars(db.select(MediaAsset)).all() == []
