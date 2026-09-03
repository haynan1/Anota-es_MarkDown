from __future__ import annotations

from flask import render_template, request

from app.blueprints.dashboard import dashboard_bp
from app.blueprints.documents.forms import ConfirmForm
from app.repositories.document_repository import DocumentRepository
from app.repositories.taxonomy_repository import TagRepository
from app.services.goal_schedule import day_and_history, today
from app.services.phrase_service import PhraseService
from app.services.progress_service import current_streak
from app.services.reports_service import PERIODS, build_report_view, resolve_period

# Quantas missões do dia cabem no painel antes de ele virar a tela de metas.
TODAY_GOALS_SHOWN = 5


@dashboard_bp.route("/")
def index():
    # One filter for the whole reports section, read from the query string so
    # a chosen period survives a reload and can be shared as a link.
    period = resolve_period(request.args.get("periodo"))
    reports = build_report_view(period)

    # As missões do dia entram no painel porque é aqui que o dia começa. A
    # lista é cortada: o painel diz como está o dia, a esteira é onde se
    # trabalha nele.
    # Uma leitura para as duas perguntas do painel: o que é de hoje, e há
    # quantos dias a sequência está de pé.
    goal_rows, completed = day_and_history(today())
    phrases = PhraseService.all_texts() if PhraseService.enabled() else []

    return render_template(
        "dashboard/index.html",
        goal_rows=goal_rows[:TODAY_GOALS_SHOWN],
        goal_total=len(goal_rows),
        goal_done=sum(1 for row in goal_rows if row.is_done),
        goal_streak=current_streak(completed),
        phrases=phrases,
        phrase=PhraseService.current(phrases) if phrases else "",
        phrase_interval=PhraseService.interval_minutes(),
        confirm_form=ConfirmForm(),
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
