from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils.dates import utcnow

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document


class DocumentVersion(db.Model):
    """Immutable snapshot of a document at a point in time."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_version_per_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    change_summary: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    document: Mapped["Document"] = relationship(back_populates="versions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DocumentVersion doc={self.document_id} v={self.version_number}>"
