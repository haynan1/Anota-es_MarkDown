"""Upload and delivery of images, GIFs and videos."""

from __future__ import annotations

from flask import jsonify, request, send_file, url_for

from app.blueprints.media import media_bp
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.media_service import (
    ALLOWED_MIME_TYPES,
    asset_path,
    get_asset,
    markdown_for,
    store_upload,
)


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
    """
    asset = get_asset(public_uuid)

    if asset.mime_type not in ALLOWED_MIME_TYPES:  # pragma: no cover - defensive
        raise ValidationError("Tipo de arquivo não suportado.")

    response = send_file(
        asset_path(asset),
        mimetype=asset.mime_type,
        as_attachment=False,
        download_name=asset.original_name or f"{asset.uuid}",
        conditional=True,  # enables range requests, which video seeking needs
    )
    # Belt and braces: never let a browser sniff its way to another type.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
