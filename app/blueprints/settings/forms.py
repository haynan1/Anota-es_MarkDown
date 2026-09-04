from __future__ import annotations

from zoneinfo import available_timezones

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, IntegerField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

from app.models import PAGE_SIZE_LABELS, PDF_THEME_LABELS
from app.services import palette as palettes
from app.services.settings_service import (
    PDF_FONT_LABELS,
    PDF_MARGIN_LABELS,
    THEME_LABELS,
)

# A curated list keeps the select usable; every IANA zone remains accepted
# because the field is validated against the full database, not the choices.
COMMON_TIMEZONES = [
    "America/Sao_Paulo",
    "America/Manaus",
    "America/Belem",
    "America/Fortaleza",
    "America/Recife",
    "America/Bahia",
    "America/Cuiaba",
    "America/Campo_Grande",
    "America/Rio_Branco",
    "America/Noronha",
    "UTC",
    "America/New_York",
    "Europe/Lisbon",
    "Europe/London",
    "Europe/Madrid",
]


def _choices(labels: dict[str, str]) -> list[tuple[str, str]]:
    return [(key, label) for key, label in labels.items()]


class SettingsForm(FlaskForm):
    app_name = StringField(
        "Nome da aplicação",
        validators=[DataRequired(message="Informe um nome."), Length(max=60)],
    )
    theme = SelectField("Tema", choices=_choices(THEME_LABELS))
    # Um RadioField e não um <select>: as opções são visuais, e uma paleta
    # escolhida por nome numa lista suspensa é uma paleta escolhida às cegas.
    # As choices vêm do registro, então acrescentar uma paleta não passa por
    # este arquivo.
    #
    # `validate_choice=False` porque a validação de verdade já acontece uma
    # camada abaixo: a definição em SETTINGS_SCHEMA declara as mesmas chaves
    # como ``choices``, e qualquer coisa fora delas volta para a padrão antes
    # de tocar o banco. Deixar o formulário reprovar aqui só transformaria um
    # valor impossível de digitar — é um grupo de rádio — numa tela inteira de
    # configurações recusada.
    palette = RadioField("Paleta", choices=palettes.choices(), validate_choice=False)
    accent_color = StringField(
        "Cor principal",
        validators=[
            DataRequired(message="Informe uma cor."),
            Regexp(
                r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                message="Use uma cor no formato #RRGGBB.",
            ),
        ],
    )
    timezone = SelectField(
        "Fuso horário",
        choices=[(zone, zone.replace("_", " ")) for zone in COMMON_TIMEZONES],
        validate_choice=False,
    )
    autosave_seconds = IntegerField(
        "Salvamento automático (segundos)",
        validators=[
            DataRequired(message="Informe o intervalo."),
            NumberRange(min=1, max=60, message="Escolha entre 1 e 60 segundos."),
        ],
    )

    pdf_page_size = SelectField("Tamanho padrão", choices=_choices(PAGE_SIZE_LABELS))
    pdf_theme = SelectField("Tema padrão do PDF", choices=_choices(PDF_THEME_LABELS))
    pdf_font = SelectField("Fonte", choices=_choices(PDF_FONT_LABELS))
    pdf_margin = SelectField("Margens", choices=_choices(PDF_MARGIN_LABELS))
    pdf_show_generated_date = BooleanField(
        "Exibir data de geração no PDF do código-fonte"
    )

    backup_keep_last = IntegerField(
        "Backups mantidos",
        validators=[
            DataRequired(message="Informe quantos backups manter."),
            NumberRange(min=1, max=200, message="Escolha entre 1 e 200."),
        ],
    )

    def validate_timezone(self, field) -> None:
        from wtforms.validators import ValidationError

        if field.data not in available_timezones():
            raise ValidationError("Fuso horário desconhecido.")

    def to_settings(self) -> dict[str, object]:
        values: dict[str, object] = {
            "app_name": self.app_name.data,
            "theme": self.theme.data,
            "accent_color": self.accent_color.data,
            "timezone": self.timezone.data,
            "autosave_seconds": self.autosave_seconds.data,
            "pdf_page_size": self.pdf_page_size.data,
            "pdf_theme": self.pdf_theme.data,
            "pdf_font": self.pdf_font.data,
            "pdf_margin": self.pdf_margin.data,
            "pdf_show_generated_date": self.pdf_show_generated_date.data,
            "backup_keep_last": self.backup_keep_last.data,
        }

        # Ausente significa "não mexa", e não "volte para a padrão". Um envio
        # sem o campo — um cliente antigo, um script, um formulário montado à
        # mão — não deve derrubar a paleta escolhida só por não falar dela.
        # ``update_many`` aplica apenas as chaves que recebe.
        if self.palette.data:
            values["palette"] = self.palette.data

        return values


class BackupRestoreForm(FlaskForm):
    file = FileField(
        "Arquivo de backup",
        validators=[
            FileRequired(message="Selecione um arquivo .zip."),
            FileAllowed(["zip"], message="Envie um arquivo .zip gerado por esta aplicação."),
        ],
    )
    mode = SelectField(
        "Modo",
        choices=[
            ("merge", "Mesclar - adiciona apenas o que não existe"),
            ("replace", "Substituir - apaga tudo e restaura o backup"),
        ],
    )
    confirmation = StringField("Confirmação", validators=[Optional()])

    def validate_confirmation(self, field) -> None:
        from wtforms.validators import ValidationError

        if self.mode.data == "replace" and (field.data or "").strip().upper() != "SUBSTITUIR":
            raise ValidationError(
                "Para substituir todos os dados, digite SUBSTITUIR no campo de confirmação."
            )
