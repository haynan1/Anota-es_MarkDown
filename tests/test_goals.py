"""Metas: o que se cria, o que se conclui e o que a tela promete.

Os testes falam pelo serviço sempre que a regra é do domínio, e pelo cliente
HTTP quando o que está em jogo é o contrato da tela - um botão que envia um
formulário, um filtro que sobrevive ao recarregar, uma ação destrutiva que
exige a palavra digitada.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.goal import STATUS_DOING, STATUS_DONE, STATUS_PENDING
from app.repositories.goal_repository import GoalRepository
from app.services.exceptions import NotFoundError, ValidationError
from app.services.goal_schedule import rows_between, rows_for_day, today
from app.services.goal_service import GoalInput, GoalService


@pytest.fixture()
def make_goal(app):
    def _make(title="Meta de teste", **kwargs):
        return GoalService.create(GoalInput(title=title, **kwargs))

    return _make


class TestCreation:
    def test_a_goal_is_created_with_todays_anchor(self, make_goal):
        goal = make_goal()

        assert goal.date == today()
        assert goal.status == STATUS_PENDING
        assert goal.completed_at is None

    def test_an_empty_title_is_refused(self, app):
        with pytest.raises(ValidationError):
            GoalService.create(GoalInput(title="   "))

    def test_the_title_is_stripped_of_markup(self, make_goal):
        goal = make_goal(title="<script>alert(1)</script>Correr")

        assert "<script>" not in goal.title
        assert goal.title == "Correr"

    def test_a_javascript_link_is_refused(self, app):
        """O campo de link não aceita o esquema que executa código."""
        with pytest.raises(ValidationError):
            GoalService.create(
                GoalInput(title="Armadilha", link_url="javascript:alert(1)")
            )

    def test_an_https_link_is_kept(self, make_goal):
        goal = make_goal(link_url="https://exemplo.com/pagina")

        assert goal.link_url == "https://exemplo.com/pagina"

    @pytest.mark.parametrize("field,value", [
        ("priority", "urgentissima"),
        ("category", "inventada"),
        ("status", "quase"),
        ("recurrence_type", "sempre"),
    ])
    def test_a_value_outside_the_closed_set_is_refused(self, app, field, value):
        with pytest.raises(ValidationError):
            GoalService.create(GoalInput(title="Meta", **{field: value}))

    def test_a_date_far_beyond_a_plan_is_refused(self, app):
        with pytest.raises(ValidationError):
            GoalService.create(
                GoalInput(title="Daqui a um século", date=today().replace(year=today().year + 100))
            )


class TestTheDocumentLink:
    """O acoplamento com a biblioteca, nas duas direções."""

    def test_a_goal_can_point_at_a_document(self, app, make_goal, document):
        goal = make_goal(title="Terminar", document_uuid=document.uuid)

        assert goal.document_id == document.id
        assert GoalRepository.linked_to_document(document.id) == [goal]

    def test_an_unknown_document_is_refused(self, app):
        with pytest.raises(NotFoundError):
            GoalService.create(
                GoalInput(title="Meta", document_uuid="nao-existe")
            )

    def test_deleting_the_document_keeps_the_goal(self, app, db, make_goal, document):
        """Perder o texto tira o atalho, nunca o compromisso."""
        goal = make_goal(document_uuid=document.uuid)

        db.session.delete(document)
        db.session.commit()
        db.session.refresh(goal)

        assert goal.document_id is None
        assert goal.title


class TestStatus:
    def test_completing_stamps_the_moment(self, make_goal):
        goal = make_goal()

        GoalService.set_status(goal, STATUS_DONE, goal.date)

        assert goal.status == STATUS_DONE
        assert goal.completed_at is not None

    def test_reopening_clears_the_stamp(self, make_goal):
        goal = make_goal()
        GoalService.toggle(goal, goal.date)

        GoalService.toggle(goal, goal.date)

        assert goal.status == STATUS_PENDING
        assert goal.completed_at is None

    def test_the_cycle_walks_the_three_columns(self, make_goal):
        goal = make_goal()

        assert GoalService.cycle(goal, goal.date) == STATUS_DOING
        assert GoalService.cycle(goal, goal.date) == STATUS_DONE
        assert GoalService.cycle(goal, goal.date) == STATUS_PENDING

    def test_a_single_goal_keeps_its_state_on_itself(self, app, make_goal):
        """Sem série, não há exceção: gravar um dia grava a própria meta."""
        goal = make_goal()

        GoalService.toggle(goal, goal.date)

        assert goal.status == STATUS_DONE
        assert GoalRepository.occurrence(goal.id, goal.date) is None

    def test_an_invalid_status_is_refused(self, make_goal):
        goal = make_goal()

        with pytest.raises(ValidationError):
            GoalService.set_status(goal, "concluidissima", goal.date)


class TestGoalsWithoutADeadline:
    def test_they_stay_out_of_the_day(self, make_goal):
        make_goal(title="Estudar inglês", has_deadline=False)

        assert rows_for_day(today()) == []

    def test_they_appear_when_asked_for(self, make_goal):
        make_goal(title="Estudar inglês", has_deadline=False)

        rows = rows_for_day(today(), include_undated=True)

        assert len(rows) == 1
        assert rows[0].date is None

    def test_they_never_read_as_overdue(self, make_goal):
        make_goal(has_deadline=False)

        rows = rows_between(
            today() - timedelta(days=30), today(), include_undated=True
        )

        assert all(not row.is_overdue for row in rows)

    def test_bringing_one_to_today_puts_it_on_the_board(self, make_goal):
        goal = make_goal(has_deadline=False)

        GoalService.move_to_today(goal)

        assert goal.has_deadline is True
        assert goal.date == today()
        assert goal.show_on_board is True

    def test_a_completed_goal_is_not_dragged_into_today(self, make_goal):
        goal = make_goal(has_deadline=False)
        GoalService.set_status(goal, STATUS_DONE)

        with pytest.raises(ValidationError):
            GoalService.move_to_today(goal)


class TestTemplates:
    def test_saving_a_template_schedules_nothing(self, app):
        GoalService.create_template(GoalInput(title="Revisar orçamento"))

        assert GoalRepository.template_count() == 1
        assert GoalRepository.total() == 0

    def test_activating_creates_the_goal_for_the_chosen_day(self, app):
        template = GoalService.create_template(
            GoalInput(title="Revisar orçamento", category="financas", priority="alta")
        )
        when = today() + timedelta(days=3)

        goal = GoalService.activate_template(template, when)

        assert goal.date == when
        assert goal.category == "financas"
        assert goal.priority == "alta"
        # E o molde continua guardado.
        assert GoalRepository.template_count() == 1

    def test_the_document_link_travels_into_the_activated_goal(self, app, document):
        template = GoalService.create_template(
            GoalInput(title="Revisar", document_uuid=document.uuid)
        )

        goal = GoalService.activate_template(template, today())

        assert goal.document_id == document.id


class TestTheScreens:
    def test_the_list_shows_a_goal(self, client, make_goal):
        make_goal(title="Comprar o presente")

        body = client.get("/metas/").data.decode("utf-8")

        assert "Comprar o presente" in body

    def test_a_filter_narrows_the_list(self, client, make_goal):
        make_goal(title="Alta prioridade", priority="alta")
        make_goal(title="Baixa prioridade", priority="baixa")

        body = client.get("/metas/?prioridade=alta").data.decode("utf-8")

        assert "Alta prioridade" in body
        assert "Baixa prioridade" not in body

    def test_a_nonsense_filter_is_ignored_rather_than_fatal(self, client, make_goal):
        """A query string é uma superfície editável à mão."""
        make_goal(title="Continua visível")

        response = client.get("/metas/?prioridade=../../etc&situacao=<script>")

        assert response.status_code == 200
        assert "Continua visível" in response.data.decode("utf-8")

    def test_the_board_only_shows_what_was_let_in(self, client, make_goal):
        make_goal(title="No fluxo", show_on_board=True)
        make_goal(title="Fora do fluxo", show_on_board=False)

        body = client.get("/metas/esteira").data.decode("utf-8")

        assert "No fluxo" in body
        assert "Fora do fluxo" not in body

    def test_the_board_surfaces_only_the_latest_overdue_day(self, client, make_goal):
        """Uma série esquecida por meses tem uma pendência, não noventa."""
        make_goal(
            title="Correr",
            date=today() - timedelta(days=30),
            recurrence_type="forever",
        )

        body = client.get("/metas/esteira").data.decode("utf-8")

        assert body.count('class="goal-row goal-pendente goal-overdue"') == 1

    def test_completing_from_a_form_works_without_javascript(self, client, make_goal):
        goal = make_goal()

        response = client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "alternar", "dia": goal.date.isoformat()},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert goal.status == STATUS_DONE

    def test_the_board_endpoint_moves_a_card(self, client, make_goal):
        goal = make_goal()

        response = client.patch(
            f"/api/metas/{goal.uuid}",
            json={"status": STATUS_DOING, "dia": goal.date.isoformat()},
        )

        assert response.status_code == 200
        assert response.get_json()["status"] == STATUS_DOING
        assert goal.status == STATUS_DOING

    def test_the_board_endpoint_refuses_an_invented_status(self, client, make_goal):
        goal = make_goal()

        response = client.patch(f"/api/metas/{goal.uuid}", json={"status": "voando"})

        assert response.status_code == 400
        assert goal.status == STATUS_PENDING

    def test_an_unknown_goal_answers_404(self, client):
        assert client.get("/metas/nao-existe/editar").status_code == 404

    def test_creating_through_the_form(self, client):
        response = client.post(
            "/metas/nova",
            data={
                "title": "Ler trinta páginas",
                "priority": "media",
                "category": "estudos",
                "status": STATUS_PENDING,
                "recurrence_type": "none",
                "has_deadline": "y",
                "date": today().isoformat(),
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert GoalRepository.total() == 1

    def test_a_form_error_comes_back_as_the_form(self, client):
        response = client.post("/metas/nova", data={"title": ""})

        assert response.status_code == 200
        assert GoalRepository.total() == 0


class TestStartingOver:
    def test_clearing_needs_the_word(self, client, make_goal):
        make_goal()

        client.post("/metas/limpar", data={"confirmacao": "sim"})

        assert GoalRepository.total() == 1

    def test_clearing_keeps_the_documents(self, client, make_goal, document):
        make_goal(document_uuid=document.uuid)
        GoalService.create_template(GoalInput(title="Molde"))

        client.post("/metas/limpar", data={"confirmacao": "limpar"})

        assert GoalRepository.total() == 0
        assert GoalRepository.template_count() == 0
        assert document.title


class TestCsrf:
    """As escritas da jornada valem o mesmo contrato do resto do app."""

    def _seed(self, csrf_app):
        with csrf_app.app_context():
            return GoalService.create(GoalInput(title="Alvo")).uuid

    def test_a_form_post_without_a_token_is_rejected(self, csrf_app):
        uuid = self._seed(csrf_app)
        client = csrf_app.test_client()

        response = client.post(f"/metas/{uuid}/excluir", data={})

        assert response.status_code == 400

    def test_the_board_endpoint_without_a_token_is_rejected(self, csrf_app):
        uuid = self._seed(csrf_app)
        client = csrf_app.test_client()

        response = client.patch(f"/api/metas/{uuid}", json={"status": STATUS_DONE})

        assert response.status_code == 400
        with csrf_app.app_context():
            assert GoalService.require(uuid).status == STATUS_PENDING
