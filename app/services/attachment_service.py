"""Attachment cards: ``[relatório.pdf](/midia/<uuid>)`` becomes a file card.

Why a renderer step instead of a syntax
---------------------------------------
The markdown the writer keeps is an ordinary link. It survives export, it
still means something in any other markdown tool, and there is nothing new to
learn. Everything the card shows — the real filename, the type and the size —
is read from the database at render time, so the card stays truthful even
after the document is edited by hand.

Resolution is front-loaded exactly like wiki links: one query per render,
never one per link.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as etree

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

from app.extensions import db
from app.models import MediaAsset
from app.services.media_service import badge_for, label_for
from app.utils.humanize import format_bytes

MEDIA_URL_PREFIX = "/midia/"

# Only a UUID-shaped identifier is ever looked up; anything else is left as the
# plain link it is.
_MEDIA_HREF_RE = re.compile(
    r"^/midia/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$"
)

# A document with thousands of media links must not turn into thousands of
# bound parameters. Past this many, the extra links are left exactly as they
# were written - see NOT_RESOLVED below.
MAX_ATTACHMENTS_RESOLVED = 500

# "This UUID was never looked up", which is not the same as "this file is
# gone". Without the distinction, every link past the cap would be rendered as
# an unavailable file - telling the writer their document is broken when it is
# not.
NOT_RESOLVED = object()


def collect_uuids(markdown_text: str) -> set[str]:
    """Every media UUID referenced by ``markdown_text``."""
    found = re.findall(
        r"/midia/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        markdown_text or "",
    )
    # dict.fromkeys keeps first-seen order, so the cap keeps the links the
    # reader meets first rather than an arbitrary subset of a set.
    return set(list(dict.fromkeys(found))[:MAX_ATTACHMENTS_RESOLVED])


def resolve_assets(uuids: set[str]) -> dict[str, MediaAsset]:
    """Map UUID -> asset in a single query."""
    if not uuids:
        return {}
    rows = db.session.scalars(
        db.select(MediaAsset).where(MediaAsset.uuid.in_(uuids))
    ).all()
    return {asset.uuid: asset for asset in rows}


def build_resolver(markdown_text: str):
    """Return a lookup closure with every referenced asset pre-resolved.

    Returns the asset, ``None`` when the UUID was looked up and no longer
    exists, or :data:`NOT_RESOLVED` when it was never looked up - because the
    document is past the cap, or because the database could not be reached.
    """
    try:
        wanted = collect_uuids(markdown_text)
        resolved = resolve_assets(wanted)
    except Exception:  # noqa: BLE001 - rendering must survive a database hiccup
        # Nothing was resolved, so nothing is claimed to be missing: the links
        # render exactly as the writer typed them.
        return lambda identifier: NOT_RESOLVED

    def lookup(identifier: str):
        if identifier not in wanted:
            return NOT_RESOLVED
        return resolved.get(identifier)

    return lookup


def _span(parent: etree.Element, class_name: str, text: str) -> etree.Element:
    element = etree.SubElement(parent, "span")
    element.set("class", class_name)
    element.text = text
    return element


def _is_plain_text_link(element: etree.Element) -> bool:
    """True when the anchor holds text only.

    ``[![capa](/midia/a)](/midia/b)`` is a deliberate image link - turning it
    into a card would throw the image away.
    """
    return len(element) == 0


class AttachmentTreeprocessor(Treeprocessor):
    """Rewrites links that point at an uploaded file into a card."""

    def __init__(self, md, resolver):
        super().__init__(md)
        self._resolver = resolver

    def run(self, root):
        for element in root.iter("a"):
            match = _MEDIA_HREF_RE.match((element.get("href") or "").strip())
            if not match or not _is_plain_text_link(element):
                continue

            asset = self._resolver(match.group(1))
            if asset is NOT_RESOLVED:
                continue  # never looked up; leave the link exactly as written

            self._render_card(element, asset, (element.text or "").strip())

    def _render_card(self, element, asset, written_label: str) -> None:
        element.text = None
        for child in list(element):
            element.remove(child)

        if asset is None:
            # The row is gone: say so instead of offering a link that 404s.
            element.set("class", "attachment attachment-missing")
            element.set("title", "Arquivo indisponível")
            _span(element, "attachment-badge", "?")
            body = etree.SubElement(element, "span")
            body.set("class", "attachment-body")
            _span(body, "attachment-name", written_label or "Arquivo indisponível")
            _span(body, "attachment-meta", "este arquivo não está mais disponível")
            return

        name = written_label or asset.original_name or "arquivo"
        details = [label_for(asset), format_bytes(asset.size_bytes)]
        # When the writer renamed the link, the real filename still matters -
        # it is what lands in the downloads folder.
        if asset.original_name and asset.original_name != name:
            details.append(asset.original_name)

        element.set("class", "attachment")
        element.set("title", f"Baixar {asset.original_name or name}")
        _span(element, "attachment-badge", badge_for(asset))
        body = etree.SubElement(element, "span")
        body.set("class", "attachment-body")
        _span(body, "attachment-name", name)
        _span(body, "attachment-meta", " · ".join(details))


class AttachmentExtension(Extension):
    """Registers the attachment card rewriter."""

    def __init__(self, resolver, **kwargs):
        self._resolver = resolver
        super().__init__(**kwargs)

    def extendMarkdown(self, md):  # noqa: N802 - Python-Markdown API
        md.treeprocessors.register(
            AttachmentTreeprocessor(md, self._resolver),
            "attachment",
            # Last, on purpose: after `inline` (20) the anchors exist, and
            # after `unescape` (0) a filename that had to be escaped in the
            # markdown source ("Relatório \[v2\].pdf") reads as the writer
            # typed it. Running earlier would compare against placeholders and
            # report every such name as a rename.
            -5,
        )
