"""Consultas sobre as frases motivacionais autorais."""

from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models import MotivationalPhrase

# Uma frase é uma linha de texto que aparece no painel. Um acervo maior que
# este não é uso, é acidente - e a rotação percorre a lista inteira.
MAX_PHRASES = 200


class PhraseRepository:
    @staticmethod
    def all(limit: int = MAX_PHRASES) -> list[MotivationalPhrase]:
        return list(
            db.session.scalars(
                select(MotivationalPhrase)
                .order_by(MotivationalPhrase.created_at.desc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def texts(limit: int = MAX_PHRASES) -> list[str]:
        """Só o texto - é o que a rotação precisa."""
        rows = db.session.execute(
            select(MotivationalPhrase.text)
            .order_by(MotivationalPhrase.created_at)
            .limit(limit)
        ).all()
        return [row[0] for row in rows]

    @staticmethod
    def get_by_uuid(public_uuid: str) -> MotivationalPhrase | None:
        return db.session.scalars(
            select(MotivationalPhrase).where(MotivationalPhrase.uuid == public_uuid)
        ).one_or_none()

    @staticmethod
    def count() -> int:
        return db.session.scalar(select(func.count(MotivationalPhrase.id))) or 0
