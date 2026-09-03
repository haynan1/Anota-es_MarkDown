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

    def test_the_editor_shows_the_goals_pointing_at_the_document(
        self, client, make_goal, document
    ):
        """A ligação tem dois lados.

        Da meta se chega ao documento pelo cartão. Sem este lado, quem abre o
        texto não saberia que ele é a missão de alguém.
        """
        make_goal(title="Terminar a proposta", document_uuid=document.uuid)

        body = client.get(f"/editor/{document.uuid}").data.decode("utf-8")

        assert "Metas ligadas a este documento" in body
        assert "Terminar a proposta" in body

    def test_the_editor_invites_when_there_is_no_goal(self, client, document):
        body = client.get(f"/editor/{document.uuid}").data.decode("utf-8")

        assert "Crie uma meta" in body

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


class TestEditingDoesNotRewriteHistory:
    """Uma edição recusada não grava, e uma aceita não reescreve o passado."""

    def test_a_refused_edit_leaves_the_goal_untouched(self, app, db, make_goal):
        """O erro clássico: escrever campo a campo e validar no meio.

        Sem validar tudo antes, a meta ficava com o título novo e o resto
        antigo — e o autoflush do SQLAlchemy levava esse meio-termo ao banco.
        """
        goal = make_goal(title="Intacta", priority="baixa")

        with pytest.raises(ValidationError):
            GoalService.update(
                goal,
                GoalInput(title="Mudada", priority="alta", link_url="javascript:x"),
            )

        db.session.rollback()
        assert goal.title == "Intacta"
        assert goal.priority == "baixa"

    def test_editing_a_completed_goal_keeps_the_moment_it_was_completed(
        self, app, make_goal
    ):
        """Corrigir o título não é concluir de novo.

        O carimbo sustenta a sequência: renová-lo a cada edição mudaria o dia
        em que a conclusão aconteceu, e com ele a contagem de dias seguidos.
        """
        goal = make_goal(title="Feita")
        GoalService.set_status(goal, STATUS_DONE, goal.date)
        stamped = goal.completed_at

        GoalService.update(goal, GoalInput(title="Feita, com acento", status=STATUS_DONE))

        assert goal.completed_at == stamped

    def test_reopening_still_clears_the_moment(self, app, make_goal):
        goal = make_goal()
        GoalService.set_status(goal, STATUS_DONE, goal.date)

        GoalService.update(goal, GoalInput(title="Reaberta", status=STATUS_PENDING))

        assert goal.completed_at is None


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

    def test_a_series_takes_one_row_not_a_year(self, client, make_goal):
        """A lista é sobre metas; os dias são assunto da esteira e do plano.

        Sem isto, um hábito diário ocupa sozinho um ano de linhas: 130 metas
        renderizavam quase doze mil linhas e meio segundo de espera.
        """
        make_goal(
            title="Correr todo dia",
            date=today() - timedelta(days=200),
            recurrence_type="forever",
        )

        body = client.get("/metas/").data.decode("utf-8")

        # Contado no título da linha: o mesmo texto aparece várias vezes por
        # linha nos rótulos acessíveis dos botões, e contar o texto solto diria
        # "cinco" para uma linha só.
        assert body.count('goal-row-title">Correr todo dia<') == 1

    def test_the_row_a_series_shows_is_its_next_day(self, client, make_goal):
        make_goal(
            title="Correr",
            date=today() - timedelta(days=10),
            recurrence_type="forever",
        )

        rows = client.get("/metas/").data.decode("utf-8")

        assert today().strftime("%d/%m") in rows

    def test_a_series_entirely_in_the_past_shows_its_latest_day(
        self, client, db, make_goal
    ):
        """Quando não há próximo dia, o que interessa é a pendência de ontem."""
        goal = make_goal(
            title="Terminou",
            date=today() - timedelta(days=20),
            recurrence_type="count",
            recurrence_days=5,
        )
        db.session.commit()

        body = client.get("/metas/").data.decode("utf-8")

        assert body.count('goal-row-title">Terminou<') == 1
        assert (today() - timedelta(days=16)).strftime("%d/%m") in body

    def test_the_filter_runs_before_the_collapse(self, client, make_goal):
        """Filtrar depois de recolher esconderia o dia que a pessoa procurou."""
        goal = make_goal(
            title="Hábito",
            date=today() - timedelta(days=5),
            recurrence_type="forever",
        )
        GoalService.set_status(goal, STATUS_DONE, today() - timedelta(days=2))

        body = client.get("/metas/?situacao=concluida").data.decode("utf-8")

        assert "Hábito" in body
        assert (today() - timedelta(days=2)).strftime("%d/%m") in body

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


class TestTheFormsThatEdit:
    """Os caminhos de edição, que é onde um formulário costuma mentir."""

    def test_the_edit_form_arrives_filled_in(self, client, make_goal, document):
        goal = make_goal(title="Original", document_uuid=document.uuid)

        body = client.get(f"/metas/{goal.uuid}/editar").data.decode("utf-8")

        assert "Original" in body
        assert f'value="{document.uuid}"' in body

    def test_editing_saves_the_new_values(self, client, make_goal):
        goal = make_goal(title="Antes", priority="baixa")

        client.post(
            f"/metas/{goal.uuid}/editar",
            data={
                "title": "Depois",
                "priority": "alta",
                "category": "saude",
                "status": STATUS_PENDING,
                "recurrence_type": "none",
                "has_deadline": "y",
                "date": today().isoformat(),
            },
            follow_redirects=True,
        )

        assert goal.title == "Depois"
        assert goal.priority == "alta"

    def test_editing_can_drop_the_document_link(self, client, make_goal, document):
        goal = make_goal(document_uuid=document.uuid)

        client.post(
            f"/metas/{goal.uuid}/editar",
            data={
                "title": "Sem documento",
                "priority": "media",
                "category": "pessoal",
                "status": STATUS_PENDING,
                "recurrence_type": "none",
                "document_uuid": "",
                "has_deadline": "y",
                "date": today().isoformat(),
            },
            follow_redirects=True,
        )

        assert goal.document_id is None

    def test_the_template_form_round_trips(self, client, app):
        template = GoalService.create_template(GoalInput(title="Molde"))

        body = client.get(
            f"/metas/predefinidas/{template.uuid}/editar"
        ).data.decode("utf-8")
        assert "Molde" in body

        client.post(
            f"/metas/predefinidas/{template.uuid}/editar",
            data={"title": "Molde novo", "priority": "alta", "category": "estudos"},
            follow_redirects=True,
        )
        assert template.title == "Molde novo"

    def test_a_template_is_removed_through_the_form(self, client, app):
        template = GoalService.create_template(GoalInput(title="Some"))

        client.post(f"/metas/predefinidas/{template.uuid}/excluir", data={})

        assert GoalRepository.template_count() == 0

    def test_activating_without_a_date_does_not_create_anything(self, client, app):
        template = GoalService.create_template(GoalInput(title="Molde"))

        response = client.post(
            f"/metas/predefinidas/{template.uuid}/ativar",
            data={"date": "quinta que vem"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert GoalRepository.total() == 0

    def test_bringing_a_goal_to_today_through_the_form(self, client, make_goal):
        goal = make_goal(has_deadline=False)

        response = client.post(f"/metas/{goal.uuid}/hoje", data={})

        assert response.headers["Location"].endswith("/metas/esteira")
        assert goal.date == today()

    def test_the_board_endpoint_handles_a_goal_without_a_day(self, client, make_goal):
        """Um cartão do acervo sem prazo manda `dia` vazio."""
        goal = make_goal(has_deadline=False)

        response = client.patch(
            f"/api/metas/{goal.uuid}", json={"status": STATUS_DONE, "dia": ""}
        )

        assert response.status_code == 200
        assert goal.status == STATUS_DONE


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


class TestTheRemainingScreens:
    """Os caminhos que só aparecem quando a jornada tem conteúdo."""

    def test_the_month_view_covers_the_month(self, client, make_goal):
        make_goal(title="Do mês", date=today())

        body = client.get("/metas/plano?janela=mes").data.decode("utf-8")

        assert "Do mês" in body
        assert today().replace(day=1).strftime("%d/%m/%Y") in body

    def test_december_is_closed_at_the_thirty_first(self):
        """O mês seguinte a dezembro é janeiro do ano seguinte."""
        from datetime import date as date_type

        from app.blueprints.goals.routes import _end_of_month

        assert _end_of_month(date_type(2026, 12, 1)) == date_type(2026, 12, 31)
        assert _end_of_month(date_type(2026, 2, 1)) == date_type(2026, 2, 28)

    def test_the_history_chart_counts_a_completion(self, client, make_goal):
        goal = make_goal(title="Feita")
        GoalService.set_status(goal, STATUS_DONE, goal.date)

        body = client.get("/metas/historico").data.decode("utf-8")

        assert "Metas concluídas" in body
        assert today().strftime("%d/%m") in body

    def test_cycling_through_the_form_advances_one_column(self, client, make_goal):
        goal = make_goal()

        client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "ciclo", "dia": goal.date.isoformat()},
            follow_redirects=True,
        )

        assert goal.status == STATUS_DOING

    def test_setting_an_exact_status_through_the_form(self, client, make_goal):
        goal = make_goal()

        client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "estado", "status": STATUS_DONE, "dia": goal.date.isoformat()},
            follow_redirects=True,
        )

        assert goal.status == STATUS_DONE

    def test_an_undated_goal_keeps_its_place_in_the_list(self, client, make_goal):
        make_goal(title="Sem prazo nenhum", has_deadline=False)

        body = client.get("/metas/").data.decode("utf-8")

        assert body.count('goal-row-title">Sem prazo nenhum<') == 1

    def test_the_new_template_form_opens(self, client):
        body = client.get("/metas/predefinidas/nova").data.decode("utf-8")

        assert "Nova meta predefinida" in body


class TestServiceRefusalsReachTheScreen:
    """O formulário aceita, o serviço recusa, e a pessoa fica sabendo.

    O validador do formulário só olha tamanho; a regra de qual endereço é
    aceitável é do domínio. Estes caminhos existem para que a recusa vire uma
    mensagem em vez de um erro 500.
    """

    def test_a_bad_link_is_refused_with_a_message(self, client):
        response = client.post(
            "/metas/nova",
            data={
                "title": "Armadilha",
                "link_url": "javascript:alert(1)",
                "priority": "media",
                "category": "pessoal",
                "status": STATUS_PENDING,
                "recurrence_type": "none",
                "has_deadline": "y",
                "date": today().isoformat(),
            },
        )

        assert response.status_code == 200
        assert GoalRepository.total() == 0

    def test_a_bad_link_on_edit_is_refused_with_a_message(self, client, make_goal):
        goal = make_goal(title="Intacta")

        response = client.post(
            f"/metas/{goal.uuid}/editar",
            data={
                "title": "Mudada",
                "link_url": "javascript:alert(1)",
                "priority": "media",
                "category": "pessoal",
                "status": STATUS_PENDING,
                "recurrence_type": "none",
                "has_deadline": "y",
                "date": today().isoformat(),
            },
        )

        assert response.status_code == 200
        assert goal.title == "Intacta"

    def test_a_bad_link_on_a_template_is_refused(self, client):
        response = client.post(
            "/metas/predefinidas/nova",
            data={
                "title": "Molde",
                "link_url": "javascript:alert(1)",
                "priority": "media",
                "category": "pessoal",
            },
        )

        assert response.status_code == 200
        assert GoalRepository.template_count() == 0


class TestHostileInput:
    """A superfície nova encarando o que ela vai receber de verdade."""

    def test_a_garbage_day_is_refused_rather_than_guessed(self, client, make_goal):
        goal = make_goal()

        response = client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "alternar", "dia": "amanhã de manhã"},
        )

        assert response.status_code == 400
        assert goal.status == STATUS_PENDING

    def test_the_api_refuses_a_garbage_day(self, client, make_goal):
        goal = make_goal()

        response = client.patch(
            f"/api/metas/{goal.uuid}",
            json={"status": STATUS_DONE, "dia": "9" * 5000},
        )

        assert response.status_code == 400
        assert response.is_json

    def test_the_api_refuses_a_status_that_is_not_a_string(self, client, make_goal):
        goal = make_goal()

        response = client.patch(f"/api/metas/{goal.uuid}", json={"status": {"a": 1}})

        assert response.status_code == 400

    def test_the_api_answers_json_for_an_unknown_goal(self, client):
        response = client.patch("/api/metas/nao-existe", json={"status": STATUS_DONE})

        assert response.status_code == 404
        assert response.is_json

    def test_a_document_uuid_is_not_a_goal_uuid(self, client, document):
        """Identificadores não se confundem entre tipos."""
        assert client.get(f"/metas/{document.uuid}/editar").status_code == 404

    def test_next_cannot_point_off_site(self, client, make_goal):
        goal = make_goal()

        response = client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "alternar", "next": "https://evil.example/x"},
        )

        assert response.status_code == 302
        assert "evil.example" not in response.headers["Location"]

    def test_protocol_relative_next_is_rejected(self, client, make_goal):
        goal = make_goal()

        response = client.post(
            f"/metas/{goal.uuid}/excluir", data={"next": "//evil.example/x"}
        )

        assert "evil.example" not in response.headers["Location"]

    def test_a_local_next_is_honoured(self, client, make_goal):
        goal = make_goal()

        response = client.post(
            f"/metas/{goal.uuid}/estado",
            data={"acao": "alternar", "next": "/metas/esteira"},
        )

        assert response.headers["Location"].endswith("/metas/esteira")

    def test_a_title_at_the_column_ceiling_is_cut_not_refused(self, app):
        goal = GoalService.create(GoalInput(title="a" * 500))

        assert len(goal.title) == 160

    def test_a_description_beyond_the_ceiling_is_cut(self, app):
        goal = GoalService.create(GoalInput(title="Meta", description="b" * 5000))

        assert len(goal.description) <= 2000

    def test_a_link_longer_than_the_column_is_refused(self, app):
        with pytest.raises(ValidationError):
            GoalService.create(
                GoalInput(title="Meta", link_url="https://x.com/" + "a" * 600)
            )
