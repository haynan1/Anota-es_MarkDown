"""Grupos de documentos: reunir o que é do mesmo assunto."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.groups import groups_bp
from app.blueprints.groups.forms import GroupForm
from app.repositories.document_repository import (
    SCOPE_ALL,
    DocumentQuery,
    DocumentRepository,
)
from app.repositories.group_repository import GroupRepository
from app.services.exceptions import ServiceError
from app.services.group_service import MAX_DOCUMENTS_PER_OPERATION, GroupService
from app.services.listing_service import list_documents

# The "add documents" panel lists what is not in the group yet. A library can
# be large, so the panel searches instead of listing everything.
CANDIDATES_SHOWN = 30


@groups_bp.route("/", methods=["GET", "POST"])
def index():
    form = GroupForm()

    if form.validate_on_submit():
        try:
            group = GroupService.create(
                name=form.name.data,
                description=form.description.data or "",
                color=form.color.data,
            )
            flash(f"Grupo “{group.name}” criado.", "success")
            return redirect(url_for("groups.detail", public_uuid=group.uuid))
        except ServiceError as error:
            flash(error.message, "error")
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "groups/index.html",
        form=form,
        usage=GroupRepository.usage(),
        confirm_form=ConfirmForm(),
    )


@groups_bp.route("/<public_uuid>")
def detail(public_uuid: str):
    group = GroupService.require(public_uuid)
    documents = GroupRepository.documents_of(group)

    search = (request.args.get("q") or "").strip()[:200]
    candidates = _candidates(group, search)

    return render_template(
        "groups/detail.html",
        group=group,
        documents=documents,
        candidates=candidates,
        candidate_search=search,
        form=GroupForm(
            name=group.name, description=group.description, color=group.color
        ),
        confirm_form=ConfirmForm(),
    )


@groups_bp.post("/<public_uuid>/editar")
def update(public_uuid: str):
    group = GroupService.require(public_uuid)
    form = GroupForm()

    if form.validate_on_submit():
        try:
            GroupService.update(
                group,
                name=form.name.data,
                description=form.description.data or "",
                color=form.color.data,
            )
            flash("Grupo atualizado.", "success")
        except ServiceError as error:
            flash(error.message, "error")
    else:
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return redirect(url_for("groups.detail", public_uuid=group.uuid))


@groups_bp.post("/<public_uuid>/excluir")
def delete(public_uuid: str):
    group = GroupService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    name = group.name
    GroupService.delete(group)
    flash(f"Grupo “{name}” removido. Os documentos foram mantidos.", "success")
    return redirect(url_for("groups.index"))


@groups_bp.post("/<public_uuid>/adicionar")
def add_documents(public_uuid: str):
    group = GroupService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    uuids = [value for value in request.form.getlist("uuids") if value]
    documents = DocumentRepository.get_many_by_uuids(uuids[:MAX_DOCUMENTS_PER_OPERATION])

    try:
        added = GroupService.add_documents(group, documents)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    if added:
        flash(
            "Documento adicionado ao grupo." if added == 1
            else f"{added} documentos adicionados ao grupo.",
            "success",
        )
    else:
        flash("Nenhum documento novo foi adicionado.", "error")

    return redirect(url_for("groups.detail", public_uuid=group.uuid))


@groups_bp.post("/<public_uuid>/remover/<document_uuid>")
def remove_document(public_uuid: str, document_uuid: str):
    group = GroupService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    documents = DocumentRepository.get_many_by_uuids([document_uuid])
    removed = GroupService.remove_documents(group, documents)

    flash(
        "Documento removido do grupo. Ele continua existindo." if removed
        else "Este documento não estava no grupo.",
        "success" if removed else "error",
    )
    return redirect(url_for("groups.detail", public_uuid=group.uuid))


@groups_bp.post("/<public_uuid>/mover/<document_uuid>")
def move_document(public_uuid: str, document_uuid: str):
    """Reordenar pelo teclado: cada linha tem subir e descer.

    Drag-and-drop is the pointer path to the same operation; this one is the
    path that works with a keyboard, a screen reader and no JavaScript at all.
    """
    group = GroupService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    documents = DocumentRepository.get_many_by_uuids([document_uuid])
    if not documents:
        flash("Documento não encontrado.", "error")
        return redirect(url_for("groups.detail", public_uuid=group.uuid))

    offset = -1 if request.form.get("direcao") == "cima" else 1
    try:
        GroupService.move(group, documents[0], offset)
    except ServiceError as error:
        flash(error.message, "error")

    return redirect(url_for("groups.detail", public_uuid=group.uuid) + f"#doc-{document_uuid}")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _candidates(group, search: str):
    """Documents that could join the group, optionally filtered by a search."""
    query = DocumentQuery(
        search=search,
        scope=SCOPE_ALL,
        sort="relevance" if search else "updated_desc",
        page=1,
        # Fetch a margin: members are filtered out below, and a page made
        # entirely of documents already in the group would look broken.
        per_page=CANDIDATES_SHOWN * 2,
    )
    result = list_documents(query, with_snippets=False)

    # Ids only: loading the group's documents in full to build a set of ids
    # would read every member's row to render a list they are not even in.
    members = GroupRepository.member_ids(group.id)
    return [item for item in result.pagination.items if item.id not in members][
        :CANDIDATES_SHOWN
    ]


def _first_error(form) -> str | None:
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return None
