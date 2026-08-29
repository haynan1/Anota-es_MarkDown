"""Bulk Markdown export.

Everything the platform holds leaves as plain ``.md`` files inside a single
ZIP: one file per document, a folder per category so the archive is navigable
in Explorer or Finder rather than only by this application, and a front matter
block carrying what Markdown itself cannot express - identity, taxonomy, state
and dates.

That block is what makes the round trip work: re-importing the archive
restores the same documents, in the same categories and groups, instead of a
pile of untitled files. :mod:`app.services.bulk_import_service` reads exactly
what is written here.

The archive is built into a spooled temporary file, not a ``BytesIO``. A
library of a few thousand documents is ordinary, and "export everything" must
not be the one operation that can exhaust memory: past a few megabytes the
spool moves to disk on its own, and the file is deleted when the response
closes.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import IO

from app.models import Document
from app.repositories.document_repository import DocumentRepository
from app.services import front_matter
from app.services.document_service import MAX_BULK_SELECTION
from app.utils.dates import as_utc, utcnow
from app.utils.files import safe_filename, safe_slug
from app.utils.humanize import format_bytes

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX = "documentos-markdown"

# Past this the archive stops growing in memory and continues on disk.
SPOOL_MAX_BYTES = 8 * 1024 * 1024

# One request may not ask the server to pack an unbounded library. Far above
# any real usage; it exists so a runaway database cannot produce an endless
# response. Applied as a SQL LIMIT, not as a break in the loop - the rows must
# not be loaded in the first place.
#
# What this bounds is the ORM identity map, which grows for the length of the
# request; the archive itself is on disk after the first few megabytes.
MAX_DOCUMENTS = 20_000

# One selection, one ceiling - shared with the listing's other bulk actions so
# that ticking 300 boxes cannot mean 200 documents archived and 300 exported.
MAX_SELECTION = MAX_BULK_SELECTION

# ZIP timestamps are MS-DOS dates and cannot predate 1980.
_MIN_ZIP_YEAR = 1980


@dataclass(slots=True)
class MarkdownArchive:
    """A ready-to-send ZIP. ``stream`` is positioned at the first byte."""

    stream: IO[bytes]
    filename: str
    document_count: int
    byte_size: int
    #: The ceiling was reached, so this archive may not be the whole library.
    #: An export that quietly stops at 20 000 documents and calls itself
    #: "tudo" is the one failure mode of this feature that a user could not
    #: detect on their own.
    truncated: bool = False


# ── One document ────────────────────────────────────────────────────────────


def build_front_matter(document: Document) -> dict[str, front_matter.FrontMatterInput]:
    """The metadata block for ``document``.

    Only what a Markdown file cannot otherwise carry. Word counts, reading
    time and rendered HTML are all derived on import, so writing them here
    would create two sources of truth for the same number.

    Booleans are written only when true: ``archived: false`` on every file is
    noise, and its absence already says the same thing.
    """
    fields: dict[str, front_matter.FrontMatterInput] = {
        "title": document.display_title,
        "uuid": document.uuid,
        "slug": document.slug,
        "category": document.category.name if document.category else None,
        "tags": list(document.tag_names),
        "groups": list(document.group_names),
    }

    if document.is_favorite:
        fields["favorite"] = True
    if document.is_archived:
        fields["archived"] = True
    if document.is_locked:
        fields["locked"] = True

    created = as_utc(document.created_at)
    updated = as_utc(document.updated_at)
    if created:
        fields["created"] = created.isoformat()
    if updated:
        fields["updated"] = updated.isoformat()

    return fields


def to_markdown(document: Document) -> str:
    """``document`` as the text of its ``.md`` file."""
    header = front_matter.dump(build_front_matter(document))
    body = (document.content_markdown or "").strip("\n")
    return f"{header}\n{body}\n" if body else header


# ── Archive layout ──────────────────────────────────────────────────────────


def _member_path(document: Document, used: dict[str, set[str]]) -> str:
    """Where ``document`` lands inside the archive, without ever colliding.

    Two documents may legitimately share a title, and a slug is not a
    filename. Names are therefore de-duplicated per folder, falling back to
    the document's own identifier if a title somehow repeats a thousand times.
    """
    folder = safe_slug(document.category.name, fallback="categoria") if document.category else ""
    name = safe_filename(document.display_title, ".md")
    taken = used.setdefault(folder, set())

    if name in taken:
        stem = name[: -len(".md")]
        for index in range(2, 1000):
            candidate = f"{stem}-{index}.md"
            if candidate not in taken:
                name = candidate
                break
        else:  # pragma: no cover - a thousand documents with one title
            name = f"{stem}-{document.uuid[:8]}.md"

    taken.add(name)
    return f"{folder}/{name}" if folder else name


def _zip_timestamp(value: datetime | None) -> tuple[int, int, int, int, int, int]:
    moment = as_utc(value) or utcnow()
    if moment.year < _MIN_ZIP_YEAR:  # pragma: no cover - defensive
        moment = moment.replace(year=_MIN_ZIP_YEAR, month=1, day=1)
    return (
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
    )


def archive_name(label: str = "") -> str:
    suffix = f"-{safe_slug(label, fallback='')}" if label else ""
    return f"{ARCHIVE_PREFIX}-{utcnow().strftime('%Y%m%d-%H%M%S')}{suffix}.zip"


# ── Public API ──────────────────────────────────────────────────────────────


def build_archive(
    documents: Iterable[Document], label: str = "", limit: int = MAX_DOCUMENTS
) -> MarkdownArchive:
    """Pack ``documents`` into a ZIP of Markdown files."""
    stream: IO[bytes] = SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES, suffix=".zip")
    used: dict[str, set[str]] = {}
    count = 0

    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            info = zipfile.ZipInfo(
                _member_path(document, used),
                date_time=_zip_timestamp(document.updated_at),
            )
            # A ZipInfo built by hand defaults to ZIP_STORED, ignoring the
            # compression the archive was opened with.
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, to_markdown(document).encode("utf-8"))
            count += 1

    byte_size = stream.tell()
    stream.seek(0)

    # Logged because "the export is enormous" and "the export is empty" are
    # the two support questions this feature generates, and neither leaves a
    # trace anywhere else - the archive is streamed out and never stored.
    truncated = count >= limit
    logger.info(
        "Exportação Markdown: %s documento(s), %s", count, format_bytes(byte_size)
    )
    if truncated:
        logger.warning(
            "Exportação Markdown atingiu o teto de %s documentos; o pacote "
            "pode não conter a biblioteca inteira.",
            limit,
        )

    return MarkdownArchive(
        stream=stream,
        filename=archive_name(label),
        document_count=count,
        byte_size=byte_size,
        truncated=truncated,
    )


def export_all() -> MarkdownArchive:
    """Every live document, archived ones included, trash excluded.

    The trash is deliberately left out: those documents were deleted, and an
    export that quietly resurrects them on the next import would be a
    surprise. A full backup remains the way to carry the bin.
    """
    return build_archive(DocumentRepository.iter_for_export(limit=MAX_DOCUMENTS))


def export_selection(
    document_ids: Sequence[int], limit: int = MAX_SELECTION
) -> MarkdownArchive:
    """The documents the listing had selected, in the archive's own order.

    Primary keys, because the selection is resolved once - by
    :mod:`app.services.selection_service` - and every bulk action reads the
    same resolved set. ``limit`` travels with it: ticking boxes and asking for
    "every result" are bounded differently, and the archive must not apply the
    smaller of the two to a selection built the other way.
    """
    wanted = list(dict.fromkeys(document_ids))[:limit]
    if not wanted:
        return build_archive([], label="selecao", limit=limit)
    return build_archive(
        DocumentRepository.iter_for_export(limit=limit, ids=wanted),
        label="selecao",
        limit=limit,
    )
