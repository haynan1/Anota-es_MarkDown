"""Uploads: images, GIFs and videos.

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
* **The stored name is generated.** No part of a request reaches the
  filesystem: the path is ``<year>/<month>/<uuid><ext>`` where the extension
  comes from the signature table, not from the upload.
* **Serving is by database lookup.** The delivery route resolves a UUID to a
  row and reads the recorded path, so a crafted name has nothing to traverse.
"""

from __future__ import annotations

import logging
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


SIGNATURES: tuple[Signature, ...] = (
    Signature(KIND_IMAGE, "image/png", ".png", 0, b"\x89PNG\r\n\x1a\n"),
    Signature(KIND_IMAGE, "image/jpeg", ".jpg", 0, b"\xff\xd8\xff"),
    Signature(KIND_IMAGE, "image/gif", ".gif", 0, b"GIF87a"),
    Signature(KIND_IMAGE, "image/gif", ".gif", 0, b"GIF89a"),
    Signature(KIND_IMAGE, "image/webp", ".webp", 0, b"RIFF", 8, b"WEBP"),
    Signature(KIND_VIDEO, "video/mp4", ".mp4", 4, b"ftyp"),
    Signature(KIND_VIDEO, "video/webm", ".webm", 0, b"\x1aE\xdf\xa3"),
)

# Read far enough to cover the deepest secondary marker.
HEADER_BYTES = 32

def max_bytes_for(kind: str) -> int:
    """Per-kind ceiling, read from configuration."""
    key = "MEDIA_MAX_VIDEO_BYTES" if kind == KIND_VIDEO else "MEDIA_MAX_IMAGE_BYTES"
    return int(current_app.config[key])

ALLOWED_MIME_TYPES = frozenset(signature.mime for signature in SIGNATURES)

# Extensions accepted by the file picker. Purely a convenience filter - the
# decision is always made from the bytes.
PICKER_ACCEPT = ".png,.jpg,.jpeg,.gif,.webp,.mp4,.webm"


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

    signature = identify(payload[:HEADER_BYTES])
    if signature is None:
        raise ValidationError(
            "Formato não suportado. Envie uma imagem (PNG, JPG, GIF, WebP) "
            "ou um vídeo (MP4, WebM)."
        )

    limit = max_bytes_for(signature.kind)
    if len(payload) > limit:
        raise ValidationError(
            f"O arquivo excede o limite de {limit // (1024 * 1024)} MB para "
            f"{'vídeos' if signature.kind == KIND_VIDEO else 'imagens'}."
        )

    # The identifier is generated up front so the storage path is known before
    # the row is inserted - stored_path is NOT NULL, and a two-phase insert
    # would have to write a placeholder first.
    identifier = new_uuid()
    now = utcnow()
    relative = PurePath(f"{now:%Y}") / f"{now:%m}" / f"{identifier}{signature.extension}"
    destination = uploads_root() / relative
    ensure_directory(destination.parent)

    asset = MediaAsset(
        uuid=identifier,
        stored_path=relative.as_posix(),
        original_name=PurePath(storage.filename).name[:255],
        mime_type=signature.mime,
        kind=signature.kind,
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


def markdown_for(asset: MediaAsset, url: str) -> str:
    """The snippet inserted into the editor for a freshly uploaded asset."""
    label = asset.original_name or "arquivo"
    if asset.is_video:
        # Markdown has no video syntax; the sanitizer allows this exact shape.
        return f'<video controls src="{url}" title="{label}"></video>'
    return f"![{label}]({url})"


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
