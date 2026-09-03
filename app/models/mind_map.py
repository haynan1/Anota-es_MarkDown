"""Mapas mentais: pensar em duas dimensões, não em uma lista.

Why a graph and not a JSON blob
--------------------------------
The obvious shortcut for a canvas is a single ``canvas_json`` column. It is
also the shortcut that costs the most later: a blob cannot be indexed, cannot
be joined, cannot enforce that a link points at a node that exists, and turns
every edit into a read-modify-write of the whole document. Two tabs editing
different corners of the same map would then overwrite each other wholesale.

So a map is stored as what it is: nodes with real foreign keys.
Deleting a map takes its nodes with it (``CASCADE``); deleting a node takes its
branch with it, because that is what "delete this branch" means in a mind map.

Uma conexão só
--------------
``parent_id`` is the whole of it: one topic connected to another is one topic
inside another, and there is nothing else. The board used to draw a second
kind of line - a free association across the map - and the two were told apart
by nothing a person could see. Which of them a gesture produced, and which one
carried the structure, became the map's most reliable source of confusion. A
mind map is a tree; it now has one line, and that line is the tree.

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

DEFAULT_MAP_COLOR = "#0F6E64"

# A node is a thought, not a document. `topic` is the ordinary one; `note` is
# the sticky square an aside gets parked on; `image` is a picture carrying its
# own caption. Anything else - a link, a document reference - is an attribute
# of a node rather than a separate kind, because a thought does not stop being
# a thought when a URL is attached to it.
NODE_KINDS = ("topic", "note", "image")
NODE_SHAPES = ("rounded", "pill", "rect", "ellipse", "diamond")

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
    # O cadeado. Um documento se protege contra sumir; um mapa precisa se
    # proteger contra *mudar*, e a diferença não é acadêmica: numa tela, uma
    # tecla errada com um tópico selecionado apaga um ramo, e um arrastar
    # distraído reescreve a hierarquia sem pedir nada a ninguém. Travado, o
    # mapa fica somente leitura - nenhuma operação, nenhum "arrumar", nenhuma
    # troca de nome ou cor, e nem a lixeira.
    #
    # O que continua liberado é o que não altera o mapa: abrir, navegar,
    # favoritar, duplicar (a cópia nasce destravada) e exportar em qualquer
    # formato. Guardar o enquadramento também, porque olhar não é editar.
    is_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
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

    # Um espelho: este nó *é* outro nó, aparecendo num segundo lugar da árvore.
    #
    # Uma árvore não sabe dizer "isto vale para todas as etapas" - um tópico
    # tem um pai. O espelho é como isso é dito sem deixar de ser uma árvore:
    # ele é uma linha como qualquer outra, e o que está na ponta dela é um
    # tópico que mora noutro lugar. Renomear o original renomeia aqui, porque
    # é o mesmo tópico; e é sempre uma folha, porque o ramo é do original.
    #
    # CASCADE: uma referência a algo que não existe mais não é nada. Apagar o
    # original leva os espelhos dele; apagar um espelho não toca no original.
    mirror_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("mind_map_nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mirror_of: Mapped["MindMapNode | None"] = relationship(
        remote_side="MindMapNode.id", foreign_keys=[mirror_of_id]
    )
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
        foreign_keys=[parent_id],
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="MindMapNode.position",
        lazy="select",
    )
    parent: Mapped["MindMapNode | None"] = relationship(
        back_populates="children",
        remote_side="MindMapNode.id",
        foreign_keys=[parent_id],
    )
    media_asset: Mapped["MediaAsset | None"] = relationship(lazy="joined")
    document: Mapped["Document | None"] = relationship(lazy="joined")

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MindMapNode {self.uuid}>"
