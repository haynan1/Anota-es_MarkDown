"""Markdown -> HTML rendering.

There is exactly one renderer in the system. The live preview, the stored
``rendered_html`` and the PDF all call :func:`render_markdown`, so what the
user sees while typing is byte-for-byte what gets saved and printed.

``markdown.Markdown`` instances are stateful and therefore not thread-safe;
the Flask development server is threaded, so one instance is kept per thread.
"""

from __future__ import annotations

import logging
import threading

import markdown
from markupsafe import escape

from app.services.align_service import AlignExtension
from app.services.attachment_service import NOT_RESOLVED, AttachmentExtension
from app.services.attachment_service import build_resolver as build_attachment_resolver
from app.services.sanitizer import pre_strip_dangerous, sanitize_html
from app.services.wikilink_service import WikiLinkExtension, build_resolver

logger = logging.getLogger(__name__)


def _current_wikilink_resolver(key: str):
    """Bridge between the cached renderer and the per-render link resolver.

    The Markdown instance is built once per thread, but wiki links must be
    resolved against the database on every render. The extension therefore
    asks this function, which reads the resolver installed for the render
    currently in progress.
    """
    resolver = getattr(_local, "wiki_resolver", None)
    return resolver(key) if resolver else None


def _current_attachment_resolver(identifier: str):
    """Same bridge, for uploaded files referenced by a link."""
    resolver = getattr(_local, "attachment_resolver", None)
    # With no resolver installed nothing was looked up, which is not the same
    # as "the file is gone" - see attachment_service.NOT_RESOLVED.
    return resolver(identifier) if resolver else NOT_RESOLVED

_EXTENSIONS = [
    "markdown.extensions.tables",
    "markdown.extensions.footnotes",
    "markdown.extensions.sane_lists",
    "markdown.extensions.attr_list",
    "markdown.extensions.def_list",
    "markdown.extensions.abbr",
    "markdown.extensions.toc",
    "markdown.extensions.md_in_html",
    "pymdownx.superfences",
    "pymdownx.highlight",
    "pymdownx.tasklist",
    "pymdownx.tilde",
    "pymdownx.caret",
    "pymdownx.smartsymbols",
    "pymdownx.magiclink",
]

_EXTENSION_CONFIGS = {
    "markdown.extensions.toc": {
        "permalink": False,
        "toc_depth": "2-4",
    },
    "markdown.extensions.footnotes": {
        "BACKLINK_TEXT": "↩",
        "BACKLINK_TITLE": "Voltar para a nota {}",
        "SEPARATOR": "-",
    },
    "pymdownx.highlight": {
        "use_pygments": True,
        "css_class": "highlight",
        # Guessing produces noisy, wrong colouring on plain fences.
        "guess_lang": False,
        "linenums": False,
    },
    "pymdownx.tasklist": {
        "custom_checkbox": False,
        "clickable_checkbox": False,
    },
    "pymdownx.magiclink": {
        "repo_url_shortener": False,
        "social_url_shortener": False,
    },
}

_local = threading.local()


def _renderer() -> markdown.Markdown:
    instance = getattr(_local, "renderer", None)
    if instance is None:
        instance = markdown.Markdown(
            extensions=[
                *_EXTENSIONS,
                AlignExtension(),
                WikiLinkExtension(_current_wikilink_resolver),
                AttachmentExtension(_current_attachment_resolver),
            ],
            extension_configs=_EXTENSION_CONFIGS,
            output_format="html",
            tab_length=4,
        )
        _local.renderer = instance
    return instance


def _plain_text_fallback(markdown_text: str) -> str:
    """Escaped, unformatted rendering used when the parser gives up.

    Losing the formatting is bad; losing the user's text is worse. The document
    still saves, still exports and still shows its content - just without
    markdown structure, with a visible explanation.
    """
    return (
        '<div class="md-render-warning" role="note">'
        "Este documento tem aninhamento profundo demais para ser formatado "
        "(por exemplo, listas com centenas de níveis). O texto está preservado "
        "abaixo sem formatação — simplifique a estrutura para voltar a "
        "visualizá-lo normalmente."
        "</div>"
        f"<pre>{escape(markdown_text)}</pre>"
    )


def render_markdown(markdown_text: str) -> str:
    """Render ``markdown_text`` to sanitized, safe-to-embed HTML."""
    if not markdown_text or not markdown_text.strip():
        return ""

    renderer = _renderer()
    renderer.reset()
    # One database lookup for every [[link]] in this document, resolved before
    # parsing starts rather than one query per link during it. Uploaded files
    # referenced by a link are resolved the same way, in one more query.
    _local.wiki_resolver = build_resolver(markdown_text)
    _local.attachment_resolver = build_attachment_resolver(markdown_text)

    try:
        raw_html = renderer.convert(pre_strip_dangerous(markdown_text))
    except RecursionError:
        # Python-Markdown recurses once per nesting level, so a line like
        # "- " * 800 exhausts the stack. Left unhandled this is a 500 on every
        # save and preview of that document - the user simply cannot store
        # their text. Degrade instead of failing.
        logger.warning(
            "Aninhamento excessivo ao renderizar markdown (%d caracteres); "
            "usando fallback em texto puro.",
            len(markdown_text),
        )
        # The parser may be left in an inconsistent state; discard it.
        _local.renderer = None
        return sanitize_html(_plain_text_fallback(markdown_text))
    finally:
        _local.wiki_resolver = None
        _local.attachment_resolver = None

    return sanitize_html(raw_html)


