"""Mapas mentais: a galeria, a tela e o protocolo que a mantém salva.

Two address spaces, one feature. ``/mapas`` is HTML that works with a form and
a page reload; ``/api/mapas`` is the canvas talking, and every one of those
routes is CSRF-checked exactly like a form post - Flask-WTF validates the
``X-CSRFToken`` header the browser sends.

The split is not cosmetic. Everything that can be done with a pointer on the
canvas can also be done from the page: rename, recolour, tidy, duplicate,
export, delete. A gesture is a shortcut for a form, never the only way in.
"""

from __future__ import annotations

import io

from flask import (
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.mindmaps import mindmaps_bp
from app.blueprints.mindmaps.forms import MindMapForm
from app.models.mind_map import (
    LAYOUT_HINTS,
    LAYOUT_LABELS,
    LAYOUTS,
    NODE_SHAPES,
)
from app.repositories.mind_map_repository import MindMapRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ServiceError, ValidationError
from app.services.media_service import (
    PICKER_ACCEPT,
    max_bytes_for,
)
from app.services.mind_map_layout import board_orientation
from app.services.mind_map_service import (
    MAX_NODES_PER_MAP,
    MAX_OPERATIONS,
    MindMapService,
)
from app.utils.files import safe_filename
from app.utils.orm import identities_of

# A palette rather than a colour wheel. Eight hues that stay legible against
# both themes and against each other - a canvas where every branch is a
# slightly different blue communicates nothing.
NODE_PALETTE = (
    ("#4F46E5", "Índigo"),
    ("#0EA5E9", "Azul"),
    ("#14B8A6", "Verde-água"),
    ("#22C55E", "Verde"),
    ("#EAB308", "Âmbar"),
    ("#F97316", "Laranja"),
    ("#EF4444", "Vermelho"),
    ("#EC4899", "Rosa"),
)

SHAPE_LABELS = {
    "rounded": "Arredondado",
    "pill": "Cápsula",
    "rect": "Retângulo",
    "ellipse": "Elipse",
    "diamond": "Losango",
}


# ── Galeria ─────────────────────────────────────────────────────────────────


@mindmaps_bp.route("/mapas/", methods=["GET", "POST"])
def index():
    form = MindMapForm()

    if form.validate_on_submit():
        try:
            mind_map = MindMapService.create(
                title=form.title.data,
                description=form.description.data or "",
                color=form.color.data,
                layout=form.layout.data,
            )
            flash(f"Mapa “{mind_map.title}” criado.", "success")
            return redirect(url_for("mindmaps.canvas", public_uuid=mind_map.uuid))
        except ServiceError as error:
            flash(error.message, "error")
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    search = (request.args.get("q") or "").strip()[:120]
    trashed = request.args.get("escopo") == "lixeira"
    favorites = request.args.get("favoritos") == "1"

    maps = MindMapRepository.listing(
        search=search, favorites_only=favorites, deleted=trashed
    )

    return render_template(
        "mindmaps/index.html",
        form=form,
        maps=maps,
        node_counts=MindMapRepository.node_counts(identities_of(maps)),
        counts=MindMapRepository.counts(),
        search=search,
        trashed=trashed,
        favorites=favorites,
        layout_labels=LAYOUT_LABELS,
        confirm_form=ConfirmForm(),
    )


# ── A tela ──────────────────────────────────────────────────────────────────


@mindmaps_bp.get("/mapas/<public_uuid>")
def canvas(public_uuid: str):
    """The canvas itself.

    The graph is embedded in the page rather than fetched after load: the map
    is the page, and a canvas that appears empty for one round trip before its
    content arrives reads as a bug every single time.
    """
    mind_map = MindMapService.require(public_uuid)

    return render_template(
        "mindmaps/canvas.html",
        mind_map=mind_map,
        graph=MindMapService.graph_payload(mind_map),
        form=MindMapForm(
            title=mind_map.title,
            description=mind_map.description,
            color=mind_map.color,
            layout=mind_map.layout,
        ),
        confirm_form=ConfirmForm(),
        palette=NODE_PALETTE,
        shapes=[(value, SHAPE_LABELS[value]) for value in NODE_SHAPES],
        layouts=[(value, LAYOUT_LABELS[value]) for value in LAYOUTS],
        layout_hints=LAYOUT_HINTS,
        orientation=board_orientation(mind_map.layout),
        upload_accept=PICKER_ACCEPT,
        upload_limit=max_bytes_for("image"),
        max_nodes=MAX_NODES_PER_MAP,
    )


@mindmaps_bp.post("/mapas/<public_uuid>/editar")
def update(public_uuid: str):
    mind_map = MindMapService.require(public_uuid)
    form = MindMapForm()

    if form.validate_on_submit():
        try:
            MindMapService.update(
                mind_map,
                title=form.title.data,
                description=form.description.data or "",
                color=form.color.data,
                layout=form.layout.data,
            )
            flash("Mapa atualizado.", "success")
        except ServiceError as error:
            flash(error.message, "error")
    else:
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return redirect(url_for("mindmaps.canvas", public_uuid=mind_map.uuid))


@mindmaps_bp.post("/mapas/<public_uuid>/favoritar")
def toggle_favorite(public_uuid: str):
    mind_map = MindMapService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.index"))

    favorite = MindMapService.toggle_favorite(mind_map)
    flash(
        "Mapa marcado como favorito." if favorite else "Mapa removido dos favoritos.",
        "success",
    )
    return redirect(_back_to(url_for("mindmaps.index")))


@mindmaps_bp.post("/mapas/<public_uuid>/duplicar")
def duplicate(public_uuid: str):
    mind_map = MindMapService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.index"))

    clone = MindMapService.duplicate(mind_map)
    flash(f"Cópia criada: “{clone.title}”.", "success")
    return redirect(url_for("mindmaps.canvas", public_uuid=clone.uuid))


@mindmaps_bp.post("/mapas/<public_uuid>/excluir")
def delete(public_uuid: str):
    """Send a map to the trash. Reversible, like everything else here."""
    mind_map = MindMapService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.index"))

    title = mind_map.title
    MindMapService.soft_delete(mind_map)
    flash(f"“{title}” foi para a lixeira de mapas.", "success")
    return redirect(url_for("mindmaps.index"))


@mindmaps_bp.post("/mapas/<public_uuid>/restaurar")
def restore(public_uuid: str):
    mind_map = MindMapService.require(public_uuid, include_deleted=True)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.index", escopo="lixeira"))

    MindMapService.restore(mind_map)
    flash(f"“{mind_map.title}” foi restaurado.", "success")
    return redirect(url_for("mindmaps.index"))


@mindmaps_bp.post("/mapas/<public_uuid>/excluir-definitivo")
def purge(public_uuid: str):
    mind_map = MindMapService.require(public_uuid, include_deleted=True)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.index", escopo="lixeira"))

    title = mind_map.title
    MindMapService.purge(mind_map)
    flash(f"“{title}” foi excluído definitivamente.", "success")
    return redirect(url_for("mindmaps.index", escopo="lixeira"))


# ── Atravessando para os documentos ─────────────────────────────────────────


@mindmaps_bp.post("/mapas/de-documento/<document_uuid>")
def from_document(document_uuid: str):
    """Turn a document into a map. Its headings are already the outline."""
    document = DocumentService.require(document_uuid)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("editor.edit", public_uuid=document.uuid))

    try:
        mind_map = MindMapService.from_document(document)
    except ServiceError as error:
        flash(error.message, "error")
        return redirect(url_for("editor.edit", public_uuid=document.uuid))

    flash("Mapa criado a partir do documento.", "success")
    return redirect(url_for("mindmaps.canvas", public_uuid=mind_map.uuid))


@mindmaps_bp.post("/mapas/<public_uuid>/documento")
def to_document(public_uuid: str):
    """Save the map into the library as a Markdown document."""
    mind_map = MindMapService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        return _expired(url_for("mindmaps.canvas", public_uuid=mind_map.uuid))

    document = MindMapService.to_document(mind_map)
    flash(f"Documento “{document.title}” criado a partir do mapa.", "success")
    return redirect(url_for("editor.edit", public_uuid=document.uuid))


@mindmaps_bp.get("/mapas/<public_uuid>/markdown")
def download_markdown(public_uuid: str) -> Response:
    mind_map = MindMapService.require(public_uuid)
    payload = MindMapService.export_markdown(mind_map).encode("utf-8")

    return send_file(
        io.BytesIO(payload),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=safe_filename(mind_map.title, ".md", fallback="mapa"),
    )


@mindmaps_bp.get("/mapas/<public_uuid>/svg")
def download_svg(public_uuid: str) -> Response:
    """The drawing, as vector.

    Always an attachment. An SVG is a document a browser will execute, and
    serving one inline from our own origin would undo the rule the upload
    pipeline is built around - even though every value in it was escaped on
    the way out.
    """
    mind_map = MindMapService.require(public_uuid)
    payload = MindMapService.export_svg(mind_map).encode("utf-8")

    response = send_file(
        io.BytesIO(payload),
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name=safe_filename(mind_map.title, ".svg", fallback="mapa"),
    )
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# ── A API da tela ───────────────────────────────────────────────────────────


@mindmaps_bp.get("/api/mapas/<public_uuid>/grafo")
def graph(public_uuid: str):
    """The whole board. Used to recover after a conflict."""
    mind_map = MindMapService.require(public_uuid)
    return jsonify({"ok": True, "graph": MindMapService.graph_payload(mind_map)})


@mindmaps_bp.post("/api/mapas/<public_uuid>/operacoes")
def operations(public_uuid: str):
    """Apply a batch of canvas operations. See ``mind_map_service``."""
    mind_map = MindMapService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    batch = payload.get("operations")
    if not isinstance(batch, list):
        raise ValidationError("Nenhuma operação recebida.")
    if len(batch) > MAX_OPERATIONS:
        raise ValidationError(
            f"Uma mesma requisição comporta até {MAX_OPERATIONS} alterações."
        )

    revision = payload.get("revision")
    result = MindMapService.apply_operations(
        mind_map,
        batch,
        expected_revision=revision if isinstance(revision, int) else None,
    )
    return jsonify({"ok": True, "revision": result.revision, "applied": result.applied})


@mindmaps_bp.post("/api/mapas/<public_uuid>/enquadramento")
def viewport(public_uuid: str):
    """Remember the camera. Not an edit, so it never bumps the revision."""
    mind_map = MindMapService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    MindMapService.save_viewport(
        mind_map,
        x=payload.get("x", 0),
        y=payload.get("y", 0),
        zoom=payload.get("zoom", 1),
    )
    return jsonify({"ok": True})


@mindmaps_bp.post("/api/mapas/<public_uuid>/organizar")
def autolayout(public_uuid: str):
    """Tidy the whole board and hand back the new coordinates."""
    mind_map = MindMapService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    direction = payload.get("layout")
    graph_payload = MindMapService.autolayout(
        mind_map, direction if isinstance(direction, str) else None
    )
    return jsonify({"ok": True, "graph": graph_payload})


# ── Helpers ─────────────────────────────────────────────────────────────────


def _first_error(form) -> str | None:
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return None


def _back_to(fallback: str) -> str:
    """Return to the page the action was fired from, never to somewhere else.

    Only a path from this application is honoured: a ``next`` carrying a full
    URL would turn every button on this page into an open redirect.
    """
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def _expired(fallback: str):
    flash("Sessão expirada. Tente novamente.", "error")
    return redirect(_back_to(fallback))
