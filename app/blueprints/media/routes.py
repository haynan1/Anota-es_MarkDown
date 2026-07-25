"""Upload and delivery of images, videos and file attachments."""

from __future__ import annotations

from flask import jsonify, request, send_file, url_for

from app.blueprints.media import media_bp
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.media_service import (
    ALLOWED_MIME_TYPES,
    INLINE_KINDS,
    asset_path,
    badge_for,
    get_asset,
    label_for,
    markdown_for,
    store_upload,
)
from app.utils.humanize import format_bytes


@media_bp.post("/api/midia")
def upload():
    """Accept one file from the editor and return the snippet to insert."""
    storage = request.files.get("file")

    document_id = None
    document_uuid = (request.form.get("document_uuid") or "").strip()
    if document_uuid:
        document_id = DocumentService.require(document_uuid).id

    asset = store_upload(storage, document_id=document_id)
    url = url_for("media.serve", public_uuid=asset.uuid)

    return jsonify(
        {
            "ok": True,
            "uuid": asset.uuid,
            "url": url,
            "kind": asset.kind,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "size_readable": format_bytes(asset.size_bytes),
            "badge": badge_for(asset),
            "type_label": label_for(asset),
            "original_name": asset.original_name,
            "markdown": markdown_for(asset, url),
        }
    )


@media_bp.get("/midia/<public_uuid>")
def serve(public_uuid: str):
    """Deliver a stored asset.

    The path comes from the database row, never from the request, and the
    Content-Type is replayed from our own allowlist so a stored file cannot
    be re-interpreted as something executable.

    Only images and videos are shown inline - they are the two kinds this
    application renders. Everything else is sent as a download: a PDF, an
    Office file or a text file has no business being interpreted inside our
    origin, and ``attachment`` is what guarantees it never is.
    """
    asset = get_asset(public_uuid)

    if asset.mime_type not in ALLOWED_MIME_TYPES:  # pragma: no cover - defensive
        raise ValidationError("Tipo de arquivo não suportado.")

    inline = asset.kind in INLINE_KINDS

    response = send_file(
        asset_path(asset),
        mimetype=asset.mime_type,
        as_attachment=not inline,
        download_name=asset.original_name or f"{asset.uuid}",
        conditional=True,  # enables range requests, which video seeking needs
    )
    # Belt and braces: never let a browser sniff its way to another type.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
