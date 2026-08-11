"""YAML front matter, restricted to the flat shape this application writes.

A Markdown archive has to survive a round trip through other tools, so the
metadata block is real YAML - but only the subset that carries scalars and
simple lists. Pulling in a full YAML parser to read eight keys would add a
dependency whose default loader is a documented deserialization risk, for a
grammar this application never emits.

Two rules keep the parser safe on untrusted input:

* it never raises - a malformed block is body text, not an error;
* every dimension is bounded - number of keys, items per list, characters per
  value - so a crafted file cannot turn an import into an allocation spike.

Unknown keys are kept as strings rather than rejected: a file written by
Obsidian, Jekyll or Hugo still imports, with the fields this application
understands picked out and the rest ignored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TypeAlias

DELIMITER = "---"

# Ceilings, not guesses: a header carries metadata about a document, never the
# document. Anything past these limits is malformed or hostile, and in both
# cases the right answer is to stop reading rather than to grow.
MAX_FIELDS = 40
MAX_LIST_ITEMS = 50
MAX_VALUE_LENGTH = 500

#: What :func:`parse` produces. A field is a string or a list of strings -
#: booleans and dates are read back through the ``flag_of`` / ``text_of``
#: helpers, so the parser itself never has to guess a type.
FrontMatterValue: TypeAlias = str | list[str]
#: What :func:`dump` accepts. ``None`` means "omit this key".
FrontMatterInput: TypeAlias = str | list[str] | bool | None

# The block must open the file: a `---` further down is a horizontal rule.
_BLOCK_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]{0,39})[ \t]*:[ \t]*(?P<value>.*)$")
_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(?P<value>.+)$")

_TRUE_VALUES = {"true", "yes", "on", "1", "sim", "verdadeiro"}


# ── Reading ─────────────────────────────────────────────────────────────────


def parse(source: str) -> tuple[dict[str, FrontMatterValue], str]:
    """Split ``source`` into ``(fields, body)``.

    Without a well-formed opening block the whole text is the body, which is
    what makes this safe to run over every imported file.
    """
    normalized = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    match = _BLOCK_RE.match(normalized)
    if match is None:
        return {}, normalized
    return _read_fields(match.group(1)), normalized[match.end() :]


def _read_fields(block: str) -> dict[str, FrontMatterValue]:
    fields: dict[str, FrontMatterValue] = {}
    pending_key: str | None = None

    for line in block.split("\n"):
        if len(fields) >= MAX_FIELDS:
            break

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        item = _ITEM_RE.match(line)
        if item is not None and pending_key is not None:
            _append_item(fields, pending_key, item.group("value"))
            continue

        field = _FIELD_RE.match(line)
        if field is None:
            # Anything else (nested mappings, folded scalars) is beyond this
            # subset. Forget the open key so its indented body is not mistaken
            # for list items belonging to it.
            pending_key = None
            continue

        key = field.group("key").lower()
        raw = field.group("value").strip()

        if not raw:
            # Either an empty scalar or the head of a block list - the next
            # line decides, so the key opens as an empty list.
            fields[key] = []
            pending_key = key
            continue

        pending_key = None
        fields[key] = _read_inline_list(raw) if raw.startswith("[") else _read_scalar(raw)

    return fields


def _append_item(fields: dict[str, FrontMatterValue], key: str, raw: str) -> None:
    current = fields.get(key)
    if not isinstance(current, list):
        return
    if len(current) >= MAX_LIST_ITEMS:
        return
    value = _read_scalar(raw)
    if value:
        current.append(value)


def _read_inline_list(raw: str) -> list[str]:
    inner = raw.strip()
    if inner.startswith("["):
        inner = inner[1:]
    if inner.endswith("]"):
        inner = inner[:-1]

    items = []
    for part in _split_items(inner):
        value = _read_scalar(part)
        if value:
            items.append(value)
        if len(items) >= MAX_LIST_ITEMS:
            break
    return items


def _split_items(raw: str) -> list[str]:
    """Split on commas that are not inside a quoted item.

    A naive ``split(",")`` would tear ``["Marketing, Vendas"]`` in half, and a
    group or category name with a comma in it is perfectly legal.
    """
    items: list[str] = []
    buffer: list[str] = []
    quote = ""
    escaped = False

    for char in raw:
        if escaped:
            buffer.append(char)
            escaped = False
        elif quote:
            buffer.append(char)
            if char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            buffer.append(char)
        elif char == ",":
            items.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)

    items.append("".join(buffer))
    return items


def _read_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        inner = _unescape(inner) if value[0] == '"' else inner.replace("''", "'")
        return inner[:MAX_VALUE_LENGTH]
    return value[:MAX_VALUE_LENGTH]


def _unescape(inner: str) -> str:
    """Undo the escaping :func:`_quote` applies, in one left-to-right pass."""
    out: list[str] = []
    escaped = False
    for char in inner:
        if escaped:
            out.append({"n": "\n", "t": "\t"}.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    return "".join(out)


# ── Typed access ────────────────────────────────────────────────────────────


def text_of(fields: Mapping[str, FrontMatterValue], key: str, default: str = "") -> str:
    """The value of ``key`` as a single string."""
    value = fields.get(key)
    if isinstance(value, list):
        return value[0] if value else default
    return value.strip() if isinstance(value, str) and value.strip() else default


def list_of(fields: Mapping[str, FrontMatterValue], key: str) -> list[str]:
    """The value of ``key`` as a list.

    A scalar is split on commas, because ``tags: alpha, beta`` is how most
    hand-written front matter states a list and refusing it would reject files
    people already have.
    """
    value = fields.get(key)
    if isinstance(value, list):
        return value[:MAX_LIST_ITEMS]
    if isinstance(value, str):
        return [part.strip() for part in _split_items(value) if part.strip()][
            :MAX_LIST_ITEMS
        ]
    return []


def flag_of(
    fields: Mapping[str, FrontMatterValue], key: str, default: bool = False
) -> bool:
    value = text_of(fields, key)
    return value.strip().lower() in _TRUE_VALUES if value else default


# ── Writing ─────────────────────────────────────────────────────────────────


def dump(fields: Mapping[str, FrontMatterInput]) -> str:
    """Render ``fields`` as a front matter block, terminated by a newline.

    Strings are always quoted. Deciding per value whether a bare scalar would
    be ambiguous is where hand-rolled YAML writers go wrong: ``title: no`` is
    a boolean, ``title: 12:30`` is a syntax error, and a title beginning with
    ``#`` disappears. Quoting everything costs two characters and removes the
    entire class of mistakes.
    """
    lines = [DELIMITER]

    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, Sequence) and not isinstance(value, str):
            items = [_quote(str(item)) for item in value[:MAX_LIST_ITEMS] if str(item).strip()]
            if items:
                lines.append(f"{key}: [{', '.join(items)}]")
        elif str(value).strip():
            lines.append(f"{key}: {_quote(str(value))}")

    lines.append(DELIMITER)
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "\\t")
    )
    return f'"{escaped[:MAX_VALUE_LENGTH]}"'
