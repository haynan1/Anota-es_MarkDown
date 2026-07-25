"""Copiar para a Wix: o documento como texto rico, pronto para colar.

The problem
-----------
The description field of a Wix product is a rich-text editor. It accepts
*formatted text* pasted from another editor - bold, italic, headings, lists,
links, line breaks - and nothing else. Pasting Markdown puts literal asterisks
on the page; pasting our full HTML loses everything the field does not
understand, silently and unpredictably.

The approach
------------
Render the document once, with the same renderer as everywhere else, then
reduce that HTML to exactly the subset the field keeps, converting - rather
than dropping - whatever has an honest equivalent:

* a table becomes one paragraph per row, each cell labelled with its column
  header, which is what a specification table is actually saying;
* a code block becomes plain paragraphs;
* a checklist keeps its boxes as ``☐`` and ``☑``;
* a horizontal rule becomes a line of dashes.

What genuinely cannot cross - images, video, and links that only resolve
inside this application - is removed and *reported*. The caller shows those
notes next to the copy button, so nothing disappears without the writer being
told.

The result is put on the clipboard as ``text/html`` with a ``text/plain``
twin, which is exactly what a paste from Word or Google Docs looks like.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as etree
from collections import Counter
from dataclasses import dataclass, field

import bleach
import html5lib

from app.services.markdown_service import render_markdown

# ── The subset Wix keeps ────────────────────────────────────────────────────

WIX_TAGS: set[str] = {
    "p", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "u", "s",
    "ul", "ol", "li",
    "blockquote",
    "a",
}
WIX_ATTRIBUTES: dict[str, list[str]] = {"a": ["href"]}
WIX_PROTOCOLS: list[str] = ["http", "https", "mailto", "tel"]

# Inline tags that survive, mapped to what they become.
_INLINE_MAP: dict[str, str] = {
    "strong": "strong", "b": "strong",
    "em": "em", "i": "em", "cite": "em", "q": "em",
    "u": "u", "ins": "u",
    "s": "s", "del": "s", "strike": "s",
    "br": "br",
}

# Block containers that only wrap other blocks: walk through them.
_TRANSPARENT_BLOCKS = {"div", "section", "article", "figure", "details", "main", "header", "footer"}

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Deep enough for any document a person writes, shallow enough that a
# pathological one cannot exhaust the stack.
MAX_BLOCK_DEPTH = 40

# A link that only resolves inside this application is not a link to anyone
# else; its text is kept, its href is not.
_INTERNAL_HREF_RE = re.compile(r"^(/|#|\.)")

_WHITESPACE_RE = re.compile(r"[ \t]+")

DIVIDER_TEXT = "———"


@dataclass(slots=True)
class WixDocument:
    """The pasteable document plus everything the writer should know."""

    html: str
    text: str
    notes: list[str] = field(default_factory=list)


# ── Tree helpers ────────────────────────────────────────────────────────────


def _append_text(parent: etree.Element, text: str | None) -> None:
    """Append ``text`` to ``parent`` at the position a new child would take."""
    if not text:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _text_of(element: etree.Element) -> str:
    return "".join(element.itertext())


def _is_empty(element: etree.Element) -> bool:
    """True when a block would paste as nothing at all."""
    if element.tag in {"ul", "ol", "br"}:
        return not len(element)
    if element.find(".//br") is not None:
        return False
    return not _text_of(element).strip()


# ── Conversion ──────────────────────────────────────────────────────────────


class _Converter:
    """Rewrites rendered HTML into the Wix subset, counting what it changes."""

    def __init__(self) -> None:
        self.stats: Counter[str] = Counter()

    # -- inline ------------------------------------------------------------

    def inline(self, source: etree.Element, target: etree.Element) -> None:
        _append_text(target, source.text)

        for child in source:
            self._inline_child(child, target)
            _append_text(target, child.tail)

    def _inline_child(self, child: etree.Element, target: etree.Element) -> None:
        tag = child.tag if isinstance(child.tag, str) else ""

        if tag == "img":
            self.stats["images"] += 1
            return

        if tag in {"video", "audio", "source", "iframe"}:
            self.stats["videos"] += 1
            return

        if tag == "input":
            # Task-list checkbox: keep the state as a character, since the
            # control itself cannot survive the paste.
            self.stats["checklists"] += 1
            _append_text(target, "☑" if child.get("checked") is not None else "☐")
            # The renderer already puts a space before the item text; add one
            # only if it does not, so the box never touches the first word.
            if not (child.tail or "").startswith((" ", "\t", "\n")):
                _append_text(target, " ")
            return

        if tag == "a":
            self._link(child, target)
            return

        if tag == "code" or tag == "kbd" or tag == "samp" or tag == "var":
            self.stats["code"] += 1
            self.inline(child, target)  # unwrap: the text stays, the styling goes
            return

        mapped = _INLINE_MAP.get(tag)
        if mapped:
            element = etree.SubElement(target, mapped)
            if mapped != "br":
                self.inline(child, element)
            return

        # Anything else (span, mark, small, sup, abbr…) keeps its text only.
        self.inline(child, target)

    def _link(self, source: etree.Element, target: etree.Element) -> None:
        href = (source.get("href") or "").strip()
        classes = (source.get("class") or "").split()

        if "attachment" in classes:
            # The card is three nested spans; only the filename means anything
            # once the file is out of reach.
            self.stats["attachments"] += 1
            name = source.find(".//span[@class='attachment-name']")
            _append_text(target, _text_of(name) if name is not None else _text_of(source))
            return

        if not href or _INTERNAL_HREF_RE.match(href):
            self.stats["internal_links"] += 1
            self.inline(source, target)
            return

        anchor = etree.SubElement(target, "a")
        anchor.set("href", href)
        self.inline(source, anchor)

    # -- blocks ------------------------------------------------------------

    def blocks(self, node: etree.Element, out: list[etree.Element], depth: int = 0) -> None:
        # Nested containers are walked recursively; the guard keeps a document
        # with pathological nesting from exhausting the stack, the same way
        # the markdown renderer degrades instead of failing.
        if depth > MAX_BLOCK_DEPTH:
            paragraph = etree.Element("p")
            paragraph.text = _text_of(node).strip()
            if paragraph.text:
                out.append(paragraph)
            return

        for child in node:
            tag = child.tag if isinstance(child.tag, str) else ""

            if tag in _HEADINGS or tag == "p":
                out.append(self._simple(child, tag))
            elif tag in {"ul", "ol"}:
                out.append(self._list(child))
            elif tag == "blockquote":
                out.extend(self._quote(child))
            elif tag == "pre":
                out.extend(self._code_block(child))
            elif tag == "table":
                out.extend(self._table(child))
            elif tag == "dl":
                out.extend(self._definitions(child))
            elif tag == "hr":
                out.append(self._divider())
            elif tag == "img":
                self.stats["images"] += 1
            elif tag in {"video", "audio", "iframe"}:
                self.stats["videos"] += 1
            elif tag in _TRANSPARENT_BLOCKS:
                if tag == "details":
                    self.stats["details"] += 1
                self.blocks(child, out, depth + 1)
            elif tag == "summary":
                out.append(self._simple(child, "p", bold=True))
            elif tag:
                out.append(self._simple(child, "p"))

    def _simple(self, source: etree.Element, tag: str, bold: bool = False) -> etree.Element:
        element = etree.Element(tag)
        if bold:
            inner = etree.SubElement(element, "strong")
            self.inline(source, inner)
        else:
            self.inline(source, element)
        return element

    def _list(self, source: etree.Element) -> etree.Element:
        element = etree.Element(source.tag)
        for item in source.findall("li"):
            entry = etree.SubElement(element, "li")
            # A list item mixes text with nested blocks; paragraphs inside it
            # are flattened so the item does not paste as an empty bullet.
            self.inline(item, entry)
            for nested in item:
                if nested.tag in {"ul", "ol"}:
                    entry.append(self._list(nested))
                elif nested.tag == "p":
                    self.inline(nested, entry)
        return element

    def _quote(self, source: etree.Element) -> list[etree.Element]:
        blocks: list[etree.Element] = []
        self.blocks(source, blocks, depth=1)
        if not blocks:
            blocks = [self._simple(source, "p")]

        quote = etree.Element("blockquote")
        for block in blocks:
            # Wix quotes hold text, not headings.
            if block.tag in _HEADINGS:
                block.tag = "p"
            quote.append(block)
        return [quote]

    def _code_block(self, source: etree.Element) -> list[etree.Element]:
        self.stats["code"] += 1
        blocks = []
        for line in _text_of(source).splitlines():
            expanded = line.replace("\t", "    ")
            stripped = expanded.lstrip(" ")
            paragraph = etree.Element("p")
            # Rich text collapses runs of spaces, so indentation is rebuilt
            # with non-breaking ones: code that loses its shape is unreadable.
            paragraph.text = " " * (len(expanded) - len(stripped)) + stripped
            blocks.append(paragraph)
        return blocks or [etree.Element("p")]

    def _table(self, source: etree.Element) -> list[etree.Element]:
        self.stats["tables"] += 1

        rows = [row for row in source.iter("tr")]
        if not rows:
            return []

        headers = [_text_of(cell).strip() for cell in rows[0] if cell.tag in {"th", "td"}]
        has_header = any(cell.tag == "th" for cell in rows[0])

        blocks: list[etree.Element] = []
        for index, row in enumerate(rows):
            if index == 0 and has_header:
                continue

            cells = [cell for cell in row if cell.tag in {"th", "td"}]
            if not cells:
                continue

            paragraph = etree.Element("p")
            for position, cell in enumerate(cells):
                if position:
                    _append_text(paragraph, " · ")
                label = headers[position] if has_header and position < len(headers) else ""
                if label:
                    strong = etree.SubElement(paragraph, "strong")
                    strong.text = f"{label}: "
                self.inline(cell, paragraph)
            blocks.append(paragraph)

        return blocks

    def _definitions(self, source: etree.Element) -> list[etree.Element]:
        blocks: list[etree.Element] = []
        for child in source:
            if child.tag == "dt":
                blocks.append(self._simple(child, "p", bold=True))
            elif child.tag == "dd":
                blocks.append(self._simple(child, "p"))
        return blocks

    def _divider(self) -> etree.Element:
        element = etree.Element("p")
        element.text = DIVIDER_TEXT
        return element


# ── Notes ───────────────────────────────────────────────────────────────────


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {plural}"


def _build_notes(stats: Counter[str]) -> list[str]:
    notes: list[str] = []

    if stats["images"]:
        notes.append(
            f"{_plural(stats['images'], 'imagem não foi copiada', 'imagens não foram copiadas')}"
            " — a Wix não aceita imagens coladas de fora; adicione pela galeria do produto."
        )
    if stats["videos"]:
        notes.append(
            f"{_plural(stats['videos'], 'vídeo não foi copiado', 'vídeos não foram copiados')}"
            " — use o campo de vídeo do produto."
        )
    if stats["tables"]:
        notes.append(
            f"{_plural(stats['tables'], 'tabela virou', 'tabelas viraram')} parágrafos,"
            " um por linha, com o título da coluna em negrito."
        )
    if stats["code"]:
        notes.append("Trechos de código viraram texto comum, sem a fonte monoespaçada.")
    if stats["attachments"]:
        notes.append(
            f"{_plural(stats['attachments'], 'anexo virou', 'anexos viraram')} texto —"
            " o arquivo continua aqui no aplicativo, mas não vai junto para a Wix."
        )
    if stats["internal_links"]:
        notes.append(
            f"{_plural(stats['internal_links'], 'link interno virou', 'links internos viraram')}"
            " texto — eles só funcionam dentro deste aplicativo."
        )
    if stats["checklists"]:
        notes.append("Caixas de seleção viraram os símbolos ☐ e ☑.")

    return notes


# ── Plain text twin ─────────────────────────────────────────────────────────


def _plain_text(blocks: list[etree.Element]) -> str:
    lines: list[str] = []

    for block in blocks:
        if block.tag in {"ul", "ol"}:
            for index, item in enumerate(block.findall("li"), start=1):
                marker = f"{index}." if block.tag == "ol" else "•"
                lines.append(f"{marker} {_WHITESPACE_RE.sub(' ', _text_of(item)).strip()}")
            lines.append("")
            continue

        text = _WHITESPACE_RE.sub(" ", _text_of(block)).strip()
        if block.tag == "blockquote":
            text = "\n".join(f"> {line}" for line in text.splitlines() if line)
        lines.append(text)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


# ── Public API ──────────────────────────────────────────────────────────────


def render_for_wix(markdown_text: str) -> WixDocument:
    """Convert ``markdown_text`` into rich text a Wix field will accept."""
    rendered = render_markdown(markdown_text or "")
    if not rendered.strip():
        return WixDocument(html="", text="", notes=[])

    tree = html5lib.parseFragment(
        rendered, treebuilder="etree", namespaceHTMLElements=False
    )

    converter = _Converter()
    blocks: list[etree.Element] = []
    converter.blocks(tree, blocks)
    blocks = [block for block in blocks if not _is_empty(block)]

    html = "".join(
        etree.tostring(block, encoding="unicode", method="html") for block in blocks
    )

    # Defence in depth: the converter is the design, this is the guarantee.
    # Whatever it might have let through is removed here, against a list that
    # is strictly smaller than the application's own.
    html = bleach.clean(
        html,
        tags=WIX_TAGS,
        attributes=WIX_ATTRIBUTES,
        protocols=WIX_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )

    return WixDocument(
        html=html,
        text=_plain_text(blocks),
        notes=_build_notes(converter.stats),
    )
