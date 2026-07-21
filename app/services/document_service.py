"""Document lifecycle: create, edit, autosave, duplicate, archive, trash.

Concurrency
-----------
Saves carry the ``revision`` the client last saw. If it no longer matches the
stored one, the write is rejected with :class:`ConflictError` instead of
silently overwriting. This is what stops a delayed autosave response from
clobbering a newer edit.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import PAGE_SIZES, PDF_THEMES, Document
from app.repositories.document_repository import DocumentRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.repositories.version_repository import VersionRepository
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.history_service import HistoryService
from app.services.markdown_service import render_markdown
from app.services.media_service import delete_for_documents
from app.services.sanitizer import sanitize_plain_text
from app.services.search_service import search_index
from app.services.settings_service import SettingsService
from app.utils.dates import as_utc, utcnow
from app.utils.files import safe_slug
from app.utils.text import (
    build_excerpt,
    content_hash,
    count_characters,
    count_words,
    reading_time_minutes,
)

MAX_TITLE_LENGTH = 200
UNTITLED = "Documento sem título"


@dataclass(slots=True)
class SaveResult:
    document: Document
    version_created: bool
    content_changed: bool


class DocumentService:
    """Business rules for documents."""

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def normalize_title(title: str | None) -> str:
        clean = sanitize_plain_text(title or "", max_length=MAX_TITLE_LENGTH)
        return clean or UNTITLED

    @staticmethod
    def build_slug(title: str, exclude_id: int | None = None) -> str:
        base = safe_slug(title, fallback="documento", max_length=180)
        candidate = base
        suffix = 2
        while DocumentRepository.slug_exists(candidate, exclude_id=exclude_id):
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 5000:  # pragma: no cover - defensive
                raise ValidationError("Não foi possível gerar um endereço único.")
        return candidate

    @staticmethod
    def validate_markdown(content: str) -> str:
        text = content or ""
        limit = current_app.config["MAX_MARKDOWN_BYTES"]
        if len(text.encode("utf-8")) > limit:
            raise ValidationError(
                f"O documento excede o tamanho máximo de {limit // (1024 * 1024)} MB."
            )
        return text

    @staticmethod
    def apply_content(document: Document, title: str, markdown_text: str) -> bool:
        """Write title/body onto ``document`` and refresh every derived field.

        Returns ``True`` when the stored content actually changed.
        """
        clean_title = DocumentService.normalize_title(title)
        clean_body = DocumentService.validate_markdown(markdown_text)
        new_hash = content_hash(clean_title, clean_body)
        changed = new_hash != document.content_hash

        document.title = clean_title
        document.content_markdown = clean_body
        document.content_hash = new_hash

        if changed:
            document.rendered_html = render_markdown(clean_body)
            document.excerpt = build_excerpt(clean_body)
            document.word_count = count_words(clean_body)
            document.character_count = count_characters(clean_body)
            document.estimated_reading_time = reading_time_minutes(
                document.word_count, current_app.config["READING_WPM"]
            )
        return changed

    @staticmethod
    def apply_metadata(
        document: Document,
        category_id: int | None = None,
        tag_names: list[str] | None = None,
        is_favorite: bool | None = None,
        page_size: str | None = None,
        pdf_theme: str | None = None,
    ) -> None:
        if category_id is not None:
            document.category = (
                CategoryRepository.get(category_id) if category_id else None
            )
        if tag_names is not None:
            document.tags = TagRepository.resolve_many(tag_names)
        if is_favorite is not None:
            document.is_favorite = bool(is_favorite)
        if page_size is not None and page_size in PAGE_SIZES:
            document.page_size = page_size
        if pdf_theme is not None and pdf_theme in PDF_THEMES:
            document.pdf_theme = pdf_theme

    # ── Creation ────────────────────────────────────────────────────────────

    @staticmethod
    def create(
        title: str = "",
        content_markdown: str = "",
        category_id: int | None = None,
        tag_names: list[str] | None = None,
        is_favorite: bool = False,
        page_size: str | None = None,
        pdf_theme: str | None = None,
        change_summary: str = "Documento criado",
    ) -> Document:
        settings = SettingsService.all()
        document = Document(
            page_size=page_size or settings["pdf_page_size"],
            pdf_theme=pdf_theme or settings["pdf_theme"],
        )
        DocumentService.apply_content(document, title, content_markdown)
        document.slug = DocumentService.build_slug(document.title)

        # Attach to the session before wiring relationships: assigning a
        # category or tags to a transient object makes SQLAlchemy skip the
        # backref bookkeeping and warn about it.
        db.session.add(document)
        DocumentService.apply_metadata(
            document,
            category_id=category_id,
            tag_names=tag_names or [],
            is_favorite=is_favorite,
        )
        db.session.flush()

        HistoryService.snapshot(document, change_summary=change_summary)
        search_index.index_document(document)
        db.session.commit()
        return document

    # ── Editing ─────────────────────────────────────────────────────────────

    @staticmethod
    def save(
        document: Document,
        title: str,
        content_markdown: str,
        expected_revision: int | None = None,
        change_summary: str | None = None,
        create_version: bool = True,
        refresh_slug: bool = False,
    ) -> SaveResult:
        """Persist an edit, guarded by optimistic concurrency control."""
        if expected_revision is not None and expected_revision != document.revision:
            raise ConflictError(
                "Este documento foi alterado em outro lugar depois que você "
                "abriu esta versão.",
                server_state=DocumentService.state_payload(document),
            )

        previous_body = document.content_markdown
        changed = DocumentService.apply_content(document, title, content_markdown)

        if not changed:
            # Touch nothing: no revision bump, no version, no needless write.
            return SaveResult(document=document, version_created=False, content_changed=False)

        if refresh_slug:
            document.slug = DocumentService.build_slug(
                document.title, exclude_id=document.id
            )

        document.revision += 1
        document.updated_at = utcnow()

        version = None
        if create_version:
            version = HistoryService.snapshot(
                document,
                change_summary=change_summary
                or HistoryService.describe_change(previous_body, document.content_markdown),
            )

        search_index.index_document(document)
        db.session.commit()
        return SaveResult(
            document=document, version_created=version is not None, content_changed=True
        )

    @staticmethod
    def state_payload(document: Document) -> dict:
        """Serializable snapshot used by the editor and the autosave endpoint."""
        return {
            "uuid": document.uuid,
            "title": document.title,
            "revision": document.revision,
            "slug": document.slug,
            "content_markdown": document.content_markdown,
            "word_count": document.word_count,
            "character_count": document.character_count,
            "reading_time": document.estimated_reading_time,
            "updated_at": (as_utc(document.updated_at) or utcnow()).isoformat(),
            "version_count": VersionRepository.count(document.id),
        }

    @staticmethod
    def rename(document: Document, title: str) -> Document:
        document.title = DocumentService.normalize_title(title)
        document.slug = DocumentService.build_slug(document.title, exclude_id=document.id)
        document.content_hash = content_hash(document.title, document.content_markdown)
        document.revision += 1
        HistoryService.snapshot(document, change_summary="Documento renomeado")
        search_index.index_document(document)
        db.session.commit()
        return document

    @staticmethod
    def touch_opened(document: Document) -> None:
        """Record that the document was opened.

        Opening a document must not make it look freshly edited in the
        "recently modified" list. ``updated_at`` carries a column-level
        ``onupdate``, which fires on any UPDATE touching this table - including
        Core statements - so it is pinned to its current value explicitly.
        """
        db.session.execute(
            db.update(Document)
            .where(Document.id == document.id)
            .values(last_opened_at=utcnow(), updated_at=document.updated_at)
        )
        db.session.commit()
        db.session.refresh(document)

    # ── State transitions ───────────────────────────────────────────────────

    @staticmethod
    def toggle_favorite(document: Document) -> bool:
        document.is_favorite = not document.is_favorite
        db.session.commit()
        return document.is_favorite

    @staticmethod
    def toggle_lock(document: Document) -> bool:
        """Protect a document from deletion, or release that protection."""
        document.is_locked = not document.is_locked
        db.session.commit()
        return document.is_locked

    @staticmethod
    def ensure_deletable(document: Document) -> None:
        """Raise if the document is locked against removal."""
        if document.is_locked:
            raise ValidationError(
                f"“{document.display_title}” está protegido por cadeado. "
                "Remova a proteção antes de excluir."
            )

    @staticmethod
    def set_archived(document: Document, archived: bool) -> Document:
        document.is_archived = bool(archived)
        db.session.commit()
        return document

    @staticmethod
    def duplicate(document: Document) -> Document:
        copy = Document(
            title=f"{document.title} (cópia)"[:MAX_TITLE_LENGTH],
            content_markdown=document.content_markdown,
            rendered_html=document.rendered_html,
            excerpt=document.excerpt,
            word_count=document.word_count,
            character_count=document.character_count,
            estimated_reading_time=document.estimated_reading_time,
            category_id=document.category_id,
            page_size=document.page_size,
            pdf_theme=document.pdf_theme,
        )
        copy.content_hash = content_hash(copy.title, copy.content_markdown)
        copy.slug = DocumentService.build_slug(copy.title)
        copy.tags = list(document.tags)

        db.session.add(copy)
        db.session.flush()
        HistoryService.snapshot(
            copy, change_summary=f"Duplicado de “{document.title}”", force=True
        )
        search_index.index_document(copy)
        db.session.commit()
        return copy

    # ── Trash ───────────────────────────────────────────────────────────────

    @staticmethod
    def move_to_trash(document: Document) -> Document:
        DocumentService.ensure_deletable(document)
        document.is_deleted = True
        document.deleted_at = utcnow()
        search_index.remove_document(document.id)
        db.session.commit()
        return document

    @staticmethod
    def restore_from_trash(document: Document) -> Document:
        if not document.is_deleted:
            return document
        document.is_deleted = False
        document.deleted_at = None
        # The original slug may have been taken while the document sat in the bin.
        if DocumentRepository.slug_exists(document.slug, exclude_id=document.id):
            document.slug = DocumentService.build_slug(
                document.title, exclude_id=document.id
            )
        search_index.index_document(document)
        db.session.commit()
        return document

    @staticmethod
    def purge(document: Document) -> None:
        """Irreversible delete. Only reachable from the trash screen."""
        if not document.is_deleted:
            raise ValidationError(
                "Só é possível excluir definitivamente documentos que estão na lixeira."
            )
        DocumentService.ensure_deletable(document)
        search_index.remove_document(document.id)
        # Before the row goes: the media foreign key is ON DELETE SET NULL, so
        # afterwards there would be nothing left linking the files to it.
        delete_for_documents([document.id])
        db.session.delete(document)
        db.session.commit()
        TagRepository.delete_orphans()
        db.session.commit()

    @staticmethod
    def empty_trash() -> int:
        """Permanently delete everything in the trash.

        Locked documents are skipped, not deleted: a bulk action must never be
        the one path that bypasses the protection the user asked for.

        Only the ids are loaded: materialising full documents would pull every
        body and rendered HTML into memory just to throw them away. The rows
        go out with a bulk DELETE, and the versions follow via ON DELETE
        CASCADE (enabled by the SQLite foreign-key pragma).
        """
        document_ids = list(
            db.session.scalars(
                db.select(Document.id).where(
                    Document.is_deleted.is_(True),
                    Document.is_locked.is_(False),
                )
            ).all()
        )
        if not document_ids:
            return 0

        for document_id in document_ids:
            search_index.remove_document(document_id)

        delete_for_documents(document_ids)

        db.session.execute(
            db.delete(Document).where(Document.id.in_(document_ids))
        )
        db.session.commit()
        # A bulk DELETE bypasses the identity map. Expire rather than expunge:
        # only the trashed rows are gone, so surviving documents must stay
        # attached to the session - they just need to be re-read.
        db.session.expire_all()

        TagRepository.delete_orphans()
        db.session.commit()
        return len(document_ids)

    # ── Lookup wrappers ─────────────────────────────────────────────────────

    @staticmethod
    def require(public_uuid: str, include_deleted: bool = False) -> Document:
        document = DocumentRepository.get_by_uuid(
            public_uuid, include_deleted=include_deleted
        )
        if document is None:
            raise NotFoundError("Documento não encontrado.")
        return document
