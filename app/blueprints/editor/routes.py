from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm, DocumentMetadataForm
from app.blueprints.editor import editor_bp
from app.extensions import db
from app.repositories.group_repository import GroupRepository
from app.repositories.taxonomy_repository import CategoryRepository
from app.repositories.version_repository import VersionRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ConflictError, ServiceError
from app.services.group_service import GroupService
from app.services.media_service import (
    KIND_FILE,
    KIND_IMAGE,
    KIND_VIDEO,
    PICKER_ACCEPT,
    max_bytes_for,
)
from app.services.settings_service import SettingsService
from app.utils.params import whole_int


@editor_bp.post("/novo")
def create_and_edit():
    """Create an empty document and jump straight into it.

    POST, not GET. A GET has to be safe — browsers prefetch links, restore
    tabs, and re-issue them on back-navigation, and CSRF protection exempts
    GET by design. While this was a GET, merely linking to it was enough to
    create documents nobody asked for, and any third-party page could do the
    same with an <img src>.
    """
    document = DocumentService.create(title="", content_markdown="")
    return redirect(url_for("editor.edit", public_uuid=document.uuid))


@editor_bp.route("/<public_uuid>", methods=["GET", "POST"])
def edit(public_uuid: str):
    document = DocumentService.require(public_uuid)
    form = DocumentMetadataForm(obj=document if request.method == "GET" else None)
    form.category_id.choices = [("", "Sem categoria")] + [
        (str(category.id), category.name) for category in CategoryRepository.all()
    ]
    # One query answers both "which groups exist" and "which ones is this
    # document in", so opening the editor does not cost two.
    membership = GroupRepository.all_with_membership(document.id)
    groups = [group for group, _ in membership]
    form.groups.choices = [(group.uuid, group.name) for group in groups]

    if request.method == "GET":
        DocumentService.touch_opened(document)
        form.title.data = document.title
        form.content_markdown.data = document.content_markdown
        form.category_id.data = str(document.category_id or "")
        form.groups.data = [group.uuid for group, is_member in membership if is_member]
        form.tags.data = ", ".join(document.tag_names)
        form.is_favorite.data = document.is_favorite
        form.page_size.data = document.page_size
        form.pdf_theme.data = document.pdf_theme
        form.expected_revision.data = str(document.revision)
    elif form.validate_on_submit():
        # The organisation panel, and the non-JS path for the whole editor.
        try:
            expected = form.expected_revision.data
            # Text first, because it is the only part that can be refused.
            # Writing the taxonomy before knowing whether the save is allowed
            # is how a rejected submission still managed to change the
            # document it claimed it could not touch.
            DocumentService.save(
                document,
                title=form.title.data or "",
                content_markdown=form.content_markdown.data or "",
                expected_revision=whole_int(expected),
                refresh_slug=True,
            )
            DocumentService.apply_metadata(
                document,
                category_id=form.selected_category_id() or 0,
                tag_names=form.tag_names(),
                is_favorite=form.is_favorite.data,
                page_size=form.page_size.data,
                pdf_theme=form.pdf_theme.data,
            )
            db.session.commit()
            GroupService.set_groups_for(document, form.selected_group_uuids())
            flash("Documento salvo.", "success")
            return redirect(url_for("editor.edit", public_uuid=document.uuid))
        except ConflictError as error:
            db.session.rollback()
            flash(error.message, "error")
        except ServiceError as error:
            db.session.rollback()
            flash(error.message, "error")
    else:
        flash("Não foi possível salvar. Verifique os campos.", "error")

    settings = SettingsService.all()
    return render_template(
        "editor/edit.html",
        media_accept=PICKER_ACCEPT,
        upload_limits={
            kind: max_bytes_for(kind)
            for kind in (KIND_IMAGE, KIND_VIDEO, KIND_FILE)
        },
        document=document,
        form=form,
        groups=groups,
        confirm_form=ConfirmForm(),
        version_count=VersionRepository.count(document.id),
        autosave_seconds=settings["autosave_seconds"],
        editor_state=DocumentService.state_payload(document),
    )
