from __future__ import annotations

import io

from flask import Response, flash, redirect, request, send_file, url_for

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.exports import exports_bp
from app.models import PAGE_SIZES, PDF_THEMES
from app.services import selection_service
from app.services.bulk_export_service import (
    MAX_SELECTION,
    MarkdownArchive,
    export_all,
    export_selection,
)
from app.services.document_service import DocumentService
from app.services.pdf_service import (
    VARIANT_RENDERED,
    VARIANTS,
    PdfGenerationError,
    render_document_pdf,
)
from app.utils.files import safe_filename
from app.utils.humanize import format_number_br


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


# ── Bulk Markdown ───────────────────────────────────────────────────────────


def _send_archive(archive: MarkdownArchive, empty_message: str):
    """Hand a built archive to the browser, or explain why there is none.

    An empty ZIP downloads perfectly happily and then explains nothing, so the
    "there was nothing to export" case is answered on the page the user came
    from instead of in their downloads folder.
    """
    if not archive.document_count:
        archive.stream.close()
        flash(empty_message, "warning")
        return redirect(url_for("documents.index"))

    return send_file(
        archive.stream,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive.filename,
    )


@exports_bp.get("/markdown/tudo")
def download_all_markdown():
    """Every document on the platform, as one ZIP of ``.md`` files.

    A GET: it changes nothing, so it can be a plain link, survive a refresh
    and be bookmarked. The trash is excluded - see ``export_all``.
    """
    return _send_archive(
        export_all(), "Não há documentos para exportar."
    )


@exports_bp.post("/markdown/selecao")
def download_selected_markdown():
    """The documents the listing selected, as one ZIP of ``.md`` files.

    Reads the selection through the same resolver the other bulk actions use,
    so "todos os N resultados" packs the same set it would have archived. The
    two modes keep their own ceilings: a request that carries its identifiers
    is bounded by what a request may carry, while a request that carries only
    the filters is bounded by how much one action should touch.
    """
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("documents.index"))

    selection = selection_service.resolve(request.form, limit=MAX_SELECTION)
    if not selection:
        flash("Selecione ao menos um documento.", "error")
        return redirect(url_for("documents.index"))

    if selection.truncated:
        flash(
            f"O pacote traz os {format_number_br(selection.limit)} primeiros "
            "resultados. Refine os filtros para baixar o restante.",
            "warning",
        )

    return _send_archive(
        export_selection(selection.ids, limit=selection.limit),
        "Nenhum documento encontrado para a seleção.",
    )


# ── PDF ─────────────────────────────────────────────────────────────────────


@exports_bp.route("/<public_uuid>/pdf", methods=["GET", "POST"])
def download_pdf(public_uuid: str):
    document = DocumentService.require(public_uuid)

    source = request.form if request.method == "POST" else request.args
    overrides = {
        "theme": source.get("tema") if source.get("tema") in PDF_THEMES else None,
        "page_size": source.get("tamanho") if source.get("tamanho") in PAGE_SIZES else None,
    }
    variant = source.get("formato")
    if variant not in VARIANTS:
        variant = VARIANT_RENDERED

    try:
        pdf_bytes, filename = render_document_pdf(document, overrides, variant=variant)
    except PdfGenerationError as error:
        flash(str(error), "error")
        return redirect(url_for("editor.edit", public_uuid=document.uuid))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
