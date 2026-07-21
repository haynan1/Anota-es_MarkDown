from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin
from app.models.tag import document_tags

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.category import Category
    from app.models.document_version import DocumentVersion
    from app.models.tag import Tag

PAGE_SIZES = ("A4", "Letter")
PDF_THEMES = ("classic", "minimal", "academic", "modern")

PAGE_SIZE_LABELS = {"A4": "A4", "Letter": "Carta (Letter)"}
PDF_THEME_LABELS = {
    "classic": "Clássico",
    "minimal": "Minimalista",
    "academic": "Acadêmico",
    "modern": "Moderno",
}


def new_uuid() -> str:
    return str(uuid_module.uuid4())


class Document(TimestampMixin, db.Model):
    __tablename__ = "documents"
    __table_args__ = (
        # Listing queries always filter on the lifecycle flags and sort by date.
        Index("ix_documents_state_updated", "is_deleted", "is_archived", "updated_at"),
        Index("ix_documents_favorite_state", "is_favorite", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(220), nullable=False, unique=True, index=True)

    content_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rendered_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    excerpt: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    # Fingerprint of the persisted revision - used to skip duplicate versions.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Monotonic counter powering optimistic concurrency control on autosave.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_reading_time: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    page_size: Mapped[str] = mapped_column(String(10), nullable=False, default="A4")
    pdf_theme: Mapped[str] = mapped_column(String(20), nullable=False, default="classic")

    category: Mapped["Category | None"] = relationship(
        back_populates="documents", lazy="joined"
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=document_tags,
        back_populates="documents",
        lazy="selectin",
        order_by="Tag.name",
    )
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentVersion.version_number.desc()",
        lazy="select",
    )

    @property
    def display_title(self) -> str:
        return self.title.strip() or "Documento sem título"

    @property
    def tag_names(self) -> list[str]:
        return [tag.name for tag in self.tags]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document {self.id} {self.title!r}>"
