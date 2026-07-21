from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import defer, joinedload, selectinload

from app.extensions import db
from app.models import Category, Document, Tag
from app.utils.dates import utcnow

SCOPE_ACTIVE = "active"
SCOPE_ARCHIVED = "archived"
SCOPE_TRASH = "trash"
SCOPE_ALL = "all"

SORT_OPTIONS = {
    "updated_desc": "Modificados recentemente",
    "updated_asc": "Modificados há mais tempo",
    "created_desc": "Criados recentemente",
    "created_asc": "Criados há mais tempo",
    "title_asc": "Título (A–Z)",
    "title_desc": "Título (Z–A)",
}


@dataclass(slots=True)
class DocumentQuery:
    """Everything the listing screen can ask for.

    ``matched_ids`` is filled in by the search service before the query
    reaches this layer: ``None`` means "no full-text result to honour, fall
    back to LIKE". Resolving it here would make the repository depend on a
    service, inverting the dependency direction of the whole codebase.
    """

    search: str = ""
    category_id: int | None = None
    tag_slugs: tuple[str, ...] = ()
    only_favorites: bool = False
    scope: str = SCOPE_ACTIVE
    sort: str = "updated_desc"
    page: int = 1
    per_page: int = 12
    matched_ids: list[int] | None = field(default=None, repr=False)

    @property
    def is_searching(self) -> bool:
        return bool(self.search.strip())

    @property
    def has_filters(self) -> bool:
        return bool(
            self.is_searching
            or self.category_id
            or self.tag_slugs
            or self.only_favorites
        )


class DocumentRepository:
    """Queries over :class:`~app.models.document.Document`."""

    # ── Single-record lookups ───────────────────────────────────────────────

    @staticmethod
    def get_by_uuid(public_uuid: str, include_deleted: bool = False) -> Document | None:
        stmt = select(Document).where(Document.uuid == public_uuid)
        if not include_deleted:
            stmt = stmt.where(Document.is_deleted.is_(False))
        return db.session.scalars(stmt).unique().one_or_none()

    @staticmethod
    def slug_exists(slug: str, exclude_id: int | None = None) -> bool:
        stmt = select(Document.id).where(Document.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(Document.id != exclude_id)
        return db.session.scalar(stmt.limit(1)) is not None

    # ── Listing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _base_statement():
        # Listings never need the full body or the rendered HTML; deferring
        # them keeps a 100-document page from pulling megabytes of text.
        #
        # Deliberately no .distinct(): tag filters are EXISTS subqueries and
        # the category is many-to-one, so no row is ever duplicated. Adding
        # DISTINCT would force the paginator's COUNT to wrap a subquery over
        # every column - reading each document's full body just to count it.
        return select(Document).options(
            defer(Document.content_markdown),
            defer(Document.rendered_html),
            joinedload(Document.category),
            selectinload(Document.tags),
        )

    @classmethod
    def _apply_scope(cls, stmt, scope: str):
        if scope == SCOPE_TRASH:
            return stmt.where(Document.is_deleted.is_(True))
        stmt = stmt.where(Document.is_deleted.is_(False))
        if scope == SCOPE_ACTIVE:
            stmt = stmt.where(Document.is_archived.is_(False))
        elif scope == SCOPE_ARCHIVED:
            stmt = stmt.where(Document.is_archived.is_(True))
        return stmt

    @classmethod
    def _apply_sort(cls, stmt, query: DocumentQuery):
        # A search ranks by relevance unless the user picked an explicit order.
        if query.matched_ids and query.sort == "relevance":
            ordering = case(
                {doc_id: index for index, doc_id in enumerate(query.matched_ids)},
                value=Document.id,
                else_=len(query.matched_ids),
            )
            return stmt.order_by(ordering, Document.updated_at.desc())

        mapping = {
            "updated_desc": Document.updated_at.desc(),
            "updated_asc": Document.updated_at.asc(),
            "created_desc": Document.created_at.desc(),
            "created_asc": Document.created_at.asc(),
            "title_asc": func.lower(Document.title).asc(),
            "title_desc": func.lower(Document.title).desc(),
        }
        return stmt.order_by(mapping.get(query.sort, Document.updated_at.desc()))

    @classmethod
    def build_statement(cls, query: DocumentQuery):
        stmt = cls._apply_scope(cls._base_statement(), query.scope)

        if query.category_id:
            stmt = stmt.where(Document.category_id == query.category_id)

        if query.only_favorites:
            stmt = stmt.where(Document.is_favorite.is_(True))

        for slug in query.tag_slugs:
            # Repeated joins implement AND semantics across tags.
            stmt = stmt.where(Document.tags.any(Tag.slug == slug))

        if query.is_searching:
            if query.matched_ids is not None:
                # -1 is a sentinel: an empty result set must match nothing,
                # not degrade into "no filter at all".
                stmt = stmt.where(Document.id.in_(query.matched_ids or [-1]))
            else:
                pattern = f"%{query.search.strip().lower()}%"
                stmt = stmt.where(
                    func.lower(Document.title).like(pattern)
                    | func.lower(Document.content_markdown).like(pattern)
                    | func.lower(Document.excerpt).like(pattern)
                )

        return cls._apply_sort(stmt, query)

    @classmethod
    def paginate(cls, query: DocumentQuery):
        return db.paginate(
            cls.build_statement(query),
            page=max(query.page, 1),
            per_page=query.per_page,
            error_out=False,
            max_per_page=100,
        )

    # ── Dashboard aggregates ────────────────────────────────────────────────

    @staticmethod
    def recent(limit: int = 6, scope: str = SCOPE_ACTIVE) -> list[Document]:
        stmt = (
            select(Document)
            .options(
                defer(Document.content_markdown),
                defer(Document.rendered_html),
                joinedload(Document.category),
                selectinload(Document.tags),
            )
            .where(Document.is_deleted.is_(False))
            .order_by(Document.updated_at.desc())
            .limit(limit)
        )
        if scope == SCOPE_ACTIVE:
            stmt = stmt.where(Document.is_archived.is_(False))
        return list(db.session.scalars(stmt).unique().all())

    @staticmethod
    def favorites(limit: int = 5) -> list[Document]:
        stmt = (
            select(Document)
            .options(defer(Document.content_markdown), defer(Document.rendered_html))
            .where(
                Document.is_deleted.is_(False),
                Document.is_favorite.is_(True),
            )
            .order_by(Document.updated_at.desc())
            .limit(limit)
        )
        return list(db.session.scalars(stmt).unique().all())

    @staticmethod
    def stats() -> dict[str, int]:
        totals = db.session.execute(
            select(
                func.count(Document.id).filter(Document.is_deleted.is_(False)),
                func.count(Document.id).filter(
                    Document.is_deleted.is_(False), Document.is_archived.is_(False)
                ),
                func.count(Document.id).filter(
                    Document.is_deleted.is_(False), Document.is_favorite.is_(True)
                ),
                func.count(Document.id).filter(
                    Document.is_deleted.is_(False), Document.is_archived.is_(True)
                ),
                func.count(Document.id).filter(Document.is_deleted.is_(True)),
                func.coalesce(
                    func.sum(
                        case((Document.is_deleted.is_(False), Document.word_count), else_=0)
                    ),
                    0,
                ),
            )
        ).one()

        # Scoped to the same set as the "active" figure it sits under -
        # otherwise the card reads "4 documentos ativos / +5 nesta semana".
        week_ago = utcnow() - timedelta(days=7)
        created_this_week = db.session.scalar(
            select(func.count(Document.id)).where(
                Document.is_deleted.is_(False),
                Document.is_archived.is_(False),
                Document.created_at >= week_ago,
            )
        )

        return {
            "total": totals[0] or 0,
            "active": totals[1] or 0,
            "favorites": totals[2] or 0,
            "archived": totals[3] or 0,
            "trashed": totals[4] or 0,
            "words": int(totals[5] or 0),
            "created_this_week": created_this_week or 0,
        }

    @staticmethod
    def category_usage(limit: int = 6) -> list[tuple[Category, int]]:
        rows = db.session.execute(
            select(Category, func.count(Document.id).label("total"))
            .outerjoin(
                Document,
                (Document.category_id == Category.id)
                & (Document.is_deleted.is_(False)),
            )
            .group_by(Category.id)
            .order_by(func.count(Document.id).desc(), Category.name.asc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]
