"""Metas: o dia, a esteira, o plano, o histórico e as conquistas.

Duas áreas de endereço, uma funcionalidade. ``/metas`` é HTML que funciona com
um formulário e um recarregar de página; ``/api/metas`` é a esteira falando
enquanto um cartão é arrastado - e o Flask-WTF valida o ``X-CSRFToken`` dessas
chamadas exatamente como validaria um POST de formulário.

A divisão não é cosmética: tudo que se faz arrastando também se faz por um
botão que envia um formulário. Concluir, avançar de coluna, trazer para hoje,
apagar - nenhum desses gestos existe só no ponteiro, porque um teclado, um
leitor de tela e um navegador sem JavaScript precisam chegar aos mesmos
lugares.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.blueprints.documents.forms import ConfirmForm
from app.blueprints.goals import goals_bp
from app.blueprints.goals.forms import (
    DOCUMENT_CHOICE_EMPTY,
    ActivateTemplateForm,
    GoalForm,
    GoalTemplateForm,
    PhraseForm,
    PhraseSettingsForm,
)
from app.models.goal import (
    CATEGORY_ICONS,
    CATEGORY_LABELS,
    GOAL_CATEGORIES,
    GOAL_PRIORITIES,
    GOAL_STATUSES,
    PRIORITY_LABELS,
    RECURRENCE_LABELS,
    STATUS_DONE,
    STATUS_LABELS,
)
from app.repositories.goal_repository import GoalRepository
from app.repositories.phrase_repository import PhraseRepository
from app.services.achievement_service import AchievementService
from app.services.chart_geometry import build_bar_chart, build_line_chart
from app.services.exceptions import ValidationError
from app.services.goal_schedule import (
    Occurrence,
    rows_between,
    rows_for_day,
    sort_rows,
    today,
)
from app.services.goal_service import GoalService
from app.services.phrase_service import (
    DEFAULT_PHRASES,
    PHRASE_INTERVALS,
    PhraseService,
)
from app.services.progress_service import build_progress
from app.services.settings_service import SettingsService

# Quanto o acervo mostra de uma vez. Um ano para trás cobre o histórico que
# alguém revisita; um mês para a frente cobre o que já foi planejado.
LIST_PAST_DAYS = 365
LIST_FUTURE_DAYS = 30

# O backlog na esteira é uma amostra, não o acervo: a esteira é sobre hoje, e
# uma coluna com trezentos cartões deixa de ser um fluxo de trabalho.
BACKLOG_PREVIEW = 10

# Quantos dias o gráfico do histórico desenha.
HISTORY_DAYS = 14

# Teto de linhas no acervo. Com uma linha por meta, uma jornada real não chega
# perto disto; o limite existe para que a tela tenha um custo declarado mesmo
# diante de um banco que alguém encheu. Quem passar daqui filtra.
MAX_LIST_ROWS = 400

WEEKDAY_NAMES = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


# ── Páginas ─────────────────────────────────────────────────────────────────


@goals_bp.get("/metas/")
def index():
    """O acervo: uma linha por meta, com os filtros que a pessoa escolheu.

    *Por meta*, e não por dia. Esta é a distinção que separa esta tela da
    esteira e do plano: ali um hábito diário é trinta coisas a fazer, aqui ele
    é uma coisa que você tem. Listar as ocorrências faria um único hábito
    ocupar um ano de linhas sozinho, empurrar todo o resto para fora da tela e
    custar meio segundo de renderização por hábito.
    """
    reference = today()
    rows = rows_between(
        reference - timedelta(days=LIST_PAST_DAYS),
        reference + timedelta(days=LIST_FUTURE_DAYS),
        include_undated=True,
    )

    status = _one_of(request.args.get("situacao"), GOAL_STATUSES)
    priority = _one_of(request.args.get("prioridade"), GOAL_PRIORITIES)
    category = _one_of(request.args.get("categoria"), GOAL_CATEGORIES)
    # Filtrar antes de recolher: escolhida a linha da série e só depois
    # aplicado o filtro, uma busca por "concluídas" mostraria o dia pendente
    # que foi escolhido para representá-la.
    rows = [
        row
        for row in rows
        if (not status or row.status == status)
        and (not priority or row.goal.priority == priority)
        and (not category or row.goal.category == category)
    ]

    rows = _one_row_per_goal(rows, reference)
    truncated = len(rows) > MAX_LIST_ROWS

    return render_template(
        "goals/index.html",
        rows=rows[:MAX_LIST_ROWS],
        total=len(rows),
        truncated=truncated,
        limit=MAX_LIST_ROWS,
        today_date=reference,
        status=status,
        priority=priority,
        category=category,
        progress=build_progress(),
        confirm_form=ConfirmForm(),
        **_labels(),
    )


@goals_bp.get("/metas/esteira")
def board():
    """Hoje, em três colunas - mais o que ficou para trás e o que não tem dia.

    As colunas mostram só o dia de hoje, de propósito: uma esteira que mistura
    a semana inteira deixa de responder à pergunta que se faz a ela, que é "o
    que eu faço agora". O atrasado aparece recolhido acima, e o acervo sem
    prazo recolhido abaixo - presentes, mas sem disputar a atenção do dia.
    """
    reference = today()
    include_undated = bool(SettingsService.get("goals_undated_on_board"))

    rows = [
        row
        for row in rows_for_day(reference, include_undated=include_undated)
        if row.goal.show_on_board
    ]
    dated = [row for row in rows if row.date is not None]
    undated = [row for row in rows if row.date is None]

    overdue = _latest_overdue(reference)
    preview = undated[:BACKLOG_PREVIEW]

    return render_template(
        "goals/board.html",
        columns=_columns(dated),
        backlog_columns=_columns(preview),
        backlog_total=len(undated),
        backlog_shown=len(preview),
        overdue=overdue,
        today_date=reference,
        confirm_form=ConfirmForm(),
        **_labels(),
    )


@goals_bp.get("/metas/plano")
def planning():
    """A semana ou o mês, em uma lista só."""
    view = "mes" if request.args.get("janela") == "mes" else "semana"
    reference = today()

    if view == "semana":
        start = reference - timedelta(days=reference.weekday())
        end = start + timedelta(days=6)
    else:
        start = reference.replace(day=1)
        end = _end_of_month(start)

    rows = rows_between(start, end)
    done = sum(1 for row in rows if row.is_done)

    return render_template(
        "goals/planning.html",
        rows=rows,
        start=start,
        end=end,
        view=view,
        done=done,
        today_date=reference,
        by_day=_group_by_day(rows, start, end),
        confirm_form=ConfirmForm(),
        **_labels(),
    )


@goals_bp.get("/metas/historico")
def history():
    """Telemetria: o ritmo dos últimos dias e o peso de cada categoria."""
    reference = today()
    start = reference - timedelta(days=HISTORY_DAYS - 1)
    days = [start + timedelta(days=offset) for offset in range(HISTORY_DAYS)]

    per_day = {day: 0 for day in days}
    for row in rows_between(start, reference):
        if row.date in per_day and row.is_done:
            per_day[row.date] += 1

    chart = build_line_chart(
        [day.strftime("%d/%m") for day in days],
        [("Metas concluídas", [per_day[day] for day in days])],
    )
    categories = build_bar_chart(
        [
            (CATEGORY_LABELS.get(name, name), total)
            for name, total in GoalRepository.category_totals()
        ]
    )

    return render_template(
        "goals/history.html",
        progress=build_progress(),
        chart=chart,
        categories=categories,
        history_days=HISTORY_DAYS,
        **_labels(),
    )


@goals_bp.get("/metas/conquistas")
def achievements():
    unlocked, total = AchievementService.summary()
    return render_template(
        "goals/achievements.html",
        groups=AchievementService.board(),
        unlocked=unlocked,
        total=total,
        progress=build_progress(),
    )


# ── Criar, editar, apagar ───────────────────────────────────────────────────


@goals_bp.route("/metas/nova", methods=["GET", "POST"])
def create():
    form = GoalForm(data={"date": today()})
    _fill_document_choices(form)

    if form.validate_on_submit():
        try:
            goal = GoalService.create(form.to_input())
        except ValidationError as error:
            flash(error.message, "error")
        else:
            _announce(f"Meta “{goal.display_title}” criada.")
            return redirect(url_for("goals.index"))
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "goals/form.html", form=form, goal=None, **_labels()
    )


@goals_bp.route("/metas/<public_uuid>/editar", methods=["GET", "POST"])
def edit(public_uuid: str):
    goal = GoalService.require(public_uuid)
    form = GoalForm(obj=goal)
    _fill_document_choices(form)

    if request.method == "GET":
        form.document_uuid.data = goal.document.uuid if goal.document else ""

    if form.validate_on_submit():
        try:
            GoalService.update(goal, form.to_input())
        except ValidationError as error:
            flash(error.message, "error")
        else:
            _announce("Meta atualizada.")
            return redirect(url_for("goals.index"))
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "goals/form.html", form=form, goal=goal, **_labels()
    )


@goals_bp.post("/metas/<public_uuid>/estado")
def change_status(public_uuid: str):
    """Concluir, avançar de coluna ou marcar um estado exato.

    Um endpoint para as três coisas porque as três são a mesma escrita, e
    porque o dia sempre viaja junto: sem ``dia``, "concluí" numa série não
    diria qual terça.
    """
    goal = GoalService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(_back_to(url_for("goals.index")))

    day = _read_day(request.form.get("dia"))
    action = request.form.get("acao") or "alternar"

    try:
        if action == "ciclo":
            GoalService.cycle(goal, day)
        elif action == "estado":
            GoalService.set_status(goal, request.form.get("status") or "", day)
        else:
            GoalService.toggle(goal, day)
    except ValidationError as error:
        flash(error.message, "error")
        return redirect(_back_to(url_for("goals.index")))

    _announce()
    return redirect(_back_to(url_for("goals.index")))


@goals_bp.post("/metas/<public_uuid>/hoje")
def move_to_today(public_uuid: str):
    goal = GoalService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(_back_to(url_for("goals.index")))

    try:
        GoalService.move_to_today(goal)
    except ValidationError as error:
        flash(error.message, "error")
        return redirect(_back_to(url_for("goals.index")))

    flash("Meta trazida para hoje.", "success")
    return redirect(url_for("goals.board"))


@goals_bp.post("/metas/<public_uuid>/excluir")
def delete(public_uuid: str):
    goal = GoalService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(_back_to(url_for("goals.index")))

    title = goal.display_title
    GoalService.delete(goal)
    flash(f"Meta “{title}” removida.", "success")
    return redirect(_back_to(url_for("goals.index")))


# ── Predefinidas ────────────────────────────────────────────────────────────


@goals_bp.get("/metas/predefinidas")
def templates():
    return render_template(
        "goals/templates.html",
        templates=GoalRepository.templates(),
        activate_form=ActivateTemplateForm(data={"date": today()}),
        confirm_form=ConfirmForm(),
        today_date=today(),
        **_labels(),
    )


@goals_bp.route("/metas/predefinidas/nova", methods=["GET", "POST"])
def create_template():
    form = GoalTemplateForm()
    _fill_document_choices(form)

    if form.validate_on_submit():
        try:
            GoalService.create_template(form.to_input())
        except ValidationError as error:
            flash(error.message, "error")
        else:
            _announce("Meta predefinida guardada.")
            return redirect(url_for("goals.templates"))
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "goals/template_form.html", form=form, template=None, **_labels()
    )


@goals_bp.route("/metas/predefinidas/<public_uuid>/editar", methods=["GET", "POST"])
def edit_template(public_uuid: str):
    template = GoalService.require_template(public_uuid)
    form = GoalTemplateForm(obj=template)
    _fill_document_choices(form)

    if request.method == "GET":
        form.document_uuid.data = template.document.uuid if template.document else ""

    if form.validate_on_submit():
        try:
            GoalService.update_template(template, form.to_input())
        except ValidationError as error:
            flash(error.message, "error")
        else:
            flash("Meta predefinida atualizada.", "success")
            return redirect(url_for("goals.templates"))
    elif form.is_submitted():
        flash(_first_error(form) or "Verifique os dados informados.", "error")

    return render_template(
        "goals/template_form.html", form=form, template=template, **_labels()
    )


@goals_bp.post("/metas/predefinidas/<public_uuid>/ativar")
def activate_template(public_uuid: str):
    template = GoalService.require_template(public_uuid)
    form = ActivateTemplateForm()

    if not form.validate_on_submit():
        flash(
            _first_error(form) or "Escolha o dia em que esta meta acontece.", "error"
        )
        return redirect(url_for("goals.templates"))

    try:
        goal = GoalService.activate_template(template, form.date.data)
    except ValidationError as error:
        flash(error.message, "error")
        return redirect(url_for("goals.templates"))

    _announce(
        f"“{goal.display_title}” marcada para {goal.date.strftime('%d/%m/%Y')}."
    )
    return redirect(
        url_for("goals.board") if goal.date == today() else url_for("goals.index")
    )


@goals_bp.post("/metas/predefinidas/<public_uuid>/excluir")
def delete_template(public_uuid: str):
    template = GoalService.require_template(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(url_for("goals.templates"))

    GoalService.delete_template(template)
    flash("Meta predefinida removida.", "success")
    return redirect(url_for("goals.templates"))


# ── Frases e preferências da jornada ────────────────────────────────────────


@goals_bp.route("/metas/frases", methods=["GET", "POST"])
def phrases():
    settings_form = PhraseSettingsForm(
        data={
            "enabled": bool(SettingsService.get("goals_phrases_enabled")),
            "interval": str(PhraseService.interval_minutes()),
            "undated_on_board": bool(SettingsService.get("goals_undated_on_board")),
        }
    )
    phrase_form = PhraseForm()

    if request.form.get("acao") == "preferencias":
        settings_form = PhraseSettingsForm()
        if settings_form.validate_on_submit():
            SettingsService.update_many(
                {
                    "goals_phrases_enabled": settings_form.enabled.data,
                    "goals_phrase_interval": settings_form.interval_minutes(),
                    "goals_undated_on_board": settings_form.undated_on_board.data,
                }
            )
            _announce("Preferências da jornada salvas.")
            return redirect(url_for("goals.phrases"))
        flash(_first_error(settings_form) or "Verifique os dados.", "error")

    elif phrase_form.validate_on_submit():
        try:
            PhraseService.create(phrase_form.text.data or "")
        except ValidationError as error:
            flash(error.message, "error")
        else:
            flash("Frase adicionada à rotação.", "success")
            return redirect(url_for("goals.phrases"))
    elif phrase_form.is_submitted():
        flash(_first_error(phrase_form) or "Verifique os dados.", "error")

    return render_template(
        "goals/phrases.html",
        settings_form=settings_form,
        phrase_form=phrase_form,
        confirm_form=ConfirmForm(),
        default_phrases=DEFAULT_PHRASES,
        custom_phrases=PhraseRepository.all(),
        intervals=PHRASE_INTERVALS,
        current_phrase=PhraseService.current(),
    )


@goals_bp.post("/metas/frases/<public_uuid>/excluir")
def delete_phrase(public_uuid: str):
    phrase = PhraseService.require(public_uuid)
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(url_for("goals.phrases"))

    PhraseService.delete(phrase)
    flash("Frase removida.", "success")
    return redirect(url_for("goals.phrases"))


@goals_bp.post("/metas/limpar")
def clear():
    """Recomeçar a jornada. Os documentos não são tocados."""
    if not ConfirmForm().validate_on_submit():
        flash("Sessão expirada. Recarregue a página e tente novamente.", "error")
        return redirect(url_for("goals.phrases"))

    if (request.form.get("confirmacao") or "").strip().upper() != "LIMPAR":
        flash("Digite LIMPAR para confirmar.", "error")
        return redirect(url_for("goals.phrases"))

    goals, templates_removed, achievements_removed = GoalService.clear_all()
    flash(
        f"{goals} meta(s), {templates_removed} predefinida(s) e "
        f"{achievements_removed} conquista(s) removidas. "
        "Seus documentos continuam intactos.",
        "success",
    )
    return redirect(url_for("goals.index"))


# ── A esteira falando ───────────────────────────────────────────────────────


@goals_bp.patch("/api/metas/<public_uuid>")
def api_change_status(public_uuid: str):
    """O cartão mudou de coluna.

    Responde com o progresso e com as conquistas recém-abertas: a página já
    está aberta e não vai recarregar, então o número no topo e a medalha nova
    precisam chegar por aqui ou não chegam.
    """
    goal = GoalService.require(public_uuid)
    payload = request.get_json(silent=True) or {}

    status = payload.get("status")
    if not isinstance(status, str):
        raise ValidationError("Status inválido.")

    raw_day = payload.get("dia")
    day = _read_day(raw_day if isinstance(raw_day, str) else None)

    GoalService.set_status(goal, status, day)

    # Calculado uma vez e emprestado: a resposta carrega o progresso e a
    # sincronização das conquistas pergunta pelos mesmos números.
    progress = build_progress()

    return jsonify(
        {
            "ok": True,
            "status": status,
            "achievements": [
                {"title": item.title, "icon": item.icon}
                for item in AchievementService.sync(progress)
            ],
            "progress": {
                "completed": progress.completed,
                "xp": progress.xp,
                "level": progress.level,
                "streak": progress.streak,
            },
        }
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _labels() -> dict[str, object]:
    """Os rótulos que quase toda tela de metas usa."""
    return {
        "status_labels": STATUS_LABELS,
        "priority_labels": PRIORITY_LABELS,
        "category_labels": CATEGORY_LABELS,
        "category_icons": CATEGORY_ICONS,
        "recurrence_labels": RECURRENCE_LABELS,
    }


def _one_row_per_goal(
    rows: list[Occurrence], reference: date
) -> list[Occurrence]:
    """Recolhe cada série ao dia que interessa dela.

    O dia que interessa é o próximo que ainda não passou - é o que responde
    "quando isto acontece de novo". Se a série inteira ficou para trás, é o
    dia mais recente dela, que é a pendência a resolver. Metas avulsas e sem
    prazo já têm uma linha só e passam por aqui intactas.
    """
    def rank(row: Occurrence) -> tuple[int, int]:
        if row.date is None:
            return (0, 0)
        if row.date >= reference:
            return (0, row.date.toordinal())
        return (1, -row.date.toordinal())

    best: dict[int, Occurrence] = {}
    for row in rows:
        current = best.get(row.goal.id)
        if current is None or rank(row) < rank(current):
            best[row.goal.id] = row
    return sort_rows(list(best.values()))


def _columns(rows: list[Occurrence]) -> list[dict[str, object]]:
    return [
        {
            "status": status,
            "label": label,
            "rows": [row for row in rows if row.status == status],
        }
        for status, label in STATUS_LABELS.items()
    ]


def _latest_overdue(reference: date) -> list[Occurrence]:
    """O que ficou para trás - uma linha por meta, a mais recente.

    Uma série esquecida por três meses tem noventa ocorrências pendentes, e
    listar as noventa não informa nada além de que ela foi esquecida. A
    pendência mais recente é a que se resolve; resolvê-la é o que traz a
    esteira de volta a refletir o plano.
    """
    latest: dict[int, Occurrence] = {}
    for row in rows_between(
        reference - timedelta(days=LIST_PAST_DAYS), reference - timedelta(days=1)
    ):
        if not row.goal.show_on_board or row.is_done or row.date is None:
            continue
        previous = latest.get(row.goal.id)
        if previous is None or (previous.date is not None and row.date > previous.date):
            latest[row.goal.id] = row
    return sorted(latest.values(), key=lambda row: (row.date or date.max))


def _group_by_day(
    rows: list[Occurrence], start: date, end: date
) -> list[dict[str, object]]:
    """O plano dia a dia, incluindo os dias vazios.

    Um dia sem nada é informação: é onde cabe a próxima coisa. Uma lista que
    pula os dias vazios esconde exatamente o espaço que o planejamento procura.
    """
    buckets: dict[date, list[Occurrence]] = {}
    for row in rows:
        if row.date is not None:
            buckets.setdefault(row.date, []).append(row)

    days: list[dict[str, object]] = []
    cursor = start
    while cursor <= end:
        entries = buckets.get(cursor, [])
        days.append(
            {
                "date": cursor,
                "weekday": WEEKDAY_NAMES[cursor.weekday()],
                "rows": entries,
                "done": sum(1 for row in entries if row.status == STATUS_DONE),
            }
        )
        cursor += timedelta(days=1)
    return days


def _fill_document_choices(form) -> None:
    form.document_uuid.choices = [DOCUMENT_CHOICE_EMPTY] + [
        (uuid, title) for _, uuid, title in GoalRepository.documents_for_picker()
    ]


def _announce(message: str | None = None) -> None:
    """Confirma a ação e anuncia o que ela desbloqueou.

    A sincronização acontece aqui, no fim de toda escrita, e não numa tarefa de
    fundo: uma conquista que aparece cinco minutos depois do gesto que a
    mereceu não é uma recompensa, é uma notificação solta.
    """
    if message:
        flash(message, "success")
    for item in AchievementService.sync():
        flash(f"Conquista desbloqueada: {item.title}", "success")


def _read_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except ValueError as error:
        raise ValidationError("Data inválida.") from error


def _one_of(raw: str | None, allowed: tuple[str, ...]) -> str:
    value = (raw or "").strip()
    return value if value in allowed else ""


def _end_of_month(start: date) -> date:
    if start.month == 12:
        return start.replace(day=31)
    return start.replace(month=start.month + 1, day=1) - timedelta(days=1)


def _first_error(form) -> str | None:
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return None


def _back_to(default: str) -> str:
    """Volta para a página de onde a ação partiu - dentro desta aplicação."""
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return default
