"""Mapas mentais: pensar em duas dimensões, não em uma lista.

Why a graph and not a JSON blob
--------------------------------
The obvious shortcut for a canvas is a single ``canvas_json`` column. It is
also the shortcut that costs the most later: a blob cannot be indexed, cannot
be joined, cannot enforce that an edge points at a node that exists, and turns
every edit into a read-modify-write of the whole document. Two tabs editing
different corners of the same map would then overwrite each other wholesale.

So a map is stored as what it is: nodes and edges, with real foreign keys.
Deleting a map takes its nodes with it (``CASCADE``); deleting a node takes its
branch with it, because that is what "delete this branch" means in a mind map.

Two kinds of connection, on purpose
-----------------------------------
``parent_id`` is the *spine*: the hierarchy a mind map is built from, the thing
auto-layout walks and the outline export reads. :class:`MindMapEdge` is the
free association - "this idea also relates to that one" - which is a graph, not
a tree, and must never be allowed to confuse the layout. Keeping them apart is
what lets one canvas be both a tidy outline and a free-form board.

A node may also point *outwards*: at an uploaded image, at a remote image, at a
URL, or at a document in this library. All four are optional and none of them
owns the node - an image whose asset is deleted degrades to a node without a
picture rather than disappearing.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin
from app.utils.dates import utcnow

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document
    from app.models.media_asset import MediaAsset

# Column widths other layers must respect. Declared next to the column so a
# value is truncated once, at the boundary that owns the rule.
MAX_TITLE_LENGTH = 140
MAX_DESCRIPTION_LENGTH = 280
MAX_NODE_TEXT_LENGTH = 500
MAX_NODE_NOTE_LENGTH = 4000
MAX_URL_LENGTH = 500
MAX_EDGE_LABEL_LENGTH = 120

DEFAULT_MAP_COLOR = "#4F46E5"

# A node is a thought, not a document. `topic` is the ordinary one; `note` is
# the sticky square an aside gets parked on; `image` is a picture carrying its
# own caption. Anything else - a link, a document reference - is an attribute
# of a node rather than a separate kind, because a thought does not stop being
# a thought when a URL is attached to it.
NODE_KINDS = ("topic", "note", "image")
NODE_SHAPES = ("rounded", "pill", "rect", "ellipse", "diamond")

# How a connection is drawn. `curve` is the default because a map read as a
# whole is easier to follow with curves; `line` and `dashed` exist for diagrams
# where precision, or tentativeness, is the point.
EDGE_STYLES = ("curve", "line", "dashed")

# How a map arranges itself, and how each arrangement is named to the person
# choosing it. `down` and `tree` both run down the page and differ in the line
# they draw between a parent and a child: `down` keeps the mind map's curve,
# `tree` squares its shoulders into the org chart people mean by "árvore".
LAYOUTS = ("right", "down", "tree", "radial")
LAYOUT_LABELS = {
    "right": "Horizontal",
    "down": "Vertical",
    "tree": "Árvore",
    "radial": "Radial",
}
# One line each, for the chooser on the board - a name alone does not tell
# anyone what "Vertical" and "Árvore" do differently.
LAYOUT_HINTS = {
    "right": "Os ramos crescem para o lado, a partir do centro.",
    "down": "O mapa desce a página, em curvas.",
    "tree": "Organograma: níveis em linha, ligados por cotovelos.",
    "radial": "Os ramos se abrem em volta da raiz.",
}

# Geometry is clamped on the way in. The canvas is huge but finite: a node at
# 10^9 would make "enquadrar" useless and every bounding box meaningless.
CANVAS_LIMIT = 100_000.0
MIN_NODE_WIDTH = 60.0
MAX_NODE_WIDTH = 640.0
MIN_NODE_HEIGHT = 32.0
MAX_NODE_HEIGHT = 640.0
MIN_ZOOM = 0.1
MAX_ZOOM = 4.0


def new_uuid() -> str:
    return str(uuid_module.uuid4())


class MindMap(TimestampMixin, db.Model):
    __tablename__ = "mind_maps"
    __table_args__ = (Index("ix_mind_maps_state_updated", "is_deleted", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(
        String(MAX_TITLE_LENGTH), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(
        String(MAX_DESCRIPTION_LENGTH), nullable=False, default=""
    )
    color: Mapped[str] = mapped_column(
        String(9), nullable=False, default=DEFAULT_MAP_COLOR
    )
    layout: Mapped[str] = mapped_column(String(10), nullable=False, default="right")

    # Optimistic concurrency, exactly as documents do it. The canvas sends the
    # revision it last saw with every batch of operations; a mismatch is
    # rejected with the server graph attached instead of silently overwriting a
    # map edited in another tab.
    #
    # The comparison belongs to the database, not to Python: it is made and the
    # counter incremented in one UPDATE, in MindMapService._claim_revision.
    # Checking a loaded value and then assigning it back is two steps, and two
    # tabs fit between them.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Where the camera was left. Restoring it is the difference between
    # reopening a map and having to find your way back into it.
    viewport_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    viewport_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    viewport_zoom: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    nodes: Mapped[list["MindMapNode"]] = relationship(
        back_populates="mind_map",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )
    edges: Mapped[list["MindMapEdge"]] = relationship(
        back_populates="mind_map",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    @property
    def display_title(self) -> str:
        return self.title.strip() or "Mapa sem título"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MindMap {self.title!r}>"


class MindMapNode(db.Model):
    __tablename__ = "mind_map_nodes"
    __table_args__ = (
        # Every read of a map walks it by parent; every layout pass reads the
        # siblings in order.
        Index("ix_mind_map_nodes_tree", "map_id", "parent_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Assigned by the client. A canvas has to draw a node the instant it is
    # created and can only refer to it by name afterwards; letting the browser
    # mint the identifier removes the temp-id round trip entirely, and the
    # server still validates the shape and refuses a duplicate.
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    map_id: Mapped[int] = mapped_column(
        ForeignKey("mind_maps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The spine. CASCADE, because deleting a branch means deleting the branch;
    # promoting the children instead is an explicit choice the service makes
    # before the row goes.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("mind_map_nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="topic")
    text: Mapped[str] = mapped_column(
        String(MAX_NODE_TEXT_LENGTH), nullable=False, default=""
    )
    # The long form behind a short label: what the branch actually means. Text,
    # not String, because a paragraph has no useful column width.
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    url: Mapped[str] = mapped_column(String(MAX_URL_LENGTH), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(
        String(MAX_URL_LENGTH), nullable=False, default=""
    )

    # An uploaded picture. SET NULL: losing the file must not delete the idea.
    media_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # A node can *be* a document in this library. SET NULL for the same reason:
    # the map outlives the document, keeping the label it was named with.
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Measured by the browser after rendering and synced back, so server-side
    # layout and SVG export place a node by its real size rather than by a
    # guess at how wide its text will turn out.
    width: Mapped[float] = mapped_column(Float, nullable=False, default=180.0)
    height: Mapped[float] = mapped_column(Float, nullable=False, default=48.0)

    color: Mapped[str] = mapped_column(String(9), nullable=False, default="")
    shape: Mapped[str] = mapped_column(String(12), nullable=False, default="rounded")
    # How this node's own branch arranges itself. ``None`` - the default, and
    # what every node keeps unless someone says otherwise - means "the same as
    # whatever I hang from", so changing the map's arrangement still moves the
    # whole map. A value here makes this branch its own little map: an
    # organogram hanging off a radial fan hanging off a horizontal spine, all
    # on one board, each drawn the way its own kind is drawn.
    layout: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_collapsed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    mind_map: Mapped["MindMap"] = relationship(back_populates="nodes")
    # No ``delete-orphan`` here, deliberately. Deleting a node must take its
    # branch with it - and it does, through the ``ON DELETE CASCADE`` on
    # ``parent_id`` that ``passive_deletes`` defers to. Adding the ORM cascade
    # on top of it would make *detaching* a child mean deleting it, and
    # detaching is a thing the canvas does on purpose: dragging a topic out to
    # the top level sets its parent to nothing and must keep the topic.
    children: Mapped[list["MindMapNode"]] = relationship(
        back_populates="parent",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="MindMapNode.position",
        lazy="select",
    )
    parent: Mapped["MindMapNode | None"] = relationship(
        back_populates="children", remote_side="MindMapNode.id"
    )
    media_asset: Mapped["MediaAsset | None"] = relationship(lazy="joined")
    document: Mapped["Document | None"] = relationship(lazy="joined")

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MindMapNode {self.uuid}>"


class MindMapEdge(db.Model):
    """A free association between two nodes, outside the hierarchy."""

    __tablename__ = "mind_map_edges"
    __table_args__ = (
        # One association per ordered pair. A second edge would be drawn
        # exactly on top of the first, so it is a duplicate by construction.
        UniqueConstraint("source_id", "target_id", name="uq_mind_map_edge_pair"),
        Index("ix_mind_map_edges_map", "map_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    map_id: Mapped[int] = mapped_column(
        ForeignKey("mind_maps.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("mind_map_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("mind_map_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    label: Mapped[str] = mapped_column(
        String(MAX_EDGE_LABEL_LENGTH), nullable=False, default=""
    )
    style: Mapped[str] = mapped_column(String(10), nullable=False, default="curve")
    color: Mapped[str] = mapped_column(String(9), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    mind_map: Mapped["MindMap"] = relationship(back_populates="edges")
    source: Mapped["MindMapNode"] = relationship(foreign_keys=[source_id])
    target: Mapped["MindMapNode"] = relationship(foreign_keys=[target_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MindMapEdge {self.source_id}->{self.target_id}>"
