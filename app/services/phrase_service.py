"""Frases motivacionais: as de fábrica, as suas, e a que está no ar agora.

A rotação é calculada, não sorteada. A frase visível é uma função do relógio
(``instante // intervalo``) e do tamanho da lista, o que dá duas propriedades
que um ``random`` não daria: o servidor e a página concordam sobre qual frase
é a atual, e recarregar a tela não troca a frase - ela troca quando o intervalo
vira, que é o que a pessoa configurou.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models import MotivationalPhrase
from app.models.phrase import MAX_PHRASE_LENGTH
from app.repositories.phrase_repository import MAX_PHRASES, PhraseRepository
from app.services.exceptions import NotFoundError, ValidationError
from app.services.sanitizer import sanitize_plain_text
from app.services.settings_service import SettingsService

DEFAULT_PHRASES: tuple[str, ...] = (
    "A direção importa mais do que a velocidade quando você está construindo uma vida.",
    "Pequenos passos repetidos criam grandes distâncias.",
    "A sua missão de hoje é o combustível do amanhã.",
    "Comece pequeno, mas continue em movimento.",
    "Cada meta concluída deixa sua jornada mais forte.",
    "Não precisa ser perfeito. Precisa ser possível hoje.",
    "O que você faz nos dias comuns decide o que os dias raros encontram pronto.",
    "Terminar uma coisa vale mais do que começar três.",
)

# Os intervalos oferecidos, em minutos. Uma lista fechada em vez de um campo
# livre: o valor entra numa conta de rotação, e "0" ou "-5" não são escolhas,
# são defeitos esperando a hora certa.
PHRASE_INTERVALS: tuple[int, ...] = (1, 5, 15, 30, 60)


class PhraseService:
    @staticmethod
    def all_texts() -> list[str]:
        """As de fábrica seguidas das suas, na ordem em que rodam."""
        return [*DEFAULT_PHRASES, *PhraseRepository.texts()]

    @staticmethod
    def interval_minutes() -> int:
        raw = SettingsService.get("goals_phrase_interval", 30)
        value = int(raw) if isinstance(raw, int) else 30
        # A configuração é validada na entrada, mas um banco editado à mão não
        # é: um intervalo fora da lista cai no mais próximo abaixo dele.
        allowed = [item for item in PHRASE_INTERVALS if item <= value]
        return allowed[-1] if allowed else PHRASE_INTERVALS[0]

    @staticmethod
    def enabled() -> bool:
        return bool(SettingsService.get("goals_phrases_enabled", True))

    @staticmethod
    def slot_for(
        epoch_ms: int, interval_minutes: int, count: int
    ) -> int:
        """O índice da frase no instante dado, ou -1 quando não há frases.

        Milissegundos, e não segundos, porque a outra metade desta conta mora
        em ``static/js/modules/phrase-rotation.js`` e o relógio do navegador
        fala em milissegundos. As duas assinaturas são iguais de propósito: é
        assim que o teste consegue comparar as duas implementações caso a caso.
        """
        if count < 1:
            return -1
        window = max(1, int(interval_minutes)) * 60 * 1000
        return (epoch_ms // window) % count

    @staticmethod
    def current(phrases: list[str] | None = None, moment: datetime | None = None) -> str:
        phrases = PhraseService.all_texts() if phrases is None else phrases
        if not phrases:
            return ""
        moment = moment or datetime.now(timezone.utc)
        slot = PhraseService.slot_for(
            int(moment.timestamp() * 1000),
            PhraseService.interval_minutes(),
            len(phrases),
        )
        return phrases[slot]

    @staticmethod
    def create(text: str) -> MotivationalPhrase:
        clean = sanitize_plain_text(text or "", max_length=MAX_PHRASE_LENGTH)
        if not clean:
            raise ValidationError("Escreva a frase antes de salvar.")
        if PhraseRepository.count() >= MAX_PHRASES:
            raise ValidationError(
                f"Limite de {MAX_PHRASES} frases atingido. "
                "Remova alguma antes de escrever outra."
            )

        phrase = MotivationalPhrase(text=clean)
        db.session.add(phrase)
        db.session.commit()
        return phrase

    @staticmethod
    def require(public_uuid: str) -> MotivationalPhrase:
        phrase = PhraseRepository.get_by_uuid(public_uuid)
        if phrase is None:
            raise NotFoundError("Frase não encontrada.")
        return phrase

    @staticmethod
    def delete(phrase: MotivationalPhrase) -> None:
        db.session.delete(phrase)
        db.session.commit()
