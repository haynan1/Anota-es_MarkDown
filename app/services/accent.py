"""The accent colour, made legible before it is served.

The user picks one hex value in Settings. That single colour then has to work
in eight places at once: as a fill behind white-ish text, as text on a pale
paper, as text on a near-black surface, as a hover state one step deeper, and
as the tint under all of them.

The previous approach did this in CSS with a fixed ``color-mix`` — the light
theme used the raw hex and the dark theme lifted it 22% toward white. A fixed
lift is a guess: it flatters a mid-tone and abandons the extremes. Someone who
picks near-black gets a dark grey accent on a black background (2.8:1, and
invisible); someone who picks a pale yellow gets white-on-white in the light
theme. CSS cannot measure contrast, so it cannot know.

This module measures. Given any hex the user can produce, it walks the colour
toward black or toward white — one small step at a time, hue and saturation
untouched — until it actually clears 4.5:1 against the background it will be
read on. The answer is different for the two themes, and it is computed, not
assumed. If the colour is already legible, nothing moves at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The two grounds an accent is read against, from base.css. They are the whole
# reason the two themes need different answers.
LIGHT_GROUND = "#FDFCFA"
DARK_GROUND = "#131110"

# Ink used when a light accent needs dark text on top of it.
LIGHT_INK = "#1A1712"
DARK_INK = "#0B0908"
PAPER = "#FFFFFF"

# WCAG AA for normal text. Accents carry labels, links and badge text, so this
# is the floor rather than the 3:1 allowed for large text or UI boundaries.
AA_TEXT = 4.5

# The two themes are aimed above the floor, and not at the same height.
#
# On paper, 5.0:1 leaves a margin for the tinted surfaces the accent also
# lands on (--surface-2, --surface-sunken) without darkening the colour so far
# that the hue drains out of it.
#
# On a near-black ground the requirement is different in kind. A colour that
# merely passes at 4.6:1 there reads as soot — it is legible and it is dead.
# Dark interfaces need the accent to hold its chroma against a very low
# luminance ground, and that means aiming much higher: 7.0:1 is where a
# mid-tone lifts into something that still looks like the colour that was
# chosen.
LIGHT_TARGET = 5.0
DARK_TARGET = 7.0

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

#: The accent the application ships with: a petrol green that sits warm
#: against paper and cool against ink, and — unlike the indigo it replaces —
#: is not the colour every other tool defaults to. Also the value substituted
#: for anything unparseable.
DEFAULT_ACCENT = "#0F6E64"
FALLBACK = DEFAULT_ACCENT


@dataclass(frozen=True)
class AccentRamp:
    """Everything ``theme.css.jinja`` needs, already proven legible."""

    light: str
    light_strong: str
    light_contrast: str
    dark: str
    dark_strong: str
    dark_contrast: str

    #: The raw colour the user chose, kept for tints where legibility is not
    #: at stake (a 4px category bar, a dot, a chart series).
    seed: str


# ── colour maths ───────────────────────────────────────────────────────────


def _parse(value: str | None) -> tuple[int, int, int]:
    match = _HEX.match((value or "").strip())
    if not match:
        match = _HEX.match(FALLBACK)
        assert match is not None  # FALLBACK is a literal six-digit hex
    digits = match.group(1)
    return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _format(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    r, g, b = _parse(colour)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def _blend(colour: str, towards: str, amount: float) -> str:
    """Move ``colour`` a fraction of the way to ``towards`` in sRGB."""
    src, dst = _parse(colour), _parse(towards)
    return _format(
        tuple(round(s + (d - s) * amount) for s, d in zip(src, dst))  # type: ignore[arg-type]
    )


def _reach(colour: str, ground: str, towards: str, target: float = AA_TEXT) -> str:
    """Push ``colour`` toward ``towards`` until it clears ``target`` on ``ground``.

    Forty steps of 2.5% each covers the full distance; the loop returns the
    first step that passes, so a colour already above the bar is returned
    untouched and a colour just below it moves as little as it has to.
    """
    if contrast(colour, ground) >= target:
        return colour
    for step in range(1, 41):
        candidate = _blend(colour, towards, step * 0.025)
        if contrast(candidate, ground) >= target:
            return candidate
    return towards


def _best_ink(background: str) -> str:
    """Whichever of paper or ink is more readable on this fill."""
    return PAPER if contrast(PAPER, background) >= contrast(LIGHT_INK, background) else LIGHT_INK


def build_ramp(seed: str | None) -> AccentRamp:
    """Derive the legible light and dark accents for one chosen colour."""
    base = _format(_parse(seed))

    # Light theme: the accent is read as text on near-white, so it descends
    # toward ink until it clears the bar.
    light = _reach(base, LIGHT_GROUND, LIGHT_INK, LIGHT_TARGET)
    # The hover state is one visible step deeper, and deeper on a pale ground
    # only ever helps contrast — no second check needed.
    light_strong = _blend(light, LIGHT_INK, 0.16)

    # Dark theme: the same colour ascends toward paper until it clears the bar
    # against the near-black ground.
    dark = _reach(base, DARK_GROUND, PAPER, DARK_TARGET)
    # Here "stronger" means brighter: deepening a colour on a black ground
    # would walk it back toward invisibility.
    dark_strong = _blend(dark, PAPER, 0.18)

    return AccentRamp(
        light=light,
        light_strong=light_strong,
        light_contrast=_best_ink(light),
        dark=dark,
        dark_strong=dark_strong,
        dark_contrast=DARK_INK if contrast(DARK_INK, dark) >= AA_TEXT else PAPER,
        seed=base,
    )
