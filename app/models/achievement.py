"""Conquistas: o que ficou marcado no caminho.

Por que o catálogo não está no banco
------------------------------------
Uma conquista tem duas metades: a **definição** ("conclua 50 metas", com
título, ícone e grupo) e o **fato** ("você conseguiu, no dia tal"). Só a
segunda é um dado - a primeira é código, porque a condição dela *é* uma
função, e uma função não cabe numa coluna.

Guardar as duas no banco obrigaria a reconciliar o catálogo a cada leitura:
inserir as definições novas, atualizar as que mudaram de texto e apagar as que
sumiram do código, tudo antes de responder a uma página. É trabalho de escrita
para responder a uma pergunta de leitura, e deixa o banco com uma cópia
desatualizada de algo que já está versionado no repositório.

Aqui a tabela guarda uma coisa só: a chave da conquista e o instante em que
ela foi desbloqueada. Renomear uma conquista é editar uma linha de Python;
remover uma do catálogo faz a linha correspondente virar história sem dono,
que a leitura simplesmente ignora - e que volta a aparecer intacta se a
conquista for reintroduzida.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.utils.dates import utcnow


class AchievementUnlock(db.Model):
    __tablename__ = "achievement_unlocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A chave do catálogo em ``app.services.achievements_catalog``.
    key: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AchievementUnlock {self.key!r}>"
