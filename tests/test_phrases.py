"""Frases motivacionais e as preferências da jornada."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.phrase_repository import MAX_PHRASES, PhraseRepository
from app.services.exceptions import ValidationError
from app.services.phrase_service import DEFAULT_PHRASES, PhraseService
from app.services.settings_service import SettingsService


class TestRotation:
    def test_the_factory_phrases_are_always_in_the_rotation(self, app):
        assert PhraseService.all_texts()[: len(DEFAULT_PHRASES)] == list(DEFAULT_PHRASES)

    def test_a_written_phrase_joins_the_rotation(self, app):
        PhraseService.create("Só mais uma página.")

        assert "Só mais uma página." in PhraseService.all_texts()

    def test_the_same_instant_always_picks_the_same_phrase(self, app):
        """O servidor e a página dividem o relógio; recarregar não sorteia."""
        moment = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

        assert PhraseService.current(moment=moment) == PhraseService.current(
            moment=moment
        )

    def test_the_phrase_turns_when_the_interval_turns(self, app):
        SettingsService.update_many({"goals_phrase_interval": 1})
        moment = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

        before = PhraseService.current(moment=moment)
        after = PhraseService.current(moment=moment + timedelta(minutes=1))

        assert before != after

    def test_an_empty_list_answers_with_nothing_rather_than_failing(self, app):
        assert PhraseService.current(phrases=[]) == ""

    def test_an_interval_edited_by_hand_falls_back_to_a_real_one(self, app, db):
        """A configuração é validada na entrada; um banco editado não é."""
        from app.models import Setting

        db.session.add(Setting(key="goals_phrase_interval", value="7"))
        db.session.commit()
        SettingsService.invalidate_cache()

        assert PhraseService.interval_minutes() == 5


class TestWriting:
    def test_an_empty_phrase_is_refused(self, app):
        with pytest.raises(ValidationError):
            PhraseService.create("   ")

    def test_markup_is_stripped(self, app):
        phrase = PhraseService.create("<b>Vai</b>")

        assert phrase.text == "Vai"

    def test_the_collection_has_a_ceiling(self, app, db):
        from app.models import MotivationalPhrase

        for index in range(MAX_PHRASES):
            db.session.add(MotivationalPhrase(text=f"Frase {index}"))
        db.session.commit()

        with pytest.raises(ValidationError):
            PhraseService.create("A que não cabe")


class TestTheScreen:
    def test_writing_through_the_form(self, client):
        client.post("/metas/frases", data={"text": "Um passo de cada vez."})

        assert PhraseRepository.count() == 1

    def test_removing_through_the_form(self, client, app):
        phrase = PhraseService.create("Some daqui.")

        client.post(f"/metas/frases/{phrase.uuid}/excluir", data={})

        assert PhraseRepository.count() == 0

    def test_preferences_are_saved(self, client, app):
        client.post(
            "/metas/frases",
            data={"acao": "preferencias", "interval": "15", "undated_on_board": "y"},
        )

        assert SettingsService.get("goals_phrase_interval") == 15
        assert SettingsService.get("goals_undated_on_board") is True
        # Desmarcada no formulário, a caixa desliga a preferência.
        assert SettingsService.get("goals_phrases_enabled") is False

    def test_an_invented_interval_falls_back_instead_of_failing(self, client, app):
        client.post("/metas/frases", data={"acao": "preferencias", "interval": "999"})

        assert SettingsService.get("goals_phrase_interval") in (30, 60)

    def test_the_backlog_setting_reaches_the_board(self, client, app):
        from app.services.goal_service import GoalInput, GoalService

        GoalService.create(GoalInput(title="Estudar inglês", has_deadline=False))

        hidden = client.get("/metas/esteira").data.decode("utf-8")
        SettingsService.update_many({"goals_undated_on_board": True})
        shown = client.get("/metas/esteira").data.decode("utf-8")

        assert "Estudar inglês" not in hidden
        assert "Estudar inglês" in shown


JS_SUITE = pathlib.Path(__file__).resolve().parent / "js" / "phrase-rotation.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node não está instalado")
class TestBothLanguagesPickTheSamePhrase:
    """O contrato entre os dois relógios.

    O servidor escolhe a frase que sai no HTML; a página escolhe a que entra no
    lugar dela sem recarregar. É uma duplicação, e o jeito honesto de sustentar
    uma duplicação é fazer algo ler os dois lados: a suíte em Node imprime a
    tabela de casos com os índices que produziu, e isto recalcula cada um deles
    aqui. Mudar um lado sozinho falha aqui, dizendo qual caso.
    """

    def table(self):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [shutil.which("node"), str(JS_SUITE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout} {result.stderr}"
        return json.loads(result.stdout)

    def test_every_case_picks_the_same_index(self, app):
        for case in self.table():
            here = PhraseService.slot_for(
                case["epochMs"], case["interval"], case["count"]
            )
            assert here == case["index"], (
                f"{case}: python {here} contra javascript {case['index']}"
            )
