"""A aritmética de contraste, num lugar só.

Dois testes precisam dela por motivos diferentes — um mede os pares de cada
paleta, outro confere que a reserva em ``base.css`` não divergiu da paleta
padrão — e contraste medido de dois jeitos ligeiramente diferentes é pior do
que contraste não medido: os dois passam e ninguém sabe qual está certo.

A montagem dos tokens é a parte que vale ler. Desde que as paletas existem,
as cores de uma tela vêm de três origens:

  * as superfícies e a tinta,   do registro em ``app/services/palette.py``;
  * o destaque e seus derivados, calculados por ``app/services/accent.py``
    contra as superfícies *daquela* paleta;
  * as cores semânticas,        de ``base.css``, porque verde de concluído e
                                vermelho de perigo são estado e não estilo, e
                                valem igual em qualquer paleta.

``tokens_for`` junta as três e devolve o que o navegador realmente teria.
"""

from __future__ import annotations

import pathlib
import re

from app.services import palette as palettes
from app.services.accent import SOFT_ALPHA, build_ramp

BASE_CSS = pathlib.Path("app/static/css/base.css")

#: A opacidade com que ``theme.css.jinja`` compõe ``--accent-soft``. Importada
#: e não copiada: o solver resolve o destaque contando com este número, e um
#: teste que medisse com outro estaria medindo uma tela que não existe.
ACCENT_SOFT_ALPHA = SOFT_ALPHA


def css_tokens(theme: str) -> dict[str, str]:
    """Os tokens declarados em ``base.css`` para um dos temas."""
    source = BASE_CSS.read_text(encoding="utf-8")
    selector = ":root {" if theme == "light" else ':root[data-theme="dark"] {'
    start = source.index(selector)
    block = source[start : source.index("\n}", start)]
    return dict(
        re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6}|rgba\([^)]+\))", block)
    )


def tokens_for(palette: palettes.Palette, theme: str) -> dict[str, str]:
    """As cores que uma tela desta paleta neste tema realmente usa."""
    surfaces = (palette.light if theme == "light" else palette.dark).as_css()

    ramp = build_ramp(
        palette.accent, palette.light.surface_sunken, palette.dark.surface_2
    )
    accent = ramp.light if theme == "light" else ramp.dark
    contrast_ink = ramp.light_contrast if theme == "light" else ramp.dark_contrast

    red, green, blue = channels(accent)[:3]
    alpha = ACCENT_SOFT_ALPHA[theme]

    # As semânticas continuam vindo do CSS; as superfícies da paleta ganham
    # delas, porque é a paleta que manda no fundo.
    tokens = css_tokens(theme)
    tokens.update(surfaces)
    tokens.update(
        {
            "accent": accent,
            "accent-contrast": contrast_ink,
            "accent-soft": f"rgba({red:.0f}, {green:.0f}, {blue:.0f}, {alpha})",
        }
    )
    return tokens


def channels(value: str) -> tuple[float, float, float, float]:
    """Um ``#rrggbb`` ou um ``rgba(...)`` como quatro números."""
    if value.startswith("#"):
        raw = value.lstrip("#")
        return (*(float(int(raw[i : i + 2], 16)) for i in (0, 2, 4)), 1.0)
    parts = [part.strip() for part in value[value.index("(") + 1 : -1].split(",")]
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))


def resolve(tokens: dict[str, str], token: str, backdrop: str) -> tuple[float, ...]:
    """A cor final de um token, com o alpha composto sobre o fundo real."""
    red, green, blue, alpha = channels(tokens[token])
    if alpha == 1.0:
        return (red, green, blue)
    base = channels(tokens[backdrop])[:3]
    return tuple(c * alpha + b * (1 - alpha) for c, b in zip((red, green, blue), base))


def luminance(colour: tuple[float, ...]) -> float:
    def channel(value: float) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ratio(front: tuple[float, ...], back: tuple[float, ...]) -> float:
    a, b = luminance(front), luminance(back)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def measure(tokens: dict[str, str], ink: str, fill: str, under: str) -> float:
    """O contraste de ``ink`` sobre ``fill``, com ``fill`` posto sobre ``under``."""
    return ratio(resolve(tokens, ink, under), resolve(tokens, fill, under))
