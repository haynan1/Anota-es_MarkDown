"""JSON endpoints used by the editor.

CSRF protection stays enabled: the browser sends the token in an
``X-CSRFToken`` header, which Flask-WTF validates like any form submission.
"""

from __future__ import annotations

from flask import jsonify, request

from app.blueprints.api import api_bp
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.markdown_service import render_markdown
from app.utils.text import (
    build_excerpt,
    count_characters,
    count_words,
    reading_time_minutes,
)

MAX_PREVIEW_BYTES = 2 * 1024 * 1024


@api_bp.post("/preview")
def preview():
    """Render markdown for the live preview.

    The preview goes through the same renderer and sanitizer as everything
    else, so what the user sees is exactly what will be stored and printed.
    """
    payload = request.get_json(silent=True) or {}
    content = payload.get("content_markdown") or ""

    if not isinstance(content, str):
        raise ValidationError("Conteúdo inválido.")
    if len(content.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise ValidationError("O documento é grande demais para a pré-visualização.")

    words = count_words(content)
    return jsonify(
        {
            "ok": True,
            "html": render_markdown(content),
            "word_count": words,
            "character_count": count_characters(content),
            "reading_time": reading_time_minutes(words),
            "excerpt": build_excerpt(content, limit=160),
        }
    )


@api_bp.post("/documentos/<public_uuid>/autosave")
def autosave(public_uuid: str):
    """Persist an edit from the editor, guarded by optimistic locking."""
    document = DocumentService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    title = payload.get("title")
    content = payload.get("content_markdown")
    if not isinstance(title, str) or not isinstance(content, str):
        raise ValidationError("Dados de salvamento inválidos.")

    revision = payload.get("revision")
    expected = revision if isinstance(revision, int) else None

    result = DocumentService.save(
        document,
        title=title,
        content_markdown=content,
        expected_revision=expected,
        refresh_slug=bool(payload.get("refresh_slug")),
    )

    return jsonify(
        {
            "ok": True,
            "saved": result.content_changed,
            "version_created": result.version_created,
            **DocumentService.state_payload(result.document),
        }
    )


@api_bp.post("/documentos/<public_uuid>/metadados")
def update_metadata(public_uuid: str):
    """Update category, tags, favourite flag and PDF preferences."""
    from app.extensions import db

    document = DocumentService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    tags = payload.get("tags")
    DocumentService.apply_metadata(
        document,
        category_id=payload.get("category_id") if "category_id" in payload else None,
        tag_names=(
            [tag for tag in tags if isinstance(tag, str)][:20]
            if isinstance(tags, list)
            else None
        ),
        is_favorite=payload.get("is_favorite"),
        page_size=payload.get("page_size"),
        pdf_theme=payload.get("pdf_theme"),
    )
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "tags": document.tag_names,
            "category_id": document.category_id,
            "is_favorite": document.is_favorite,
            "page_size": document.page_size,
            "pdf_theme": document.pdf_theme,
        }
    )


@api_bp.get("/busca")
def search_suggestions():
    """Type-ahead results for the global search field."""
    term = (request.args.get("q") or "").strip()[:120]
    if len(term) < 2:
        return jsonify({"ok": True, "results": []})

    from app.repositories.document_repository import DocumentQuery

    query = DocumentQuery(search=term, per_page=8, page=1, sort="relevance")
    pagination = DocumentRepository.paginate(query)

    return jsonify(
        {
            "ok": True,
            "results": [
                {
                    "uuid": document.uuid,
                    "title": document.title,
                    "excerpt": document.excerpt,
                    "url": f"/editor/{document.uuid}",
                }
                for document in pagination.items
            ],
        }
    )
