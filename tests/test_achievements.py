"""Conquistas: o catálogo, o contexto e o que nunca é revogado."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.goal import STATUS_DONE
from app.repositories.achievement_repository import AchievementRepository
from app.services.achievement_service import AchievementService
from app.services.achievements_catalog import BY_KEY, CATALOG, GROUP_ORDER
from app.services.goal_service import GoalInput, GoalService
from app.services.phrase_service import PhraseService
from app.services.settings_service import SettingsService


@pytest.fixture()
def make_goal(app):
    def _make(title="Meta", **kwargs):
        return GoalService.create(GoalInput(title=title, **kwargs))

    return _make


class TestTheCatalogue:
    def test_every_key_is_unique(self):
        """Uma chave repetida faria uma conquista esconder a outra."""
        keys = [item.key for item in CATALOG]

        assert len(keys) == len(set(keys))
        assert len(BY_KEY) == len(CATALOG)

    def test_every_condition_answers_on_an_empty_journey(self):
        """Nenhuma condição pode explodir num banco recém-criado."""
        from app.services.achievements_catalog import AchievementContext

        empty = AchievementContext()
        for item in CATALOG:
            assert isinstance(item.condition(empty), bool), item.key

    def test_a_tier_family_does_not_close_over_the_loop_variable(self):
        """O erro clássico: todas as faixas exigindo o último número."""
        from app.services.achievements_catalog import AchievementContext

        low = AchievementContext(completed=1)
        unlocked = [item.key for item in CATALOG if item.condition(low)]

        assert "completed_1" in unlocked
        assert "completed_1000" not in unlocked

    def test_every_group_has_cards(self, app):
        board = AchievementService.board()

        assert [name for name, _ in board] == list(GROUP_ORDER)
        assert all(cards for _, cards in board)

    def test_every_icon_exists_in_the_sprite(self):
        """Um ícone sem símbolo é um quadrado vazio na tela."""
        import pathlib
        import re

        sprite = pathlib.Path("app/templates/components/icon_sprite.html").read_text(
            encoding="utf-8"
        )
        available = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', sprite))

        missing = {item.icon for item in CATALOG} - available
        assert not missing, f"ícones sem símbolo no sprite: {sorted(missing)}"


class TestUnlocking:
    def test_the_first_goal_opens_the_first_launch(self, make_goal):
        make_goal()

        fresh = AchievementService.sync()

        assert "created_1" in {item.key for item in fresh}

    def test_a_conquest_is_announced_once(self, make_goal):
        make_goal()
        AchievementService.sync()

        assert AchievementService.sync() == []

    def test_completing_opens_the_completion_tier(self, make_goal):
        goal = make_goal()
        GoalService.set_status(goal, STATUS_DONE, goal.date)

        keys = {item.key for item in AchievementService.sync()}

        assert "completed_1" in keys

    def test_an_unlocked_conquest_survives_the_goal_that_earned_it(self, make_goal):
        """A data em que você chegou lá continua sendo verdade."""
        goal = make_goal()
        GoalService.set_status(goal, STATUS_DONE, goal.date)
        AchievementService.sync()

        GoalService.delete(goal)

        assert "completed_1" in AchievementRepository.unlocked()

    def test_linking_a_document_is_its_own_conquest(self, app, document):
        GoalService.create(GoalInput(title="Terminar", document_uuid=document.uuid))

        keys = {item.key for item in AchievementService.sync()}

        assert "linked_document" in keys

    def test_a_template_is_its_own_conquest(self, app):
        GoalService.create_template(GoalInput(title="Molde"))

        keys = {item.key for item in AchievementService.sync()}

        assert "template_created" in keys

    def test_the_summary_counts_only_the_current_catalogue(self, app, db):
        """Uma chave que saiu do código não conta nem aparece - mas fica."""
        AchievementRepository.record(["completed_1", "conquista_aposentada"])
        db.session.commit()

        unlocked, total = AchievementService.summary()

        assert unlocked == 1
        assert total == len(CATALOG)
        assert "conquista_aposentada" in AchievementRepository.unlocked()

    def test_clearing_the_journey_clears_the_medals(self, app, make_goal):
        make_goal()
        AchievementService.sync()

        GoalService.clear_all()

        assert AchievementRepository.count() == 0


class TestTheContext:
    def test_the_completion_hour_is_read_in_the_configured_timezone(self, app, db, make_goal):
        """Um horário lido no fuso errado cai no dia errado.

        Tudo é guardado em UTC. A pergunta "concluiu de madrugada?" e a
        pergunta "concluiu no fim de semana?" são sobre o relógio de quem
        concluiu, não sobre o de Greenwich.
        """
        from datetime import datetime, timezone

        goal = make_goal()
        GoalService.set_status(goal, STATUS_DONE, goal.date)
        # 08:00 UTC de um domingo são 05:00 de domingo em São Paulo: fim de
        # semana e madrugada ao mesmo tempo. Lido em UTC, seriam 08:00 - nem
        # uma coisa nem a outra.
        goal.completed_at = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
        db.session.commit()

        context = AchievementService.build_context()

        assert context.weekend_completed is True
        assert context.early_bird is True

    def test_preferences_are_part_of_the_context(self, app):
        SettingsService.update_many({"theme": "light", "goals_phrases_enabled": True})

        context = AchievementService.build_context()

        assert context.light_theme is True
        assert context.phrases_enabled is True
        assert PhraseService.enabled() is True

    def test_the_journey_counts_from_the_first_goal(self, app, db, make_goal):
        from app.utils.dates import utcnow

        goal = make_goal()
        goal.created_at = utcnow() - timedelta(days=45)
        db.session.commit()

        assert AchievementService.build_context().journey_days >= 44

    def test_the_page_renders_with_a_locked_catalogue(self, client):
        body = client.get("/metas/conquistas").data.decode("utf-8")

        assert "Conquistas" in body
        assert "Primeiro lançamento" in body
