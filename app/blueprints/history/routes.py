from __future__ import annotations

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.history import history_bp
from app.extensions import db
from app.repositories.version_repository import VersionRepository
from app.services.document_service import DocumentService
from app.services.history_service import HistoryService
from app.services.markdown_service import render_markdown
from app.services.search_service import search_index
from app.utils.params import positive_int


@history_bp.get("/<public_uuid>/historico")
def index(public_uuid: str):
    document = DocumentService.require(public_uuid)
    page = positive_int(request.args.get("pagina")) or 1

    return render_template(
        "history/index.html",
        document=document,
        pagination=VersionRepository.paginate(
            document.id, page=page, per_page=current_app.config["VERSIONS_PER_PAGE"]
        ),
        confirm_form=ConfirmForm(),
    )


@history_bp.get("/<public_uuid>/historico/<int:version_number>")
def view_version(public_uuid: str, version_number: int):
    document = DocumentService.require(public_uuid)
    version = VersionRepository.get(document.id, version_number)
    if version is None:
        abort(404)

    return render_template(
        "history/view.html",
        document=document,
        version=version,
        rendered_html=render_markdown(version.content_markdown),
        confirm_form=ConfirmForm(),
    )


@history_bp.get("/<public_uuid>/historico/<int:version_number>/comparar")
def compare(public_uuid: str, version_number: int):
    document = DocumentService.require(public_uuid)
    version = VersionRepository.get(document.id, version_number)
    if version is None:
        abort(404)

    diff = HistoryService.build_diff(version.content_markdown, document.content_markdown)
    return render_template(
        "history/compare.html",
        document=document,
        version=version,
        diff=diff,
        confirm_form=ConfirmForm(),
    )


@history_bp.post("/<public_uuid>/historico/<int:version_number>/restaurar")
def restore(public_uuid: str, version_number: int):
    document = DocumentService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("history.index", public_uuid=public_uuid))

    try:
        version = HistoryService.restore(document, version_number)
    except LookupError:
        abort(404)

    # Recompute every derived field, then snapshot the restored state.
    DocumentService.apply_content(document, version.title, version.content_markdown)
    document.revision += 1
    HistoryService.snapshot(
        document, change_summary=f"Restaurado da versão {version_number}", force=True
    )
    search_index.index_document(document)
    db.session.commit()

    flash(f"Versão {version_number} restaurada.", "success")
    return redirect(url_for("editor.edit", public_uuid=document.uuid))
