"""Uploads: images, GIFs, videos and file attachments.

Threat model
------------
An uploaded file is the most dangerous input this application accepts, so the
rules here are deliberately narrow:

* **Type is decided by content, never by the client.** The declared MIME type
  and the filename extension are both ignored for the decision; the leading
  bytes are matched against a table of signatures. A ``.png`` that is really
  an HTML file is rejected.
* **SVG is not accepted at all.** It is a script-bearing document format, and
  serving one same-origin is stored XSS regardless of sanitisation elsewhere.
* **Only images and videos are ever served inline.** Everything else - PDFs,
  Office documents, archives, text - is delivered with
  ``Content-Disposition: attachment``, so the browser downloads it instead of
  interpreting it in our origin. Nothing an attachment contains can execute
  as this application.
* **The stored name is generated.** No part of a request reaches the
  filesystem: the path is ``<year>/<month>/<uuid><ext>`` where the extension
  comes from the signature table, not from the upload.
* **Serving is by database lookup.** The delivery route resolves a UUID to a
  row and reads the recorded path, so a crafted name has nothing to traverse.

Plain text is the one format with no signature to match - a ``.csv`` and a
``.txt`` are both just bytes. It is admitted under two conditions that together
keep the guarantee above intact: the content must decode as UTF-8 with no NUL
bytes, and the *extension* must be one of a short list of inert text types.
The extension only picks a label; the file is stored as text, served as
``text/plain`` and always downloaded, never rendered. An ``.html`` or ``.js``
extension is not on the list and therefore never reaches disk.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePath

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import MediaAsset
from app.models.media_asset import new_uuid
from app.services.exceptions import NotFoundError, ValidationError
from app.utils.dates import utcnow
from app.utils.files import ensure_directory

logger = logging.getLogger(__name__)

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_FILE = "file"

# Only these two are ever handed to the browser inline. See the module docstring.
INLINE_KINDS = frozenset({KIND_IMAGE, KIND_VIDEO})

KIND_LABELS = {KIND_IMAGE: "imagens", KIND_VIDEO: "vídeos", KIND_FILE: "arquivos"}


@dataclass(frozen=True, slots=True)
class Signature:
    kind: str
    mime: str
    extension: str
    offset: int
    magic: bytes
    # Some containers need a second marker further in (RIFF/WEBP, ISO-BMFF).
    secondary_offset: int | None = None
    secondary: bytes | None = None

    def matches(self, head: bytes) -> bool:
        if head[self.offset : self.offset + len(self.magic)] != self.magic:
            return False
        if self.secondary is None:
            return True
        start = self.secondary_offset or 0
        return head[start : start + len(self.secondary)] == self.secondary


@dataclass(frozen=True, slots=True)
class Detected:
    """What an upload turned out to be, decided from its bytes."""

    kind: str
    mime: str
    extension: str


SIGNATURES: tuple[Signature, ...] = (
    # ── Inline media ────────────────────────────────────────────────────────
    Signature(KIND_IMAGE, "image/png", ".png", 0, b"\x89PNG\r\n\x1a\n"),
    Signature(KIND_IMAGE, "image/jpeg", ".jpg", 0, b"\xff\xd8\xff"),
    Signature(KIND_IMAGE, "image/gif", ".gif", 0, b"GIF87a"),
    Signature(KIND_IMAGE, "image/gif", ".gif", 0, b"GIF89a"),
    Signature(KIND_IMAGE, "image/webp", ".webp", 0, b"RIFF", 8, b"WEBP"),
    Signature(KIND_VIDEO, "video/mp4", ".mp4", 4, b"ftyp"),
    Signature(KIND_VIDEO, "video/webm", ".webm", 0, b"\x1aE\xdf\xa3"),
    # ── Attachments: downloaded, never interpreted ──────────────────────────
    Signature(KIND_FILE, "application/pdf", ".pdf", 0, b"%PDF-"),
    # Every OOXML and OpenDocument file is a ZIP; _refine_zip narrows it down.
    Signature(KIND_FILE, "application/zip", ".zip", 0, b"PK\x03\x04"),
    Signature(KIND_FILE, "application/zip", ".zip", 0, b"PK\x05\x06"),  # empty
    Signature(KIND_FILE, "application/x-7z-compressed", ".7z", 0, b"7z\xbc\xaf\x27\x1c"),
    Signature(KIND_FILE, "application/vnd.rar", ".rar", 0, b"Rar!\x1a\x07"),
    Signature(KIND_FILE, "application/gzip", ".gz", 0, b"\x1f\x8b"),
    Signature(KIND_FILE, "application/rtf", ".rtf", 0, b"{\\rtf1"),
    # Legacy Office (.doc/.xls/.ppt) share one OLE2 container. Telling them
    # apart needs a compound-file parser; the original filename already gives
    # the user the right extension back on download, so it is not worth one.
    Signature(
        KIND_FILE,
        "application/x-ole-storage",
        ".ole",
        0,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    ),
    Signature(KIND_FILE, "audio/mpeg", ".mp3", 0, b"ID3"),
    Signature(KIND_FILE, "audio/wav", ".wav", 0, b"RIFF", 8, b"WAVE"),
    Signature(KIND_FILE, "audio/ogg", ".ogg", 0, b"OggS"),
)

# Read far enough to cover the deepest secondary marker.
HEADER_BYTES = 32

# ── ZIP refinement ──────────────────────────────────────────────────────────
# OpenDocument and EPUB declare themselves in an uncompressed "mimetype" member.
_ZIP_DECLARED_MIMES: dict[str, str] = {
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.oasis.opendocument.graphics": ".odg",
    "application/epub+zip": ".epub",
}

# OOXML has no declared mimetype; the part prefix identifies the application.
_OOXML_PREFIXES: tuple[tuple[str, str, str], ...] = (
    (
        "word/",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    (
        "xl/",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    (
        "ppt/",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
)

# A refinement pass must not become a way to make the server do work: only the
# beginning of the member list is inspected, and only one tiny member is read.
_ZIP_MEMBERS_INSPECTED = 64
_ZIP_MIMETYPE_MAX = 128

# ── Plain text ──────────────────────────────────────────────────────────────
# Inert, human-readable formats. Deliberately excludes every extension a
# browser or shell would execute (.html, .svg, .js, .bat, .ps1, .sh …).
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json",
        ".log", ".yml", ".yaml", ".ini", ".rst", ".srt", ".vtt",
    }
)
TEXT_MIME = "text/plain"

# Human labels for the attachment card. Anything absent falls back to the
# uppercased extension, which is always meaningful ("PDF", "DOCX", "ZIP").
TYPE_LABELS: dict[str, str] = {
    "application/pdf": "PDF",
    "application/zip": "Arquivo compactado",
    "application/x-7z-compressed": "Arquivo compactado",
    "application/vnd.rar": "Arquivo compactado",
    "application/gzip": "Arquivo compactado",
    "application/rtf": "Documento RTF",
    "application/x-ole-storage": "Documento do Office",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "Documento do Word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        "Planilha do Excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        "Apresentação do PowerPoint",
    "application/vnd.oasis.opendocument.text": "Documento OpenDocument",
    "application/vnd.oasis.opendocument.spreadsheet": "Planilha OpenDocument",
    "application/vnd.oasis.opendocument.presentation": "Apresentação OpenDocument",
    "application/vnd.oasis.opendocument.graphics": "Desenho OpenDocument",
    "application/epub+zip": "Livro EPUB",
    "audio/mpeg": "Áudio",
    "audio/wav": "Áudio",
    "audio/ogg": "Áudio",
    TEXT_MIME: "Texto",
    "image/png": "Imagem PNG",
    "image/jpeg": "Imagem JPEG",
    "image/gif": "Imagem GIF",
    "image/webp": "Imagem WebP",
    "video/mp4": "Vídeo MP4",
    "video/webm": "Vídeo WebM",
}


def max_bytes_for(kind: str) -> int:
    """Per-kind ceiling, read from configuration."""
    key = {
        KIND_VIDEO: "MEDIA_MAX_VIDEO_BYTES",
        KIND_FILE: "MEDIA_MAX_FILE_BYTES",
    }.get(kind, "MEDIA_MAX_IMAGE_BYTES")
    return int(current_app.config[key])


ALLOWED_MIME_TYPES = frozenset(
    (
        *(signature.mime for signature in SIGNATURES),
        *_ZIP_DECLARED_MIMES,
        *(mime for _, mime, _ in _OOXML_PREFIXES),
        TEXT_MIME,
    )
)

# Extensions offered by the file picker. Purely a convenience filter - the
# decision is always made from the bytes. Legacy Office extensions are listed
# because an OLE2 container is accepted; ".ole" itself is never typed by anyone.
PICKER_EXTENSIONS: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp4", ".webm",
    ".pdf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".odg", ".epub", ".rtf",
    ".zip", ".7z", ".rar", ".gz",
    ".mp3", ".wav", ".ogg",
    *sorted(TEXT_EXTENSIONS),
)
PICKER_ACCEPT = ",".join(PICKER_EXTENSIONS)

UNSUPPORTED_MESSAGE = (
    "Formato não suportado. Envie imagens (PNG, JPG, GIF, WebP), vídeos "
    "(MP4, WebM), documentos (PDF, Word, Excel, PowerPoint, OpenDocument, "
    "RTF, EPUB), áudio (MP3, WAV, OGG), arquivos compactados (ZIP, 7Z, RAR) "
    "ou texto (TXT, MD, CSV, JSON, YAML)."
)


def uploads_root() -> Path:
    return ensure_directory(Path(current_app.config["UPLOAD_DIR"]))


def enforce_content_length(limit_bytes: int) -> None:
    """Reject an oversized request before its body is parsed.

    The global Werkzeug ceiling has to accommodate the largest upload the app
    accepts (a video). Endpoints that take something much smaller check the
    declared Content-Length first, so a 100 MB body aimed at the ``.md``
    importer is refused without being buffered.
    """
    from flask import request

    declared = request.content_length
    if declared is not None and declared > limit_bytes:
        raise ValidationError(
            f"O envio excede o limite de {limit_bytes // (1024 * 1024)} MB "
            "para este tipo de arquivo."
        )


def identify(head: bytes) -> Signature | None:
    """Return the signature matching ``head``, or ``None`` if unrecognised."""
    for signature in SIGNATURES:
        if signature.matches(head):
            return signature
    return None


def _refine_zip(payload: bytes) -> Detected:
    """Narrow a ZIP container down to the document format it really is.

    A ``.docx`` and a ``.zip`` are byte-for-byte the same kind of file at the
    signature level; the difference is what is inside. Nothing here can fail
    the upload - an unreadable or unrecognised archive is simply stored as a
    plain ZIP, which is what it is.
    """
    fallback = Detected(KIND_FILE, "application/zip", ".zip")

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()[:_ZIP_MEMBERS_INSPECTED]

            if "mimetype" in names:
                with archive.open("mimetype") as member:
                    declared = member.read(_ZIP_MIMETYPE_MAX).decode(
                        "ascii", "ignore"
                    ).strip()
                extension = _ZIP_DECLARED_MIMES.get(declared)
                if extension:
                    return Detected(KIND_FILE, declared, extension)

            if "[Content_Types].xml" in names:
                for prefix, mime, extension in _OOXML_PREFIXES:
                    if any(name.startswith(prefix) for name in names):
                        return Detected(KIND_FILE, mime, extension)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        return fallback

    return fallback


def _is_inert_text(payload: bytes) -> bool:
    """True when ``payload`` is UTF-8 text with no binary content."""
    if b"\x00" in payload:
        return False
    try:
        payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def text_extension_of(filename: str) -> str | None:
    """The text extension claimed by ``filename``, if it is one we accept."""
    extension = PurePath(filename or "").suffix.lower()
    return extension if extension in TEXT_EXTENSIONS else None


def detect(payload: bytes, filename: str = "") -> Detected | None:
    """Decide what ``payload`` is, or ``None`` if it is not accepted."""
    signature = identify(payload[:HEADER_BYTES])

    if signature is not None:
        if signature.mime == "application/zip":
            return _refine_zip(payload)
        return Detected(signature.kind, signature.mime, signature.extension)

    extension = text_extension_of(filename)
    if extension and _is_inert_text(payload):
        return Detected(KIND_FILE, TEXT_MIME, extension)

    return None


_UNSAFE_LABEL_RE = re.compile(r"[\x00-\x1f\x7f\"\\]")


def clean_original_name(filename: str) -> str:
    """The label kept for display and for the download filename.

    Path components are discarded and control characters removed: this string
    ends up inside a ``Content-Disposition`` header and in rendered HTML, and
    must not be able to inject either.
    """
    name = _UNSAFE_LABEL_RE.sub("", PurePath(filename or "").name).strip()
    return name[:255] or "arquivo"


def _read_upload(storage: FileStorage) -> bytes:
    storage.stream.seek(0)
    payload = storage.stream.read()
    storage.stream.seek(0)
    return payload


def store_upload(
    storage: FileStorage | None, document_id: int | None = None
) -> MediaAsset:
    """Validate an upload and persist it. Returns the saved asset."""
    if storage is None or not storage.filename:
        raise ValidationError("Nenhum arquivo foi enviado.")

    payload = _read_upload(storage)
    if not payload:
        raise ValidationError("O arquivo está vazio.")

    detected = detect(payload, storage.filename)
    if detected is None:
        if text_extension_of(storage.filename):
            # The extension is one we accept, so the bytes are the problem:
            # say so instead of claiming the format is unsupported.
            raise ValidationError(
                "Este arquivo de texto não está em UTF-8 ou contém dados "
                "binários. Salve-o novamente como UTF-8 e tente de novo."
            )
        raise ValidationError(UNSUPPORTED_MESSAGE)

    limit = max_bytes_for(detected.kind)
    if len(payload) > limit:
        raise ValidationError(
            f"O arquivo excede o limite de {limit // (1024 * 1024)} MB para "
            f"{KIND_LABELS[detected.kind]}."
        )

    # The identifier is generated up front so the storage path is known before
    # the row is inserted - stored_path is NOT NULL, and a two-phase insert
    # would have to write a placeholder first.
    identifier = new_uuid()
    now = utcnow()
    relative = PurePath(f"{now:%Y}") / f"{now:%m}" / f"{identifier}{detected.extension}"
    destination = uploads_root() / relative
    ensure_directory(destination.parent)

    asset = MediaAsset(
        uuid=identifier,
        stored_path=relative.as_posix(),
        original_name=clean_original_name(storage.filename),
        mime_type=detected.mime,
        kind=detected.kind,
        size_bytes=len(payload),
        document_id=document_id,
    )
    db.session.add(asset)

    try:
        destination.write_bytes(payload)
    except OSError as exc:
        db.session.rollback()
        logger.exception("Falha ao gravar o upload")
        raise ValidationError("Não foi possível salvar o arquivo enviado.") from exc

    try:
        db.session.commit()
    except Exception:  # noqa: BLE001 - must not leave the file orphaned
        db.session.rollback()
        destination.unlink(missing_ok=True)
        raise

    return asset


def get_asset(public_uuid: str) -> MediaAsset:
    asset = db.session.scalars(
        db.select(MediaAsset).where(MediaAsset.uuid == public_uuid)
    ).one_or_none()
    if asset is None:
        raise NotFoundError("Arquivo não encontrado.")
    return asset


def asset_path(asset: MediaAsset) -> Path:
    """Absolute path of a stored asset, re-checked against the uploads root.

    ``stored_path`` is written by this module and never by a request, but the
    containment check stays: a bug or a doctored database row must not become
    an arbitrary file read.
    """
    root = uploads_root().resolve()
    resolved = (root / asset.stored_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:  # pragma: no cover - defensive
        raise NotFoundError("Arquivo não encontrado.") from exc
    if not resolved.is_file():
        raise NotFoundError("Arquivo não encontrado.")
    return resolved


# ── Presentation ────────────────────────────────────────────────────────────

_BADGE_RE = re.compile(r"^[A-Za-z0-9]{1,5}$")


def badge_for(asset: MediaAsset) -> str:
    """Short uppercase tag shown on the attachment card: PDF, DOCX, XLS…

    Taken from the original filename when it is a plain extension - that is
    what the user recognises, and it is the only way a legacy ``.xls`` reads
    as a spreadsheet rather than as the OLE container it shares with ``.doc``.
    Falls back to the stored extension, which this module generated.
    """
    claimed = PurePath(asset.original_name or "").suffix.lstrip(".")
    if _BADGE_RE.match(claimed):
        return claimed.upper()
    return PurePath(asset.stored_path or "").suffix.lstrip(".").upper() or "ARQUIVO"


def label_for(asset: MediaAsset) -> str:
    """Human description of the asset's type: "PDF", "Planilha do Excel"…"""
    return TYPE_LABELS.get(asset.mime_type) or badge_for(asset)


def _escape_link_text(value: str) -> str:
    """Keep a filename from breaking the markdown link it sits inside."""
    return value.replace("\\", "").replace("[", r"\[").replace("]", r"\]")


def markdown_for(asset: MediaAsset, url: str) -> str:
    """The snippet inserted into the editor for a freshly uploaded asset."""
    label = asset.original_name or "arquivo"
    if asset.is_video:
        # Markdown has no video syntax; the sanitizer allows this exact shape.
        return f'<video controls src="{url}" title="{label}"></video>'
    if asset.kind == KIND_FILE:
        # A plain link, on purpose: it survives export, it still means
        # something in any other markdown tool, and the renderer turns it into
        # a card by looking the UUID up - no syntax for the writer to learn.
        return f"[{_escape_link_text(label)}]({url})"
    return f"![{_escape_link_text(label)}]({url})"


def delete_asset(asset: MediaAsset, commit: bool = True) -> None:
    """Remove the row and the file behind it."""
    _unlink(asset)
    db.session.delete(asset)
    if commit:
        db.session.commit()


def _unlink(asset: MediaAsset) -> None:
    try:
        asset_path(asset).unlink(missing_ok=True)
    except (NotFoundError, OSError):
        # A row without a file is still worth deleting; the file is the copy.
        logger.warning("Arquivo de mídia ausente ao remover %s", asset.uuid)


def delete_for_documents(document_ids: list[int]) -> int:
    """Delete the media belonging to documents that are being purged.

    The foreign key is ``ON DELETE SET NULL``, so without this the rows would
    survive their document as orphans and the files would stay on disk
    indefinitely — storage that only grows, holding content the user believes
    they deleted.

    The caller commits; this runs inside the same transaction as the purge.
    """
    if not document_ids:
        return 0

    assets = db.session.scalars(
        db.select(MediaAsset).where(MediaAsset.document_id.in_(document_ids))
    ).all()

    for asset in assets:
        _unlink(asset)
        db.session.delete(asset)

    return len(assets)


def prune_orphans(max_age_hours: int = 24) -> tuple[int, int]:
    """Delete assets no document references any more.

    An upload that was never saved into a document — or whose reference was
    edited away — has no owner. The age guard keeps a file that was uploaded
    seconds ago, while its editor tab is still open and unsaved, from being
    swept up.

    Returns ``(rows_removed, files_removed)``.
    """
    from app.models import Document

    cutoff = utcnow() - timedelta(hours=max_age_hours)
    candidates = db.session.scalars(
        db.select(MediaAsset).where(MediaAsset.created_at < cutoff)
    ).all()
    if not candidates:
        return (0, 0)

    # One pass over the corpus rather than a LIKE per asset. Documents in the
    # trash count as references: the trash is reversible, and pruning their
    # media would restore a document with broken images. Only a purge — which
    # calls delete_for_documents directly — ends a reference.
    bodies = db.session.scalars(db.select(Document.content_markdown)).all()
    referenced = {
        asset.uuid
        for asset in candidates
        if any(asset.uuid in (body or "") for body in bodies)
    }

    removed_files = 0
    for asset in candidates:
        if asset.uuid in referenced:
            continue
        before = asset_exists(asset)
        _unlink(asset)
        removed_files += 1 if before else 0
        db.session.delete(asset)

    rows = len(candidates) - len(referenced)
    db.session.commit()
    return (rows, removed_files)


def asset_exists(asset: MediaAsset) -> bool:
    try:
        return asset_path(asset).is_file()
    except NotFoundError:
        return False
