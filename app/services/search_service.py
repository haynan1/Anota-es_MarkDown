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

import logging
import re

from markupsafe import Markup, escape
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.utils.text import strip_markdown

logger = logging.getLogger(__name__)

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
    """Lifecycle and queries for the FTS5 index.

    The index is an optimisation, never a source of truth. Two rules follow
    from that and are load-bearing:

    * **A failure here must never cost the user their text.** Index writes run
      inside a SAVEPOINT, so a broken index rolls back only its own statements
      and leaves the surrounding document transaction intact. Calling
      ``session.rollback()`` here would discard the very save that triggered
      the indexing.
    * **Availability is tracked per database**, not globally. The same process
      can serve more than one application instance (tests do exactly that),
      and one database having no FTS table says nothing about another.
    """

    def __init__(self) -> None:
        self._available_by_engine: dict[str, bool] = {}

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @staticmethod
    def _engine_key() -> str:
        try:
            return str(db.engine.url)
        except RuntimeError:  # pragma: no cover - outside an application context
            return ""

    @property
    def available(self) -> bool:
        return self._available_by_engine.get(self._engine_key(), False)

    def _set_available(self, value: bool) -> bool:
        self._available_by_engine[self._engine_key()] = value
        return value

    def ensure(self) -> bool:
        """Create the virtual table if the backend supports it."""
        if db.engine.dialect.name != "sqlite":
            return self._set_available(False)
        try:
            db.session.execute(text(_CREATE_SQL))
            db.session.commit()
            return self._set_available(True)
        except SQLAlchemyError:  # pragma: no cover - depends on the SQLite build
            db.session.rollback()
            logger.warning("Índice FTS5 indisponível; a busca usará LIKE.")
            return self._set_available(False)

    def index_document(self, document) -> None:
        """Insert or refresh one document in the index."""
        if not self.available:
            return
        try:
            # SAVEPOINT: a failure discards only these two statements.
            with db.session.begin_nested():
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
        except SQLAlchemyError:
            logger.warning(
                "Falha ao indexar o documento %s; o salvamento continua válido.",
                getattr(document, "id", "?"),
                exc_info=True,
            )
            self._set_available(False)

    def remove_document(self, document_id: int) -> None:
        if not self.available:
            return
        try:
            with db.session.begin_nested():
                db.session.execute(
                    text(f"DELETE FROM {FTS_TABLE} WHERE document_id = :doc_id"),
                    {"doc_id": document_id},
                )
        except SQLAlchemyError:  # pragma: no cover
            logger.warning("Falha ao remover o documento %s do índice.", document_id)
            self._set_available(False)

    def rebuild(self, batch_size: int = 200) -> int:
        """Reindex every non-deleted document. Returns the number indexed.

        Streams the corpus in batches, selecting only the three columns the
        index needs. Loading whole ORM objects would pull ``rendered_html``
        as well and hold the entire library in memory.

        No per-document SAVEPOINT here: the whole rebuild is one operation, so
        a failure should abandon all of it rather than leave a half-built
        index that queries would trust.
        """
        # Deferred: app.models imports app.extensions, which this module also
        # imports at load time. A module-level import here would create a cycle.
        from app.models import Document

        self._available_by_engine.pop(self._engine_key(), None)
        if not self.ensure():
            return 0

        try:
            db.session.execute(text(f"DELETE FROM {FTS_TABLE}"))

            insert = text(
                f"INSERT INTO {FTS_TABLE} (title, body, document_id) "
                "VALUES (:title, :body, :doc_id)"
            )
            rows = db.session.execute(
                db.select(Document.id, Document.title, Document.content_markdown)
                .where(Document.is_deleted.is_(False))
                .execution_options(yield_per=batch_size)
            )

            total = 0
            batch: list[dict[str, object]] = []
            for document_id, title, content in rows:
                batch.append(
                    {
                        "title": title or "",
                        "body": strip_markdown(content or ""),
                        "doc_id": document_id,
                    }
                )
                if len(batch) >= batch_size:
                    db.session.execute(insert, batch)
                    total += len(batch)
                    batch = []

            if batch:
                db.session.execute(insert, batch)
                total += len(batch)

            db.session.commit()
            return total
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Falha ao reconstruir o índice de busca.")
            self._set_available(False)
            return 0

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
        except SQLAlchemyError:  # pragma: no cover - malformed FTS expression
            db.session.rollback()
            logger.warning("Consulta FTS rejeitada; usando busca por LIKE.")
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
        except SQLAlchemyError:  # pragma: no cover
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
