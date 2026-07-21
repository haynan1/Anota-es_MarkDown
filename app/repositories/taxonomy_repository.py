from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models import Category, Document, Tag, document_tags
from app.utils.files import safe_slug


# Taxonomies are curated by hand, so these ceilings are far above any real
# usage. They exist so a runaway import can never turn a page render into an
# unbounded query.
MAX_CATEGORIES = 500
MAX_TAGS = 2000


class CategoryRepository:
    @staticmethod
    def all(limit: int = MAX_CATEGORIES) -> list[Category]:
        return list(
            db.session.scalars(
                select(Category).order_by(Category.name).limit(limit)
            ).all()
        )

    @staticmethod
    def get(category_id: int) -> Category | None:
        return db.session.get(Category, category_id)

    @staticmethod
    def get_by_name(name: str) -> Category | None:
        stmt = select(Category).where(func.lower(Category.name) == name.strip().lower())
        return db.session.scalars(stmt).one_or_none()

    @staticmethod
    def get_or_create(name: str, color: str | None = None) -> Category:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("O nome da categoria não pode ficar vazio.")
        existing = CategoryRepository.get_by_name(clean)
        if existing:
            return existing
        category = Category(
            name=clean[:80],
            slug=safe_slug(clean, fallback="categoria"),
            color=color or "#4F46E5",
        )
        db.session.add(category)
        db.session.flush()
        return category


class TagRepository:
    @staticmethod
    def all(limit: int = MAX_TAGS) -> list[Tag]:
        return list(
            db.session.scalars(select(Tag).order_by(Tag.name).limit(limit)).all()
        )

    @staticmethod
    def get_by_slug(slug: str) -> Tag | None:
        return db.session.scalars(select(Tag).where(Tag.slug == slug)).one_or_none()

    @staticmethod
    def get_or_create(name: str) -> Tag:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("O nome da etiqueta não pode ficar vazio.")
        slug = safe_slug(clean, fallback="etiqueta", max_length=60)
        existing = TagRepository.get_by_slug(slug)
        if existing:
            return existing
        tag = Tag(name=clean[:60], slug=slug)
        db.session.add(tag)
        db.session.flush()
        return tag

    @staticmethod
    def resolve_many(names: list[str], limit: int = 20) -> list[Tag]:
        """Map free-typed names onto tags, creating the missing ones."""
        resolved: list[Tag] = []
        seen: set[str] = set()
        for raw in names:
            slug = safe_slug(raw, fallback="", max_length=60)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            resolved.append(TagRepository.get_or_create(raw))
            if len(resolved) >= limit:
                break
        return resolved

    @staticmethod
    def usage(limit: int = 40) -> list[tuple[Tag, int]]:
        rows = db.session.execute(
            select(Tag, func.count(Document.id))
            .outerjoin(document_tags, document_tags.c.tag_id == Tag.id)
            .outerjoin(
                Document,
                (Document.id == document_tags.c.document_id)
                & (Document.is_deleted.is_(False)),
            )
            .group_by(Tag.id)
            .order_by(func.count(Document.id).desc(), Tag.name.asc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    def delete_orphans(limit: int = MAX_TAGS) -> int:
        """Remove tags that no longer belong to any document."""
        orphans = db.session.scalars(
            select(Tag).where(~Tag.documents.any()).limit(limit)
        ).all()
        for tag in orphans:
            db.session.delete(tag)
        return len(orphans)
