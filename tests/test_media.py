"""Uploads of images, GIFs and videos.

The upload endpoint is the most dangerous input this application accepts, so
most of this file is about what must be *refused*.
"""

from __future__ import annotations

import base64
import io

import pytest
from werkzeug.datastructures import FileStorage

from app.models import MediaAsset
from app.services.exceptions import ValidationError
from app.services.markdown_service import render_markdown
from app.services.media_service import (
    KIND_IMAGE,
    KIND_VIDEO,
    asset_path,
    identify,
    max_bytes_for,
    store_upload,
)

# A real 1x1 PNG - decodable by the PDF engine, not just signature-shaped.
REAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
GIF = b"GIF89a" + b"\x01\x00\x01\x00" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 40
MP4 = b"\x00\x00\x00\x20" + b"ftypisom" + b"\x00" * 60
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 60


def upload(payload: bytes, filename="arquivo.png"):
    return FileStorage(stream=io.BytesIO(payload), filename=filename)


class TestAcceptedFormats:
    @pytest.mark.parametrize(
        "payload,expected_kind,expected_mime",
        [
            (REAL_PNG, KIND_IMAGE, "image/png"),
            (JPEG, KIND_IMAGE, "image/jpeg"),
            (GIF, KIND_IMAGE, "image/gif"),
            (WEBP, KIND_IMAGE, "image/webp"),
            (MP4, KIND_VIDEO, "video/mp4"),
            (WEBM, KIND_VIDEO, "video/webm"),
        ],
    )
    def test_supported_types_are_stored(self, app, payload, expected_kind, expected_mime):
        asset = store_upload(upload(payload))

        assert asset.kind == expected_kind
        assert asset.mime_type == expected_mime
        assert asset.size_bytes == len(payload)
        assert asset_path(asset).read_bytes() == payload

    def test_gif87a_is_also_accepted(self, app):
        asset = store_upload(upload(b"GIF87a" + b"\x00" * 40))
        assert asset.mime_type == "image/gif"


class TestTypeIsDecidedByContent:
    def test_html_disguised_as_png_is_rejected(self, app):
        """The classic stored-XSS attempt: an HTML file with an image name."""
        payload = b"<html><script>alert(1)</script></html>"
        with pytest.raises(ValidationError):
            store_upload(upload(payload, "inocente.png"))

    def test_svg_is_rejected(self, app):
        """SVG is a script-bearing document; serving it same-origin is XSS."""
        payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises(ValidationError):
            store_upload(upload(payload, "imagem.svg"))

    def test_php_disguised_as_gif_is_rejected(self, app):
        with pytest.raises(ValidationError):
            store_upload(upload(b"<?php system($_GET[0]); ?>", "shell.gif"))

    def test_a_real_png_named_exe_is_still_accepted(self, app):
        """The extension carries no authority in either direction."""
        asset = store_upload(upload(REAL_PNG, "estranho.exe"))
        assert asset.mime_type == "image/png"

    def test_signature_prefix_alone_is_not_enough_for_webp(self, app):
        """RIFF without the WEBP marker is some other RIFF container."""
        assert identify(b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 20) is None

    def test_unknown_binary_is_rejected(self, app):
        with pytest.raises(ValidationError):
            store_upload(upload(b"\x00\x01\x02\x03binario qualquer" * 10))

    def test_empty_file_is_rejected(self, app):
        with pytest.raises(ValidationError):
            store_upload(upload(b""))

    def test_missing_file_is_rejected(self, app):
        with pytest.raises(ValidationError):
            store_upload(None)


class TestLimits:
    def test_oversized_image_is_rejected(self, app):
        app.config["MEDIA_MAX_IMAGE_BYTES"] = 1024
        payload = REAL_PNG + b"\x00" * 2048

        with pytest.raises(ValidationError) as excinfo:
            store_upload(upload(payload))

        assert "limite" in excinfo.value.message

    def test_video_limit_is_higher_than_the_image_limit(self, app):
        assert max_bytes_for(KIND_VIDEO) > max_bytes_for(KIND_IMAGE)

    def test_the_global_ceiling_does_not_shadow_the_video_limit(self, app):
        """Found in QA: a global 8 MB cap made every real video fail with 413.

        The Werkzeug ceiling has to sit above the largest per-kind limit,
        otherwise the feature's own validation is unreachable.
        """
        ceiling = app.config["MAX_CONTENT_LENGTH"]

        assert ceiling > max_bytes_for(KIND_VIDEO)
        assert ceiling > max_bytes_for(KIND_IMAGE)
        assert ceiling > app.config["MAX_DOCUMENT_UPLOAD_BYTES"]

    def test_markdown_import_keeps_its_own_smaller_ceiling(self, client, app):
        """The generous global limit must not leak into other endpoints.

        The import ceiling is the bulk one - a single request may carry a whole
        library - but it is still well below the video-sized global cap, and
        it is enforced before the body is parsed.
        """
        assert app.config["MAX_BULK_IMPORT_BYTES"] < app.config["MAX_CONTENT_LENGTH"]

        # Lowered rather than exercised at its real size: this asserts that the
        # gate is wired to the endpoint, not that Werkzeug can buffer 64 MB.
        app.config["MAX_BULK_IMPORT_BYTES"] = 4096
        oversized = b"x" * 16384

        response = client.post(
            "/documentos/importar",
            data={"files": (io.BytesIO(oversized), "grande.md"), "action": "import"},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert "limite".encode() in response.data


class TestStorageSafety:
    def test_stored_name_is_generated_not_supplied(self, app):
        asset = store_upload(upload(REAL_PNG, "../../etc/passwd.png"))

        assert asset.uuid in asset.stored_path
        assert ".." not in asset.stored_path
        assert "passwd" not in asset.stored_path
        assert asset.stored_path.endswith(".png")

    def test_original_name_is_kept_only_as_a_label(self, app):
        asset = store_upload(upload(REAL_PNG, "../../etc/passwd.png"))
        assert asset.original_name == "passwd.png"

    def test_file_lands_inside_the_uploads_root(self, app):
        from app.services.media_service import uploads_root

        asset = store_upload(upload(REAL_PNG))
        resolved = asset_path(asset).resolve()
        resolved.relative_to(uploads_root().resolve())  # raises if outside

    def test_a_doctored_row_cannot_escape_the_root(self, app, db):
        from app.services.exceptions import NotFoundError

        asset = store_upload(upload(REAL_PNG))
        asset.stored_path = "../../../app/config.py"
        db.session.commit()

        with pytest.raises(NotFoundError):
            asset_path(asset)


class TestDelivery:
    def test_asset_is_served_with_its_recorded_type(self, client, app):
        asset = store_upload(upload(REAL_PNG))
        response = client.get(f"/midia/{asset.uuid}")

        assert response.status_code == 200
        assert response.mimetype == "image/png"
        assert response.data == REAL_PNG

    def test_delivery_forbids_content_sniffing(self, client, app):
        asset = store_upload(upload(REAL_PNG))
        response = client.get(f"/midia/{asset.uuid}")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in response.headers["Content-Security-Policy"]

    def test_unknown_asset_is_a_404(self, client, app):
        response = client.get("/midia/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "identifier", ["../../app/config.py", "..%2f..%2fconfig.py", "nao-e-uuid"]
    )
    def test_traversal_in_the_url_finds_nothing(self, client, app, identifier):
        response = client.get(f"/midia/{identifier}")
        assert response.status_code == 404
        assert b"SECRET_KEY" not in response.data


class TestUploadEndpoint:
    def test_upload_returns_the_snippet_to_insert(self, client, app):
        response = client.post(
            "/api/midia",
            data={"file": (io.BytesIO(REAL_PNG), "foto.png")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["kind"] == "image"
        assert payload["markdown"] == f"![foto.png]({payload['url']})"
        assert payload["url"].startswith("/midia/")

    def test_video_upload_produces_a_video_tag(self, client, app):
        response = client.post(
            "/api/midia",
            data={"file": (io.BytesIO(MP4), "clipe.mp4")},
            content_type="multipart/form-data",
        )

        payload = response.get_json()
        assert payload["kind"] == "video"
        assert payload["markdown"].startswith("<video controls")
        assert payload["url"] in payload["markdown"]

    def test_rejected_upload_returns_a_readable_error(self, client, app):
        response = client.post(
            "/api/midia",
            data={"file": (io.BytesIO(b"<html>nao</html>"), "falso.png")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        body = response.get_json()
        assert body["ok"] is False
        assert "Formato" in body["error"]

    def test_upload_can_be_attached_to_a_document(self, client, app, document, db):
        response = client.post(
            "/api/midia",
            data={
                "file": (io.BytesIO(REAL_PNG), "anexo.png"),
                "document_uuid": document.uuid,
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        asset = db.session.scalars(db.select(MediaAsset)).one()
        assert asset.document_id == document.id

    def test_upload_requires_csrf(self, csrf_app):
        client = csrf_app.test_client()
        response = client.post(
            "/api/midia",
            data={"file": (io.BytesIO(REAL_PNG), "foto.png")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400


class TestRenderingAndSanitisation:
    def test_uploaded_image_renders(self, app):
        html = render_markdown("![foto](/midia/abc-123)")
        assert '<img' in html
        assert 'src="/midia/abc-123"' in html

    def test_internal_video_survives_with_controls(self, app):
        html = render_markdown('<video controls src="/midia/abc-123"></video>')

        assert "<video" in html
        assert 'src="/midia/abc-123"' in html
        assert "controls" in html

    @pytest.mark.parametrize(
        "src",
        [
            "https://evil.test/tracker.mp4",
            "//evil.test/tracker.mp4",
            "file:///etc/passwd",
            "/static/../instance/app.db",
            "javascript:alert(1)",
        ],
    )
    def test_video_pointing_elsewhere_is_removed(self, app, src):
        html = render_markdown(f'<video controls src="{src}"></video>')

        assert "<video" not in html
        assert "evil.test" not in html
        assert "javascript:" not in html.lower()

    def test_autoplay_is_stripped(self, app):
        """A document must not be able to start making noise on its own."""
        html = render_markdown('<video autoplay controls src="/midia/abc-123"></video>')
        assert "autoplay" not in html.lower()

    def test_event_handlers_on_video_are_stripped(self, app):
        html = render_markdown('<video src="/midia/abc" onerror="alert(1)"></video>')
        assert "onerror" not in html.lower()


class TestPdfIntegration:
    def test_uploaded_image_resolves_for_the_pdf(self, app):
        from app.services.pdf_service import _resolve_local_asset

        asset = store_upload(upload(REAL_PNG))
        with app.test_request_context():
            resolved = _resolve_local_asset(f"/midia/{asset.uuid}")

        assert resolved is not None
        assert resolved.read_bytes() == REAL_PNG

    def test_uploaded_video_is_not_embedded(self, app):
        from app.services.pdf_service import _resolve_local_asset

        asset = store_upload(upload(MP4, "clipe.mp4"))
        with app.test_request_context():
            assert _resolve_local_asset(f"/midia/{asset.uuid}") is None

    def test_unknown_media_reference_resolves_to_nothing(self, app):
        from app.services.pdf_service import _resolve_local_asset

        with app.test_request_context():
            assert _resolve_local_asset("/midia/../../app/config.py") is None
            assert _resolve_local_asset("/midia/nao-e-uuid") is None

    def test_video_becomes_a_placeholder_in_print(self, app):
        from app.services.pdf_service import replace_videos_for_print

        printed = replace_videos_for_print(
            '<p>antes</p><video controls src="/midia/x"></video><p>depois</p>'
        )
        assert "<video" not in printed
        assert "media-placeholder" in printed
        assert "antes" in printed and "depois" in printed

    def test_document_with_media_exports_to_pdf(self, app, make_document):
        from app.services.pdf_service import render_document_pdf

        asset = store_upload(upload(REAL_PNG))
        document = make_document(
            title="Com mídia",
            content=f"# Título\n\n![foto](/midia/{asset.uuid})\n\n"
            f'<video controls src="/midia/{asset.uuid}"></video>',
        )

        pdf_bytes, _ = render_document_pdf(document)
        assert pdf_bytes.startswith(b"%PDF")
