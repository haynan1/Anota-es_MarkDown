"""Metas: o que você decidiu fazer, e o que já fez.

Por que uma meta não é um documento
-----------------------------------
Um documento guarda o que você *escreveu*. Uma meta guarda o que você
*combinou consigo mesmo* - e as duas coisas se comportam de formas opostas.
Um documento é uma coisa só, que muda ao longo do tempo. Uma meta é uma
intenção que pode acontecer muitas vezes: "correr 30 minutos" às terças e
quintas até o fim do mês não são catorze metas, é uma meta e catorze
ocorrências.

Daí a forma desta tabela. A meta guarda a *regra* (a data-âncora, o tipo de
repetição, até quando) e cada dia em que essa regra cai é calculado, não
armazenado. Materializar as ocorrências criaria milhares de linhas para uma
meta "todos os dias, sem prazo final", e mudar o horário dela viraria uma
migração de dados.

O que é armazenado é a **exceção**: :class:`GoalOccurrence` existe só para os
dias em que o estado fugiu do padrão da série - a terça em que você concluiu,
a quinta em que começou e parou no meio. Um dia sem linha nenhuma é um dia
que ainda está como a série manda.

A meta olha para fora
---------------------
``document_id`` é o que faz uma meta pertencer a *esta* aplicação e não a
qualquer app de tarefas: a missão "terminar a proposta" aponta para a proposta,
que está aqui do lado, e abre com um clique. ``SET NULL``, como em toda ligação
opcional deste código: apagar o documento tira o atalho, nunca a meta - o
compromisso continua de pé mesmo que o texto tenha ido embora.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import date as date_type
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - resolved by SQLAlchemy at runtime
    from app.models.document import Document

# Larguras de coluna que as outras camadas respeitam. Declaradas ao lado da
# coluna para que o corte aconteça uma vez só, na fronteira que é dona da regra.
MAX_TITLE_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 2000
MAX_URL_LENGTH = 500

# As categorias são fechadas de propósito. Um campo livre vira trinta grafias
# do mesmo assunto em três semanas, e nenhum relatório consegue somar isso.
GOAL_CATEGORIES = (
    "estudos",
    "trabalho",
    "saude",
    "financas",
    "espiritual",
    "pessoal",
    "familia",
    "empreendedorismo",
    "outros",
)
CATEGORY_LABELS = {
    "estudos": "Estudos",
    "trabalho": "Trabalho",
    "saude": "Saúde",
    "financas": "Finanças",
    "espiritual": "Espiritual",
    "pessoal": "Pessoal",
    "familia": "Família",
    "empreendedorismo": "Empreendedorismo",
    "outros": "Outros",
}
# Um ícone por categoria, do mesmo sprite que o resto da interface usa.
CATEGORY_ICONS = {
    "estudos": "book",
    "trabalho": "briefcase",
    "saude": "pulse",
    "financas": "wallet",
    "espiritual": "sparkles",
    "pessoal": "person",
    "familia": "users",
    "empreendedorismo": "rocket",
    "outros": "compass",
}

GOAL_PRIORITIES = ("baixa", "media", "alta")
PRIORITY_LABELS = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}
# Ordem de exibição, não de valor: alta primeiro porque é o que a pessoa
# precisa ver antes de decidir o que fazer agora.
PRIORITY_ORDER = {"alta": 0, "media": 1, "baixa": 2}

STATUS_PENDING = "pendente"
STATUS_DOING = "em_andamento"
STATUS_DONE = "concluida"
GOAL_STATUSES = (STATUS_PENDING, STATUS_DOING, STATUS_DONE)
STATUS_LABELS = {
    STATUS_PENDING: "A fazer",
    STATUS_DOING: "Em andamento",
    STATUS_DONE: "Concluída",
}
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_DOING)
# O ciclo de um clique: a fazer -> em andamento -> concluída -> a fazer.
NEXT_STATUS = {
    STATUS_PENDING: STATUS_DOING,
    STATUS_DOING: STATUS_DONE,
    STATUS_DONE: STATUS_PENDING,
}

RECURRENCE_NONE = "none"
RECURRENCE_TYPES = ("none", "weekdays", "weekends", "count", "forever")
RECURRENCE_LABELS = {
    "none": "Não repetir",
    "weekdays": "Dias úteis",
    "weekends": "Fins de semana",
    "count": "Por uma quantidade de dias",
    "forever": "Todos os dias, sem fim",
}
# Quantos dias uma repetição "por quantidade" pode cobrir. Alto o bastante para
# um ano de hábito diário, baixo o bastante para que a expansão de ocorrências
# continue sendo uma conta e não uma varredura.
MAX_RECURRENCE_DAYS = 365


def new_uuid() -> str:
    return str(uuid_module.uuid4())


class Goal(TimestampMixin, db.Model):
    __tablename__ = "goals"
    __table_args__ = (
        # Toda leitura de uma janela filtra por data e depois desenha em ordem
        # de prioridade; todo painel conta por status.
        Index("ix_goals_window", "has_deadline", "date"),
        Index("ix_goals_status_date", "status", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link_url: Mapped[str] = mapped_column(
        String(MAX_URL_LENGTH), nullable=False, default=""
    )
    # O atalho para dentro da biblioteca. Ver o cabeçalho do módulo.
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # A data-âncora. Para uma meta avulsa é o dia dela; para uma série é o dia
    # em que a série começa a contar. Nunca nula, mesmo quando ``has_deadline``
    # é falso: a âncora é o que a aritmética de repetição usa como origem, e
    # uma origem opcional espalharia um ``if`` por todo o cálculo. O que a
    # interface mostra é decidido por ``has_deadline``, não por esta coluna.
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    time: Mapped[time | None] = mapped_column(Time, nullable=True)

    # "Isto tem dia para acontecer?" Uma meta sem prazo - "estudar inglês" -
    # não é uma meta atrasada todos os dias; ela fica no acervo até você
    # decidir puxá-la para hoje.
    has_deadline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Você escolhe o que entra no fluxo do dia. Uma meta pode existir na lista
    # e ficar fora da esteira sem ser apagada.
    show_on_board: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="media")
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="pessoal")

    # Numa meta avulsa, este é o estado dela. Numa série, é o estado *padrão*
    # das ocorrências que não têm exceção - e por isso o serviço o mantém em
    # "pendente": uma série marcada como concluída na origem apareceria
    # concluída em todos os dias em que ela cai, inclusive nos que ainda nem
    # chegaram.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_PENDING
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recurrence_type: Mapped[str] = mapped_column(
        String(15), nullable=False, default=RECURRENCE_NONE
    )
    recurrence_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence_end_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)

    document: Mapped["Document | None"] = relationship(lazy="joined")
    occurrences: Mapped[list["GoalOccurrence"]] = relationship(
        back_populates="goal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    @property
    def is_recurring(self) -> bool:
        return self.recurrence_type != RECURRENCE_NONE

    @property
    def display_title(self) -> str:
        return self.title.strip() or "Meta sem título"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Goal {self.title!r}>"


class GoalOccurrence(db.Model):
    """A exceção de um dia dentro de uma série.

    Só existe quando o estado daquele dia deixou de ser o padrão da meta.
    Ausência de linha é informação: significa "este dia ainda está como a
    série manda".
    """

    __tablename__ = "goal_occurrences"
    __table_args__ = (
        UniqueConstraint("goal_id", "occurrence_date", name="uq_goal_occurrence_day"),
        Index("ix_goal_occurrences_status_date", "status", "occurrence_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurrence_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    goal: Mapped["Goal"] = relationship(back_populates="occurrences")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GoalOccurrence {self.goal_id} {self.occurrence_date}>"


class GoalTemplate(TimestampMixin, db.Model):
    """Uma meta guardada pronta, esperando o dia de entrar em ação.

    Existe porque cadastrar não é começar. "Revisar o orçamento" é uma coisa
    que você sabe que vai fazer de novo, mas não hoje - e criar a meta com uma
    data qualquer só para não esquecer o formato dela suja a esteira de hoje
    com uma coisa que não é de hoje. A predefinida guarda o formato; ativar
    escolhe o dia.
    """

    __tablename__ = "goal_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link_url: Mapped[str] = mapped_column(
        String(MAX_URL_LENGTH), nullable=False, default=""
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    time: Mapped[time | None] = mapped_column(Time, nullable=True)
    show_on_board: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="media")
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="pessoal")

    document: Mapped["Document | None"] = relationship(lazy="joined")

    @property
    def display_title(self) -> str:
        return self.title.strip() or "Predefinida sem título"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GoalTemplate {self.title!r}>"
