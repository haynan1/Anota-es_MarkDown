"""Consultas sobre grupos de documentos."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import defer, joinedload, selectinload

from app.extensions import db
from app.models import Document, Group, document_groups

# Groups are curated by hand; these ceilings exist so a runaway import can never
# turn a page render into an unbounded query.
MAX_GROUPS = 500
MAX_DOCUMENTS_IN_GROUP = 500


class GroupRepository:
    @staticmethod
    def all(limit: int = MAX_GROUPS) -> list[Group]:
        return list(
            db.session.scalars(select(Group).order_by(Group.name).limit(limit)).all()
        )

    @staticmethod
    def get(group_id: int) -> Group | None:
        return db.session.get(Group, group_id)

    @staticmethod
    def get_by_uuid(public_uuid: str) -> Group | None:
        return db.session.scalars(
            select(Group).where(Group.uuid == public_uuid)
        ).one_or_none()

    @staticmethod
    def get_by_name(name: str) -> Group | None:
        return db.session.scalars(
            select(Group).where(func.lower(Group.name) == (name or "").strip().lower())
        ).one_or_none()

    @staticmethod
    def get_by_slug(slug: str) -> Group | None:
        return db.session.scalars(select(Group).where(Group.slug == slug)).one_or_none()

    @staticmethod
    def usage(limit: int = MAX_GROUPS) -> list[tuple[Group, int]]:
        """Every group with the number of live documents in it.

        One query for the whole screen: a per-group count would be N+1 on a
        page whose entire job is to list groups.
        """
        rows = db.session.execute(
            select(Group, func.count(Document.id))
            .outerjoin(document_groups, document_groups.c.group_id == Group.id)
            .outerjoin(
                Document,
                (Document.id == document_groups.c.document_id)
                & (Document.is_deleted.is_(False)),
            )
            .group_by(Group.id)
            .order_by(Group.name.asc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    def member_ids(group_id: int) -> set[int]:
        """Ids currently in the group - one query, no rows materialised."""
        return set(
            db.session.scalars(
                select(document_groups.c.document_id).where(
                    document_groups.c.group_id == group_id
                )
            ).all()
        )

    @staticmethod
    def documents_of(
        group: Group, include_archived: bool = True, limit: int = MAX_DOCUMENTS_IN_GROUP
    ) -> list[Document]:
        """The group's documents, in the order the writer arranged them.

        Bounded like every other listing in this codebase. Membership is
        capped on the way in (``GroupService.MAX_DOCUMENTS_PER_GROUP``), so
        this ceiling is never the thing a real group runs into - it is the
        guarantee that a doctored database cannot turn this page into an
        unbounded query.
        """
        stmt = (
            select(Document)
            .join(document_groups, document_groups.c.document_id == Document.id)
            .where(
                document_groups.c.group_id == group.id,
                Document.is_deleted.is_(False),
            )
            .options(
                defer(Document.content_markdown),
                defer(Document.rendered_html),
                joinedload(Document.category),
                selectinload(Document.tags),
            )
            .order_by(document_groups.c.position, Document.title)
            .limit(limit)
        )
        if not include_archived:
            stmt = stmt.where(Document.is_archived.is_(False))
        return list(db.session.scalars(stmt).unique().all())

    @staticmethod
    def all_with_membership(
        document_id: int, limit: int = MAX_GROUPS
    ) -> list[tuple[Group, bool]]:
        """Every group, flagged with whether ``document_id`` is in it.

        The editor panel needs both facts at once - the list to offer and the
        boxes to tick. A LEFT JOIN answers both in one statement instead of
        loading the catalogue and then the document's memberships separately.
        """
        rows = db.session.execute(
            select(Group, document_groups.c.document_id)
            .outerjoin(
                document_groups,
                (document_groups.c.group_id == Group.id)
                & (document_groups.c.document_id == document_id),
            )
            .order_by(Group.name)
            .limit(limit)
        ).all()
        return [(row[0], row[1] is not None) for row in rows]

    @staticmethod
    def next_position(group_id: int) -> int:
        highest = db.session.scalar(
            select(func.max(document_groups.c.position)).where(
                document_groups.c.group_id == group_id
            )
        )
        return (highest or 0) + 1

    @staticmethod
    def contains(group_id: int, document_id: int) -> bool:
        """Whether one document is in one group. Batch work uses `member_ids`."""
        return (
            db.session.scalar(
                select(document_groups.c.document_id).where(
                    document_groups.c.group_id == group_id,
                    document_groups.c.document_id == document_id,
                )
            )
            is not None
        )
