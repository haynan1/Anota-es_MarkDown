"""Regras de negócio dos mapas mentais.

The write protocol
------------------
A canvas cannot save the way a form saves. Dragging a node produces dozens of
intermediate states a second, and a "save the whole map" request would either
lose the last gesture or overwrite work done in another tab. So the canvas
sends *operations* - create this node, move that one, delete this branch - in
batches, each batch carrying the revision it was composed against:

* **The revision is a gate, not a merge.** If the map moved on since the client
  last read it, the batch is refused with :class:`ConflictError` and the server
  graph attached, and the canvas reloads rather than guessing. Silent
  last-write-wins is how a canvas loses an afternoon.
* **A batch is one transaction.** Either every operation lands or none does.
  Half a batch is a map with a topic hanging off one that was never created.
* **Identity comes from the client.** A node must be drawn before the server
  has heard of it, so the browser mints its UUID and the server validates it.
  That removes the temp-id round trip and makes a retried batch harmless: the
  second create of the same UUID is refused, not duplicated.

Invariants that are checked rather than hoped for
-------------------------------------------------
* one batch wins - the revision is claimed with a conditional UPDATE, so two
  tabs that both read the same number cannot both be told they were first;
* the hierarchy stays a forest - re-parenting a node into its own subtree is
  refused, so the layout can never be handed a cycle to walk;
* depth is bounded, so no recursive walk anywhere in the app can be driven off
  a cliff by a crafted request;
* a map has a node ceiling, so one request cannot ask the browser to render an
  unbounded board;
* every string is sanitised and every number clamped at this boundary, so the
  templates and the SVG exporter both receive values that are already safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import update

from app.extensions import db
from app.models import Document, MediaAsset, MindMap, MindMapNode
from app.models.mind_map import (
    CANVAS_LIMIT,
    DEFAULT_MAP_COLOR,
    LAYOUTS,
    MAX_DESCRIPTION_LENGTH,
    MAX_NODE_NOTE_LENGTH,
    MAX_NODE_TEXT_LENGTH,
    MAX_NODE_WIDTH,
    MAX_NODE_HEIGHT,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    MAX_ZOOM,
    MIN_NODE_HEIGHT,
    MIN_NODE_WIDTH,
    MIN_ZOOM,
    NODE_KINDS,
    NODE_SHAPES,
)
from app.models.mind_map import new_uuid as new_node_uuid
from app.repositories.mind_map_repository import MAX_MAPS, MindMapRepository
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.media_service import release_assets
from app.services.mind_map_export import (
    OutlineItem,
    parse_outline,
    to_markdown,
    to_svg,
)
from app.services.mind_map_layout import LayoutNode, bounding_box, compute_layout
from app.services.sanitizer import sanitize_multiline_text, sanitize_plain_text
from app.utils.dates import utcnow
from app.utils.files import safe_slug

UNTITLED = "Mapa sem título"
DEFAULT_ROOT_TEXT = "Ideia central"

# A canvas is rendered whole, in the DOM, every time it is opened. These are
# the numbers that keep "open a map" an O(1)-feeling action rather than a
# gamble on how much someone pasted into it.
MAX_NODES_PER_MAP = 1_000
# One gesture never produces more than a few operations; a batch is a burst of
# gestures. Five hundred is generous for "select everything and drag".
MAX_OPERATIONS = 500
# Deep enough for any real outline, shallow enough that every recursive walk in
# the app - layout, export, delete - has a hard floor.
MAX_DEPTH = 20

OPERATION_TYPES = frozenset(
    {
        "node.create",
        "node.update",
        "node.move",
        "node.delete",
    }
)

# Only schemes a browser can follow without handing the page a script. The
# ``javascript:`` classic is rejected here rather than at render time, so a URL
# that reaches the database is already one that is safe to put in an href.
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})
# A picture is fetched by the browser, so only the two schemes that can carry
# one over the network. ``data:`` is excluded: it would let an arbitrary
# payload be stored in a column and re-served under our own origin.
ALLOWED_IMAGE_SCHEMES = frozenset({"http", "https"})

_UUID_LENGTH = 36


@dataclass(slots=True)
class ApplyResult:
    revision: int
    applied: int


class MindMapService:
    # ── Lifecycle ───────────────────────────────────────────────────────────

    @staticmethod
    def require(public_uuid: str, include_deleted: bool = False) -> MindMap:
        mind_map = MindMapRepository.get_by_uuid(
            public_uuid, include_deleted=include_deleted
        )
        if mind_map is None:
            raise NotFoundError("Mapa mental não encontrado.")
        return mind_map

    @staticmethod
    def create(
        title: str,
        description: str = "",
        color: str | None = None,
        layout: str | None = None,
        root_text: str | None = None,
    ) -> MindMap:
        """Create a map with its central idea already on the board.

        An empty canvas is a worse starting point than a wrong one: the root
        node is what the first Tab key attaches to, so it exists from the
        beginning and carries the map title until it is renamed.
        """
        clean_title = sanitize_plain_text(title or "", max_length=MAX_TITLE_LENGTH)
        if not clean_title:
            raise ValidationError("Informe um nome para o mapa.")

        if MindMapRepository.counts()["total"] >= MAX_MAPS:
            raise ValidationError(
                f"Limite de {MAX_MAPS} mapas atingido. Remova algum antes de criar outro."
            )

        mind_map = MindMap(
            title=clean_title,
            slug=MindMapService._unique_slug(clean_title),
            description=sanitize_plain_text(
                description or "", max_length=MAX_DESCRIPTION_LENGTH
            ),
            color=_clean_color(color, DEFAULT_MAP_COLOR),
            layout=layout if layout in LAYOUTS else "right",
        )
        db.session.add(mind_map)
        db.session.flush()

        root = MindMapNode(
            uuid=new_node_uuid(),
            map_id=mind_map.id,
            parent_id=None,
            position=0,
            kind="topic",
            text=sanitize_plain_text(
                root_text or clean_title, max_length=MAX_NODE_TEXT_LENGTH
            ),
            shape="pill",
            color=mind_map.color,
            x=0.0,
            y=0.0,
            width=200.0,
            height=56.0,
        )
        db.session.add(root)
        db.session.commit()
        return mind_map

    @staticmethod
    def update(
        mind_map: MindMap,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        layout: str | None = None,
    ) -> MindMap:
        if title is not None:
            clean_title = sanitize_plain_text(title, max_length=MAX_TITLE_LENGTH)
            if not clean_title:
                raise ValidationError("Informe um nome para o mapa.")
            if clean_title != mind_map.title:
                mind_map.title = clean_title
                mind_map.slug = MindMapService._unique_slug(
                    clean_title, exclude_id=mind_map.id
                )
        if description is not None:
            mind_map.description = sanitize_plain_text(
                description, max_length=MAX_DESCRIPTION_LENGTH
            )
        if color is not None:
            mind_map.color = _clean_color(color, DEFAULT_MAP_COLOR)
        if layout is not None:
            if layout not in LAYOUTS:
                raise ValidationError("Disposição inválida.")
            mind_map.layout = layout

        mind_map.updated_at = utcnow()
        db.session.commit()
        return mind_map

    @staticmethod
    def toggle_favorite(mind_map: MindMap) -> bool:
        mind_map.is_favorite = not mind_map.is_favorite
        db.session.commit()
        return mind_map.is_favorite

    @staticmethod
    def soft_delete(mind_map: MindMap) -> None:
        """Send the map to the trash. Nothing is destroyed."""
        mind_map.is_deleted = True
        mind_map.deleted_at = utcnow()
        db.session.commit()

    @staticmethod
    def restore(mind_map: MindMap) -> None:
        mind_map.is_deleted = False
        mind_map.deleted_at = None
        db.session.commit()

    @staticmethod
    def purge(mind_map: MindMap) -> None:
        """Delete the map for good, reclaiming the pictures only it held.

        Nodes go with the map through ``ON DELETE CASCADE``. Files
        are not the database's to cascade, so the ids are noted first, the
        rows are deleted, and only then is each picture offered back to
        ``release_assets`` - which keeps any that a document or a second map
        still points at. Reading the references *after* the delete is what
        makes that answer correct rather than optimistic.
        """
        asset_ids = {asset.id for asset in MindMapRepository.assets_of(mind_map.id)}

        db.session.delete(mind_map)
        db.session.flush()

        release_assets(asset_ids)
        db.session.commit()

    @staticmethod
    def duplicate(mind_map: MindMap) -> MindMap:
        """Copy a map whole - nodes, links and pictures alike.

        The copy points at the same uploaded assets rather than re-uploading
        them: an image is content-addressed by its row, and two maps sharing a
        picture is exactly what the reference is for. Purging one map therefore
        checks nothing else claims the file first (see :meth:`purge`).
        """
        clone = MindMap(
            title=sanitize_plain_text(
                f"{mind_map.title} (cópia)", max_length=MAX_TITLE_LENGTH
            ),
            description=mind_map.description,
            color=mind_map.color,
            layout=mind_map.layout,
            viewport_x=mind_map.viewport_x,
            viewport_y=mind_map.viewport_y,
            viewport_zoom=mind_map.viewport_zoom,
        )
        clone.slug = MindMapService._unique_slug(clone.title)
        db.session.add(clone)
        db.session.flush()

        nodes = MindMapRepository.nodes_of(mind_map)
        mapping: dict[int, MindMapNode] = {}
        for node in nodes:
            copy = MindMapNode(
                uuid=new_node_uuid(),
                map_id=clone.id,
                position=node.position,
                kind=node.kind,
                text=node.text,
                note=node.note,
                url=node.url,
                image_url=node.image_url,
                media_asset_id=node.media_asset_id,
                document_id=node.document_id,
                x=node.x,
                y=node.y,
                width=node.width,
                height=node.height,
                color=node.color,
                shape=node.shape,
                layout=node.layout,
                is_collapsed=node.is_collapsed,
            )
            db.session.add(copy)
            mapping[node.id] = copy

        db.session.flush()

        # Second pass: the parent of a copy is the copy of the parent, which
        # only exists once every node has been created. O mesmo vale para um
        # tópico compartilhado: a cópia aponta para a cópia, e não para o
        # tópico do mapa original - dois mapas que se editam mutuamente não é
        # o que "duplicar" quer dizer.
        for node in nodes:
            if node.parent_id in mapping:
                mapping[node.id].parent = mapping[node.parent_id]
            if node.mirror_of_id in mapping:
                mapping[node.id].mirror_of_id = mapping[node.mirror_of_id].id

        db.session.commit()
        return clone

    @staticmethod
    def _unique_slug(title: str, exclude_id: int | None = None) -> str:
        base = safe_slug(title, fallback="mapa", max_length=140)
        candidate = base
        suffix = 2
        while True:
            existing = MindMapRepository.get_by_slug(candidate)
            if existing is None or existing.id == exclude_id:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 1000:  # pragma: no cover - defensive
                raise ValidationError("Não foi possível gerar um endereço único.")

    # ── Reading ─────────────────────────────────────────────────────────────

    @staticmethod
    def graph_payload(mind_map: MindMap) -> dict:
        """The whole board as JSON: what the canvas boots from.

        Serialised here rather than in the blueprint because the same shape is
        also what a conflict response carries back - the client must be able to
        adopt it without a second request.
        """
        nodes = MindMapRepository.nodes_of(mind_map)
        by_id = {node.id: node.uuid for node in nodes}

        return {
            "uuid": mind_map.uuid,
            "title": mind_map.title,
            "description": mind_map.description,
            "color": mind_map.color,
            "layout": mind_map.layout,
            "revision": mind_map.revision,
            "viewport": {
                "x": mind_map.viewport_x,
                "y": mind_map.viewport_y,
                "zoom": mind_map.viewport_zoom,
            },
            "nodes": [_node_payload(node, by_id) for node in nodes],
            "limits": {
                "nodes": MAX_NODES_PER_MAP,
                "text": MAX_NODE_TEXT_LENGTH,
                "note": MAX_NODE_NOTE_LENGTH,
                "url": MAX_URL_LENGTH,
            },
        }

    # ── Writing ─────────────────────────────────────────────────────────────

    @staticmethod
    def save_viewport(mind_map: MindMap, x: float, y: float, zoom: float) -> None:
        """Remember where the camera was left.

        Deliberately outside the operation protocol and deliberately *not* a
        revision bump: panning is not an edit, and making it one would have
        every scroll invalidate the other tab's batch.
        """
        mind_map.viewport_x = _clamp(x, -CANVAS_LIMIT, CANVAS_LIMIT)
        mind_map.viewport_y = _clamp(y, -CANVAS_LIMIT, CANVAS_LIMIT)
        mind_map.viewport_zoom = _clamp(zoom, MIN_ZOOM, MAX_ZOOM)
        db.session.commit()

    @staticmethod
    def apply_operations(
        mind_map: MindMap,
        operations: Sequence[dict],
        expected_revision: int | None = None,
    ) -> ApplyResult:
        """Apply a batch of canvas operations atomically. See module docstring."""
        if not isinstance(operations, list):
            raise ValidationError("Operações inválidas.")
        if len(operations) > MAX_OPERATIONS:
            raise ValidationError(
                f"Uma mesma requisição comporta até {MAX_OPERATIONS} alterações."
            )

        if not operations:
            # Nothing to write, so nothing to claim - but a client working from
            # a revision the map has moved past still has to be told.
            if expected_revision is not None and expected_revision != mind_map.revision:
                raise MindMapService._conflict(mind_map)
            return ApplyResult(revision=mind_map.revision, applied=0)

        # The claim comes first, and it is the whole concurrency check: reading
        # the revision and then bumping it are two steps, and between them a
        # second tab can do both. See _claim_revision.
        revision = MindMapService._claim_revision(mind_map, expected_revision)
        if revision is None:
            raise MindMapService._conflict(mind_map)

        batch = _Batch(mind_map)
        try:
            for operation in operations:
                batch.dispatch(operation)
            db.session.commit()
        except Exception:  # noqa: BLE001 - a batch is all or nothing
            # Rolled back here rather than left to the request teardown: a
            # batch that fails half way through must not leave the session
            # holding the half it managed, where the *next* commit - from any
            # other code path - would quietly flush it. The claim goes back
            # with it, which is what makes the counter mean something.
            db.session.rollback()
            raise

        return ApplyResult(revision=revision, applied=len(operations))

    @staticmethod
    def _claim_revision(mind_map: MindMap, expected: int | None) -> int | None:
        """Take the next revision, or refuse because someone else took it.

        One statement does the checking and the incrementing together::

            UPDATE mind_maps SET revision = revision + 1
             WHERE id = ? AND revision = ?

        which is the point. Comparing ``expected`` against the loaded object
        and *then* assigning ``revision + 1`` is a read followed by a write,
        and two tabs that both read 4 both wrote 5: each believed it had won,
        and the second silently overwrote the first with the fields its client
        had been holding. The database decides now, and the loser gets no row
        back.

        Returns the revision now in force, or ``None`` when the guard did not
        match. ``expected`` of ``None`` is a client that never sent one - it
        still gets a fresh number, just no protection.
        """
        statement = (
            update(MindMap)
            .where(MindMap.id == mind_map.id)
            .values(revision=MindMap.revision + 1, updated_at=utcnow())
            .returning(MindMap.revision)
            .execution_options(synchronize_session=False)
        )
        if expected is not None:
            statement = statement.where(MindMap.revision == expected)

        claimed = db.session.execute(statement).scalar_one_or_none()
        if claimed is None:
            return None

        # The statement went round the identity map, so the loaded object still
        # holds the old numbers. Expiring them is what stops a later flush from
        # writing the stale revision back over the one just claimed.
        db.session.expire(mind_map, ["revision", "updated_at"])
        return claimed

    @staticmethod
    def _conflict(mind_map: MindMap) -> ConflictError:
        """The refusal, carrying the board as it actually stands.

        Rolled back first: whatever this request had already read is from
        before the winner committed, and handing that back would tell the
        client to adopt a version that never existed.
        """
        db.session.rollback()
        return ConflictError(
            "Este mapa foi alterado em outro lugar. Recarregamos a versão mais recente.",
            server_state=MindMapService.graph_payload(mind_map),
        )

    @staticmethod
    def autolayout(mind_map: MindMap, direction: str | None = None) -> dict:
        """Tidy the whole board and return the new graph.

        The arithmetic lives in :mod:`app.services.mind_map_layout`; this is
        only the part that needs a database - reading the sizes the browser
        measured, and writing the coordinates back.
        """
        chosen = direction if direction in LAYOUTS else mind_map.layout
        nodes = MindMapRepository.nodes_of(mind_map)
        if not nodes:
            return MindMapService.graph_payload(mind_map)

        by_id = {node.id: node.uuid for node in nodes}
        positions = compute_layout(
            [
                LayoutNode(
                    key=node.uuid,
                    parent=by_id.get(node.parent_id) if node.parent_id else None,
                    width=node.width,
                    height=node.height,
                    collapsed=node.is_collapsed,
                    layout=node.layout,
                    mirror_of=by_id.get(node.mirror_of_id) if node.mirror_of_id else None,
                )
                for node in nodes
            ],
            direction=chosen,
        )

        for node in nodes:
            placement = positions.get(node.uuid)
            if placement is not None:
                node.x, node.y = placement

        mind_map.layout = chosen
        # Claimed the same way a batch is, so two tidies arriving together
        # advance the counter twice instead of both landing on the same number.
        # No guard: "arrumar" has no revision to be stale against - it reads
        # the board it is given and rewrites every coordinate on it.
        MindMapService._claim_revision(mind_map, None)
        db.session.commit()
        return MindMapService.graph_payload(mind_map)

    @staticmethod
    def frame(mind_map: MindMap) -> dict:
        """The rectangle every node fits inside - what "enquadrar" needs."""
        nodes = MindMapRepository.nodes_of(mind_map)
        box = bounding_box(
            [(node.x, node.y, node.width, node.height) for node in nodes]
        )
        return {
            "x": box.min_x,
            "y": box.min_y,
            "width": box.width,
            "height": box.height,
        }


    # ── Crossing over to documents ──────────────────────────────────────────

    @staticmethod
    def from_outline(
        title: str,
        items: Sequence[OutlineItem],
        description: str = "",
        color: str | None = None,
    ) -> MindMap:
        """Build a map from a parsed outline, already tidied.

        The outline decides the hierarchy; the layout decides the geometry.
        A map that arrives as a pile of nodes at the origin is a map nobody
        opens twice, so the tidy pass runs before it is ever shown.
        """
        mind_map = MindMapService.create(
            title=title, description=description, color=color
        )
        root = MindMapRepository.nodes_of(mind_map)[0]

        # One node per outline item, hung off the last node seen at the level
        # above. A jump of two levels is treated as a jump of one: malformed
        # nesting must not lose a heading.
        stack: list[MindMapNode] = [root]
        created = 0
        for item in items[:MAX_NODES_PER_MAP - 1]:
            depth = max(0, min(item.depth, len(stack) - 1, MAX_DEPTH - 2))
            parent = stack[depth]
            node = MindMapNode(
                uuid=new_node_uuid(),
                map_id=mind_map.id,
                parent=parent,
                position=created,
                kind="topic",
                text=sanitize_plain_text(item.text, max_length=MAX_NODE_TEXT_LENGTH),
                url=_clean_url(item.url, ALLOWED_URL_SCHEMES) if item.url else "",
                shape="rounded",
                width=_estimated_width(item.text),
                height=48.0,
            )
            db.session.add(node)
            created += 1

            del stack[depth + 1 :]
            stack.append(node)

        db.session.commit()
        MindMapService.autolayout(mind_map)
        return mind_map

    @staticmethod
    def from_document(document: Document) -> MindMap:
        """Turn a document into a map. Its headings are already the outline."""
        items = parse_outline(document.content_markdown)
        if not items:
            raise ValidationError(
                "Este documento não tem títulos nem listas para virar um mapa."
            )
        return MindMapService.from_outline(
            title=document.title,
            items=items,
            description=f"Gerado a partir de “{document.title}”.",
        )

    @staticmethod
    def to_document(mind_map: MindMap) -> Document:
        """Save the map as a document, so it joins the library it came from."""
        from app.services.document_service import DocumentService

        nodes = MindMapRepository.nodes_of(mind_map)
        return DocumentService.create(
            title=mind_map.title,
            content_markdown=to_markdown(mind_map, nodes),
        )

    @staticmethod
    def export_markdown(mind_map: MindMap) -> str:
        return to_markdown(mind_map, MindMapRepository.nodes_of(mind_map))

    @staticmethod
    def export_svg(mind_map: MindMap) -> str:
        return to_svg(mind_map, MindMapRepository.nodes_of(mind_map))


# ── The batch ───────────────────────────────────────────────────────────────


class _Batch:
    """One transaction's worth of operations over one map.

    The whole graph is read once into dictionaries and every operation is
    resolved against those. Looking a node up by UUID per operation would be a
    query per gesture, which is precisely the shape a canvas produces most of.
    """

    def __init__(self, mind_map: MindMap) -> None:
        self.map = mind_map
        self.nodes: dict[str, MindMapNode] = {
            node.uuid: node for node in MindMapRepository.nodes_of(mind_map)
        }

        by_id = {node.id: node.uuid for node in self.nodes.values()}
        # Parenthood is tracked by UUID rather than read off the ORM, because
        # within a batch a node may be re-parented onto another node created
        # moments earlier and not yet flushed.
        self.parent_of: dict[str, str | None] = {
            uuid: (by_id.get(node.parent_id) if node.parent_id else None)
            for uuid, node in self.nodes.items()
        }
        self.children_of: dict[str | None, list[str]] = {}
        for uuid, node in sorted(self.nodes.items(), key=lambda item: item[1].position):
            self.children_of.setdefault(self.parent_of[uuid], []).append(uuid)

        self.node_budget = MAX_NODES_PER_MAP - len(self.nodes)

    # ── Dispatch ────────────────────────────────────────────────────────────

    def dispatch(self, operation: dict) -> None:
        if not isinstance(operation, dict):
            raise ValidationError("Operação inválida.")

        kind = operation.get("type")
        if kind not in OPERATION_TYPES:
            raise ValidationError("Operação desconhecida.")

        handler = {
            "node.create": self.create_node,
            "node.update": self.update_node,
            "node.move": self.move_node,
            "node.delete": self.delete_node,
        }[kind]
        handler(operation)

    # ── Nodes ───────────────────────────────────────────────────────────────

    def create_node(self, operation: dict) -> None:
        identifier = _require_uuid(operation.get("uuid"))
        if identifier in self.nodes:
            # A retried batch must not double a node. Treat the second create
            # as the update it effectively is.
            self.update_node(operation)
            return

        if self.node_budget <= 0:
            raise ValidationError(
                f"Um mapa comporta até {MAX_NODES_PER_MAP} tópicos. "
                "Divida o assunto em outro mapa."
            )

        parent_uuid = operation.get("parent")
        parent = None
        if parent_uuid:
            parent = self._node(parent_uuid)
            self._refuse_under_mirror(_require_uuid(parent_uuid))
            if self._depth(parent_uuid) + 1 >= MAX_DEPTH:
                raise ValidationError(
                    f"O mapa chegou ao limite de {MAX_DEPTH} níveis de profundidade."
                )

        node = MindMapNode(
            uuid=identifier,
            map_id=self.map.id,
            parent=parent,
            position=self._next_position(parent_uuid if parent else None),
            kind="topic",
            shape="rounded",
        )
        db.session.add(node)

        self.nodes[identifier] = node
        self.parent_of[identifier] = parent_uuid if parent else None
        self.children_of.setdefault(self.parent_of[identifier], []).append(identifier)
        self.node_budget -= 1

        self._assign(node, operation)

    def update_node(self, operation: dict) -> None:
        node = self._node(operation.get("uuid"))
        self._assign(node, operation)

    def move_node(self, operation: dict) -> None:
        """Reposition a node, and optionally hang it off a new parent."""
        identifier = _require_uuid(operation.get("uuid"))
        node = self._node(identifier)

        if "x" in operation:
            node.x = _coordinate(operation.get("x"))
        if "y" in operation:
            node.y = _coordinate(operation.get("y"))

        if "parent" in operation:
            self._reparent(identifier, node, operation.get("parent"))

        if "position" in operation:
            self._reorder(identifier, operation.get("position"))

        node.updated_at = utcnow()

    def delete_node(self, operation: dict) -> None:
        """Delete a node. ``mode`` decides what happens to what hung off it.

        ``subtree`` (the default) removes the branch, which is what deleting a
        thought with sub-thoughts means. ``promote`` keeps the children by
        hanging them on their grandparent, for the case where the node was
        merely a heading in the way.
        """
        identifier = _require_uuid(operation.get("uuid"))
        node = self.nodes.get(identifier)
        if node is None:
            # Deleting something already gone is the state the caller wanted.
            return

        children = list(self.children_of.get(identifier, []))
        if operation.get("mode") == "promote":
            grandparent = self.parent_of.get(identifier)
            for child_uuid in children:
                child = self.nodes.get(child_uuid)
                if child is None:
                    continue
                child.parent = self.nodes.get(grandparent) if grandparent else None
                self.parent_of[child_uuid] = grandparent
                siblings = self.children_of.setdefault(grandparent, [])
                siblings.append(child_uuid)
                # A promoted child joins the end of its new sibling list rather
                # than keeping the slot it held under a parent that is gone.
                child.position = len(siblings) - 1
            self.children_of[identifier] = []
        else:
            for descendant in self._descendants(identifier):
                self._forget(descendant)

        self._forget(identifier)
        db.session.delete(node)

    def _refuse_under_mirror(self, parent_uuid: str) -> None:
        """Um espelho nunca ganha ramo próprio.

        O ramo é do original, e é essa a promessa que faz o espelho ser o
        mesmo tópico em vez de uma cópia: aceitar filhos aqui criaria dois
        lugares onde o mesmo assunto continua de formas diferentes, que é
        exatamente o que duplicar um bloco custa.
        """
        parent = self.nodes.get(parent_uuid)
        if parent is not None and parent.mirror_of_id is not None:
            raise ValidationError(
                "Este é um tópico compartilhado. Adicione o subtópico no "
                "original - ele aparece aqui junto."
            )

    def _assign_mirror(self, node: MindMapNode, target: object) -> None:
        """Aponta este nó para o tópico que ele repete, ou desfaz o espelho.

        Três recusas, e cada uma existe por um motivo que se vê na tela:

        * um espelho de si mesmo é uma linha que não diz nada;
        * um espelho de um espelho seria uma cadeia a resolver a cada desenho,
          então ele aponta direto para o tópico de verdade - dois espelhos do
          mesmo tópico são dois espelhos, não uma fila;
        * um espelho não é uma cópia: ele nunca tem ramo próprio, porque o
          ramo é do original, e aceitar filhos aqui criaria dois lugares onde
          o mesmo assunto continua de formas diferentes.
        """
        if target in (None, ""):
            node.mirror_of_id = None
            return

        original = self._node(target)
        if original is node:
            raise ValidationError("Um tópico não pode ser um espelho de si mesmo.")
        if self.children_of.get(node.uuid):
            raise ValidationError(
                "Este tópico tem subtópicos. Um espelho não tem ramo próprio - "
                "o ramo é do original."
            )
        # Um espelho de um espelho aponta para o mesmo tópico que ele.
        while original.mirror_of_id is not None:
            nxt = next(
                (n for n in self.nodes.values() if n.id == original.mirror_of_id), None
            )
            if nxt is None or nxt is original:
                break
            original = nxt
        if original is node:
            raise ValidationError("Um tópico não pode ser um espelho de si mesmo.")
        node.mirror_of_id = original.id

    # ── Field assignment ────────────────────────────────────────────────────

    def _assign(self, node: MindMapNode, operation: dict) -> None:
        """Write the fields present in ``operation`` onto ``node``.

        Absent means "leave alone", which is what lets the canvas send a
        two-field patch for a colour change instead of the whole node.
        """
        fields = operation.get("fields")
        source = fields if isinstance(fields, dict) else operation

        if "text" in source:
            node.text = sanitize_plain_text(
                _as_text(source.get("text")), max_length=MAX_NODE_TEXT_LENGTH
            )
        if "note" in source:
            node.note = sanitize_multiline_text(
                _as_text(source.get("note")), max_length=MAX_NODE_NOTE_LENGTH
            )
        if "url" in source:
            node.url = _clean_url(source.get("url"), ALLOWED_URL_SCHEMES)
        if "image_url" in source:
            node.image_url = _clean_url(source.get("image_url"), ALLOWED_IMAGE_SCHEMES)

        if "media_uuid" in source:
            node.media_asset_id = _resolve_asset(source.get("media_uuid"))
        if "document_uuid" in source:
            node.document_id = _resolve_document(source.get("document_uuid"))

        if "kind" in source:
            kind = source.get("kind")
            if kind not in NODE_KINDS:
                raise ValidationError("Tipo de tópico inválido.")
            node.kind = kind
        elif (node.media_asset_id or node.image_url) and node.kind == "topic":
            # Attaching a picture to a plain topic makes it a picture. Asking
            # the writer to also change a "kind" dropdown would be asking them
            # to say the same thing twice.
            node.kind = "image"

        if "shape" in source:
            shape = source.get("shape")
            if shape not in NODE_SHAPES:
                raise ValidationError("Formato inválido.")
            node.shape = shape
        if "layout" in source:
            layout = source.get("layout")
            # Empty means "the same as whatever this branch hangs from", which
            # is a real answer and the default one - not a missing value. An
            # unknown name is refused rather than quietly stored: a node
            # arranged by something nothing can draw would fall back to the
            # map's arrangement on screen and still claim otherwise in the
            # panel.
            if layout in (None, ""):
                node.layout = None
            elif layout in LAYOUTS:
                node.layout = layout
            else:
                raise ValidationError("Disposição de ramo inválida.")
        if "mirror_of" in source:
            self._assign_mirror(node, source.get("mirror_of"))
        if "color" in source:
            node.color = _clean_color(source.get("color"), "")
        if "collapsed" in source:
            node.is_collapsed = bool(source.get("collapsed"))

        if "x" in source:
            node.x = _coordinate(source.get("x"))
        if "y" in source:
            node.y = _coordinate(source.get("y"))
        if "width" in source:
            node.width = _clamp(
                _as_number(source.get("width"), node.width), MIN_NODE_WIDTH, MAX_NODE_WIDTH
            )
        if "height" in source:
            node.height = _clamp(
                _as_number(source.get("height"), node.height),
                MIN_NODE_HEIGHT,
                MAX_NODE_HEIGHT,
            )

        node.updated_at = utcnow()

    def _node(self, identifier: object) -> MindMapNode:
        node = self.nodes.get(_require_uuid(identifier))
        if node is None:
            raise NotFoundError("Tópico não encontrado neste mapa.")
        return node

    def _reparent(self, identifier: str, node: MindMapNode, parent_uuid: object) -> None:
        if parent_uuid in (None, ""):
            new_parent_uuid = None
        else:
            new_parent_uuid = _require_uuid(parent_uuid)
            if new_parent_uuid == identifier:
                raise ValidationError("Um tópico não pode ser filho de si mesmo.")
            self._refuse_under_mirror(new_parent_uuid)
            # Walking up from the *new* parent is the cycle test: if the node
            # being moved is anywhere on that path, the move would close a loop
            # and orphan the whole branch from the root.
            cursor: str | None = new_parent_uuid
            steps = 0
            while cursor is not None:
                if cursor == identifier:
                    raise ValidationError(
                        "Não é possível mover um tópico para dentro dele mesmo."
                    )
                cursor = self.parent_of.get(cursor)
                steps += 1
                if steps > MAX_DEPTH * 2:  # pragma: no cover - defensive
                    raise ValidationError("A hierarquia deste mapa está inconsistente.")

            if self._depth(new_parent_uuid) + 1 + self._height(identifier) >= MAX_DEPTH:
                raise ValidationError(
                    f"O mapa chegou ao limite de {MAX_DEPTH} níveis de profundidade."
                )

        previous = self.parent_of.get(identifier)
        if previous in self.children_of and identifier in self.children_of[previous]:
            self.children_of[previous].remove(identifier)

        # Resolved through _node, not .get: a UUID this map does not hold is a
        # mistake, and silently treating it as "no parent" would move the topic
        # to the top level instead of refusing the operation.
        node.parent = self._node(new_parent_uuid) if new_parent_uuid else None
        self.parent_of[identifier] = new_parent_uuid
        siblings = self.children_of.setdefault(new_parent_uuid, [])
        siblings.append(identifier)
        node.position = self._next_position(new_parent_uuid)

    def _reorder(self, identifier: str, raw_index: object) -> None:
        """Put a node at a given place among its siblings and renumber them.

        Renumbering the whole sibling group rather than shifting neighbours is
        the same choice groups make: there is no interleaving of operations
        that can leave two nodes fighting over one slot.
        """
        parent = self.parent_of.get(identifier)
        siblings = self.children_of.setdefault(parent, [])
        if identifier not in siblings:
            siblings.append(identifier)

        index = int(_as_number(raw_index, 0))
        siblings.remove(identifier)
        siblings.insert(max(0, min(index, len(siblings))), identifier)

        for slot, sibling in enumerate(siblings):
            node = self.nodes.get(sibling)
            if node is not None:
                node.position = slot

    def _next_position(self, parent_uuid: str | None) -> int:
        return len(self.children_of.get(parent_uuid, []))

    def _depth(self, identifier: str) -> int:
        depth = 0
        cursor = self.parent_of.get(identifier)
        while cursor is not None and depth <= MAX_DEPTH * 2:
            depth += 1
            cursor = self.parent_of.get(cursor)
        return depth

    def _height(self, identifier: str) -> int:
        """How many levels hang below a node - checked before a move deepens
        the tree past its ceiling."""
        height = 0
        frontier = list(self.children_of.get(identifier, []))
        while frontier and height <= MAX_DEPTH * 2:
            height += 1
            following: list[str] = []
            for child in frontier:
                following.extend(self.children_of.get(child, []))
            frontier = following
        return height

    def _descendants(self, identifier: str) -> list[str]:
        found: list[str] = []
        frontier = list(self.children_of.get(identifier, []))
        seen: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            found.append(current)
            frontier.extend(self.children_of.get(current, []))
        return found

    def _forget(self, identifier: str) -> None:
        """Drop a node from the batch's bookkeeping.

        The row itself goes through the database cascade; this keeps the
        in-memory picture honest for the operations that follow in the same
        batch.
        """
        parent = self.parent_of.pop(identifier, None)
        if parent in self.children_of and identifier in self.children_of[parent]:
            self.children_of[parent].remove(identifier)
        self.children_of.pop(identifier, None)
        self.nodes.pop(identifier, None)
        self.node_budget += 1


# ── Value cleaning ──────────────────────────────────────────────────────────


def _node_payload(node: MindMapNode, by_id: dict[int, str]) -> dict:
    """One node as the canvas needs it.

    Identities, never addresses. Building a URL here would mean calling
    ``url_for``, which needs a request context - and this same payload is what
    a conflict carries back from deep inside a service, and what the tests
    exercise with no request in sight. The page publishes the two URL templates
    once and the canvas fills in the UUID, which is the presentation layer's
    job anyway.
    """
    document = None
    if node.document_id and node.document is not None and not node.document.is_deleted:
        document = {"uuid": node.document.uuid, "title": node.document.title}

    return {
        "uuid": node.uuid,
        "parent": by_id.get(node.parent_id) if node.parent_id else None,
        "position": node.position,
        "kind": node.kind,
        "text": node.text,
        "note": node.note,
        "url": node.url,
        # Two fields, not one: an uploaded picture is named by ``media_uuid``
        # and a remote one by ``image_url``. Collapsing them would make an
        # address indistinguishable from an upload the next time the client
        # diffed the node against the server.
        "image_url": node.image_url,
        "media_uuid": node.media_asset.uuid if node.media_asset else "",
        "document": document,
        "x": node.x,
        "y": node.y,
        "width": node.width,
        "height": node.height,
        "color": node.color,
        "shape": node.shape,
        "collapsed": node.is_collapsed,
        # Quando presente, este nó *é* aquele: uma segunda aparição do mesmo
        # tópico. O texto, a cor e o resto vêm de lá, na tela e na exportação.
        "mirror_of": by_id.get(node.mirror_of_id) if node.mirror_of_id else None,
        # ``""`` rather than ``null``: the canvas compares this field against
        # the server's copy on every save, and a select whose "same as the
        # map" option carried the value ``null`` would compare unequal to its
        # own empty string forever, resending the node on every batch.
        "layout": node.layout or "",
    }


def _require_uuid(value: object) -> str:
    """A client-minted identifier, validated before it reaches a query.

    Only the shape is checked - length and alphabet. That is enough: the value
    is bound as a parameter everywhere it is used, and a UUID that does not
    exist simply resolves to nothing.
    """
    if not isinstance(value, str):
        raise ValidationError("Identificador inválido.")
    candidate = value.strip()
    if len(candidate) != _UUID_LENGTH:
        raise ValidationError("Identificador inválido.")
    if not all(char.isalnum() or char == "-" for char in candidate):
        raise ValidationError("Identificador inválido.")
    return candidate


def _estimated_width(label: str) -> float:
    """A sensible box for a node nobody has measured yet.

    Only used for nodes born on the server - an import, an outline. The browser
    overwrites it with the real width the first time the map is rendered.
    """
    return _clamp(len((label or "").strip()) * 7.6 + 40.0, 140.0, 320.0)


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_number(value: object, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        number = float(value)
        # NaN and infinity survive JSON parsing in several clients and would
        # poison every bounding box computed from them afterwards.
        return number if number == number and abs(number) != float("inf") else fallback
    return fallback


def _clamp(value: object, low: float, high: float) -> float:
    return max(low, min(high, _as_number(value, low)))


def _coordinate(value: object) -> float:
    return _clamp(value, -CANVAS_LIMIT, CANVAS_LIMIT)


def _clean_color(value: object, fallback: str) -> str:
    """Accept ``#RGB``/``#RRGGBB`` and nothing else.

    A colour reaches the page as a CSS custom property. Anything that is not a
    hex literal is refused rather than escaped, because the safest thing to put
    in a stylesheet is a value that could never have been anything else.
    """
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if not candidate:
        return fallback
    if len(candidate) not in {4, 7} or not candidate.startswith("#"):
        return fallback
    try:
        int(candidate[1:], 16)
    except ValueError:
        return fallback
    return candidate


def _clean_url(value: object, schemes: frozenset[str]) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    if len(candidate) > MAX_URL_LENGTH:
        raise ValidationError("O endereço é longo demais.")

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in schemes:
        raise ValidationError(
            "Use um endereço começando por https:// (ou http://, mailto:)."
        )
    if parts.scheme.lower() != "mailto" and not parts.netloc:
        raise ValidationError("Endereço incompleto.")
    return candidate


def _resolve_asset(value: object) -> int | None:
    """Turn an upload's public UUID into the row it names, or clear the link."""
    if value in (None, ""):
        return None
    identifier = _require_uuid(value)
    asset = db.session.scalars(
        db.select(MediaAsset).where(MediaAsset.uuid == identifier)
    ).one_or_none()
    if asset is None:
        raise NotFoundError("Imagem não encontrada.")
    if asset.kind != "image":
        raise ValidationError("Só imagens podem ser colocadas em um tópico.")
    return asset.id


def _resolve_document(value: object) -> int | None:
    if value in (None, ""):
        return None
    identifier = _require_uuid(value)
    document = db.session.scalars(
        db.select(Document).where(
            Document.uuid == identifier, Document.is_deleted.is_(False)
        )
    ).one_or_none()
    if document is None:
        raise NotFoundError("Documento não encontrado.")
    return document.id
