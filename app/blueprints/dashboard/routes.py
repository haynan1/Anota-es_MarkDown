from __future__ import annotations

from flask import render_template, request

from app.blueprints.dashboard import dashboard_bp
from app.repositories.document_repository import DocumentRepository
from app.repositories.taxonomy_repository import TagRepository
from app.services.reports_service import PERIODS, build_report_view, resolve_period


@dashboard_bp.route("/")
def index():
    # One filter for the whole reports section, read from the query string so
    # a chosen period survives a reload and can be shared as a link.
    period = resolve_period(request.args.get("periodo"))
    reports = build_report_view(period)

    return render_template(
        "dashboard/index.html",
        stats=DocumentRepository.stats(),
        recent=DocumentRepository.recent(limit=6),
        favorites=DocumentRepository.favorites(limit=5),
        category_usage=DocumentRepository.category_usage(limit=6),
        tag_usage=TagRepository.usage(limit=12),
        reports=reports,
        summary=reports.summary,
        periods=PERIODS,
        current_period=period,
    )
