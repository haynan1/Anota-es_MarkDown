"""A jornada dentro do backup.

Um backup que salva os documentos e perde as metas restaura metade da
aplicação - e a metade perdida é justamente a que não se reconstrói relendo os
arquivos ``.md`` do arquivo. Estes testes fixam a viagem de ida e volta.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.goal import STATUS_DONE
from app.repositories.achievement_repository import AchievementRepository
from app.repositories.goal_repository import GoalRepository
from app.repositories.phrase_repository import PhraseRepository
from app.services.achievement_service import AchievementService
from app.services.backup_service import (
    build_export_payload,
    create_backup,
    restore_backup,
)
from app.services.goal_schedule import today
from app.services.goal_service import GoalInput, GoalService
from app.services.phrase_service import PhraseService


@pytest.fixture()
def a_journey(app, document):
    """Uma jornada com um pouco de cada coisa que pode se perder."""
    goal = GoalService.create(
        GoalInput(
            title="Terminar a proposta",
            description="Duas páginas e o orçamento.",
            document_uuid=document.uuid,
            priority="alta",
            category="trabalho",
            date=today() - timedelta(days=2),
            recurrence_type="forever",
        )
    )
    GoalService.set_status(goal, STATUS_DONE, today() - timedelta(days=1))
    GoalService.create(GoalInput(title="Estudar inglês", has_deadline=False))
    GoalService.create_template(GoalInput(title="Revisar orçamento", category="financas"))
    PhraseService.create("Um passo por dia já é uma direção.")
    AchievementService.sync()
    return goal


class TestExport:
    def test_the_payload_carries_the_journey(self, a_journey):
        payload = build_export_payload()

        assert len(payload["goals"]) == 2
        assert len(payload["goal_templates"]) == 1
        assert len(payload["motivational_phrases"]) == 1
        assert payload["achievements"]

    def test_a_goal_travels_with_its_exceptions(self, a_journey):
        payload = build_export_payload()
        recurring = next(
            entry for entry in payload["goals"] if entry["recurrence_type"] == "forever"
        )

        assert len(recurring["occurrences"]) == 1
        assert recurring["occurrences"][0]["status"] == STATUS_DONE

    def test_the_document_travels_by_uuid(self, a_journey, document):
        """Um id só faz sentido no banco que o gerou."""
        payload = build_export_payload()
        entry = next(item for item in payload["goals"] if item["document_uuid"])

        assert entry["document_uuid"] == document.uuid


class TestRestore:
    def test_replacing_brings_the_journey_back(self, app, a_journey):
        backup = create_backup(label="jornada")
        GoalService.clear_all()
        assert GoalRepository.total() == 0

        report = restore_backup(backup.path, mode="replace")

        assert report.goals_created == 2
        assert report.goal_templates_created == 1
        assert report.phrases_created == 1
        assert report.achievements_restored
        assert GoalRepository.total() == 2

    def test_the_exceptions_come_back_with_the_goal(self, app, a_journey):
        backup = create_backup()
        GoalService.clear_all()

        restore_backup(backup.path, mode="replace")

        goal = next(
            item for item in GoalRepository.window(
                today() - timedelta(days=5), today()
            )
            if item.recurrence_type == "forever"
        )
        assert GoalRepository.occurrence(goal.id, today() - timedelta(days=1))

    def test_the_document_link_is_rebuilt(self, app, a_journey, document):
        backup = create_backup()
        GoalService.clear_all()

        restore_backup(backup.path, mode="replace")

        assert GoalRepository.linked_to_document(document.id)

    def test_merging_does_not_duplicate_a_goal_already_here(self, app, a_journey):
        backup = create_backup()

        report = restore_backup(backup.path, mode="merge")

        assert report.goals_created == 0
        assert GoalRepository.total() == 2

    def test_a_malformed_goal_is_skipped_rather_than_fatal(self, app, db):
        """O arquivo é entrada hostil: uma linha ruim não derruba as boas."""
        from app.services.backup_service import RestoreReport, _restore_journey

        report = RestoreReport(mode="merge")
        _restore_journey(
            {
                "goals": [
                    "isto não é um dicionário",
                    {"uuid": "sem-data", "title": "Sem data"},
                    {"uuid": "boa", "title": "Boa", "date": today().isoformat()},
                ]
            },
            report,
        )

        assert report.goals_created == 1
        assert GoalRepository.total() == 1

    def test_a_repeated_day_in_the_archive_does_not_break_the_restore(self, app):
        """A unicidade (meta, dia) é do banco, e o arquivo não a respeita."""
        from app.services.backup_service import RestoreReport, _restore_journey

        day = today().isoformat()
        report = RestoreReport(mode="merge")
        _restore_journey(
            {
                "goals": [
                    {
                        "uuid": "repetida",
                        "title": "Correr",
                        "date": day,
                        "recurrence_type": "forever",
                        "occurrences": [
                            {"date": day, "status": STATUS_DONE},
                            {"date": day, "status": "pendente"},
                        ],
                    }
                ]
            },
            report,
        )

        assert report.goals_created == 1

    def test_an_executable_link_in_the_archive_is_dropped(self, app):
        """O arquivo é entrada hostil, e um href é execução, não texto.

        O Jinja escapa o texto de um atributo, mas não decide o esquema do
        endereço: um ``javascript:`` restaurado sairia clicável. O link some;
        a meta fica, porque descartar a meta inteira por causa do campo mais
        opcional que ela tem trocaria um link ruim por um compromisso perdido.
        """
        from app.services.backup_service import RestoreReport, _restore_journey

        report = RestoreReport(mode="merge")
        _restore_journey(
            {
                "goals": [
                    {
                        "uuid": "armadilha",
                        "title": "Meta forjada",
                        "date": today().isoformat(),
                        "link_url": "javascript:alert(document.cookie)",
                    }
                ],
                "goal_templates": [
                    {
                        "uuid": "molde-forjado",
                        "title": "Molde",
                        "link_url": "data:text/html,<script>1</script>",
                    }
                ],
            },
            report,
        )

        assert report.goals_created == 1
        assert report.goal_templates_created == 1
        goals = GoalRepository.window(today(), today())
        assert goals[0].link_url == ""
        assert GoalRepository.templates()[0].link_url == ""

    def test_an_https_link_in_the_archive_survives(self, app):
        from app.services.backup_service import RestoreReport, _restore_journey

        report = RestoreReport(mode="merge")
        _restore_journey(
            {
                "goals": [
                    {
                        "uuid": "boa",
                        "title": "Meta",
                        "date": today().isoformat(),
                        "link_url": "https://exemplo.com/x",
                    }
                ]
            },
            report,
        )

        assert GoalRepository.window(today(), today())[0].link_url == (
            "https://exemplo.com/x"
        )

    def test_markup_in_a_restored_description_is_stripped(self, app):
        from app.services.backup_service import RestoreReport, _restore_journey

        report = RestoreReport(mode="merge")
        _restore_journey(
            {
                "goals": [
                    {
                        "uuid": "html",
                        "title": "Meta",
                        "date": today().isoformat(),
                        "description": "<script>alert(1)</script>Fazer",
                    }
                ]
            },
            report,
        )

        description = GoalRepository.window(today(), today())[0].description
        assert "<script>" not in description
        assert "Fazer" in description

    def test_an_invented_achievement_key_is_kept_but_not_counted(self, app):
        from app.services.backup_service import RestoreReport, _restore_journey

        report = RestoreReport(mode="merge")
        _restore_journey({"achievements": [{"key": "conquista_de_outra_era"}]}, report)

        assert report.achievements_restored == 1
        assert "conquista_de_outra_era" in AchievementRepository.unlocked()
        assert AchievementService.summary()[0] == 0

    def test_a_restored_phrase_joins_the_rotation(self, app, a_journey):
        backup = create_backup()
        GoalService.clear_all()
        for phrase in PhraseRepository.all():
            PhraseService.delete(phrase)

        restore_backup(backup.path, mode="replace")

        assert "Um passo por dia já é uma direção." in PhraseService.all_texts()
