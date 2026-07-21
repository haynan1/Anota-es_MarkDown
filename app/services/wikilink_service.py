"""Obsidian-style links between documents: ``[[Título]]``.

Syntax
------
``[[Guia de vendas]]``            link with the document title as its text
``[[Guia de vendas|o guia]]``     link with custom text

Resolution matches on the title (case- and accent-insensitive, via the slug),
so a link keeps working when the document is renamed only in punctuation or
capitalisation. A target that does not exist still renders - as a muted
"broken link" that offers to create it - because a note-taking tool has to let
you write a link before the page behind it exists.

The rendered anchor points at the document's UUID, not its slug: renaming a
document must never break links that already point to it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as etree

from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor

from app.extensions import db
from app.models import Document
from app.utils.files import safe_slug

# [[target]] or [[target|label]] — no nested brackets, no newlines.
WIKILINK_RE = r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]+?))?\]\]"

MAX_TARGETS_CACHED = 5000
# A title is capped at 200 characters, so a longer target cannot match one
# and only serves to bloat the rendered href.
MAX_TARGET_LENGTH = 200


def resolve_targets(names: set[str]) -> dict[str, tuple[str, str]]:
    """Map normalised link targets to ``(uuid, title)``.

    One query for the whole document, regardless of how many links it holds.
    """
    if not names:
        return {}

    rows = db.session.execute(
        db.select(Document.uuid, Document.title, Document.slug)
        .where(Document.is_deleted.is_(False))
        .limit(MAX_TARGETS_CACHED)
    ).all()

    index: dict[str, tuple[str, str]] = {}
    for uuid, title, slug in rows:
        # Slug first so "Guia de Vendas" and "guia-de-vendas" both resolve;
        # an exact title match wins if two documents share a slug root.
        index.setdefault(slug, (uuid, title))
        index[safe_slug(title or "", fallback="")] = (uuid, title)

    return {name: index[name] for name in names if name in index}


class WikiLinkProcessor(InlineProcessor):
    def __init__(self, pattern, md, resolver):
        super().__init__(pattern, md)
        self._resolver = resolver

    def handleMatch(self, match, data):  # noqa: N802 - Python-Markdown API
        raw_target = (match.group(1) or "").strip()[:MAX_TARGET_LENGTH]
        label = ((match.group(2) or "").strip() or raw_target)[:MAX_TARGET_LENGTH]

        if not raw_target:
            return None, None, None

        key = safe_slug(raw_target, fallback="")
        resolved = self._resolver(key)

        element = etree.Element("a")
        element.text = label

        if resolved:
            uuid, title = resolved
            element.set("href", f"/editor/{uuid}")
            element.set("class", "wikilink")
            element.set("title", title)
        else:
            # Deliberately still an anchor: it takes you to a pre-filled
            # "create this document" flow instead of being a dead end.
            element.set("href", f"/documentos/novo-por-titulo?titulo={raw_target}")
            element.set("class", "wikilink wikilink-missing")
            element.set("title", f"“{raw_target}” ainda não existe — criar")

        return element, match.start(0), match.end(0)


class WikiLinkExtension(Extension):
    """Registers the ``[[...]]`` inline pattern."""

    def __init__(self, resolver, **kwargs):
        self._resolver = resolver
        super().__init__(**kwargs)

    def extendMarkdown(self, md):  # noqa: N802 - Python-Markdown API
        md.inlinePatterns.register(
            WikiLinkProcessor(WIKILINK_RE, md, self._resolver),
            "wikilink",
            # Above `link` so [[x]] is not eaten by the standard [x] parser.
            185,
        )


def collect_targets(markdown_text: str) -> set[str]:
    """Normalised targets referenced by ``markdown_text``."""
    targets = set()
    for match in re.finditer(WIKILINK_RE, markdown_text or ""):
        key = safe_slug((match.group(1) or "").strip(), fallback="")
        if key:
            targets.add(key)
    return targets


def build_resolver(markdown_text: str):
    """Return a lookup closure with every target pre-resolved.

    Resolving inside the inline processor would issue one query per link;
    this front-loads them into a single lookup.
    """
    try:
        resolved = resolve_targets(collect_targets(markdown_text))
    except Exception:  # noqa: BLE001 - rendering must survive a database hiccup
        resolved = {}

    return lambda key: resolved.get(key)
