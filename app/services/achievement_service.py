"""Ler a jornada, decidir o que foi conquistado.

O serviço faz três coisas, nesta ordem: monta o contexto (uma leitura), avalia
o catálogo contra ele (aritmética pura) e grava as chaves novas (uma escrita).
Só o que ainda não estava desbloqueado é gravado, e uma conquista desbloqueada
nunca é revogada - a data em que você chegou lá continua sendo verdade mesmo
que a meta que levou até lá seja apagada depois.

Os horários são convertidos para o fuso das configurações antes de qualquer
pergunta sobre "de madrugada" ou "no fim de semana". Guardado em UTC, um
sábado às 21h de São Paulo é um domingo, e a conquista de fim de semana
começaria a cair no dia errado.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from app.extensions import db
from app.repositories.achievement_repository import AchievementRepository
from app.repositories.goal_repository import GoalRepository
from app.services.achievements_catalog import (
    BY_KEY,
    CATALOG,
    GROUP_ORDER,
    Achievement,
    AchievementContext,
)
from app.services.progress_service import Progress, build_progress
from app.services.settings_service import SettingsService
from app.utils.dates import to_local, utcnow

EARLY_HOUR = 7
LATE_HOUR = 22


@dataclass(slots=True, frozen=True)
class AchievementCard:
    """Uma conquista como a tela precisa dela: definição mais estado."""

    achievement: Achievement
    unlocked_at: datetime | None

    @property
    def is_unlocked(self) -> bool:
        return self.unlocked_at is not None


class AchievementService:
    @staticmethod
    def build_context(progress: Progress | None = None) -> AchievementContext:
        """O contexto da jornada.

        ``progress`` é aceito de fora porque quem acabou de calculá-lo não deve
        pagar por ele de novo: o endpoint da esteira devolve o progresso na
        resposta *e* pergunta se algo foi desbloqueado, e as duas coisas leem a
        mesma janela de 400 dias. Sem isto, cada cartão arrastado varria o
        calendário duas vezes.
        """
        progress = progress or build_progress()
        timezone = SettingsService.get("timezone")

        categories: Counter[str] = Counter()
        priorities: Counter[str] = Counter()
        per_day: Counter[date] = Counter()
        early = night = weekend = undated = False

        for category, priority, completed_at, has_deadline in (
            GoalRepository.completion_rows()
        ):
            categories[category] += 1
            priorities[priority] += 1
            if not has_deadline:
                undated = True

            local = to_local(completed_at, timezone)
            if local is None:
                continue
            per_day[local.date()] += 1
            early = early or local.hour < EARLY_HOUR
            night = night or local.hour >= LATE_HOUR
            weekend = weekend or local.weekday() >= 5

        first_created = to_local(GoalRepository.first_created_at(), timezone)
        journey_days = (
            (to_local(utcnow(), timezone) - first_created).days if first_created else 0
        )

        return AchievementContext(
            created=progress.created,
            completed=progress.completed,
            level=progress.level,
            streak=progress.streak,
            record=progress.record,
            productive_days=progress.productive_days,
            categories_completed=frozenset(categories),
            max_category_count=max(categories.values(), default=0),
            priority_alta=priorities.get("alta", 0),
            priority_media=priorities.get("media", 0),
            priority_baixa=priorities.get("baixa", 0),
            priorities_completed=sum(
                1 for name in ("alta", "media", "baixa") if priorities.get(name)
            ),
            early_bird=early,
            night_owl=night,
            weekend_completed=weekend,
            undated_completed=undated,
            max_per_day=max(per_day.values(), default=0),
            completion_rate=progress.completion_rate,
            journey_days=max(journey_days, 0),
            light_theme=SettingsService.get("theme") == "light",
            phrases_enabled=bool(SettingsService.get("goals_phrases_enabled")),
            has_template=GoalRepository.template_count() > 0,
            linked_to_document=GoalRepository.linked_count() > 0,
        )

    @staticmethod
    def sync(progress: Progress | None = None) -> list[Achievement]:
        """Desbloqueia o que passou a ser verdade. Devolve só o que é novidade.

        Chamado depois de toda ação que muda a jornada. O retorno é o que a
        interface anuncia - uma lista vazia é o caso comum e não custa nada
        além da leitura do contexto.
        """
        unlocked = AchievementRepository.unlocked()
        context = AchievementService.build_context(progress)

        fresh = [
            item
            for item in CATALOG
            if item.key not in unlocked and item.condition(context)
        ]
        if fresh:
            AchievementRepository.record(item.key for item in fresh)
            db.session.commit()
        return fresh

    @staticmethod
    def board() -> list[tuple[str, list[AchievementCard]]]:
        """O catálogo inteiro, agrupado e marcado, na ordem do catálogo."""
        unlocked = AchievementRepository.unlocked()
        cards = [
            AchievementCard(achievement=item, unlocked_at=unlocked.get(item.key))
            for item in CATALOG
        ]
        by_group: dict[str, list[AchievementCard]] = {name: [] for name in GROUP_ORDER}
        for card in cards:
            by_group[card.achievement.group].append(card)
        return [(name, by_group[name]) for name in GROUP_ORDER if by_group[name]]

    @staticmethod
    def summary() -> tuple[int, int]:
        """``(desbloqueadas, total)`` - contando só o catálogo atual.

        Uma chave gravada que não existe mais no catálogo não é contada nem
        exibida. Ela continua na tabela: se a conquista voltar, ela volta
        desbloqueada, com a data original.
        """
        unlocked = AchievementRepository.unlocked()
        return sum(1 for key in unlocked if key in BY_KEY), len(CATALOG)
