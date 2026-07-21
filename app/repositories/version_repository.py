from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from app.extensions import db
from app.models import DocumentVersion


class VersionRepository:
    """Queries over document history."""

    @staticmethod
    def latest_number(document_id: int) -> int:
        return (
            db.session.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document_id
                )
            )
            or 0
        )

    @staticmethod
    def latest(document_id: int) -> DocumentVersion | None:
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        return db.session.scalars(stmt).one_or_none()

    @staticmethod
    def paginate(document_id: int, page: int = 1, per_page: int = 20):
        # The history list shows metadata only - the body is fetched on demand.
        stmt = (
            select(DocumentVersion)
            .options(defer(DocumentVersion.content_markdown))
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
        )
        return db.paginate(
            stmt, page=max(page, 1), per_page=per_page, error_out=False, max_per_page=100
        )

    @staticmethod
    def get(document_id: int, version_number: int) -> DocumentVersion | None:
        stmt = select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
        return db.session.scalars(stmt).one_or_none()

    @staticmethod
    def count(document_id: int) -> int:
        return (
            db.session.scalar(
                select(func.count(DocumentVersion.id)).where(
                    DocumentVersion.document_id == document_id
                )
            )
            or 0
        )
