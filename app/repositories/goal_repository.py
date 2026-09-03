"""Consultas sobre metas, ocorrências e predefinidas.

Duas regras governam este arquivo.

**Nada aqui devolve uma coleção sem teto.** Uma janela de planejamento é uma
janela; um histórico é um histórico. Toda consulta carrega um limite, para que
nenhuma tela dependa do tamanho que o banco resolveu ter.

**Exceções são lidas por janela, nunca por meta.** Carregar
``Goal.occurrences`` para desenhar sete dias traria todas as exceções que a
série já acumulou desde que existe - centenas de linhas para responder sobre
sete. Aqui as ocorrências vêm em uma consulta própria, recortada pelas mesmas
datas da janela, e voltam indexadas por (meta, dia).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Document, Goal, GoalOccurrence, GoalTemplate
from app.models.goal import ACTIVE_STATUSES, RECURRENCE_NONE, STATUS_DONE

# Uma janela pode conter muitas metas, mas não uma biblioteca inteira delas.
# Este é o teto que impede que uma importação maluca transforme o desenho de
# uma semana numa consulta sem fim.
MAX_GOALS_IN_WINDOW = 2000
MAX_OCCURRENCES_IN_WINDOW = 5000
MAX_TEMPLATES = 200

# Quantas conclusões o cálculo de conquistas olha. Uma conquista pergunta
# "quantas", "em que categoria" e "a que horas" - perguntas que precisam das
# linhas, não de um agregado. O teto existe para que esse custo seja conhecido.
MAX_COMPLETION_ROWS = 5000


class GoalRepository:
    # ── Metas ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_by_uuid(public_uuid: str) -> Goal | None:
        return db.session.scalars(
            select(Goal)
            .options(joinedload(Goal.document))
            .where(Goal.uuid == public_uuid)
        ).unique().one_or_none()

    @staticmethod
    def window(
        start: date, end: date, include_undated: bool = False
    ) -> list[Goal]:
        """Metas que podem produzir alguma ocorrência entre ``start`` e ``end``.

        Uma série pode ter começado meses antes da janela e ainda cair dentro
        dela, então a data-âncora não pode ser filtrada pelos dois lados: o
        corte à esquerda vale só para metas avulsas, cuja única ocorrência
        possível é o próprio dia delas. Sem essa distinção, ou a série some da
        semana que vem, ou todo o histórico avulso entra em toda consulta.
        """
        stmt = (
            select(Goal)
            .options(joinedload(Goal.document))
            .where(Goal.date <= end)
        )
        if include_undated:
            stmt = stmt.where(
                or_(
                    Goal.has_deadline.is_(False),
                    Goal.recurrence_type != RECURRENCE_NONE,
                    Goal.date >= start,
                )
            )
        else:
            stmt = stmt.where(
                Goal.has_deadline.is_(True),
                or_(Goal.recurrence_type != RECURRENCE_NONE, Goal.date >= start),
            )

        return list(
            db.session.scalars(
                stmt.order_by(Goal.date, Goal.created_at).limit(MAX_GOALS_IN_WINDOW)
            )
            .unique()
            .all()
        )

    @staticmethod
    def occurrences_between(
        goal_ids: Sequence[int], start: date, end: date
    ) -> dict[tuple[int, date], GoalOccurrence]:
        """As exceções da janela, indexadas por (meta, dia)."""
        if not goal_ids:
            return {}

        rows = db.session.scalars(
            select(GoalOccurrence)
            .where(
                GoalOccurrence.goal_id.in_(goal_ids),
                GoalOccurrence.occurrence_date >= start,
                GoalOccurrence.occurrence_date <= end,
            )
            .limit(MAX_OCCURRENCES_IN_WINDOW)
        ).all()
        return {(row.goal_id, row.occurrence_date): row for row in rows}

    @staticmethod
    def occurrence(goal_id: int, day: date) -> GoalOccurrence | None:
        return db.session.scalars(
            select(GoalOccurrence).where(
                GoalOccurrence.goal_id == goal_id,
                GoalOccurrence.occurrence_date == day,
            )
        ).one_or_none()

    @staticmethod
    def linked_to_document(document_id: int, limit: int = 50) -> list[Goal]:
        """Metas apontando para um documento - o outro lado do atalho."""
        return list(
            db.session.scalars(
                select(Goal)
                .where(Goal.document_id == document_id)
                .order_by(Goal.date.desc())
                .limit(limit)
            ).all()
        )

    # ── Contagens ───────────────────────────────────────────────────────────

    @staticmethod
    def total() -> int:
        return db.session.scalar(select(func.count(Goal.id))) or 0

    @staticmethod
    def active_count() -> int:
        return (
            db.session.scalar(
                select(func.count(Goal.id)).where(Goal.status.in_(ACTIVE_STATUSES))
            )
            or 0
        )

    @staticmethod
    def completed_count() -> int:
        """Quantas conclusões existem - não quantas metas foram concluídas.

        Um hábito cumprido trinta vezes vale trinta. A meta avulsa guarda o
        estado nela mesma; a série guarda em cada exceção, e é por isso que a
        soma tem duas metades. Séries são excluídas da primeira metade de
        propósito: ali ``status`` é o padrão da série, não uma conclusão.
        """
        singles = (
            db.session.scalar(
                select(func.count(Goal.id)).where(
                    Goal.recurrence_type == RECURRENCE_NONE,
                    Goal.status == STATUS_DONE,
                )
            )
            or 0
        )
        recurring = (
            db.session.scalar(
                select(func.count(GoalOccurrence.id)).where(
                    GoalOccurrence.status == STATUS_DONE
                )
            )
            or 0
        )
        return singles + recurring

    @staticmethod
    def linked_count() -> int:
        """Quantas metas apontam para um documento desta biblioteca."""
        return (
            db.session.scalar(
                select(func.count(Goal.id)).where(Goal.document_id.is_not(None))
            )
            or 0
        )

    @staticmethod
    def first_created_at():
        """Quando a jornada começou, ou ``None`` se ainda não começou."""
        return db.session.scalar(select(func.min(Goal.created_at)))

    @staticmethod
    def completion_rows(limit: int = MAX_COMPLETION_ROWS) -> list[tuple]:
        """``(categoria, prioridade, concluída_em, tem_prazo)`` por conclusão.

        As duas metades da contagem acima, agora com as colunas que as
        conquistas perguntam. Vem do banco já projetado: carregar objetos
        ``Goal`` inteiros para ler quatro campos leria o texto de todas as
        descrições junto.
        """
        singles = db.session.execute(
            select(
                Goal.category, Goal.priority, Goal.completed_at, Goal.has_deadline
            )
            .where(
                Goal.recurrence_type == RECURRENCE_NONE, Goal.status == STATUS_DONE
            )
            .limit(limit)
        ).all()

        recurring = db.session.execute(
            select(
                Goal.category,
                Goal.priority,
                GoalOccurrence.completed_at,
                Goal.has_deadline,
            )
            .join(Goal, Goal.id == GoalOccurrence.goal_id)
            .where(GoalOccurrence.status == STATUS_DONE)
            .limit(limit)
        ).all()

        return [tuple(row) for row in singles] + [tuple(row) for row in recurring]

    @staticmethod
    def category_totals() -> list[tuple[str, int]]:
        """Conclusões por categoria, em uma consulta agregada por metade."""
        totals: dict[str, int] = {}
        singles = db.session.execute(
            select(Goal.category, func.count(Goal.id))
            .where(Goal.recurrence_type == RECURRENCE_NONE, Goal.status == STATUS_DONE)
            .group_by(Goal.category)
        ).all()
        recurring = db.session.execute(
            select(Goal.category, func.count(GoalOccurrence.id))
            .join(Goal, Goal.id == GoalOccurrence.goal_id)
            .where(GoalOccurrence.status == STATUS_DONE)
            .group_by(Goal.category)
        ).all()
        for category, total in [*singles, *recurring]:
            totals[category] = totals.get(category, 0) + total
        return sorted(totals.items(), key=lambda item: (-item[1], item[0]))

    # ── Predefinidas ────────────────────────────────────────────────────────

    @staticmethod
    def templates(limit: int = MAX_TEMPLATES) -> list[GoalTemplate]:
        return list(
            db.session.scalars(
                select(GoalTemplate)
                .options(joinedload(GoalTemplate.document))
                .order_by(GoalTemplate.created_at.desc())
                .limit(limit)
            )
            .unique()
            .all()
        )

    @staticmethod
    def template_by_uuid(public_uuid: str) -> GoalTemplate | None:
        return db.session.scalars(
            select(GoalTemplate)
            .options(joinedload(GoalTemplate.document))
            .where(GoalTemplate.uuid == public_uuid)
        ).unique().one_or_none()

    @staticmethod
    def template_count() -> int:
        return db.session.scalar(select(func.count(GoalTemplate.id))) or 0

    # ── Documentos ligados ──────────────────────────────────────────────────

    @staticmethod
    def documents_for_picker(limit: int = 200) -> list[tuple[int, str, str]]:
        """``(id, uuid, título)`` dos documentos que uma meta pode apontar.

        Sem o corpo do texto: o seletor mostra títulos. A projeção é explícita
        em vez de um ``defer`` sobre a entidade inteira - só as três colunas
        que o campo precisa saem do banco.
        """
        rows = db.session.execute(
            select(Document.id, Document.uuid, Document.title)
            .where(Document.is_deleted.is_(False))
            .order_by(Document.updated_at.desc())
            .limit(limit)
        ).all()
        return [tuple(row) for row in rows]

    @staticmethod
    def document_id_for_uuid(public_uuid: str) -> int | None:
        if not public_uuid:
            return None
        return db.session.scalar(
            select(Document.id).where(
                Document.uuid == public_uuid, Document.is_deleted.is_(False)
            )
        )

    @staticmethod
    def delete_all(goal_ids: Iterable[int] | None = None) -> int:
        """Apaga metas - todas, ou só as indicadas.

        Pelo ORM e não por um ``DELETE`` em massa: as exceções dependem do
        ``ON DELETE CASCADE``, que o SQLite só respeita com as chaves
        estrangeiras ligadas por conexão. O caminho do ORM não depende disso.
        """
        stmt = select(Goal)
        if goal_ids is not None:
            stmt = stmt.where(Goal.id.in_(list(goal_ids)))
        goals = list(db.session.scalars(stmt).all())
        for goal in goals:
            db.session.delete(goal)
        return len(goals)
