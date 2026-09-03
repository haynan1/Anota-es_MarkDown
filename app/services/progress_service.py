"""Progresso: XP, nível, sequência e recorde.

Nada aqui é armazenado. XP é uma leitura das conclusões, nível é uma leitura do
XP, e sequência é uma leitura do calendário de conclusões - todos derivados, e
por isso sempre coerentes com o que a pessoa vê nas listas. Guardar um contador
de XP numa coluna criaria uma segunda verdade, que passa a divergir na primeira
meta apagada.

A sequência não precisa do programa aberto para continuar contando: ela é
calculada a partir dos dias em que houve conclusão, e não de um relógio que
alguém precisou deixar rodando.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.repositories.goal_repository import GoalRepository
from app.services.goal_schedule import completed_days, today

# Uma conclusão vale isto. Criar uma meta não vale nada de propósito: o prêmio
# é por fazer, não por planejar - senão a forma mais rápida de subir de nível
# passa a ser cadastrar metas que ninguém vai cumprir.
XP_PER_COMPLETION = 10

# Quanto XP custa cada nível. O custo cresce com o nível (nível N custa
# N * 150), então subir fica progressivamente mais difícil sem que exista um
# teto - o que mantém o nível interessante no primeiro mês e no terceiro ano.
XP_PER_LEVEL_STEP = 150

# Um teto para a escalada de nível. Só existe para que a conta seja um laço
# provadamente finito, mesmo diante de um banco com números absurdos.
MAX_LEVEL = 999


@dataclass(slots=True, frozen=True)
class Progress:
    created: int
    completed: int
    xp: int
    level: int
    level_xp: int
    level_needed: int
    streak: int
    record: int
    productive_days: int

    @property
    def level_percent(self) -> int:
        if not self.level_needed:
            return 0
        return round(self.level_xp * 100 / self.level_needed)

    @property
    def completion_rate(self) -> float:
        return (self.completed / self.created) if self.created else 0.0

    @property
    def completion_percent(self) -> int:
        return round(self.completion_rate * 100)


def level_for(xp: int) -> tuple[int, int, int]:
    """``(nível, XP dentro do nível, XP que o nível pede)``."""
    level = 1
    remaining = max(xp, 0)
    while level < MAX_LEVEL and remaining >= level * XP_PER_LEVEL_STEP:
        remaining -= level * XP_PER_LEVEL_STEP
        level += 1
    return level, remaining, level * XP_PER_LEVEL_STEP


def current_streak(days: set[date], reference: date | None = None) -> int:
    """Dias seguidos até hoje.

    O dia de hoje ainda não conta contra você: se nada foi concluído hoje, a
    contagem começa ontem. Uma sequência de doze dias não desaparece às 00h01,
    ela desaparece quando o dia passa em branco.
    """
    reference = reference or today()
    cursor = reference if reference in days else reference - timedelta(days=1)
    total = 0
    while cursor in days:
        total += 1
        cursor -= timedelta(days=1)
    return total


def longest_streak(days: set[date]) -> int:
    best = 0
    run = 0
    previous: date | None = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def build_progress() -> Progress:
    """Tudo que as telas mostram sobre progresso, em uma passada.

    ``completed_days`` é calculado uma vez e emprestado para a sequência e para
    o recorde: as duas perguntas leem o mesmo calendário, e refazer a varredura
    seria pagar duas vezes pela mesma janela.
    """
    completed = GoalRepository.completed_count()
    xp = completed * XP_PER_COMPLETION
    level, level_xp, level_needed = level_for(xp)
    days = completed_days()

    return Progress(
        created=GoalRepository.total(),
        completed=completed,
        xp=xp,
        level=level,
        level_xp=level_xp,
        level_needed=level_needed,
        streak=current_streak(days),
        record=longest_streak(days),
        productive_days=len(days),
    )
