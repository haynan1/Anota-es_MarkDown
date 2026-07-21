"""Markdown -> HTML rendering.

There is exactly one renderer in the system. The live preview, the stored
``rendered_html`` and the PDF all call :func:`render_markdown`, so what the
user sees while typing is byte-for-byte what gets saved and printed.

``markdown.Markdown`` instances are stateful and therefore not thread-safe;
the Flask development server is threaded, so one instance is kept per thread.
"""

from __future__ import annotations

import threading

import markdown

from app.services.sanitizer import pre_strip_dangerous, sanitize_html

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
            extensions=_EXTENSIONS,
            extension_configs=_EXTENSION_CONFIGS,
            output_format="html",
            tab_length=4,
        )
        _local.renderer = instance
    return instance


def render_markdown(markdown_text: str) -> str:
    """Render ``markdown_text`` to sanitized, safe-to-embed HTML."""
    if not markdown_text or not markdown_text.strip():
        return ""

    renderer = _renderer()
    renderer.reset()
    raw_html = renderer.convert(pre_strip_dangerous(markdown_text))
    return sanitize_html(raw_html)


def render_table_of_contents(markdown_text: str) -> str:
    """Render ``markdown_text`` and return only the generated table of contents."""
    if not markdown_text or not markdown_text.strip():
        return ""

    renderer = _renderer()
    renderer.reset()
    renderer.convert(pre_strip_dangerous(markdown_text))
    return sanitize_html(getattr(renderer, "toc", "") or "")
