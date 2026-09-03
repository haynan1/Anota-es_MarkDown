"""Quando uma meta acontece.

Este módulo responde a uma pergunta só, e responde sem tocar em HTTP: *dado um
intervalo de dias, quais metas caem nele e em que estado cada dia está?*

A resposta é uma lista de :class:`Occurrence` - o par (meta, dia) que a
interface inteira desenha. A esteira, o plano da semana, o histórico, a
sequência e o painel de hoje são todos a mesma função com datas diferentes, o
que é exatamente o objetivo: um dia não pode estar concluído numa tela e
pendente na outra porque duas telas contaram de jeitos diferentes.

A expansão é feita em Python, não em SQL, porque a regra é um calendário -
"dias úteis até o fim do mês" não é uma cláusula ``WHERE``. O custo é
conhecido: a janela é limitada em dias (:data:`MAX_WINDOW_DAYS`) e o número de
metas é limitado pelo repositório, então o pior caso é um retângulo com os dois
lados medidos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.models import Goal, GoalOccurrence
from app.models.goal import (
    PRIORITY_ORDER,
    RECURRENCE_NONE,
    STATUS_DONE,
)
from app.repositories.goal_repository import GoalRepository
from app.services.settings_service import SettingsService
from app.utils.dates import today_in

# Nenhuma tela pede mais do que isto de uma vez. O plano do mês pede 31 dias; o
# histórico e a sequência pedem pouco mais de um ano. Um pedido maior que este
# é recortado, e não recusado: a resposta continua correta, só não é infinita.
MAX_WINDOW_DAYS = 420

# Até onde a sequência olha para trás. Nenhuma sequência diária real passa
# disso, e o limite evita que o cálculo cresça com a idade da instalação.
STREAK_LOOKBACK_DAYS = 400


@dataclass(slots=True)
class Occurrence:
    """Uma meta em um dia - ou uma meta sem dia nenhum.

    ``date`` é ``None`` para as metas sem prazo. Elas não pertencem a nenhuma
    data e por isso não podem atrasar; aparecem no acervo e na esteira apenas
    se você pedir.
    """

    goal: Goal
    date: date | None
    status: str
    completed_at: datetime | None
    is_overdue: bool = False

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def day_iso(self) -> str:
        """O dia em ISO, ou vazio - é assim que o formulário o devolve."""
        return self.date.isoformat() if self.date else ""


def today() -> date:
    """Hoje, no fuso escolhido nas configurações."""
    return today_in(SettingsService.get("timezone"))


def clamp_window(start: date, end: date) -> tuple[date, date]:
    """Garante que a janela pedida não passe do teto, sem inverter as pontas."""
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_WINDOW_DAYS:
        end = start + timedelta(days=MAX_WINDOW_DAYS)
    return start, end


def occurrence_dates(goal: Goal, start: date, end: date) -> list[date]:
    """Os dias em que ``goal`` cai dentro de ``[start, end]``.

    Uma meta avulsa tem no máximo um dia: o dela. Uma série é percorrida dia a
    dia a partir do maior entre a âncora e o início da janela - a aritmética
    de "dias úteis" e "fins de semana" é sobre o dia da semana, e não existe
    fórmula fechada que a substitua sem virar uma tabela de casos.
    """
    if not goal.has_deadline:
        return []

    if goal.recurrence_type == RECURRENCE_NONE:
        return [goal.date] if start <= goal.date <= end else []

    last = end
    if goal.recurrence_end_date is not None:
        last = min(last, goal.recurrence_end_date)

    days: list[date] = []
    cursor = max(goal.date, start)
    while cursor <= last:
        elapsed = (cursor - goal.date).days
        if _falls_on(goal, cursor, elapsed):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _falls_on(goal: Goal, day: date, elapsed: int) -> bool:
    if goal.recurrence_type == "forever":
        return True
    if goal.recurrence_type == "weekdays":
        return day.weekday() < 5
    if goal.recurrence_type == "weekends":
        return day.weekday() >= 5
    if goal.recurrence_type == "count":
        return 0 <= elapsed < (goal.recurrence_days or 0)
    return False


def expand(
    goals: Sequence[Goal],
    overrides: dict[tuple[int, date], GoalOccurrence],
    start: date,
    end: date,
    include_undated: bool = False,
) -> list[Occurrence]:
    """Transforma metas + exceções em linhas de tela, já ordenadas.

    Função pura: não consulta nada. É o que torna a regra de datas testável
    sem banco, e o que permite ao repositório escolher como buscar as
    exceções sem que esta camada saiba disso.
    """
    reference = today()
    rows: list[Occurrence] = []

    for goal in goals:
        if not goal.has_deadline:
            if include_undated:
                rows.append(
                    Occurrence(
                        goal=goal,
                        date=None,
                        status=goal.status,
                        completed_at=goal.completed_at,
                    )
                )
            continue

        for day in occurrence_dates(goal, start, end):
            override = overrides.get((goal.id, day))
            status = override.status if override else goal.status
            rows.append(
                Occurrence(
                    goal=goal,
                    date=day,
                    status=status,
                    completed_at=(
                        override.completed_at if override else goal.completed_at
                    ),
                    is_overdue=day < reference and status != STATUS_DONE,
                )
            )

    return sort_rows(rows)


def sort_rows(rows: list[Occurrence]) -> list[Occurrence]:
    """Data, horário, prioridade, criação - nessa ordem, e sem prazo por último.

    A ordem é a de quem vai executar: o que é para hoje antes do que é para
    amanhã, o que tem hora antes do que não tem, e o que é urgente antes do que
    pode esperar. As metas sem prazo fecham a lista porque nenhuma delas
    disputa o dia com as que têm.
    """
    return sorted(
        rows,
        key=lambda row: (
            row.date is None,
            row.date or date.max,
            row.goal.time or time.max,
            PRIORITY_ORDER.get(row.goal.priority, 1),
            row.goal.created_at,
        ),
    )


def rows_between(
    start: date, end: date, include_undated: bool = False
) -> list[Occurrence]:
    """A janela inteira: duas consultas e uma expansão."""
    start, end = clamp_window(start, end)
    goals = GoalRepository.window(start, end, include_undated=include_undated)
    overrides = GoalRepository.occurrences_between(
        [goal.id for goal in goals], start, end
    )
    return expand(goals, overrides, start, end, include_undated=include_undated)


def rows_for_day(day: date, include_undated: bool = False) -> list[Occurrence]:
    return rows_between(day, day, include_undated=include_undated)


def status_on(goal: Goal, day: date | None) -> str:
    """O estado de uma meta num dia, respeitando a exceção se houver."""
    if day is None or not goal.has_deadline:
        return goal.status
    override = GoalRepository.occurrence(goal.id, day)
    return override.status if override else goal.status


def completed_days(lookback: int = STREAK_LOOKBACK_DAYS) -> set[date]:
    """Os dias em que ao menos uma meta com prazo foi concluída.

    Metas sem prazo ficam de fora por definição: elas não acontecem em um dia,
    então não podem sustentar uma sequência de dias.
    """
    return day_and_history()[1]


def day_and_history(
    reference: date | None = None, lookback: int = STREAK_LOOKBACK_DAYS
) -> tuple[list[Occurrence], set[date]]:
    """As linhas de hoje e o calendário de conclusões, em uma leitura só.

    O painel precisa das duas coisas ao mesmo tempo - o que fazer hoje, e há
    quantos dias a sequência está de pé - e a janela da segunda contém a
    primeira. Pedi-las separadamente seria ler o mesmo intervalo duas vezes
    para responder a uma pergunta só.
    """
    reference = reference or today()
    rows = rows_between(reference - timedelta(days=lookback), reference)
    return (
        [row for row in rows if row.date == reference],
        {row.date for row in rows if row.date is not None and row.is_done},
    )
