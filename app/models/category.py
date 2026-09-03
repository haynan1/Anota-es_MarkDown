from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.utils.dates import utcnow

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document

DEFAULT_CATEGORY_COLOR = "#0F6E64"

# Column width other layers truncate against, named next to the column.
MAX_CATEGORY_NAME_LENGTH = 80


class Category(db.Model):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(MAX_CATEGORY_NAME_LENGTH), nullable=False, unique=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(90), nullable=False, unique=True, index=True)
    color: Mapped[str] = mapped_column(
        String(9), nullable=False, default=DEFAULT_CATEGORY_COLOR
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Lazy on purpose: the app always navigates Document -> Category, never the
    # reverse. Eager-loading here would pull every document of a category on
    # each category load.
    documents: Mapped[list["Document"]] = relationship(
        back_populates="category", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Category {self.name!r}>"
