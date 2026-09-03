"""Regras das metas.

Três decisões governam este arquivo.

**Uma série não tem estado.** ``Goal.status`` é o padrão das ocorrências de uma
série, e por isso o serviço o mantém em "pendente" para qualquer meta que se
repete. Deixar alguém marcar a série inteira como concluída faria as terças de
setembro nascerem concluídas - inclusive as que ainda não chegaram. Quem
conclui é o dia, e o dia é uma :class:`~app.models.goal.GoalOccurrence`.

**Concluir grava o instante.** ``completed_at`` é o que sustenta a sequência,
o recorde e metade das conquistas. Um estado "concluída" sem carimbo é um dado
que não responde à única pergunta que se faz dele: *quando*.

**Validar é uma coisa só, num lugar só.** O formulário HTML e o endpoint JSON
entram pela mesma porta (:class:`GoalInput`), então não existe caminho que
aceite um título de dez mil caracteres porque a outra tela é que checava isso.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import time as time_type

from app.extensions import db
from app.models import Goal, GoalOccurrence, GoalTemplate
from app.models.goal import (
    GOAL_CATEGORIES,
    GOAL_PRIORITIES,
    GOAL_STATUSES,
    MAX_DESCRIPTION_LENGTH,
    MAX_RECURRENCE_DAYS,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    NEXT_STATUS,
    RECURRENCE_NONE,
    RECURRENCE_TYPES,
    STATUS_DONE,
    STATUS_PENDING,
)
from app.repositories.achievement_repository import AchievementRepository
from app.repositories.goal_repository import (
    MAX_TEMPLATES,
    GoalRepository,
)
from app.services.exceptions import NotFoundError, ValidationError
from app.services.goal_schedule import status_on, today
from app.services.sanitizer import sanitize_link, sanitize_multiline_text, sanitize_plain_text
from app.utils.dates import utcnow

# Teto do acervo. Nenhuma pessoa administra mais metas do que isto à mão; o
# limite existe para que uma importação em laço não transforme cada tela numa
# varredura de banco.
MAX_GOALS = 5000

# Até onde uma data pode ir, para cada lado. Dez anos cobrem qualquer plano
# que alguém escreve de verdade; o que passa disso é um dedo escorregando no
# seletor de ano.
MAX_FUTURE_DAYS = 3650
MAX_PAST_DAYS = 3650


@dataclass(slots=True)
class GoalInput:
    """O que chega de fora, antes de virar uma meta.

    Uma estrutura em vez de treze argumentos: a ordem deixa de importar, o
    formulário e a API montam a mesma coisa, e acrescentar um campo não muda
    a assinatura de ninguém.
    """

    title: str = ""
    description: str = ""
    link_url: str = ""
    document_uuid: str = ""
    date: date_type | None = None
    time: time_type | None = None
    has_deadline: bool = True
    show_on_board: bool = True
    priority: str = "media"
    category: str = "pessoal"
    status: str = STATUS_PENDING
    recurrence_type: str = RECURRENCE_NONE
    recurrence_days: int | None = None
    recurrence_end_date: date_type | None = None


class GoalService:
    # ── Leitura ─────────────────────────────────────────────────────────────

    @staticmethod
    def require(public_uuid: str) -> Goal:
        goal = GoalRepository.get_by_uuid(public_uuid)
        if goal is None:
            raise NotFoundError("Meta não encontrada.")
        return goal

    @staticmethod
    def require_template(public_uuid: str) -> GoalTemplate:
        template = GoalRepository.template_by_uuid(public_uuid)
        if template is None:
            raise NotFoundError("Meta predefinida não encontrada.")
        return template

    # ── Escrita ─────────────────────────────────────────────────────────────

    @staticmethod
    def create(data: GoalInput) -> Goal:
        if GoalRepository.total() >= MAX_GOALS:
            raise ValidationError(
                f"Limite de {MAX_GOALS} metas atingido. "
                "Remova o que já não é da sua jornada antes de criar outra."
            )
        goal = Goal()
        GoalService._apply(goal, data)
        db.session.add(goal)
        db.session.commit()
        return goal

    @staticmethod
    def update(goal: Goal, data: GoalInput) -> Goal:
        GoalService._apply(goal, data)
        db.session.commit()
        return goal

    @staticmethod
    def delete(goal: Goal) -> None:
        db.session.delete(goal)
        db.session.commit()

    @staticmethod
    def _apply(goal: Goal, data: GoalInput) -> None:
        """Escreve na meta o que ``_clean`` aprovou. Não faz commit.

        A validação inteira acontece antes de a primeira coluna ser tocada, e
        isso não é estilo. Escrever campo a campo e só então descobrir que o
        link é inválido deixa a meta com o título novo e o resto antigo - e o
        SQLAlchemy leva esse estado pela metade ao banco no próximo autoflush,
        que a própria renderização da página de erro dispara. A edição
        recusada gravava.
        """
        clean = GoalService._clean(data)

        # Corrigir o título de uma meta concluída não é concluí-la de novo. O
        # carimbo é o que sustenta a sequência e as conquistas de horário:
        # renová-lo a cada edição mudaria o dia em que a conclusão aconteceu.
        if (
            clean["status"] == STATUS_DONE
            and goal.status == STATUS_DONE
            and goal.completed_at is not None
        ):
            clean["completed_at"] = goal.completed_at

        for field, value in clean.items():
            setattr(goal, field, value)

    @staticmethod
    def _clean(data: GoalInput) -> dict[str, object]:
        """Toda a validação, e nada além dela. Não toca em nenhuma meta."""
        title = sanitize_plain_text(data.title or "", max_length=MAX_TITLE_LENGTH)
        if not title:
            raise ValidationError("Escreva o título da meta.")

        if data.priority not in GOAL_PRIORITIES:
            raise ValidationError("Prioridade inválida.")
        if data.category not in GOAL_CATEGORIES:
            raise ValidationError("Categoria inválida.")
        if data.status not in GOAL_STATUSES:
            raise ValidationError("Status inválido.")
        if data.recurrence_type not in RECURRENCE_TYPES:
            raise ValidationError("Tipo de repetição inválido.")

        has_deadline = bool(data.has_deadline)
        if has_deadline:
            anchor = GoalService._check_date(data.date or today())
            moment = data.time
            recurrence = data.recurrence_type
        else:
            # Sem prazo: a âncora existe só para a aritmética, e nenhuma regra
            # de repetição faz sentido sem um dia para repetir a partir de.
            anchor = today()
            moment = None
            recurrence = RECURRENCE_NONE

        days, end_date = GoalService._clean_recurrence(recurrence, anchor, data)

        # Ver o cabeçalho: numa série, ``status`` é o padrão do dia, não uma
        # conclusão. Concluir uma série se faz um dia de cada vez.
        status = STATUS_PENDING if recurrence != RECURRENCE_NONE else data.status

        return {
            "title": title,
            "description": sanitize_multiline_text(
                data.description or "", max_length=MAX_DESCRIPTION_LENGTH
            ),
            "link_url": sanitize_link(data.link_url or "", max_length=MAX_URL_LENGTH),
            "document_id": GoalService._resolve_document(data.document_uuid),
            "priority": data.priority,
            "category": data.category,
            "has_deadline": has_deadline,
            "show_on_board": bool(data.show_on_board),
            "date": anchor,
            "time": moment,
            "recurrence_type": recurrence,
            "recurrence_days": days,
            "recurrence_end_date": end_date,
            "status": status,
            "completed_at": utcnow() if status == STATUS_DONE else None,
        }

    @staticmethod
    def _clean_recurrence(
        recurrence: str, anchor: date_type, data: GoalInput
    ) -> tuple[int | None, date_type | None]:
        if recurrence == RECURRENCE_NONE:
            return None, None

        days: int | None = None
        if recurrence == "count":
            requested = data.recurrence_days or 0
            if requested < 1:
                raise ValidationError("Diga por quantos dias esta meta se repete.")
            if requested > MAX_RECURRENCE_DAYS:
                raise ValidationError(
                    f"A repetição por quantidade vai até {MAX_RECURRENCE_DAYS} dias."
                )
            days = requested

        if recurrence == "forever":
            # "Sem fim" é literal: guardar uma data final aqui seria guardar
            # uma contradição que alguma tela acabaria acreditando.
            return days, None

        end = data.recurrence_end_date
        if end is not None:
            if end < anchor:
                raise ValidationError(
                    "A data final da repetição é anterior ao começo dela."
                )
            GoalService._check_date(end)
        return days, end

    @staticmethod
    def _check_date(value: date_type) -> date_type:
        """Uma data precisa caber na vida de quem a escreveu.

        Os dois lados importam. Uma data em 3021 sujaria para sempre o fim de
        toda lista ordenada por data; uma em 1901 arrastaria a janela do
        planejamento para trás sem que nada apareça nela.
        """
        distance = (value - today()).days
        if distance > MAX_FUTURE_DAYS:
            raise ValidationError("Essa data está longe demais para ser um plano.")
        if distance < -MAX_PAST_DAYS:
            raise ValidationError("Essa data está longe demais no passado.")
        return value

    @staticmethod
    def _resolve_document(public_uuid: str) -> int | None:
        if not public_uuid:
            return None
        document_id = GoalRepository.document_id_for_uuid(public_uuid)
        if document_id is None:
            raise NotFoundError("Documento não encontrado.")
        return document_id

    # ── Estado de um dia ────────────────────────────────────────────────────

    @staticmethod
    def set_status(goal: Goal, status: str, day: date_type | None = None) -> str:
        """Grava o estado de uma meta - da meta, ou de um dia dela.

        A escolha entre os dois é do domínio, não de quem chama: um dia de uma
        série vira exceção, e o único dia de uma meta avulsa é a própria meta.
        Confundir os dois faria "concluí hoje" apagar o histórico da série ou
        criar uma exceção órfã para uma meta que não se repete.
        """
        if status not in GOAL_STATUSES:
            raise ValidationError("Status inválido.")

        stamp = utcnow() if status == STATUS_DONE else None
        if GoalService._is_occurrence(goal, day):
            occurrence = GoalRepository.occurrence(goal.id, day)
            if occurrence is None:
                occurrence = GoalOccurrence(goal_id=goal.id, occurrence_date=day)
                db.session.add(occurrence)
            occurrence.status = status
            occurrence.completed_at = stamp
        else:
            goal.status = status
            goal.completed_at = stamp

        db.session.commit()
        return status

    @staticmethod
    def _is_occurrence(goal: Goal, day: date_type | None) -> bool:
        if day is None or not goal.has_deadline:
            return False
        return goal.is_recurring or day != goal.date

    @staticmethod
    def cycle(goal: Goal, day: date_type | None = None) -> str:
        """A fazer -> em andamento -> concluída -> a fazer."""
        current = status_on(goal, day)
        return GoalService.set_status(goal, NEXT_STATUS[current], day)

    @staticmethod
    def toggle(goal: Goal, day: date_type | None = None) -> str:
        """Concluída, ou de volta ao começo. O gesto de um clique na lista."""
        current = status_on(goal, day)
        target = STATUS_PENDING if current == STATUS_DONE else STATUS_DONE
        return GoalService.set_status(goal, target, day)

    @staticmethod
    def move_to_today(goal: Goal) -> Goal:
        """Puxa uma meta do acervo para o dia de hoje.

        Uma meta sem prazo que vira meta de hoje deixa de ser uma série e
        passa a aparecer na esteira - é o que "vou fazer isto agora" quer
        dizer. Uma meta já concluída não é puxada: reabrir é uma decisão
        explícita, e movê-la em silêncio apagaria a conclusão.
        """
        if status_on(goal, None) == STATUS_DONE:
            raise ValidationError(
                "Esta meta já está concluída. Reabra-a antes de trazê-la para hoje."
            )
        goal.has_deadline = True
        goal.date = today()
        goal.show_on_board = True
        goal.recurrence_type = RECURRENCE_NONE
        goal.recurrence_days = None
        goal.recurrence_end_date = None
        db.session.commit()
        return goal

    # ── Predefinidas ────────────────────────────────────────────────────────

    @staticmethod
    def create_template(data: GoalInput) -> GoalTemplate:
        if GoalRepository.template_count() >= MAX_TEMPLATES:
            raise ValidationError(
                f"Limite de {MAX_TEMPLATES} metas predefinidas atingido."
            )
        template = GoalTemplate()
        GoalService._apply_template(template, data)
        db.session.add(template)
        db.session.commit()
        return template

    @staticmethod
    def update_template(template: GoalTemplate, data: GoalInput) -> GoalTemplate:
        GoalService._apply_template(template, data)
        db.session.commit()
        return template

    @staticmethod
    def delete_template(template: GoalTemplate) -> None:
        db.session.delete(template)
        db.session.commit()

    @staticmethod
    def _apply_template(template: GoalTemplate, data: GoalInput) -> None:
        """Valida tudo, escreve depois - pelo mesmo motivo que ``_apply``."""
        title = sanitize_plain_text(data.title or "", max_length=MAX_TITLE_LENGTH)
        if not title:
            raise ValidationError("Escreva o título da meta predefinida.")
        if data.priority not in GOAL_PRIORITIES:
            raise ValidationError("Prioridade inválida.")
        if data.category not in GOAL_CATEGORIES:
            raise ValidationError("Categoria inválida.")

        clean = {
            "title": title,
            "description": sanitize_multiline_text(
                data.description or "", max_length=MAX_DESCRIPTION_LENGTH
            ),
            "link_url": sanitize_link(data.link_url or "", max_length=MAX_URL_LENGTH),
            "document_id": GoalService._resolve_document(data.document_uuid),
            "time": data.time,
            "show_on_board": bool(data.show_on_board),
            "priority": data.priority,
            "category": data.category,
        }
        for field, value in clean.items():
            setattr(template, field, value)

    @staticmethod
    def activate_template(template: GoalTemplate, day: date_type) -> Goal:
        """Cria a meta do dia a partir do molde. O molde continua guardado."""
        return GoalService.create(
            GoalInput(
                title=template.title,
                description=template.description,
                link_url=template.link_url,
                document_uuid=(
                    template.document.uuid if template.document else ""
                ),
                date=day,
                time=template.time,
                has_deadline=True,
                show_on_board=template.show_on_board,
                priority=template.priority,
                category=template.category,
            )
        )

    # ── Recomeçar ───────────────────────────────────────────────────────────

    @staticmethod
    def clear_all() -> tuple[int, int, int]:
        """Apaga metas, predefinidas e conquistas. Devolve o que foi apagado.

        Os documentos não são tocados: esta é a jornada, não a biblioteca. As
        conquistas vão junto porque elas descrevem um histórico que deixou de
        existir - mantê-las seria exibir uma medalha de "100 metas concluídas"
        sobre um acervo vazio.
        """
        goals = GoalRepository.delete_all()
        templates = list(db.session.scalars(db.select(GoalTemplate)).all())
        for template in templates:
            db.session.delete(template)
        achievements = AchievementRepository.clear()
        db.session.commit()
        return goals, len(templates), achievements
