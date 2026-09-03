"""Formulários dos grupos de documentos."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from app.models.group import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH


class GroupForm(FlaskForm):
    name = StringField(
        "Nome",
        validators=[
            DataRequired(message="Informe o nome do grupo."),
            Length(
                max=MAX_NAME_LENGTH,
                message=f"O nome deve ter no máximo {MAX_NAME_LENGTH} caracteres.",
            ),
        ],
    )
    description = TextAreaField(
        "Descrição",
        validators=[
            Optional(),
            Length(
                max=MAX_DESCRIPTION_LENGTH,
                message=f"A descrição deve ter no máximo {MAX_DESCRIPTION_LENGTH} caracteres.",
            ),
        ],
        description="Opcional. Uma linha sobre o que reúne estes documentos.",
    )
    color = StringField(
        "Cor",
        validators=[
            Optional(),
            Regexp(
                r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                message="Use uma cor no formato #RRGGBB.",
            ),
        ],
        default="#0F6E64",
    )
