from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.utils.dates import utcnow


def new_uuid() -> str:
    return str(uuid_module.uuid4())


class MediaAsset(db.Model):
    """An uploaded image, GIF or video.

    The database is the only index of what exists on disk: files are stored
    under a generated name, and the original filename is kept purely as a
    label. Nothing in a request is ever used to build a filesystem path.
    """

    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    # Path relative to the uploads root, e.g. "2026/07/<uuid>.png".
    stored_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Shown to the user; never touches the filesystem.
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Decided by magic-byte sniffing, not by the client.
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MediaAsset {self.uuid} {self.mime_type}>"
