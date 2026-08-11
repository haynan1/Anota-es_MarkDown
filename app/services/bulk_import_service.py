"""Bulk Markdown import.

Two shapes arrive here, and both end up in the same place:

* a multi-file selection of ``.md`` files;
* a ZIP - typically the one :mod:`app.services.bulk_export_service` produced,
  but any archive of Markdown files works.

Three rules shape the whole module:

**Nothing aborts the batch.** A file in the wrong encoding, an entry with a
hostile path, a header with a malformed date - each is counted, described in
the report and stepped over. Importing 400 documents must not fail because
the 87th was written by a broken tool.

**Re-importing is idempotent.** A document whose ``uuid`` already exists is
skipped, not duplicated. Running the same archive twice leaves the library
exactly as the first run did.

**The archive is untrusted.** Member paths, declared sizes and member counts
are all checked before a single byte is read, so an archive cannot traverse
out of itself or expand into a zip bomb.
"""

from __future__ import annotations

import logging
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Document
from app.models.category import MAX_CATEGORY_NAME_LENGTH
from app.repositories.document_repository import DocumentRepository
from app.repositories.taxonomy_repository import CategoryRepository
from app.services import front_matter
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.group_service import GroupService
from app.services.import_service import (
    ImportPreview,
    clean_labels,
    parse_markdown,
    validate_bytes,
)
from app.utils.dates import parse_iso
from app.utils.files import is_safe_archive_member

logger = logging.getLogger(__name__)

ARCHIVE_EXTENSION = ".zip"

# One request, one sitting. Well past a realistic drag-and-drop selection.
MAX_FILES_PER_REQUEST = 500

# Zip-bomb guards, in the same spirit as the backup restore path: the member
# count and the *declared* uncompressed total are both checked before reading.
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024

# Directories produced by macOS and by editors, never by a person.
_IGNORED_DIRECTORIES = {"__MACOSX", "$RECYCLE.BIN"}
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Enough context to act on, short enough that a broken archive cannot flood
# the screen with a warning per file.
MAX_WARNINGS = 20


@dataclass(slots=True)
class BulkImportReport:
    """What a bulk import did, in the terms the user asked the question in."""

    created: int = 0
    #: Already present - same ``uuid`` as a document that is already stored.
    skipped: int = 0
    #: Present in the selection but not Markdown - images, folders, a PDF.
    ignored: int = 0
    #: Markdown that could not be read: wrong encoding, empty, oversized.
    failed: int = 0
    warnings: list[str] = field(default_factory=list)
    #: The first document created, so a single-file import can open it.
    first: Document | None = None

    @property
    def touched(self) -> int:
        return self.created + self.skipped + self.ignored + self.failed

    def note(self, message: str) -> None:
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(message)


# ── Entry point ─────────────────────────────────────────────────────────────


def import_files(
    storages: Sequence[FileStorage], category_id: int | None = None
) -> BulkImportReport:
    """Import every file in ``storages``, expanding any ZIP it finds.

    ``category_id`` overrides whatever the files declare: the user picked a
    destination on the form, and an explicit choice outranks a value stored in
    a header.
    """
    report = BulkImportReport()
    usable = [item for item in storages if item is not None and item.filename]
    if not usable:
        raise ValidationError("Selecione ao menos um arquivo para importar.")

    if len(usable) > MAX_FILES_PER_REQUEST:
        report.note(
            f"Apenas os primeiros {MAX_FILES_PER_REQUEST} arquivos foram processados."
        )
        usable = usable[:MAX_FILES_PER_REQUEST]

    for storage in usable:
        name = PurePath(storage.filename).name
        if PurePath(name).suffix.lower() == ARCHIVE_EXTENSION:
            _import_archive(storage, name, category_id, report)
        else:
            _import_upload(storage, name, category_id, report)

    return report


def _import_upload(
    storage: FileStorage,
    name: str,
    category_id: int | None,
    report: BulkImportReport,
) -> None:
    suffix = PurePath(name).suffix.lower()
    if suffix not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
        report.ignored += 1
        report.note(f"{name}: formato não suportado.")
        return

    raw = storage.read()
    storage.seek(0)
    _import_bytes(name, raw, category_id, report)


def _import_bytes(
    name: str, raw: bytes, category_id: int | None, report: BulkImportReport
) -> None:
    try:
        preview = parse_markdown(name, validate_bytes(raw))
    except ValidationError as error:
        report.failed += 1
        report.note(f"{name}: {error.message}")
        return
    _create_document(preview, category_id, report)


# ── Archives ────────────────────────────────────────────────────────────────


def _import_archive(
    storage: FileStorage,
    name: str,
    category_id: int | None,
    report: BulkImportReport,
) -> None:
    try:
        archive = zipfile.ZipFile(storage)
    except (zipfile.BadZipFile, OSError, ValueError):
        # BadZipFile for a corrupt central directory; OSError/ValueError for a
        # stream this reader cannot seek back through.
        report.failed += 1
        report.note(f"{name}: não é um arquivo ZIP válido.")
        return

    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            report.failed += 1
            report.note(f"{name}: o arquivo contém entradas demais.")
            return

        declared = sum(member.file_size for member in members)
        if declared > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            report.failed += 1
            report.note(f"{name}: o conteúdo descompactado é grande demais.")
            return

        allowed = current_app.config["ALLOWED_IMPORT_EXTENSIONS"]
        for member in members:
            if not _is_importable(member, allowed):
                continue
            _import_member(archive, member, category_id, report)


def _is_importable(member: zipfile.ZipInfo, allowed: set[str]) -> bool:
    """Whether this entry is a Markdown file worth reading.

    Everything rejected here is rejected silently: a ZIP of documents that
    also carries images, a ``.DS_Store`` and a folder structure is normal, and
    reporting each of those as a problem would bury the real ones.

    A ZIP inside the ZIP is one of the things rejected - ``.zip`` is not an
    import extension. That is not an oversight: recursive expansion is how a
    42-kilobyte archive becomes a petabyte, and no legitimate export nests.
    """
    if member.is_dir():
        return False
    path = member.filename.replace("\\", "/")
    parts = path.split("/")
    if any(part in _IGNORED_DIRECTORIES or part.startswith(".") for part in parts):
        return False
    if not is_safe_archive_member(member.filename):
        return False
    return PurePath(path).suffix.lower() in allowed


def _import_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    category_id: int | None,
    report: BulkImportReport,
) -> None:
    name = PurePath(member.filename.replace("\\", "/")).name

    # Measured before it is read. `read()` allocates the whole member, and the
    # aggregate ceiling above would happily allow one 128 MB entry through - so
    # the per-document limit is applied to the declared size first, and the
    # oversized entry is rejected without ever being decompressed. A lie in
    # that field costs nothing: zipfile stops the output at file_size and then
    # fails the CRC, which is the branch below.
    limit = current_app.config["MAX_MARKDOWN_BYTES"]
    if member.file_size > limit:
        report.failed += 1
        report.note(f"{name}: excede o limite de {limit // (1024 * 1024)} MB.")
        return

    try:
        raw = archive.read(member)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
        # Corrupt entry, failed CRC, or an encrypted member (RuntimeError).
        report.failed += 1
        report.note(f"{name}: não foi possível ler ({type(error).__name__}).")
        return
    _import_bytes(name, raw, category_id, report)


# ── Persistence ─────────────────────────────────────────────────────────────


def _create_document(
    preview: ImportPreview, category_id: int | None, report: BulkImportReport
) -> None:
    identifier = front_matter.text_of(preview.fields, "uuid")
    if identifier and DocumentRepository.uuid_exists(identifier):
        report.skipped += 1
        return

    try:
        document = DocumentService.create(
            title=preview.title,
            content_markdown=preview.content_markdown,
            category_id=category_id or _category_from(preview.fields),
            tag_names=preview.tags,
            is_favorite=front_matter.flag_of(preview.fields, "favorite"),
            change_summary=f"Importado de {preview.filename}",
        )
    except (ValidationError, SQLAlchemyError) as error:
        # A single unusable document must not cost the rest of the batch, and
        # a failed flush leaves the session dirty for whatever comes next.
        db.session.rollback()
        logger.exception("Falha ao importar documento de %s", preview.filename)
        report.failed += 1
        report.note(f"{preview.filename}: {_describe(error)}")
        return

    _restore_state(document, preview, identifier, report)

    report.created += 1
    if report.first is None:
        report.first = document


def _restore_state(
    document: Document,
    preview: ImportPreview,
    identifier: str,
    report: BulkImportReport,
) -> None:
    """Apply what the front matter declared beyond title, body and tags.

    Everything here is optional. A plain ``.md`` file with no header reaches
    this function and leaves it untouched - which is why the commit is
    conditional rather than unconditional.
    """
    fields = preview.fields
    changed = False

    if identifier and _UUID_RE.match(identifier):
        document.uuid = identifier
        changed = True
    if front_matter.flag_of(fields, "archived"):
        document.is_archived = True
        changed = True
    if front_matter.flag_of(fields, "locked"):
        document.is_locked = True
        changed = True

    for key, attribute in (("created", "created_at"), ("updated", "updated_at")):
        moment = parse_iso(front_matter.text_of(fields, key))
        if moment is not None:
            # Assigned explicitly so the column's ``onupdate`` does not
            # overwrite it with "now" on the way out.
            setattr(document, attribute, moment)
            changed = True

    if changed:
        try:
            db.session.commit()
        except SQLAlchemyError:
            # The document itself is already committed; only this metadata is
            # lost, so the import stays a success with a note attached.
            db.session.rollback()
            logger.exception("Falha ao aplicar metadados de %s", preview.filename)
            report.note(f"{preview.filename}: metadados não aplicados.")

    # Memberships are applied last and separately: a full group raises, and
    # that must cost the document its groups, not its content.
    groups = front_matter.list_of(fields, "groups")
    if not groups:
        return
    try:
        GroupService.attach_by_names(document, groups)
    except (ValidationError, SQLAlchemyError) as error:
        db.session.rollback()
        logger.exception("Falha ao restaurar grupos de %s", preview.filename)
        report.note(f"{preview.filename}: grupos não aplicados ({_describe(error)})")


def _category_from(fields: dict[str, front_matter.FrontMatterValue]) -> int | None:
    """The category the file asks for, created if it does not exist yet.

    Sanitised first: this name comes out of an untrusted header and becomes a
    row that every listing renders.
    """
    names = clean_labels([front_matter.text_of(fields, "category")], MAX_CATEGORY_NAME_LENGTH)
    if not names:
        return None
    try:
        return CategoryRepository.get_or_create(names[0]).id
    except ValueError:  # pragma: no cover - defensive
        return None


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return error.message
    return "não foi possível salvar o documento."
