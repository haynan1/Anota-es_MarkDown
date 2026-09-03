"""Formulários dos mapas mentais."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from app.models.mind_map import (
    LAYOUT_LABELS,
    LAYOUTS,
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
)


class MindMapForm(FlaskForm):
    title = StringField(
        "Nome do mapa",
        validators=[
            DataRequired(message="Informe o nome do mapa."),
            Length(
                max=MAX_TITLE_LENGTH,
                message=f"O nome deve ter no máximo {MAX_TITLE_LENGTH} caracteres.",
            ),
        ],
    )
    description = TextAreaField(
        "Descrição",
        validators=[
            Optional(),
            Length(
                max=MAX_DESCRIPTION_LENGTH,
                message=(
                    f"A descrição deve ter no máximo {MAX_DESCRIPTION_LENGTH} caracteres."
                ),
            ),
        ],
        description="Opcional. Uma linha sobre o que este mapa organiza.",
    )
    color = StringField(
        "Cor predominante",
        validators=[
            Optional(),
            Regexp(
                r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                message="Use uma cor no formato #RRGGBB.",
            ),
        ],
        default="#4F46E5",
    )
    layout = SelectField(
        "Disposição",
        choices=[(value, LAYOUT_LABELS[value]) for value in LAYOUTS],
        default="right",
        validate_choice=False,
        description="Como a arrumação automática distribui os ramos.",
    )
