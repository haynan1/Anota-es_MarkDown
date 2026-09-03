"""Frases motivacionais escritas por quem usa o app.

O catálogo padrão vive em ``app.services.phrase_service``, pelo mesmo motivo
que o catálogo de conquistas vive em código: ele não muda por ação de
ninguém e não precisa de uma linha para existir. Esta tabela guarda só o que é
autoral - a frase que a pessoa escreveu para si mesma, que entra na rotação
junto com as de fábrica.
"""

from __future__ import annotations

import uuid as uuid_module

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin

MAX_PHRASE_LENGTH = 255


def new_uuid() -> str:
    return str(uuid_module.uuid4())


class MotivationalPhrase(TimestampMixin, db.Model):
    __tablename__ = "motivational_phrases"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Como todo endereço público deste código: o identificador que aparece na
    # URL não é a chave primária.
    uuid: Mapped[str] = mapped_column(
        String(36), default=new_uuid, nullable=False, unique=True, index=True
    )
    text: Mapped[str] = mapped_column(String(MAX_PHRASE_LENGTH), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MotivationalPhrase {self.text[:30]!r}>"
