from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm, DocumentMetadataForm
from app.blueprints.editor import editor_bp
from app.repositories.taxonomy_repository import CategoryRepository
from app.repositories.version_repository import VersionRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ConflictError, ServiceError
from app.services.settings_service import SettingsService


@editor_bp.get("/novo")
def create_and_edit():
    """Create an empty document and jump straight into it."""
    document = DocumentService.create(title="", content_markdown="")
    return redirect(url_for("editor.edit", public_uuid=document.uuid))


@editor_bp.route("/<public_uuid>", methods=["GET", "POST"])
def edit(public_uuid: str):
    document = DocumentService.require(public_uuid)
    form = DocumentMetadataForm(obj=document if request.method == "GET" else None)
    form.category_id.choices = [("", "Sem categoria")] + [
        (str(category.id), category.name) for category in CategoryRepository.all()
    ]

    if request.method == "GET":
        DocumentService.touch_opened(document)
        form.title.data = document.title
        form.content_markdown.data = document.content_markdown
        form.category_id.data = str(document.category_id or "")
        form.tags.data = ", ".join(document.tag_names)
        form.is_favorite.data = document.is_favorite
        form.page_size.data = document.page_size
        form.pdf_theme.data = document.pdf_theme
        form.expected_revision.data = str(document.revision)
    elif form.validate_on_submit():
        # Non-JS fallback: the browser posts the whole form.
        try:
            expected = form.expected_revision.data
            DocumentService.apply_metadata(
                document,
                category_id=form.selected_category_id() or 0,
                tag_names=form.tag_names(),
                is_favorite=form.is_favorite.data,
                page_size=form.page_size.data,
                pdf_theme=form.pdf_theme.data,
            )
            DocumentService.save(
                document,
                title=form.title.data or "",
                content_markdown=form.content_markdown.data or "",
                expected_revision=int(expected) if (expected or "").isdigit() else None,
                refresh_slug=True,
            )
            flash("Documento salvo.", "success")
            return redirect(url_for("editor.edit", public_uuid=document.uuid))
        except ConflictError as error:
            flash(error.message, "error")
        except ServiceError as error:
            flash(error.message, "error")
    else:
        flash("Não foi possível salvar. Verifique os campos.", "error")

    settings = SettingsService.all()
    return render_template(
        "editor/edit.html",
        document=document,
        form=form,
        confirm_form=ConfirmForm(),
        version_count=VersionRepository.count(document.id),
        autosave_seconds=settings["autosave_seconds"],
        editor_state=DocumentService.state_payload(document),
    )
