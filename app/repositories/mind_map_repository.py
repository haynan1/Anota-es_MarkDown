"""Consultas sobre mapas mentais.

Reading a map is deliberately three statements - the map, its nodes, its edges
- and never more, whatever the shape of the graph. Walking ``node.children``
would be an N+1 over the depth of the tree, and the canvas needs every node
anyway: it draws the whole board at once.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Document, MediaAsset, MindMap, MindMapNode

# Ceilings so a doctored database can never turn a page render into an
# unbounded query. Membership is capped on the way in by the service; these are
# the second line of defence.
MAX_MAPS = 500
MAX_NODES_READ = 5_000


class MindMapRepository:
    # ── Maps ────────────────────────────────────────────────────────────────

    @staticmethod
    def get_by_uuid(public_uuid: str, include_deleted: bool = False) -> MindMap | None:
        stmt = select(MindMap).where(MindMap.uuid == public_uuid)
        if not include_deleted:
            stmt = stmt.where(MindMap.is_deleted.is_(False))
        return db.session.scalars(stmt).one_or_none()

    @staticmethod
    def get_by_slug(slug: str) -> MindMap | None:
        return db.session.scalars(
            select(MindMap).where(MindMap.slug == slug)
        ).one_or_none()

    @staticmethod
    def listing(
        search: str = "",
        favorites_only: bool = False,
        deleted: bool = False,
        limit: int = MAX_MAPS,
    ) -> list[MindMap]:
        """Maps for the gallery, most recently touched first.

        The search is a LIKE over title and description rather than an FTS
        query: the corpus is at most a few hundred short strings, and adding a
        second virtual table to keep in sync would cost more than it returns.
        """
        stmt = select(MindMap).where(MindMap.is_deleted.is_(deleted))
        if favorites_only:
            stmt = stmt.where(MindMap.is_favorite.is_(True))

        term = (search or "").strip()
        if term:
            pattern = f"%{term}%"
            stmt = stmt.where(
                MindMap.title.ilike(pattern) | MindMap.description.ilike(pattern)
            )

        stmt = stmt.order_by(MindMap.updated_at.desc()).limit(limit)
        return list(db.session.scalars(stmt).all())

    @staticmethod
    def counts() -> dict[str, int]:
        """Totals for the gallery header. One statement, three numbers."""
        row = db.session.execute(
            select(
                func.count(MindMap.id).filter(MindMap.is_deleted.is_(False)),
                func.count(MindMap.id).filter(
                    MindMap.is_deleted.is_(False), MindMap.is_favorite.is_(True)
                ),
                func.count(MindMap.id).filter(MindMap.is_deleted.is_(True)),
            )
        ).one()
        return {"total": row[0] or 0, "favorites": row[1] or 0, "trashed": row[2] or 0}

    @staticmethod
    def node_counts(map_ids: list[int]) -> dict[int, int]:
        """Nodes per map for a whole gallery page, in one grouped query."""
        if not map_ids:
            return {}
        rows = db.session.execute(
            select(MindMapNode.map_id, func.count(MindMapNode.id))
            .where(MindMapNode.map_id.in_(map_ids))
            .group_by(MindMapNode.map_id)
        ).all()
        return {row[0]: row[1] for row in rows}

    # ── Graph ───────────────────────────────────────────────────────────────

    @staticmethod
    def nodes_of(mind_map: MindMap, limit: int = MAX_NODES_READ) -> list[MindMapNode]:
        """Every node of the map, parents before children where possible.

        ``joinedload`` on the two outward references is what keeps rendering a
        map with fifty pictures at three queries instead of fifty-three.
        """
        return list(
            db.session.scalars(
                select(MindMapNode)
                .where(MindMapNode.map_id == mind_map.id)
                .options(
                    joinedload(MindMapNode.media_asset),
                    joinedload(MindMapNode.document).load_only(
                        Document.uuid, Document.title, Document.is_deleted
                    ),
                )
                .order_by(MindMapNode.parent_id.nulls_first(), MindMapNode.position)
                .limit(limit)
            )
            .unique()
            .all()
        )

    @staticmethod
    def node_count(map_id: int) -> int:
        return (
            db.session.scalar(
                select(func.count(MindMapNode.id)).where(MindMapNode.map_id == map_id)
            )
            or 0
        )

    @staticmethod
    def referenced_asset_ids() -> set[int]:
        """Media referenced by any node of any map.

        The orphan sweeper reads document bodies to decide what is still in
        use; a picture that only ever lived on a canvas would be invisible to
        it and deleted out from under the map. This is how the canvas declares
        what it holds.
        """
        return set(
            db.session.scalars(
                select(MindMapNode.media_asset_id).where(
                    MindMapNode.media_asset_id.is_not(None)
                )
            ).all()
        )

    @staticmethod
    def assets_of(map_id: int) -> list[MediaAsset]:
        """The uploaded pictures a map owns, for purging it for good."""
        return list(
            db.session.scalars(
                select(MediaAsset)
                .join(MindMapNode, MindMapNode.media_asset_id == MediaAsset.id)
                .where(MindMapNode.map_id == map_id)
            )
            .unique()
            .all()
        )
