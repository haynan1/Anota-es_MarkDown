"""HTML sanitization.

Every piece of HTML that reaches a template, a PDF or the live preview passes
through :func:`sanitize_html`. The policy is an allowlist: anything not named
here is removed.

Design notes
------------
* ``style`` attributes are **not** allowed. Python-Markdown emits
  ``style="text-align: …"`` on table cells; :func:`_preserve_table_alignment`
  rewrites those into CSS classes so the Content-Security-Policy can forbid
  inline styles outright (no ``'unsafe-inline'`` anywhere).
* URL schemes are restricted to ``http``, ``https``, ``mailto`` and ``tel``
  plus relative/fragment links. ``javascript:``, ``vbscript:``, ``file:`` and
  ``data:`` are rejected, which is what blocks the classic
  ``[click](javascript:alert(1))`` payload.
* ``class`` and ``id`` are allowed because Pygments, footnotes and the table of
  contents depend on them. They carry no script capability, and rendered
  content lives inside a scoped ``.markdown-body`` container.
"""

from __future__ import annotations

import re

import bleach
from bleach.html5lib_shim import Filter

# ── Allowlist ───────────────────────────────────────────────────────────────

ALLOWED_TAGS: set[str] = {
    "p", "br", "hr", "div", "span", "section",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "small",
    "sub", "sup", "abbr", "cite", "q", "kbd", "samp", "var", "time",
    "blockquote", "pre", "code",
    "ul", "ol", "li", "dl", "dt", "dd",
    "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "details", "summary",
    # Rendered by the task-list extension; attributes are locked down below.
    "input",
    # Uploaded video. `src` is restricted to our own delivery route by
    # InternalMediaFilter below — no remote or scripted source can survive.
    "video", "source",
}

# Uploaded media is served from exactly one path prefix.
MEDIA_URL_PREFIX = "/midia/"

_GLOBAL_ATTRS = ["class", "id", "title", "dir", "lang"]

ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "*": _GLOBAL_ATTRS,
    "a": [*_GLOBAL_ATTRS, "href", "name", "rel", "target"],
    "img": [*_GLOBAL_ATTRS, "src", "alt", "width", "height", "loading", "decoding"],
    "th": [*_GLOBAL_ATTRS, "colspan", "rowspan", "scope", "abbr"],
    "td": [*_GLOBAL_ATTRS, "colspan", "rowspan", "headers"],
    "col": [*_GLOBAL_ATTRS, "span"],
    "colgroup": [*_GLOBAL_ATTRS, "span"],
    "ol": [*_GLOBAL_ATTRS, "start", "reversed", "type"],
    "li": [*_GLOBAL_ATTRS, "value"],
    "input": ["type", "checked", "disabled", "class"],
    "details": [*_GLOBAL_ATTRS, "open"],
    "time": [*_GLOBAL_ATTRS, "datetime"],
    "abbr": [*_GLOBAL_ATTRS],
    "video": [*_GLOBAL_ATTRS, "src", "controls", "poster", "width", "height",
              "muted", "loop", "playsinline", "preload"],
    "source": ["src", "type"],
}

ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto", "tel"]

# Raw <script>/<style>/<iframe>/<object> blocks are removed *before* markdown
# rendering. Bleach would already neutralise the tags, but this also discards
# their textual payload so no "alert(1)" litter survives into the document.
# Defense in depth - the allowlist above remains the primary control.
_RAW_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|template|noscript)\b[^>]*>.*?"
    r"<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_DANGEROUS_VOID_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|template|noscript|base|meta|link|form)\b[^>]*>",
    re.IGNORECASE,
)

_ALIGN_RE = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.IGNORECASE)
_TASK_ITEM_RE = re.compile(r"<li>\s*<input\b", re.IGNORECASE)


def _preserve_table_alignment(html: str) -> str:
    """Rewrite cell alignment styles as classes so ``style`` can stay banned."""

    def replace(match: re.Match[str]) -> str:
        tag, attrs = match.group(1), match.group(2)
        align_match = _ALIGN_RE.search(attrs)
        if not align_match:
            return match.group(0)
        align = align_match.group(1).lower()
        cleaned = re.sub(r'\sstyle\s*=\s*"[^"]*"', "", attrs)
        cleaned = re.sub(r"\sstyle\s*=\s*'[^']*'", "", cleaned)
        return f'<{tag}{cleaned} class="md-align-{align}">'

    return re.sub(
        r"<(th|td)((?:\s[^>]*)?)>", replace, html, flags=re.IGNORECASE
    )


class ExternalLinkFilter(Filter):
    """Force ``rel``/``target`` on links that leave the application."""

    def __iter__(self):
        for token in Filter.__iter__(self):
            if token.get("type") == "StartTag" and token.get("name") == "a":
                attrs = token.get("data") or {}
                href = attrs.get((None, "href"), "")
                if href.lower().startswith(("http://", "https://")):
                    attrs[(None, "target")] = "_blank"
                    attrs[(None, "rel")] = "noopener noreferrer nofollow"
                token["data"] = attrs
            yield token


class CheckboxOnlyInputFilter(Filter):
    """Drop every ``<input>`` that is not a task-list checkbox.

    ``input`` has to be allowed for markdown task lists, but nothing else
    justifies a form control inside rendered content - a text or password
    field in a document would be a credible phishing surface.
    """

    def __iter__(self):
        for token in Filter.__iter__(self):
            is_input = (
                token.get("type") in {"StartTag", "EmptyTag"}
                and token.get("name") == "input"
            )
            if is_input:
                attrs = token.get("data") or {}
                if (attrs.get((None, "type")) or "").lower() != "checkbox":
                    continue
                # Rendered checkboxes are always read-only.
                attrs[(None, "disabled")] = ""
                token["data"] = attrs
            yield token


class InternalMediaFilter(Filter):
    """Drop any ``<video>``/``<source>`` that does not point at our own media.

    Allowing ``video`` at all is only safe while its ``src`` cannot be aimed
    elsewhere: a remote source would leak the reader's IP on every open, and
    ``autoplay`` is withheld so a document can never make noise by itself.
    """

    MEDIA_TAGS = {"video", "source"}

    def __iter__(self):
        skipping = False
        for token in Filter.__iter__(self):
            name = token.get("name")
            kind = token.get("type")

            if kind in {"StartTag", "EmptyTag"} and name in self.MEDIA_TAGS:
                attrs = token.get("data") or {}
                src = (attrs.get((None, "src")) or "").strip()
                if not src.startswith(MEDIA_URL_PREFIX) or src.startswith("//"):
                    if name == "video":
                        skipping = True
                    continue
                attrs[(None, "controls")] = ""
                attrs[(None, "preload")] = "metadata"
                attrs.pop((None, "autoplay"), None)
                token["data"] = attrs

            elif kind == "EndTag" and name == "video" and skipping:
                skipping = False
                continue

            yield token


_cleaner = bleach.Cleaner(
    tags=ALLOWED_TAGS,
    attributes=ALLOWED_ATTRIBUTES,
    protocols=ALLOWED_PROTOCOLS,
    strip=True,
    strip_comments=True,
    filters=[ExternalLinkFilter, CheckboxOnlyInputFilter, InternalMediaFilter],
)


def pre_strip_dangerous(markdown_text: str) -> str:
    """Remove raw executable HTML blocks from markdown before rendering."""
    if not markdown_text:
        return ""
    text = _RAW_DANGEROUS_BLOCK_RE.sub("", markdown_text)
    return _RAW_DANGEROUS_VOID_RE.sub("", text)


def sanitize_html(html: str) -> str:
    """Return ``html`` reduced to the allowlist above."""
    if not html:
        return ""
    prepared = _preserve_table_alignment(html)
    cleaned = _cleaner.clean(prepared)
    # Tag list items that contain a checkbox so CSS can drop the bullet.
    return _TASK_ITEM_RE.sub('<li class="task-list-item"><input', cleaned)


def sanitize_plain_text(value: str, max_length: int | None = None) -> str:
    """Strip every tag from ``value`` - used for titles and search snippets.

    Executable blocks are removed *with* their contents first, so a title of
    ``<script>x</script>`` becomes empty rather than the bare word ``x``.
    """
    cleaned = bleach.clean(
        pre_strip_dangerous(value or ""), tags=set(), attributes={}, strip=True
    )
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if max_length is not None and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned
