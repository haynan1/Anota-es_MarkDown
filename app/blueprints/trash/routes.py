from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm, EmptyTrashForm
from app.blueprints.trash import trash_bp
from app.repositories.document_repository import (
    SCOPE_TRASH,
    DocumentQuery,
    DocumentRepository,
)
from app.services.document_service import DocumentService
from app.services.exceptions import ServiceError
from app.utils.params import positive_int


@trash_bp.get("/")
def index():
    query = DocumentQuery(
        scope=SCOPE_TRASH,
        sort="updated_desc",
        page=positive_int(request.args.get("pagina")) or 1,
        per_page=current_app.config["DOCUMENTS_PER_PAGE"],
    )
    return render_template(
        "trash/index.html",
        pagination=DocumentRepository.paginate(query),
        confirm_form=ConfirmForm(),
        empty_form=EmptyTrashForm(),
    )


@trash_bp.post("/<public_uuid>/restaurar")
def restore(public_uuid: str):
    document = DocumentService.require(public_uuid, include_deleted=True)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("trash.index"))

    DocumentService.restore_from_trash(document)
    flash(f"“{document.title}” foi restaurado.", "success")
    return redirect(url_for("trash.index"))


@trash_bp.post("/<public_uuid>/excluir")
def purge(public_uuid: str):
    document = DocumentService.require(public_uuid, include_deleted=True)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("trash.index"))

    title = document.title
    try:
        DocumentService.purge(document)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("trash.index"))

    flash(f"“{title}” foi excluído definitivamente.", "success")
    return redirect(url_for("trash.index"))


@trash_bp.post("/esvaziar")
def empty():
    form = EmptyTrashForm()
    if not form.validate_on_submit():
        flash(
            "Digite EXCLUIR para confirmar o esvaziamento da lixeira.",
            "error",
        )
        return redirect(url_for("trash.index"))

    total = DocumentService.empty_trash()
    flash(
        f"{total} documento(s) excluído(s) definitivamente."
        if total
        else "A lixeira já estava vazia.",
        "success" if total else "info",
    )
    return redirect(url_for("trash.index"))
