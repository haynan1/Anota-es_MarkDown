"""Full-text search.

Primary engine is SQLite FTS5 with the ``unicode61`` tokenizer and
``remove_diacritics 2``, which makes searches accent- and case-insensitive -
the right behaviour for Portuguese ("codigo" finds "código").

If FTS5 is unavailable (non-SQLite backend, or a build without the module) the
service degrades to parameterised ``LIKE`` queries. User input is *never*
concatenated into SQL: the FTS MATCH expression is rebuilt from tokenised
terms, and everything else goes through bound parameters.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape
from sqlalchemy import text

from app.extensions import db
from app.utils.text import strip_markdown

FTS_TABLE = "documents_fts"

# Sentinels survive HTML escaping, so snippets can be escaped first and only
# then decorated with real <mark> tags.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

_CREATE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title,
    body,
    document_id UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
)
"""


class SearchIndex:
    """Lifecycle and queries for the FTS5 index."""

    def __init__(self) -> None:
        self.available = False

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def ensure(self) -> bool:
        """Create the virtual table if the backend supports it."""
        if db.engine.dialect.name != "sqlite":
            self.available = False
            return False
        try:
            db.session.execute(text(_CREATE_SQL))
            db.session.commit()
            self.available = True
        except Exception:  # pragma: no cover - depends on the SQLite build
            db.session.rollback()
            self.available = False
        return self.available

    def index_document(self, document) -> None:
        """Insert or refresh one document in the index."""
        if not self.available:
            return
        try:
            db.session.execute(
                text(f"DELETE FROM {FTS_TABLE} WHERE document_id = :doc_id"),
                {"doc_id": document.id},
            )
            if not document.is_deleted:
                db.session.execute(
                    text(
                        f"INSERT INTO {FTS_TABLE} (title, body, document_id) "
                        "VALUES (:title, :body, :doc_id)"
                    ),
                    {
                        "title": document.title or "",
                        "body": strip_markdown(document.content_markdown or ""),
                        "doc_id": document.id,
                    },
                )
        except Exception:  # pragma: no cover - index must never break a save
            db.session.rollback()
            self.available = False

    def remove_document(self, document_id: int) -> None:
        if not self.available:
            return
        try:
            db.session.execute(
                text(f"DELETE FROM {FTS_TABLE} WHERE document_id = :doc_id"),
                {"doc_id": document_id},
            )
        except Exception:  # pragma: no cover
            db.session.rollback()

    def rebuild(self) -> int:
        """Reindex every non-deleted document. Returns the number indexed."""
        from app.models import Document

        if not self.ensure():
            return 0
        db.session.execute(text(f"DELETE FROM {FTS_TABLE}"))
        documents = db.session.scalars(
            db.select(Document).filter_by(is_deleted=False)
        ).all()
        for document in documents:
            self.index_document(document)
        db.session.commit()
        return len(documents)

    # ── Querying ────────────────────────────────────────────────────────────

    @staticmethod
    def build_match_expression(query: str) -> str:
        """Turn free text into a safe FTS5 MATCH expression.

        Each token becomes a quoted prefix term, so no user character is ever
        interpreted as FTS syntax.
        """
        tokens = _TOKEN_RE.findall(query or "")
        if not tokens:
            return ""
        return " AND ".join(f'"{token}"*' for token in tokens[:12])

    def search_ids(self, query: str, limit: int = 500) -> list[int] | None:
        """Document ids matching ``query``, best match first.

        Returns ``None`` when the index cannot answer, signalling the caller to
        fall back to LIKE.
        """
        if not self.available:
            return None
        match = self.build_match_expression(query)
        if not match:
            return None
        try:
            rows = db.session.execute(
                text(
                    f"SELECT document_id FROM {FTS_TABLE} "
                    f"WHERE {FTS_TABLE} MATCH :match ORDER BY rank LIMIT :limit"
                ),
                {"match": match, "limit": limit},
            ).all()
        except Exception:  # pragma: no cover - malformed expressions
            db.session.rollback()
            return None
        return [row[0] for row in rows]

    def snippets(self, query: str, document_ids: list[int]) -> dict[int, Markup]:
        """Highlighted excerpts keyed by document id."""
        if not self.available or not document_ids:
            return {}
        match = self.build_match_expression(query)
        if not match:
            return {}
        placeholders = ", ".join(f":id{i}" for i in range(len(document_ids)))
        params: dict[str, object] = {
            f"id{i}": doc_id for i, doc_id in enumerate(document_ids)
        }
        params.update({"match": match, "open": _MARK_OPEN, "close": _MARK_CLOSE})
        try:
            rows = db.session.execute(
                text(
                    f"SELECT document_id, snippet({FTS_TABLE}, 1, :open, :close, '…', 18) "
                    f"FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :match "
                    f"AND document_id IN ({placeholders})"
                ),
                params,
            ).all()
        except Exception:  # pragma: no cover
            db.session.rollback()
            return {}
        return {row[0]: highlight_sentinels(row[1]) for row in rows}


def highlight_sentinels(raw: str) -> Markup:
    """Escape ``raw`` and only then convert sentinels into ``<mark>`` tags."""
    escaped = str(escape(raw or ""))
    escaped = escaped.replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")
    return Markup(escaped)  # noqa: S704 - content escaped immediately above


def highlight_terms(value: str, query: str, limit: int = 220) -> Markup:
    """Fallback highlighter used when FTS snippets are unavailable."""
    plain = (value or "")[: limit * 3]
    tokens = _TOKEN_RE.findall(query or "")
    escaped = str(escape(plain[:limit] + ("…" if len(plain) > limit else "")))
    for token in sorted(set(tokens), key=len, reverse=True)[:6]:
        escaped = re.sub(
            f"({re.escape(escape(token))})",
            r"<mark>\1</mark>",
            escaped,
            flags=re.IGNORECASE,
        )
    return Markup(escaped)  # noqa: S704 - built from escaped fragments only


search_index = SearchIndex()
