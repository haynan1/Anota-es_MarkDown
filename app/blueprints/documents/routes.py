from __future__ import annotations

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.blueprints.documents import documents_bp
from app.blueprints.documents.forms import (
    CategoryForm,
    ConfirmForm,
    ImportForm,
    RenameForm,
)
from app.extensions import db
from app.repositories.document_repository import (
    SCOPE_ACTIVE,
    SCOPE_ALL,
    SCOPE_ARCHIVED,
    SORT_OPTIONS,
    DocumentQuery,
    DocumentRepository,
)
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ServiceError
from app.services.import_service import build_preview, import_document
from app.services.search_service import search_index

VALID_SCOPES = {SCOPE_ACTIVE, SCOPE_ARCHIVED, SCOPE_ALL}
VALID_VIEWS = {"cards", "list"}


def _build_query() -> DocumentQuery:
    args = request.args
    scope = args.get("escopo", SCOPE_ACTIVE)
    sort = args.get("ordem", "updated_desc")
    search = (args.get("q") or "").strip()[:200]

    return DocumentQuery(
        search=search,
        category_id=(
            int(args["categoria"]) if (args.get("categoria") or "").isdigit() else None
        ),
        tag_slugs=tuple(tag for tag in args.getlist("etiqueta") if tag)[:5],
        only_favorites=args.get("favoritos") == "1",
        scope=scope if scope in VALID_SCOPES else SCOPE_ACTIVE,
        sort=(
            sort
            if sort in SORT_OPTIONS
            else ("relevance" if search else "updated_desc")
        ),
        page=int(args["pagina"]) if (args.get("pagina") or "").isdigit() else 1,
        per_page=current_app.config["DOCUMENTS_PER_PAGE"],
    )


@documents_bp.route("/")
def index():
    query = _build_query()
    pagination = DocumentRepository.paginate(query)

    view = request.args.get("visual") or session.get("documents_view") or "cards"
    if view not in VALID_VIEWS:
        view = "cards"
    session["documents_view"] = view

    snippets = {}
    if query.is_searching and query.matched_ids:
        snippets = search_index.snippets(
            query.search, [document.id for document in pagination.items]
        )

    return render_template(
        "documents/index.html",
        pagination=pagination,
        query=query,
        view=view,
        snippets=snippets,
        categories=CategoryRepository.all(),
        tag_usage=TagRepository.usage(limit=30),
        sort_options=SORT_OPTIONS,
        rename_form=RenameForm(),
        confirm_form=ConfirmForm(),
    )


@documents_bp.post("/novo")
def create():
    form = ConfirmForm()
    if not form.validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("documents.index"))

    document = DocumentService.create(title="", content_markdown="")
    return redirect(url_for("editor.edit", public_uuid=document.uuid))


@documents_bp.post("/<public_uuid>/renomear")
def rename(public_uuid: str):
    document = DocumentService.require(public_uuid)
    form = RenameForm()
    if form.validate_on_submit():
        DocumentService.rename(document, form.title.data)
        flash("Documento renomeado.", "success")
    else:
        flash(_first_error(form) or "Não foi possível renomear o documento.", "error")
    return redirect(_back_to(url_for("documents.index")))


@documents_bp.post("/<public_uuid>/favorito")
def toggle_favorite(public_uuid: str):
    document = DocumentService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    is_favorite = DocumentService.toggle_favorite(document)
    flash(
        "Adicionado aos favoritos." if is_favorite else "Removido dos favoritos.",
        "success",
    )
    return redirect(_back_to(url_for("documents.index")))


@documents_bp.post("/<public_uuid>/duplicar")
def duplicate(public_uuid: str):
    document = DocumentService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    copy = DocumentService.duplicate(document)
    flash(f"Cópia criada: “{copy.title}”.", "success")
    return redirect(url_for("editor.edit", public_uuid=copy.uuid))


@documents_bp.post("/<public_uuid>/arquivar")
def toggle_archive(public_uuid: str):
    document = DocumentService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    DocumentService.set_archived(document, not document.is_archived)
    flash(
        "Documento arquivado." if document.is_archived else "Documento desarquivado.",
        "success",
    )
    return redirect(_back_to(url_for("documents.index")))


@documents_bp.post("/<public_uuid>/lixeira")
def move_to_trash(public_uuid: str):
    document = DocumentService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    DocumentService.move_to_trash(document)
    flash("Documento movido para a lixeira.", "success")
    return redirect(url_for("documents.index"))


# ── Import ──────────────────────────────────────────────────────────────────


@documents_bp.route("/importar", methods=["GET", "POST"])
def import_markdown():
    form = ImportForm()
    form.category_id.choices = _category_choices()
    preview = None

    if form.validate_on_submit():
        action = request.form.get("action", "import")
        try:
            if action == "preview":
                preview = build_preview(form.file.data)
            else:
                document = import_document(
                    form.file.data, category_id=form.selected_category_id()
                )
                flash(f"“{document.title}” foi importado.", "success")
                return redirect(url_for("editor.edit", public_uuid=document.uuid))
        except ServiceError as error:
            flash(error.message, "error")
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique o arquivo enviado.", "error")

    return render_template("documents/import.html", form=form, preview=preview)


# ── Categories ──────────────────────────────────────────────────────────────


@documents_bp.route("/categorias", methods=["GET", "POST"])
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        try:
            CategoryRepository.get_or_create(form.name.data, form.color.data)
            db.session.commit()
            flash("Categoria salva.", "success")
            return redirect(url_for("documents.categories"))
        except ValueError as error:
            flash(str(error), "error")
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "documents/categories.html",
        form=form,
        usage=DocumentRepository.category_usage(limit=100),
        tag_usage=TagRepository.usage(limit=100),
        confirm_form=ConfirmForm(),
    )


@documents_bp.post("/categorias/<int:category_id>/excluir")
def delete_category(category_id: int):
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("documents.categories"))

    category = CategoryRepository.get(category_id)
    if category is None:
        flash("Categoria não encontrada.", "error")
        return redirect(url_for("documents.categories"))

    # Documents survive: the foreign key is cleared, not cascaded.
    db.session.delete(category)
    db.session.commit()
    flash("Categoria removida. Os documentos foram mantidos.", "success")
    return redirect(url_for("documents.categories"))


@documents_bp.post("/etiquetas/<slug>/excluir")
def delete_tag(slug: str):
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(url_for("documents.categories"))

    tag = TagRepository.get_by_slug(slug)
    if tag is None:
        flash("Etiqueta não encontrada.", "error")
        return redirect(url_for("documents.categories"))

    db.session.delete(tag)
    db.session.commit()
    flash("Etiqueta removida.", "success")
    return redirect(url_for("documents.categories"))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _category_choices() -> list[tuple[str, str]]:
    return [("", "Sem categoria")] + [
        (str(category.id), category.name) for category in CategoryRepository.all()
    ]


def _first_error(form) -> str | None:
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return None


def _back_to(default: str) -> str:
    """Return to the page the action came from, but only within this app."""
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return default
