"""Markdown import, markdown download and PDF export."""

from __future__ import annotations

import io

import pytest
from werkzeug.datastructures import FileStorage

from app.services.bulk_import_service import import_files
from app.services.exceptions import ValidationError
from app.services.import_service import build_preview, decode_markdown
from app.services.pdf_service import (
    _resolve_local_asset,
    engine_info,
    render_document_pdf,
)
from app.utils.files import is_safe_archive_member, safe_filename


def upload(content: bytes, filename="documento.md"):
    return FileStorage(stream=io.BytesIO(content), filename=filename)


class TestImport:
    def test_imports_a_valid_file(self, app):
        report = import_files([upload("# Meu título\n\nCorpo.".encode("utf-8"))])
        assert report.created == 1
        assert report.first.title == "Meu título"
        assert "Corpo." in report.first.content_markdown

    def test_title_comes_from_the_filename_when_there_is_no_heading(self, app):
        preview = build_preview(upload(b"Apenas texto solto.", "notas-do-projeto.md"))
        assert preview.title == "notas do projeto"

    def test_front_matter_is_parsed(self, app):
        raw = "---\ntitle: Do front matter\ntags: alpha, beta\n---\n\n# Ignorado\n\nCorpo."
        preview = build_preview(upload(raw.encode("utf-8")))
        assert preview.title == "Do front matter"
        assert preview.tags == ["alpha", "beta"]
        assert not preview.content_markdown.startswith("---")

    def test_utf8_bom_is_handled(self, app):
        preview = build_preview(upload("# Ação\n\nAcentuação.".encode("utf-8-sig")))
        assert preview.title == "Ação"
        assert "Acentuação" in preview.content_markdown

    def test_accents_survive_the_round_trip(self, app):
        report = import_files([upload("# Ação\n\nÓrgão e coração.".encode("utf-8"))])
        assert "Órgão e coração." in report.first.content_markdown

    def test_non_utf8_file_is_rejected_with_a_clear_message(self, app):
        with pytest.raises(ValidationError) as excinfo:
            decode_markdown("acentuação".encode("latin-1"))
        assert "UTF-8" in excinfo.value.message

    def test_wrong_extension_is_rejected(self, app):
        with pytest.raises(ValidationError):
            build_preview(upload(b"conteudo", "malicioso.exe"))

    def test_empty_file_is_rejected(self, app):
        with pytest.raises(ValidationError):
            build_preview(upload(b"", "vazio.md"))

    def test_binary_disguised_as_markdown_is_rejected(self, app):
        with pytest.raises(ValidationError):
            build_preview(upload(b"PK\x03\x04\x00\x00binario", "falso.md"))

    def test_oversized_file_is_rejected(self, app):
        app.config["MAX_MARKDOWN_BYTES"] = 50
        with pytest.raises(ValidationError):
            build_preview(upload(b"x" * 500))

    def test_import_page_renders(self, client):
        response = client.get("/documentos/importar")
        assert response.status_code == 200
        assert "Importar".encode() in response.data

    def test_import_route_creates_a_document(self, client, app, db):
        from app.models import Document

        response = client.post(
            "/documentos/importar",
            data={
                "files": (io.BytesIO("# Importado\n\nOk.".encode("utf-8")), "arquivo.md"),
                "action": "import",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 1

    def test_import_route_preview_does_not_persist(self, client, app, db):
        from app.models import Document

        client.post(
            "/documentos/importar",
            data={
                "files": (io.BytesIO(b"# Somente previa"), "arquivo.md"),
                "action": "preview",
            },
            content_type="multipart/form-data",
        )
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0

    def test_import_route_rejects_a_bad_extension(self, client, app, db):
        from app.models import Document

        client.post(
            "/documentos/importar",
            data={"files": (io.BytesIO(b"x"), "virus.exe"), "action": "import"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert db.session.scalar(db.select(db.func.count(Document.id))) == 0


class TestMarkdownExport:
    def test_download_returns_the_original_markdown(self, client, document):
        response = client.get(f"/exportar/{document.uuid}/markdown")
        assert response.status_code == 200
        assert response.mimetype == "text/markdown"
        assert response.data.decode("utf-8") == document.content_markdown

    def test_filename_is_built_from_the_slug(self, client, make_document):
        document = make_document(title="Guia Profissional de Google Ads!")
        response = client.get(f"/exportar/{document.uuid}/markdown")
        disposition = response.headers["Content-Disposition"]
        assert "guia-profissional-de-google-ads.md" in disposition

    def test_utf8_is_preserved(self, client, make_document):
        document = make_document(title="Ação", content="Coração, órgão e ímã.")
        response = client.get(f"/exportar/{document.uuid}/markdown")
        assert "Coração, órgão e ímã." in response.data.decode("utf-8")


class TestFilenameSafety:
    @pytest.mark.parametrize(
        "title",
        ["../../etc/passwd", "C:\\Windows\\system32", "arquivo/com/barras", "CON", ""],
    )
    def test_filenames_never_contain_path_components(self, title):
        name = safe_filename(title, ".pdf")
        assert "/" not in name
        assert "\\" not in name
        assert ".." not in name
        assert name.endswith(".pdf")
        assert len(name) > 4

    @pytest.mark.parametrize(
        "member",
        ["../evil.md", "/absolute.md", "C:/windows/x.md", "a/../../b.md", "\\unc\\x.md"],
    )
    def test_unsafe_archive_members_are_rejected(self, member):
        assert is_safe_archive_member(member) is False

    def test_normal_archive_members_are_accepted(self):
        assert is_safe_archive_member("documents/guia.md") is True


class TestPdfExport:
    def test_an_engine_is_available(self, app):
        info = engine_info()
        assert info["active"] in {"weasyprint", "xhtml2pdf"}
        # xhtml2pdf is pure Python, so at least one engine always works.
        assert info["xhtml2pdf_available"] is True

    def test_pdf_is_generated(self, app, make_document):
        document = make_document(
            title="Documento para PDF",
            content="# Título\n\nTexto com **negrito**.\n\n| A | B |\n|---|---|\n| 1 | 2 |",
        )
        pdf_bytes, filename = render_document_pdf(document)

        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 800
        assert filename == "documento-para-pdf.pdf"

    @pytest.mark.parametrize("theme", ["classic", "minimal", "academic", "modern"])
    def test_every_theme_renders(self, app, document, theme):
        pdf_bytes, _ = render_document_pdf(document, {"theme": theme})
        assert pdf_bytes.startswith(b"%PDF")

    @pytest.mark.parametrize("page_size", ["A4", "Letter"])
    def test_both_page_sizes_render(self, app, document, page_size):
        pdf_bytes, _ = render_document_pdf(document, {"page_size": page_size})
        assert pdf_bytes.startswith(b"%PDF")

    def test_pdf_route_returns_a_file(self, client, document):
        response = client.get(f"/exportar/{document.uuid}/pdf")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF")

    def test_pdf_of_a_code_heavy_document(self, app, make_document):
        document = make_document(
            title="Com código",
            content="```python\n" + "\n".join(f"linha_{i} = {i}" for i in range(40)) + "\n```",
        )
        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")


class TestPdfResourcePolicy:
    """The URL fetcher is the choke point against SSRF and local file reads."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.test/logo.png",
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/admin",
            "ftp://example.com/file",
            "file:///etc/passwd",
            "file:///C:/Windows/win.ini",
        ],
    )
    def test_external_and_arbitrary_local_resources_are_blocked(self, app, url):
        with app.test_request_context():
            assert _resolve_local_asset(url) is None

    def test_traversal_out_of_static_is_blocked(self, app):
        with app.test_request_context():
            assert _resolve_local_asset("/static/../../../../etc/passwd") is None

    def test_a_real_static_asset_resolves(self, app):
        with app.test_request_context():
            resolved = _resolve_local_asset("/static/css/base.css")
            assert resolved is not None
            assert resolved.name == "base.css"

    def test_remote_image_in_a_document_does_not_break_export(self, app, make_document):
        document = make_document(
            title="Com imagem remota",
            content="![logo](https://evil.test/tracker.png)\n\nTexto normal.",
        )
        # The image is skipped; the document still exports.
        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")
