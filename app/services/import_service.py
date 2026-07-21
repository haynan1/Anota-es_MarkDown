"""Markdown import.

Uploads are validated before anything touches the database: extension, size,
and a strict UTF-8 decode (with a BOM-tolerant retry and a clear error message
when the file is in another encoding).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.services.exceptions import ValidationError
from app.utils.text import build_excerpt, count_words

_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FM_TITLE_RE = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_FM_TAGS_RE = re.compile(r"^tags\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


@dataclass(slots=True)
class ImportPreview:
    title: str
    content_markdown: str
    excerpt: str
    word_count: int
    size_bytes: int
    filename: str
    tags: list[str]


def _title_from_filename(filename: str) -> str:
    stem = PurePath(filename or "").stem or "Documento importado"
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return stem[:200] or "Documento importado"


def _parse_front_matter(text: str) -> tuple[str | None, list[str], str]:
    """Pull ``title`` / ``tags`` out of a simple YAML front matter block."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return None, [], text

    block = match.group(1)
    title_match = _FM_TITLE_RE.search(block)
    title = title_match.group(1).strip().strip("\"'") if title_match else None

    tags: list[str] = []
    tags_match = _FM_TAGS_RE.search(block)
    if tags_match:
        raw = tags_match.group(1).strip().strip("[]")
        tags = [part.strip().strip("\"'") for part in raw.split(",") if part.strip()]

    return title, tags[:20], text[match.end() :]


def decode_markdown(raw: bytes) -> str:
    """Decode ``raw`` as UTF-8, tolerating a BOM."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError(
        "O arquivo não está em UTF-8. Salve-o novamente com codificação UTF-8 "
        "e tente de novo."
    )


def validate_upload(storage: FileStorage | None) -> bytes:
    """Check the upload and return its bytes."""
    if storage is None or not storage.filename:
        raise ValidationError("Selecione um arquivo Markdown para importar.")

    suffix = PurePath(storage.filename).suffix.lower()
    allowed = current_app.config["ALLOWED_IMPORT_EXTENSIONS"]
    if suffix not in allowed:
        readable = ", ".join(sorted(allowed))
        raise ValidationError(
            f"Formato não suportado. Envie um arquivo com extensão: {readable}."
        )

    raw = storage.read()
    storage.seek(0)

    if not raw:
        raise ValidationError("O arquivo está vazio.")

    limit = current_app.config["MAX_MARKDOWN_BYTES"]
    if len(raw) > limit:
        raise ValidationError(
            f"O arquivo excede o limite de {limit // (1024 * 1024)} MB."
        )

    # A NUL byte means this is a binary file wearing a .md extension.
    if b"\x00" in raw[:4096]:
        raise ValidationError("O arquivo não parece ser um documento de texto.")

    return raw


def build_preview(storage: FileStorage) -> ImportPreview:
    """Validate and parse an upload without persisting anything."""
    raw = validate_upload(storage)
    text = decode_markdown(raw).replace("\r\n", "\n").replace("\r", "\n")

    front_title, tags, body = _parse_front_matter(text)

    title = front_title
    if not title:
        heading = _H1_RE.search(body)
        title = heading.group(1).strip() if heading else None
    if not title:
        title = _title_from_filename(storage.filename)

    return ImportPreview(
        title=title[:200],
        content_markdown=body.strip("\n"),
        excerpt=build_excerpt(body),
        word_count=count_words(body),
        size_bytes=len(raw),
        filename=PurePath(storage.filename).name,
        tags=tags,
    )


def import_document(storage: FileStorage, category_id: int | None = None):
    """Validate an upload and create the document."""
    from app.services.document_service import DocumentService

    preview = build_preview(storage)
    return DocumentService.create(
        title=preview.title,
        content_markdown=preview.content_markdown,
        category_id=category_id,
        tag_names=preview.tags,
        change_summary=f"Importado de {preview.filename}",
    )
