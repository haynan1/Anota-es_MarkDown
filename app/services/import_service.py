"""Markdown import - one file.

Uploads are validated before anything touches the database: extension, size,
and a strict UTF-8 decode (with a BOM-tolerant retry and a clear error message
when the file is in another encoding).

Parsing is split from uploading on purpose. :mod:`app.services.bulk_import_service`
reads members out of a ZIP, where there is no ``FileStorage`` to validate -
only bytes and a name - and it must go through exactly the same parser as a
single upload, or the two paths would drift apart the first time either one
changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.models.document import MAX_TITLE_LENGTH
from app.models.tag import MAX_TAG_NAME_LENGTH
from app.services import front_matter
from app.services.exceptions import ValidationError
from app.services.sanitizer import sanitize_plain_text
from app.utils.text import build_excerpt, count_words

_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)

MAX_TAGS = 20


@dataclass(slots=True)
class ImportPreview:
    title: str
    content_markdown: str
    excerpt: str
    word_count: int
    size_bytes: int
    filename: str
    tags: list[str]
    #: Everything the front matter block declared, including keys this
    #: application does not act on. The bulk importer reads identity, state
    #: and dates out of here; a single upload only needs title and tags.
    fields: dict[str, front_matter.FrontMatterValue] = field(default_factory=dict)


def _title_from_filename(filename: str) -> str:
    stem = PurePath(filename or "").stem or "Documento importado"
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return stem[:MAX_TITLE_LENGTH] or "Documento importado"


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

    return validate_bytes(raw)


def validate_bytes(raw: bytes) -> bytes:
    """Size and shape checks that apply wherever the bytes came from."""
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


def parse_markdown(filename: str, raw: bytes) -> ImportPreview:
    """Turn validated bytes into everything needed to create a document.

    The title is taken from the front matter, then from the first heading,
    then from the filename - in that order, because each is a weaker statement
    of intent than the one before it.

    Labels - title and tags - are stripped of markup here rather than on the
    way to the database. A header is attacker-controlled text: a file someone
    sent you can name its category ``<img src=x onerror=…>`` just as easily as
    "Marketing". Templates escape it, so this is not what stands between the
    file and an XSS, but a label is a label, and the preview screen must show
    the same string that will be stored.
    """
    text = decode_markdown(raw).replace("\r\n", "\n").replace("\r", "\n")
    fields, body = front_matter.parse(text)

    title = sanitize_plain_text(front_matter.text_of(fields, "title"), MAX_TITLE_LENGTH)
    if not title:
        heading = _H1_RE.search(body)
        title = sanitize_plain_text(heading.group(1), MAX_TITLE_LENGTH) if heading else ""
    if not title:
        title = _title_from_filename(filename)

    return ImportPreview(
        title=title,
        content_markdown=body.strip("\n"),
        excerpt=build_excerpt(body),
        word_count=count_words(body),
        size_bytes=len(raw),
        filename=PurePath(filename or "").name or "documento.md",
        tags=clean_labels(front_matter.list_of(fields, "tags"), MAX_TAG_NAME_LENGTH)[:MAX_TAGS],
        fields=fields,
    )


def clean_labels(names: list[str], max_length: int) -> list[str]:
    """Strip markup from a list of taxonomy names, dropping the empties."""
    cleaned = (sanitize_plain_text(name, max_length) for name in names)
    return [name for name in cleaned if name]


def build_preview(storage: FileStorage) -> ImportPreview:
    """Validate and parse an upload without persisting anything.

    Persisting is deliberately not here. One file and four hundred files are
    the same operation with the same rules - identity, duplicates, partial
    failure - and :mod:`app.services.bulk_import_service` owns all of them.
    """
    return parse_markdown(storage.filename, validate_upload(storage))
