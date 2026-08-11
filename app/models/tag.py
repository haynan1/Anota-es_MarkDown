from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils.dates import utcnow

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document

# Column width other layers truncate against, named next to the column.
MAX_TAG_NAME_LENGTH = 60

document_tags = Table(
    "document_tags",
    db.metadata,
    Column(
        "document_id",
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_document_tags_tag_id", "tag_id"),
)


class Tag(db.Model):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(MAX_TAG_NAME_LENGTH), nullable=False, unique=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(70), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Reverse side; queried explicitly by TagRepository when counts are needed.
    documents: Mapped[list["Document"]] = relationship(
        secondary=document_tags, back_populates="tags", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tag {self.name!r}>"
