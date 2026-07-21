"""Shared column mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.utils.dates import utcnow


class TimestampMixin:
    """``created_at`` / ``updated_at`` stored as timezone-aware UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
        index=True,
    )
