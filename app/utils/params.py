"""Reading request parameters into the types the application actually uses.

Everything here treats its input as hostile: a query string is a hand-editable
surface, and a stale bookmark is indistinguishable from an attack. Nothing
raises - an unusable value becomes ``None``, and the caller decides what the
absence means.
"""

from __future__ import annotations

#: Beyond this a value is not an identifier anyone navigated to. The check is
#: on the *string*, before ``int``: since Python 3.11 converting a literal of
#: more than 4300 digits raises, so an unbounded ``?pagina=999…9`` would answer
#: an ordinary filter with a 500.
MAX_ID_DIGITS = 12


def whole_int(raw: str | None, max_digits: int = MAX_ID_DIGITS) -> int | None:
    """A non-negative integer from a request value, or ``None``.

    ``isdecimal`` rather than ``isdigit``: the latter also accepts "²" and the
    other superscripts, which ``int`` then refuses - a 500 handed back for a
    character somebody pasted by accident.
    """
    value = (raw or "").strip()
    if not value.isdecimal() or len(value) > max_digits:
        return None
    return int(value)


def positive_int(raw: str | None, max_digits: int = MAX_ID_DIGITS) -> int | None:
    """The same, for the values where zero means "none of them".

    Page numbers and row identifiers both start at one, so "0" is not a
    smaller answer - it is no answer, and the caller's own default should win.
    """
    return whole_int(raw, max_digits) or None
