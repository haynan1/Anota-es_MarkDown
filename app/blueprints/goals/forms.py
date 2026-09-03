"""Formulários das metas.

O Flask-WTF traz o CSRF; os validadores aqui são a metade servidora de cada
restrição que a interface anuncia. A conversão para :class:`GoalInput` mora
neste arquivo para que a rota não precise saber o nome de nenhum campo - ela
recebe o formulário validado e entrega o resultado ao serviço.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.goal import (
    CATEGORY_LABELS,
    MAX_DESCRIPTION_LENGTH,
    MAX_RECURRENCE_DAYS,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    PRIORITY_LABELS,
    RECURRENCE_LABELS,
    STATUS_LABELS,
)
from app.services.goal_service import GoalInput
from app.services.phrase_service import PHRASE_INTERVALS

DOCUMENT_CHOICE_EMPTY = ("", "Nenhum documento")


def _choices(labels: dict[str, str]) -> list[tuple[str, str]]:
    return list(labels.items())


class GoalForm(FlaskForm):
    title = StringField(
        "Título",
        validators=[
            DataRequired(message="Escreva o título da meta."),
            Length(max=MAX_TITLE_LENGTH),
        ],
    )
    description = TextAreaField(
        "Descrição",
        validators=[Optional(), Length(max=MAX_DESCRIPTION_LENGTH)],
        description="Opcional. O que é preciso para considerar isto feito.",
    )
    link_url = StringField(
        "Link de apoio",
        validators=[Optional(), Length(max=MAX_URL_LENGTH)],
        description="Um endereço externo que ajuda a cumprir esta meta.",
    )
    # O acoplamento com a biblioteca: a meta aponta para o documento de que ela
    # trata. ``validate_choice=False`` porque a lista é montada por consulta e
    # o serviço confere o UUID de novo antes de gravar.
    document_uuid = SelectField(
        "Documento",
        validators=[Optional()],
        validate_choice=False,
        description="Ligue esta meta a um documento da sua biblioteca.",
    )

    has_deadline = BooleanField("Esta meta tem um dia para acontecer", default=True)
    date = DateField("Data", validators=[Optional()])
    time = TimeField("Horário", validators=[Optional()])
    show_on_board = BooleanField("Mostrar na esteira", default=True)

    priority = SelectField("Prioridade", choices=_choices(PRIORITY_LABELS))
    category = SelectField("Categoria", choices=_choices(CATEGORY_LABELS))
    status = SelectField("Situação", choices=_choices(STATUS_LABELS))

    recurrence_type = SelectField("Repetição", choices=_choices(RECURRENCE_LABELS))
    recurrence_days = IntegerField(
        "Por quantos dias",
        validators=[
            Optional(),
            NumberRange(
                min=1,
                max=MAX_RECURRENCE_DAYS,
                message=f"Escolha entre 1 e {MAX_RECURRENCE_DAYS} dias.",
            ),
        ],
    )
    recurrence_end_date = DateField("Repetir até", validators=[Optional()])

    def to_input(self) -> GoalInput:
        return GoalInput(
            title=self.title.data or "",
            description=self.description.data or "",
            link_url=self.link_url.data or "",
            document_uuid=self.document_uuid.data or "",
            date=self.date.data,
            time=self.time.data,
            has_deadline=bool(self.has_deadline.data),
            show_on_board=bool(self.show_on_board.data),
            priority=self.priority.data,
            category=self.category.data,
            status=self.status.data,
            recurrence_type=self.recurrence_type.data,
            recurrence_days=self.recurrence_days.data,
            recurrence_end_date=self.recurrence_end_date.data,
        )


class GoalTemplateForm(FlaskForm):
    """O molde. Não tem data - a data é escolhida na hora de ativar."""

    title = StringField(
        "Título",
        validators=[
            DataRequired(message="Escreva o título da meta predefinida."),
            Length(max=MAX_TITLE_LENGTH),
        ],
    )
    description = TextAreaField(
        "Descrição", validators=[Optional(), Length(max=MAX_DESCRIPTION_LENGTH)]
    )
    link_url = StringField(
        "Link de apoio", validators=[Optional(), Length(max=MAX_URL_LENGTH)]
    )
    document_uuid = SelectField(
        "Documento", validators=[Optional()], validate_choice=False
    )
    time = TimeField("Horário", validators=[Optional()])
    show_on_board = BooleanField("Mostrar na esteira", default=True)
    priority = SelectField("Prioridade", choices=_choices(PRIORITY_LABELS))
    category = SelectField("Categoria", choices=_choices(CATEGORY_LABELS))

    def to_input(self) -> GoalInput:
        return GoalInput(
            title=self.title.data or "",
            description=self.description.data or "",
            link_url=self.link_url.data or "",
            document_uuid=self.document_uuid.data or "",
            time=self.time.data,
            show_on_board=bool(self.show_on_board.data),
            priority=self.priority.data,
            category=self.category.data,
        )


class ActivateTemplateForm(FlaskForm):
    date = DateField(
        "Ativar em",
        validators=[DataRequired(message="Escolha o dia em que esta meta acontece.")],
    )


class PhraseForm(FlaskForm):
    text = TextAreaField(
        "Nova frase",
        validators=[
            DataRequired(message="Escreva a frase antes de salvar."),
            Length(max=255, message="A frase pode ter até 255 caracteres."),
        ],
    )


class PhraseSettingsForm(FlaskForm):
    enabled = BooleanField("Mostrar frases motivacionais")
    interval = SelectField(
        "Trocar a cada",
        choices=[
            (str(value), f"{value} minuto" if value == 1 else f"{value} minutos")
            for value in PHRASE_INTERVALS
        ],
    )
    undated_on_board = BooleanField("Mostrar metas sem prazo na esteira")

    def interval_minutes(self) -> int:
        try:
            value = int(self.interval.data)
        except (TypeError, ValueError):
            return 30
        return value if value in PHRASE_INTERVALS else 30
