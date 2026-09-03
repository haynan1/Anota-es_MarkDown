"""Forms for document and taxonomy management.

Flask-WTF supplies CSRF protection; the validators here are the server-side
half of every constraint the UI advertises.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired, MultipleFileField
from werkzeug.datastructures import FileStorage
from wtforms import (
    BooleanField,
    HiddenField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.widgets import CheckboxInput, ListWidget
from wtforms.validators import (
    DataRequired,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from app.utils.params import positive_int

CATEGORY_CHOICE_EMPTY = ("", "Sem categoria")


class DocumentMetadataForm(FlaskForm):
    """Title, taxonomy and PDF preferences, submitted from the editor."""

    title = StringField(
        "Título",
        validators=[Length(max=200, message="O título deve ter no máximo 200 caracteres.")],
    )
    content_markdown = TextAreaField("Conteúdo")
    category_id = SelectField("Categoria", validators=[Optional()], validate_choice=False)
    tags = StringField(
        "Etiquetas",
        validators=[Length(max=400, message="Lista de etiquetas longa demais.")],
        description="Separe por vírgula",
    )
    # Checkboxes rather than a multi-select list: a document belongs to few
    # groups out of few, and a native multi-select needs Ctrl-click to add a
    # second value - a gesture most people never discover.
    groups = SelectMultipleField(
        "Grupos",
        validators=[Optional()],
        validate_choice=False,
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
    )
    is_favorite = BooleanField("Favorito")
    page_size = SelectField(
        "Tamanho da página",
        choices=[("A4", "A4"), ("Letter", "Carta (Letter)")],
        validate_choice=False,
    )
    pdf_theme = SelectField(
        "Tema do PDF",
        choices=[
            ("classic", "Clássico"),
            ("minimal", "Minimalista"),
            ("academic", "Acadêmico"),
            ("modern", "Moderno"),
        ],
        validate_choice=False,
    )
    expected_revision = HiddenField()

    def tag_names(self) -> list[str]:
        raw = self.tags.data or ""
        return [part.strip() for part in raw.split(",") if part.strip()][:20]

    def selected_category_id(self) -> int | None:
        return positive_int(self.category_id.data)

    def selected_group_uuids(self) -> list[str]:
        return [value for value in (self.groups.data or []) if value][:50]


class RenameForm(FlaskForm):
    title = StringField(
        "Título",
        validators=[
            DataRequired(message="Informe um título."),
            Length(max=200, message="O título deve ter no máximo 200 caracteres."),
        ],
    )


class CategoryForm(FlaskForm):
    name = StringField(
        "Nome",
        validators=[
            DataRequired(message="Informe o nome da categoria."),
            Length(max=80),
        ],
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


class ImportForm(FlaskForm):
    """One or many Markdown files, or a ZIP holding them.

    A ``MultipleFileField`` rather than a plain one: importing a library is
    the same gesture as importing a file, and making people repeat it once per
    document is not an import feature. A single selection still behaves
    exactly as it did - it is just a list of one.
    """

    files = MultipleFileField(
        "Arquivos Markdown",
        validators=[
            FileRequired(message="Selecione ao menos um arquivo."),
            FileAllowed(
                ["md", "markdown", "mdown", "txt", "zip"],
                message="Envie arquivos .md, .markdown, .mdown, .txt ou um .zip com eles.",
            ),
        ],
    )
    category_id = SelectField("Categoria", validators=[Optional()], validate_choice=False)

    def uploads(self) -> list[FileStorage]:
        """The selected files, in the order the browser sent them."""
        data = self.files.data or []
        return [item for item in data if isinstance(item, FileStorage) and item.filename]

    def selected_category_id(self) -> int | None:
        return positive_int(self.category_id.data)


class ConfirmForm(FlaskForm):
    """Bare CSRF-carrying form for destructive POST actions."""


class EmptyTrashForm(FlaskForm):
    confirmation = StringField(
        "Confirmação",
        validators=[DataRequired(message="Digite EXCLUIR para confirmar.")],
    )

    def validate_confirmation(self, field) -> None:
        if (field.data or "").strip().upper() != "EXCLUIR":
            raise ValidationError("Digite EXCLUIR para confirmar.")
