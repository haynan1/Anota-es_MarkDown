from __future__ import annotations

from flask import render_template

from app.blueprints.dashboard import dashboard_bp
from app.repositories.document_repository import DocumentRepository
from app.repositories.taxonomy_repository import TagRepository


@dashboard_bp.route("/")
def index():
    stats = DocumentRepository.stats()
    return render_template(
        "dashboard/index.html",
        stats=stats,
        recent=DocumentRepository.recent(limit=6),
        favorites=DocumentRepository.favorites(limit=5),
        category_usage=DocumentRepository.category_usage(limit=6),
        tag_usage=TagRepository.usage(limit=12),
    )
