"""Quais conquistas já foram desbloqueadas, e quando."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select

from app.extensions import db
from app.models import AchievementUnlock

# O catálogo é finito e escrito à mão; este teto só existe para que a leitura
# tenha um custo declarado mesmo diante de um banco adulterado.
MAX_UNLOCKS = 1000


class AchievementRepository:
    @staticmethod
    def unlocked() -> dict[str, datetime]:
        """``{chave: quando}`` para tudo que já foi desbloqueado."""
        rows = db.session.scalars(
            select(AchievementUnlock).limit(MAX_UNLOCKS)
        ).all()
        return {row.key: row.unlocked_at for row in rows}

    @staticmethod
    def count() -> int:
        return db.session.scalar(select(func.count(AchievementUnlock.id))) or 0

    @staticmethod
    def record(keys: Iterable[str]) -> None:
        """Marca as chaves como desbloqueadas. **Não faz commit.**

        Deliberado: desbloquear acontece no fim de uma operação que já tem uma
        transação aberta - concluir uma meta e ganhar a medalha por isso são o
        mesmo instante, e ou os dois acontecem ou nenhum acontece.
        """
        for key in keys:
            db.session.add(AchievementUnlock(key=key))

    @staticmethod
    def clear() -> int:
        total = AchievementRepository.count()
        db.session.execute(db.delete(AchievementUnlock))
        return total
