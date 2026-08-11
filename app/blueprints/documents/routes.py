from __future__ import annotations

from pathlib import PurePath

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
from app.repositories.group_repository import GroupRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.services.bulk_import_service import import_files
from app.services.document_service import MAX_BULK_SELECTION, DocumentService
from app.services.exceptions import ServiceError
from app.services.group_service import GroupService
from app.services.import_service import ImportPreview, build_preview
from app.services.listing_service import list_documents
from app.services.media_service import enforce_content_length

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
        group_uuid=(args.get("grupo") or "").strip()[:36],
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
    result = list_documents(query)

    view = request.args.get("visual") or session.get("documents_view") or "cards"
    if view not in VALID_VIEWS:
        view = "cards"
    session["documents_view"] = view

    return render_template(
        "documents/index.html",
        pagination=result.pagination,
        query=query,
        view=view,
        snippets=result.snippets,
        categories=CategoryRepository.all(),
        groups=GroupRepository.all(),
        active_group=(
            GroupRepository.get_by_uuid(query.group_uuid) if query.group_uuid else None
        ),
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


@documents_bp.route("/novo-por-titulo", methods=["GET", "POST"])
def create_from_title():
    """Target of a wiki link whose document does not exist yet.

    A broken ``[[link]]`` is not a dead end: it offers to create the document
    with that title and drops you into the editor.

    The GET only *asks*; the POST creates. Creating on GET meant a browser
    prefetching the link, a restored tab or a back-navigation silently wrote a
    document, and CSRF protection does not cover GET. It also reads better:
    following a broken link now says what it is about to do.
    """
    title = (request.args.get("titulo") or request.form.get("titulo") or "").strip()[:200]
    form = ConfirmForm()

    if form.validate_on_submit():
        document = DocumentService.create(
            title=title,
            content_markdown="",
            change_summary="Criado a partir de um link",
        )
        return redirect(url_for("editor.edit", public_uuid=document.uuid))

    return render_template("documents/create_from_title.html", title=title, form=form)


@documents_bp.post("/<public_uuid>/cadeado")
def toggle_lock(public_uuid: str):
    document = DocumentService.require(public_uuid, include_deleted=True)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    locked = DocumentService.toggle_lock(document)
    flash(
        "Documento protegido contra exclusão." if locked
        else "Proteção removida. O documento pode ser excluído.",
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

    try:
        DocumentService.move_to_trash(document)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(_back_to(url_for("documents.index")))

    flash("Documento movido para a lixeira.", "success")
    return redirect(url_for("documents.index"))


# Adding to a group is the one bulk action that needs a second value (which
# group), so it is handled here rather than inside DocumentService.bulk_apply -
# that method is deliberately about state flags on documents alone.
ACTION_ADD_TO_GROUP = "group"

# Success line per action, as (singular, plural). The count is prefixed only
# to the plural, so a one-document action reads "Documento arquivado." rather
# than "1 documento arquivado.".
_BULK_MESSAGES = {
    "trash": ("Documento movido para a lixeira.", "documentos movidos para a lixeira."),
    "archive": ("Documento arquivado.", "documentos arquivados."),
    "unarchive": ("Documento desarquivado.", "documentos desarquivados."),
    "lock": ("Documento protegido contra exclusão.", "documentos protegidos contra exclusão."),
    "unlock": ("Proteção removida.", "proteções removidas."),
}


@documents_bp.post("/acoes-em-massa")
def bulk_action():
    """Apply one action to several selected documents at once.

    The listing checkboxes are plain HTML with no <form> of their own (nesting
    them inside the per-document action forms would be invalid markup), so the
    client gathers the selected UUIDs into this form on submit. Everything is
    still validated here: the action name, the selection size and, for trash,
    the per-document lock.
    """
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Tente novamente.", "error")
        return redirect(_back_to(url_for("documents.index")))

    action = request.form.get("acao", "")
    if action not in DocumentService.BULK_ACTIONS and action != ACTION_ADD_TO_GROUP:
        flash("Ação inválida.", "error")
        return redirect(_back_to(url_for("documents.index")))

    uuids = [uuid for uuid in request.form.getlist("uuids") if uuid][:MAX_BULK_SELECTION]
    if not uuids:
        flash("Selecione ao menos um documento.", "error")
        return redirect(_back_to(url_for("documents.index")))

    if action == ACTION_ADD_TO_GROUP:
        return _bulk_add_to_group(uuids)

    # Lock and unlock reach documents in any state; the other actions operate
    # only on live ones, matching what the listing can show.
    include_deleted = action in {"lock", "unlock"}
    documents = DocumentRepository.get_many_by_uuids(uuids, include_deleted=include_deleted)
    if not documents:
        flash("Nenhum documento encontrado para a seleção.", "error")
        return redirect(_back_to(url_for("documents.index")))

    affected, skipped = DocumentService.bulk_apply(documents, action)

    if affected:
        singular, plural = _BULK_MESSAGES[action]
        flash(singular if affected == 1 else f"{affected} {plural}", "success")
    if skipped:
        plural = skipped != 1
        flash(
            f"{skipped} documento{'s' if plural else ''} protegido{'s' if plural else ''} "
            f"por cadeado {'foram ignorados' if plural else 'foi ignorado'}. "
            "Remova a proteção antes de excluir.",
            "warning" if affected else "error",
        )
    elif not affected:
        flash("Nenhum documento foi alterado.", "error")

    # Trashing removes rows from the current listing; return to a clean page 1
    # rather than a filtered URL that may now point past the last page.
    if action == "trash":
        return redirect(url_for("documents.index"))
    return redirect(_back_to(url_for("documents.index")))


def _bulk_add_to_group(uuids: list[str]):
    """Put a whole selection into one group."""
    group_uuid = (request.form.get("grupo") or "").strip()
    if not group_uuid:
        flash("Escolha um grupo para adicionar os documentos.", "error")
        return redirect(_back_to(url_for("documents.index")))

    try:
        group = GroupService.require(group_uuid)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(_back_to(url_for("documents.index")))

    documents = DocumentRepository.get_many_by_uuids(uuids)
    try:
        added = GroupService.add_documents(group, documents)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(_back_to(url_for("documents.index")))

    if added == 1:
        flash(f"Documento adicionado ao grupo “{group.name}”.", "success")
    elif added:
        flash(f"{added} documentos adicionados ao grupo “{group.name}”.", "success")
    else:
        flash(f"Os documentos selecionados já estavam em “{group.name}”.", "warning")

    return redirect(_back_to(url_for("documents.index")))


# ── Import ──────────────────────────────────────────────────────────────────


@documents_bp.route("/importar", methods=["GET", "POST"])
def import_markdown():
    """Import one file, a whole selection, or a ZIP holding a library.

    The single-file path keeps its old shape - import one document and land in
    the editor with it open. Anything larger reports back on this page instead:
    with 200 files in flight, the useful answer is what happened to all of
    them, not which one happens to be first.
    """
    if request.method == "POST":
        # The global ceiling is sized for video uploads; a markdown import
        # must not be allowed to ride on it.
        enforce_content_length(current_app.config["MAX_BULK_IMPORT_BYTES"])

    form = ImportForm()
    form.category_id.choices = _category_choices()
    preview = None
    report = None

    if form.validate_on_submit():
        uploads = form.uploads()
        try:
            if request.form.get("action", "import") == "preview":
                preview = _build_import_preview(uploads)
            else:
                report = import_files(uploads, category_id=form.selected_category_id())
                if report.created == 1 and report.touched == 1 and report.first:
                    flash(f"“{report.first.title}” foi importado.", "success")
                    return redirect(url_for("editor.edit", public_uuid=report.first.uuid))
                flash(_import_summary(report), "success" if report.created else "error")
        except ServiceError as error:
            flash(error.message, "error")
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os arquivos enviados.", "error")

    return render_template(
        "documents/import.html", form=form, preview=preview, report=report
    )


def _build_import_preview(uploads: list) -> ImportPreview | None:
    """Preview the first Markdown file of a selection.

    A preview answers "did this file parse the way I expected"; repeating that
    for 200 files would answer nothing. The archive has no preview at all -
    reading a whole ZIP to describe it costs as much as importing it.
    """
    first = uploads[0]
    if PurePath(first.filename or "").suffix.lower() == ".zip":
        flash(
            "A pré-visualização não se aplica a arquivos ZIP. "
            "Importe o arquivo para ver o resultado.",
            "warning",
        )
        return None

    if len(uploads) > 1:
        flash(
            f"Prévia do primeiro arquivo. Os {len(uploads)} arquivos "
            "selecionados serão importados juntos.",
            "warning",
        )
    return build_preview(first)


def _import_summary(report) -> str:
    parts = [f"{report.created} documento(s) importado(s)"]
    if report.skipped:
        parts.append(f"{report.skipped} já existente(s) ignorado(s)")
    if report.ignored:
        parts.append(f"{report.ignored} fora do formato")
    if report.failed:
        parts.append(f"{report.failed} com erro")
    return ". ".join(parts) + "."


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
