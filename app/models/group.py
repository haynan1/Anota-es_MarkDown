"""Grupos: coleções de documentos sobre um mesmo assunto.

Why this exists next to categories and tags
-------------------------------------------
A category answers "what kind of document is this?" - one per document, so it
partitions the library. A tag answers "what is it about?" - free, many, and
flat. Neither answers "which documents belong together, in this order?", which
is what a body of work on one subject actually is: a manual and its appendix,
a proposal and its three revisions, a course and its lessons.

A group is therefore many-to-many *and ordered*. The order lives on the
association row, not on the document, because the same document can sit third
in one group and first in another.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils.dates import utcnow

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document

DEFAULT_GROUP_COLOR = "#0F6E64"

MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 280


def new_uuid() -> str:
    return str(uuid_module.uuid4())


document_groups = Table(
    "document_groups",
    db.metadata,
    Column(
        "document_id",
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # Position within the group. Sparse and rewritten wholesale on reorder,
    # so no two rows in a group are ever asked to share a slot.
    Column("position", Integer, nullable=False, default=0, server_default="0"),
    Column("added_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Index("ix_document_groups_group_id", "group_id"),
    Index("ix_document_groups_order", "group_id", "position"),
)


class Group(db.Model):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(
        String(MAX_NAME_LENGTH), nullable=False, unique=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(110), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(
        String(MAX_DESCRIPTION_LENGTH), nullable=False, default=""
    )
    color: Mapped[str] = mapped_column(
        String(9), nullable=False, default=DEFAULT_GROUP_COLOR
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # Ordered by the association's own column: the group's sequence is a
    # property of the membership, not of the document.
    documents: Mapped[list["Document"]] = relationship(
        secondary=document_groups,
        back_populates="groups",
        lazy="select",
        order_by=document_groups.c.position,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Group {self.name!r}>"
