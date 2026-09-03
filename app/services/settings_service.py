"""Typed application settings backed by the ``settings`` key/value table.

Every key is declared in :data:`SETTINGS_SCHEMA` with a type, a default and -
where relevant - the allowed choices. Reads coerce and validate, so a corrupted
or hand-edited row degrades to the default instead of breaking a page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from flask import g

from app.extensions import db
from app.models import PAGE_SIZES, PDF_THEMES, Setting
from app.services.sanitizer import sanitize_plain_text

# Settings are a closed set of three shapes, not arbitrary objects. Naming the
# union instead of reaching for ``Any`` keeps the coercion functions honest and
# turns an unexpected type into a type error rather than a runtime surprise.
SettingValue: TypeAlias = str | int | bool
SettingKind: TypeAlias = Literal["str", "int", "bool", "color"]

THEMES = ("auto", "light", "dark")
PDF_FONTS = ("sans", "serif", "mono")
PDF_MARGINS = ("compact", "normal", "wide")

PDF_FONT_LABELS = {"sans": "Sem serifa", "serif": "Com serifa", "mono": "Monoespaçada"}
PDF_MARGIN_LABELS = {"compact": "Compactas", "normal": "Normais", "wide": "Largas"}
THEME_LABELS = {"auto": "Automático", "light": "Claro", "dark": "Escuro"}

_HEX_COLOR_LENGTHS = {4, 7}


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    default: SettingValue
    kind: SettingKind = "str"
    choices: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    max_length: int = 200


SETTINGS_SCHEMA: tuple[SettingDefinition, ...] = (
    SettingDefinition("app_name", "Markdown Studio", max_length=60),
    SettingDefinition("theme", "auto", choices=THEMES),
    SettingDefinition("accent_color", "#4F46E5", kind="color"),
    SettingDefinition("timezone", "America/Sao_Paulo", max_length=64),
    SettingDefinition("autosave_seconds", 3, kind="int", minimum=1, maximum=60),
    SettingDefinition("pdf_page_size", "A4", choices=PAGE_SIZES),
    SettingDefinition("pdf_theme", "classic", choices=PDF_THEMES),
    SettingDefinition("pdf_font", "serif", choices=PDF_FONTS),
    SettingDefinition("pdf_margin", "normal", choices=PDF_MARGINS),
    # No pdf_header / pdf_footer / pdf_show_page_numbers: the rendered PDF is
    # the Markdown and nothing else, so there is no chrome left for them to
    # configure. A setting that changes nothing is worse than a missing one -
    # it promises an effect it cannot deliver. Old backups may still carry
    # these keys; import_values drops anything not declared here.
    #
    # This one survives because the Markdown *source* listing still stamps
    # itself with a generation date - a code listing, not a document.
    SettingDefinition("pdf_show_generated_date", True, kind="bool"),
    SettingDefinition("backup_keep_last", 10, kind="int", minimum=1, maximum=200),
    # ── Metas ───────────────────────────────────────────────────────────────
    # Preferências da jornada. Ficam aqui, e não numa tabela de perfil: esta
    # aplicação tem um dono só, e uma segunda tabela de preferências seria uma
    # segunda verdade sobre a mesma pessoa.
    SettingDefinition("goals_phrases_enabled", True, kind="bool"),
    SettingDefinition(
        "goals_phrase_interval", 30, kind="int", minimum=1, maximum=60
    ),
    SettingDefinition("goals_undated_on_board", False, kind="bool"),
)

_BY_KEY = {definition.key: definition for definition in SETTINGS_SCHEMA}
_CACHE_KEY = "_markdown_studio_settings"


def _coerce(definition: SettingDefinition, raw: str | None) -> SettingValue:
    if raw is None:
        return definition.default

    if definition.kind == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    if definition.kind == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return definition.default
        if definition.minimum is not None:
            value = max(definition.minimum, value)
        if definition.maximum is not None:
            value = min(definition.maximum, value)
        return value

    if definition.kind == "color":
        color = (raw or "").strip()
        if (
            color.startswith("#")
            and len(color) in _HEX_COLOR_LENGTHS
            and all(char in "0123456789abcdefABCDEF" for char in color[1:])
        ):
            return color
        return definition.default

    text = sanitize_plain_text(raw, max_length=definition.max_length)
    if definition.choices and text not in definition.choices:
        return definition.default
    return text


def _serialize(definition: SettingDefinition, value: SettingValue) -> str:
    if definition.kind == "bool":
        return "true" if value else "false"
    return str(value)


class SettingsService:
    """Read/write access to application preferences."""

    @staticmethod
    def defaults() -> dict[str, SettingValue]:
        return {definition.key: definition.default for definition in SETTINGS_SCHEMA}

    @staticmethod
    def all() -> dict[str, SettingValue]:
        """All settings, coerced. Cached for the lifetime of the request."""
        cached = g.get(_CACHE_KEY) if g else None
        if cached is not None:
            return cached

        rows = {row.key: row.value for row in db.session.scalars(db.select(Setting))}
        resolved = {
            definition.key: _coerce(definition, rows.get(definition.key))
            for definition in SETTINGS_SCHEMA
        }
        try:
            setattr(g, _CACHE_KEY, resolved)
        except RuntimeError:  # pragma: no cover - outside an application context
            pass
        return resolved

    @staticmethod
    def get(key: str, default: SettingValue | None = None) -> SettingValue | None:
        definition = _BY_KEY.get(key)
        if definition is None:
            return default
        return SettingsService.all().get(key, definition.default)

    @staticmethod
    def set(key: str, value: SettingValue) -> SettingValue:
        """Stage one setting. **Does not commit** — the caller must.

        Deliberate: a settings form applies many keys and either all of them
        land or none do. Use ``update_many`` unless you are batching writes
        yourself; calling this alone leaves the change in the session and it
        disappears when the request ends.
        """
        definition = _BY_KEY.get(key)
        if definition is None:
            raise KeyError(f"Configuração desconhecida: {key}")

        coerced = _coerce(definition, _serialize(definition, value))
        row = db.session.scalars(
            db.select(Setting).where(Setting.key == key)
        ).one_or_none()
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = _serialize(definition, coerced)
        SettingsService.invalidate_cache()
        return coerced

    @staticmethod
    def update_many(values: dict[str, SettingValue]) -> dict[str, SettingValue]:
        applied = {
            key: SettingsService.set(key, value)
            for key, value in values.items()
            if key in _BY_KEY
        }
        db.session.commit()
        SettingsService.invalidate_cache()
        return applied

    @staticmethod
    def reset_to_defaults() -> dict[str, SettingValue]:
        db.session.execute(db.delete(Setting))
        db.session.commit()
        SettingsService.invalidate_cache()
        return SettingsService.defaults()

    @staticmethod
    def invalidate_cache() -> None:
        try:
            if g and _CACHE_KEY in g:
                g.pop(_CACHE_KEY)
        except RuntimeError:  # pragma: no cover - outside an application context
            pass

    @staticmethod
    def export() -> dict[str, str]:
        """Raw key/value pairs for inclusion in a backup archive."""
        return {row.key: row.value for row in db.session.scalars(db.select(Setting))}

    @staticmethod
    def import_values(values: dict[str, SettingValue]) -> int:
        applied = SettingsService.update_many(
            {key: value for key, value in (values or {}).items() if key in _BY_KEY}
        )
        return len(applied)
