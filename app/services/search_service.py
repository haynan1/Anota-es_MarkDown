"""Full-text search.

Two indexes, because "find this document" is two different questions.

* **Words.** SQLite FTS5 over title and body with the ``unicode61`` tokenizer
  and ``remove_diacritics 2``, which makes searches accent- and
  case-insensitive - the right behaviour for Portuguese ("codigo" finds
  "código"). This is what ranks results.
* **Fragments of a title.** A word index can only find whole words and the
  prefixes of words, so no amount of tuning would ever let "trofia" reach
  "Hipertrofia Muscular". A second, tiny FTS5 index over titles alone, with
  the ``trigram`` tokenizer, answers exactly that: any run of characters,
  anywhere in a title, accents and case ignored.

Both are optional. If FTS5 is unavailable (non-SQLite backend, or a build
without the module) the service degrades to parameterised ``LIKE`` queries; if
only the trigram tokenizer is missing (SQLite older than 3.34) word search
still works and mid-word matches are simply not found. User input is *never*
concatenated into SQL: MATCH expressions are rebuilt from tokenised terms or
quoted whole, and everything else goes through bound parameters.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from markupsafe import Markup, escape
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.utils.text import strip_markdown

logger = logging.getLogger(__name__)

FTS_TABLE = "documents_fts"
TITLES_TABLE = "documents_titles_fts"

#: ``bm25`` column weights over (title, body). A word in the title is what the
#: writer named the document; the same word in the body is one of thousands.
#: Unweighted they rank identically, and searching a title used to return
#: every document that ever mentioned it with the document itself buried
#: among them - which is what made the search feel like it wanted the exact
#: full title and nothing less.
_TITLE_WEIGHT = 10.0
_BODY_WEIGHT = 1.0

#: A trigram index cannot answer anything shorter than one trigram. Below this
#: the prefix search already covers the ground.
MIN_SUBSTRING_LENGTH = 3

# Sentinels survive HTML escaping, so snippets can be escaped first and only
# then decorated with real <mark> tags.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Control characters, NUL included. They cannot appear in a title - those go
# through ``sanitize_plain_text`` - so dropping them from a search term loses
# nothing and keeps a hand-typed "%00" from reaching SQLite as a query it will
# reject.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CREATE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title,
    body,
    document_id UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
)
"""

# Titles only. Trigrams cost roughly three index entries per character, which
# is why the body stays out of it: over a library of Markdown it would multiply
# the database for a question nobody asks of a body.
_CREATE_TITLES_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {TITLES_TABLE} USING fts5(
    title,
    document_id UNINDEXED,
    tokenize = "trigram remove_diacritics 1"
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
        self._titles_by_engine: dict[str, bool] = {}

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

    @property
    def titles_available(self) -> bool:
        """Whether mid-word title matching can be answered on this database."""
        return self._titles_by_engine.get(self._engine_key(), False)

    def _set_titles_available(self, value: bool) -> bool:
        self._titles_by_engine[self._engine_key()] = value
        return value

    def ensure(self, backfill: bool = True) -> bool:
        """Create the virtual tables the backend supports.

        The word index decides whether search is available at all. The trigram
        one is a bonus tracked separately: it needs SQLite 3.34 for the
        tokenizer and 3.45 for its ``remove_diacritics`` option, and losing it
        costs only mid-word matches, never the search itself.

        ``backfill`` is for :meth:`rebuild`, which is about to write every row
        itself and would only pay for the same pass twice.
        """
        if db.engine.dialect.name != "sqlite":
            self._set_titles_available(False)
            return self._set_available(False)

        try:
            db.session.execute(text(_CREATE_SQL))
            db.session.commit()
        except SQLAlchemyError:  # pragma: no cover - depends on the SQLite build
            db.session.rollback()
            logger.warning("Índice FTS5 indisponível; a busca usará LIKE.")
            self._set_titles_available(False)
            return self._set_available(False)

        self._set_available(True)
        try:
            db.session.execute(text(_CREATE_TITLES_SQL))
            db.session.commit()
            self._set_titles_available(True)
        except SQLAlchemyError:  # pragma: no cover - older SQLite builds
            db.session.rollback()
            logger.info(
                "Tokenizador trigram indisponível; a busca não encontrará "
                "trechos no meio de uma palavra."
            )
            self._set_titles_available(False)
            return True

        if backfill:
            self._backfill_titles()
        return True

    def _backfill_titles(self, batch_size: int = 500) -> None:
        """Fill the title index the first time it exists over a full library.

        ``CREATE ... IF NOT EXISTS`` is silent about which of the two it did,
        so an installation that already had documents would come back from the
        upgrade with an empty index and a search quietly missing every
        mid-word match. The check costs one ``LIMIT 1``.

        Written in batches like :meth:`rebuild`, and for the same reason:
        ``yield_per`` bounds what the driver buffers, but collecting the rows
        into one list before inserting them would hold every title in the
        library in memory anyway and undo it.
        """
        from app.models import Document

        try:
            already = db.session.scalar(
                text(f"SELECT 1 FROM {TITLES_TABLE} LIMIT 1")
            )
            if already is not None:
                return

            insert = text(
                f"INSERT INTO {TITLES_TABLE} (title, document_id) "
                "VALUES (:title, :doc_id)"
            )
            rows = db.session.execute(
                db.select(Document.id, Document.title)
                .where(Document.is_deleted.is_(False))
                .execution_options(yield_per=batch_size)
            )

            total = 0
            batch: list[dict[str, object]] = []
            for document_id, title in rows:
                batch.append({"title": title or "", "doc_id": document_id})
                if len(batch) >= batch_size:
                    db.session.execute(insert, batch)
                    total += len(batch)
                    batch = []

            if batch:
                db.session.execute(insert, batch)
                total += len(batch)

            if total:
                logger.info("Índice de títulos preenchido com %s documentos.", total)
            db.session.commit()
        except SQLAlchemyError:  # pragma: no cover
            db.session.rollback()
            logger.warning("Não foi possível preencher o índice de títulos.", exc_info=True)
            self._set_titles_available(False)

    def index_document(self, document) -> None:
        """Insert or refresh one document in both indexes."""
        if self.available:
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

        # Outside the guard above, like the removal below it: the two indexes
        # fail independently, and one of them giving up must never quietly
        # freeze the other on a stale copy of the library.
        self._write_title(document.id, None if document.is_deleted else document.title)

    def remove_document(self, document_id: int) -> None:
        self.remove_documents([document_id])

    def remove_documents(self, document_ids: Sequence[int]) -> None:
        """Drop a whole set of documents from both indexes.

        Two statements for the batch rather than two per document: sending a
        thousand documents to the trash used to be two thousand round trips
        through a SAVEPOINT each. The ``IN`` list is an expanding bind
        parameter - nothing here is ever spliced into the SQL.
        """
        ids = [int(document_id) for document_id in document_ids]
        if not ids:
            return

        if self.available:
            try:
                with db.session.begin_nested():
                    db.session.execute(
                        text(
                            f"DELETE FROM {FTS_TABLE} WHERE document_id IN :doc_ids"
                        ).bindparams(bindparam("doc_ids", expanding=True)),
                        {"doc_ids": ids},
                    )
            except SQLAlchemyError:  # pragma: no cover
                logger.warning("Falha ao remover %s documento(s) do índice.", len(ids))
                self._set_available(False)

        self._remove_titles(ids)

    def _remove_titles(self, document_ids: Sequence[int]) -> None:
        """Drop the same set from the trigram index, failing independently."""
        if not self.titles_available or not document_ids:
            return
        try:
            with db.session.begin_nested():
                db.session.execute(
                    text(
                        f"DELETE FROM {TITLES_TABLE} WHERE document_id IN :doc_ids"
                    ).bindparams(bindparam("doc_ids", expanding=True)),
                    {"doc_ids": list(document_ids)},
                )
        except SQLAlchemyError:  # pragma: no cover
            logger.warning(
                "Falha ao remover %s título(s) do índice de trigramas.",
                len(document_ids),
                exc_info=True,
            )
            self._set_titles_available(False)

    def _write_title(self, document_id: int, title: str | None) -> None:
        """Refresh one title in the trigram index; ``None`` removes it.

        Its own SAVEPOINT, separate from the word index above: the two are
        independent answers to independent questions, and one of them failing
        must not take the other down with it.
        """
        if not self.titles_available:
            return
        try:
            with db.session.begin_nested():
                db.session.execute(
                    text(f"DELETE FROM {TITLES_TABLE} WHERE document_id = :doc_id"),
                    {"doc_id": document_id},
                )
                if title is not None:
                    db.session.execute(
                        text(
                            f"INSERT INTO {TITLES_TABLE} (title, document_id) "
                            "VALUES (:title, :doc_id)"
                        ),
                        {"title": title or "", "doc_id": document_id},
                    )
        except SQLAlchemyError:  # pragma: no cover
            logger.warning(
                "Falha ao indexar o título do documento %s; a busca continua "
                "funcionando por palavra.",
                document_id,
                exc_info=True,
            )
            self._set_titles_available(False)

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
        self._titles_by_engine.pop(self._engine_key(), None)
        if not self.ensure(backfill=False):
            return 0

        titles = self.titles_available

        def flush(batch: list[dict[str, object]]) -> None:
            db.session.execute(insert, batch)
            if titles:
                db.session.execute(
                    insert_title,
                    [
                        {"title": row["title"], "doc_id": row["doc_id"]}
                        for row in batch
                    ],
                )

        try:
            db.session.execute(text(f"DELETE FROM {FTS_TABLE}"))
            if titles:
                db.session.execute(text(f"DELETE FROM {TITLES_TABLE}"))

            insert = text(
                f"INSERT INTO {FTS_TABLE} (title, body, document_id) "
                "VALUES (:title, :body, :doc_id)"
            )
            insert_title = text(
                f"INSERT INTO {TITLES_TABLE} (title, document_id) "
                "VALUES (:title, :doc_id)"
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
                    flush(batch)
                    total += len(batch)
                    batch = []

            if batch:
                flush(batch)
                total += len(batch)

            db.session.commit()
            return total
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Falha ao reconstruir o índice de busca.")
            self._set_available(False)
            self._set_titles_available(False)
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

    @staticmethod
    def build_substring_expression(query: str) -> str:
        """Turn free text into a trigram MATCH expression, or "" if too short.

        The whole phrase is quoted as one string - inner runs of whitespace
        collapsed, so what is typed matches what is displayed - and a literal
        quote is doubled, the FTS5 escape. Against a trigram index that is a
        plain substring search, and nothing the user types can be read as
        query syntax.
        """
        term = " ".join(_CONTROL_RE.sub("", query or "").split())
        if len(term) < MIN_SUBSTRING_LENGTH:
            return ""
        return '"' + term.replace('"', '""') + '"'

    def _title_fragment_ids(self, query: str, limit: int) -> list[int]:
        """Documents whose *title* contains the text, shortest title first.

        Shortest first because a title is a name: among the documents whose
        title contains "elegance", the one actually called "Emerald Elegance"
        is the one being looked for, not the essay that mentions it in a
        subtitle.
        """
        expression = self.build_substring_expression(query)
        if not self.titles_available or not expression:
            return []
        try:
            rows = db.session.execute(
                text(
                    f"SELECT document_id FROM {TITLES_TABLE} "
                    f"WHERE {TITLES_TABLE} MATCH :match "
                    "ORDER BY length(title), document_id LIMIT :limit"
                ),
                {"match": expression, "limit": limit},
            ).all()
        except SQLAlchemyError:  # pragma: no cover - malformed trigram expression
            # A query the engine refused says nothing about whether the index
            # works, so availability is left alone. Marking it dead here meant
            # one odd search term cost every later search its mid-word matches
            # until the process restarted.
            db.session.rollback()
            logger.warning("Consulta de trecho rejeitada; ignorando esta passagem.")
            return []
        return [row[0] for row in rows]

    def search_ids(self, query: str, limit: int = 500) -> list[int] | None:
        """Document ids matching ``query``, best match first.

        Two passes, in the order a reader expects them:

        1. **Titles containing the text**, anywhere inside them. If what was
           typed appears in a title, that document is what was meant - and a
           word index structurally cannot find it mid-word.
        2. **Everything the word index matches**, ranked with the title
           weighted far above the body, so a document *about* the term
           outranks the hundred that merely mention it.

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
                    f"WHERE {FTS_TABLE} MATCH :match "
                    f"ORDER BY bm25({FTS_TABLE}, :title_weight, :body_weight) "
                    "LIMIT :limit"
                ),
                {
                    "match": match,
                    "title_weight": _TITLE_WEIGHT,
                    "body_weight": _BODY_WEIGHT,
                    "limit": limit,
                },
            ).all()
        except SQLAlchemyError:  # pragma: no cover - malformed FTS expression
            db.session.rollback()
            logger.warning("Consulta FTS rejeitada; usando busca por LIKE.")
            return None

        ordered: list[int] = []
        seen: set[int] = set()
        for document_id in self._title_fragment_ids(query, limit):
            if document_id not in seen:
                seen.add(document_id)
                ordered.append(document_id)
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                ordered.append(row[0])
        return ordered[:limit]

    def snippets(self, query: str, document_ids: list[int]) -> dict[int, Markup]:
        """Highlighted excerpts keyed by document id.

        The ``IN`` list is an *expanding* bind parameter rather than a row of
        ``:id0, :id1, …`` built by hand. Both are safe, but hand-built
        placeholders are the only value-shaped interpolation the file would
        contain, and the rule worth having here is the absolute one: nothing
        but a table name is ever spliced into a statement.
        """
        if not self.available or not document_ids:
            return {}
        match = self.build_match_expression(query)
        if not match:
            return {}
        try:
            rows = db.session.execute(
                text(
                    f"SELECT document_id, snippet({FTS_TABLE}, 1, :open, :close, '…', 18) "
                    f"FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :match "
                    "AND document_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {
                    "match": match,
                    "open": _MARK_OPEN,
                    "close": _MARK_CLOSE,
                    "ids": document_ids,
                },
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
