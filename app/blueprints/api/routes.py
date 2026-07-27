"""JSON endpoints used by the editor.

CSRF protection stays enabled: the browser sends the token in an
``X-CSRFToken`` header, which Flask-WTF validates like any form submission.
"""

from __future__ import annotations

from flask import jsonify, request, url_for

from app.blueprints.api import api_bp
from app.extensions import db
from app.repositories.document_repository import DocumentQuery
from app.services.document_service import DocumentService
from app.services.exceptions import ValidationError
from app.services.group_service import GroupService
from app.services.listing_service import list_documents
from app.services.markdown_service import render_markdown
from app.services.wix_service import render_for_wix
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


@api_bp.post("/texto-rico")
def rich_text():
    """The document as rich text, ready to paste into a Wix description.

    Takes the markdown from the editor rather than the stored document: the
    writer copies what is on screen, including the paragraph typed a second
    ago. The conversion itself lives in ``wix_service``.
    """
    payload = request.get_json(silent=True) or {}
    content = payload.get("content_markdown") or ""

    if not isinstance(content, str):
        raise ValidationError("Conteúdo inválido.")
    if len(content.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise ValidationError("O documento é grande demais para converter.")

    result = render_for_wix(content)
    return jsonify(
        {
            "ok": True,
            "html": result.html,
            "text": result.text,
            "notes": result.notes,
            # The same document cut at its pictures: Wix takes one upload at a
            # time, so the writer pastes, uploads, and comes back for the next.
            "parts": [
                {
                    "html": part.html,
                    "text": part.text,
                    "media": (
                        {
                            "kind": part.media_after.kind,
                            "label": part.media_after.label,
                            "description": part.media_after.description,
                        }
                        if part.media_after
                        else None
                    ),
                }
                for part in result.parts
            ],
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


@api_bp.post("/grupos/<public_uuid>/ordem")
def reorder_group(public_uuid: str):
    """Persist a drag-and-drop reordering of a group.

    The arrow buttons on the page do the same thing with a plain form post;
    this endpoint exists so the pointer gesture does not need a page reload.
    """
    group = GroupService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    uuids = payload.get("uuids")
    if not isinstance(uuids, list) or not all(isinstance(item, str) for item in uuids):
        raise ValidationError("Ordem inválida.")

    total = GroupService.reorder(group, uuids)
    return jsonify({"ok": True, "total": total})


@api_bp.get("/busca")
def search_suggestions():
    """Type-ahead results for the global search field."""
    term = (request.args.get("q") or "").strip()[:120]
    if len(term) < 2:
        return jsonify({"ok": True, "results": []})

    query = DocumentQuery(search=term, per_page=8, page=1, sort="relevance")
    result = list_documents(query, with_snippets=False)

    return jsonify(
        {
            "ok": True,
            "results": [
                {
                    "uuid": document.uuid,
                    "title": document.title,
                    "excerpt": document.excerpt,
                    "url": url_for("editor.edit", public_uuid=document.uuid),
                }
                for document in result.items
            ],
        }
    )
