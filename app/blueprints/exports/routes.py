from __future__ import annotations

import io

from flask import Response, flash, redirect, request, send_file, url_for

from app.blueprints.exports import exports_bp
from app.models import PAGE_SIZES, PDF_THEMES
from app.services.document_service import DocumentService
from app.services.pdf_service import PdfGenerationError, render_document_pdf
from app.utils.files import safe_filename


@exports_bp.get("/<public_uuid>/markdown")
def download_markdown(public_uuid: str) -> Response:
    """Download the original ``.md`` file, UTF-8 with a BOM-free body."""
    document = DocumentService.require(public_uuid)
    payload = (document.content_markdown or "").encode("utf-8")

    return send_file(
        io.BytesIO(payload),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=safe_filename(document.title, ".md"),
    )


@exports_bp.route("/<public_uuid>/pdf", methods=["GET", "POST"])
def download_pdf(public_uuid: str):
    document = DocumentService.require(public_uuid)

    source = request.form if request.method == "POST" else request.args
    overrides = {
        "theme": source.get("tema") if source.get("tema") in PDF_THEMES else None,
        "page_size": source.get("tamanho") if source.get("tamanho") in PAGE_SIZES else None,
    }

    try:
        pdf_bytes, filename = render_document_pdf(document, overrides)
    except PdfGenerationError as error:
        flash(str(error), "error")
        return redirect(url_for("editor.edit", public_uuid=document.uuid))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
