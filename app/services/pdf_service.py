"""PDF generation.

Two engines sit behind one interface:

``weasyprint``
    Preferred. Real CSS layout, ``@page`` margin boxes, proper page counters,
    clickable links. Requires native GTK/Pango/Cairo libraries, which are not
    bundled on Windows.

``xhtml2pdf``
    Pure-Python fallback (ReportLab). No native dependencies, so it always
    works, at the cost of a reduced CSS subset - see ``pdf/document_fallback``.

``PDF_ENGINE=auto`` (the default) probes WeasyPrint once and falls back
automatically. Nothing is ever fetched from the network during rendering.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from flask import current_app, render_template

from app.models import Document
from app.services.exceptions import NotFoundError
from app.services.media_service import asset_path, get_asset
from app.services.settings_service import SettingsService
from app.utils.dates import format_datetime, utcnow
from app.utils.files import safe_filename

logger = logging.getLogger(__name__)

MARGIN_VALUES = {"compact": "15mm", "normal": "22mm", "wide": "32mm"}

FONT_STACKS = {
    "serif": '"Iowan Old Style", Georgia, "Times New Roman", serif',
    "sans": '"Segoe UI", Helvetica, Arial, sans-serif',
    "mono": '"Cascadia Mono", Consolas, "Courier New", monospace',
}

# ReportLab only guarantees the PDF base-14 fonts unless extra faces are
# registered; the fallback engine therefore uses those names directly.
FALLBACK_FONTS = {"serif": "Times-Roman", "sans": "Helvetica", "mono": "Courier"}


class PdfGenerationError(RuntimeError):
    """Raised when no engine could produce a document."""


@dataclass(slots=True)
class RenderContext:
    document: Document
    theme: str
    page_size: str
    margin: str
    font: str
    header: str
    footer: str
    show_page_numbers: bool
    show_generated_date: bool
    generated_at: str
    app_name: str


# ── Resource access policy ──────────────────────────────────────────────────


def _static_root() -> Path:
    return Path(current_app.static_folder).resolve()


MEDIA_URL_PREFIX = "/midia/"
_MEDIA_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _resolve_media_asset(path: str) -> Path | None:
    """Resolve ``/midia/<uuid>`` to the stored file, or ``None``.

    The UUID is validated by shape and then looked up in the database; the
    filesystem path comes from the row. Nothing from the document reaches the
    filesystem, so an embedded ``/midia/../../secret`` resolves to nothing.
    """
    identifier = path[len(MEDIA_URL_PREFIX) :].split("/", 1)[0].split("?", 1)[0]
    if not _MEDIA_UUID_RE.match(identifier):
        return None

    try:
        asset = get_asset(identifier)
        if asset.kind != "image":
            return None  # Video frames cannot be embedded in a PDF.
        return asset_path(asset)
    except NotFoundError:
        return None


def _resolve_local_asset(url: str) -> Path | None:
    """Map a URL onto a file this application owns, or ``None`` if it escapes.

    This is the single choke point that prevents a crafted document from
    reading arbitrary files (``file:///etc/passwd``) or reaching internal
    network addresses (SSRF) while the PDF is being rendered. Only two
    sources are permitted: the bundled ``static/`` tree and uploaded images
    resolved through the database.
    """
    parsed = urlparse(url)

    if parsed.scheme in {"http", "https"}:
        return None  # Remote fetches are never performed - see README.

    if (parsed.path or "").startswith(MEDIA_URL_PREFIX):
        return _resolve_media_asset(parsed.path)

    if parsed.scheme and parsed.scheme != "file":
        return None

    raw_path = unquote(parsed.path or "")
    if not raw_path:
        return None

    root = _static_root()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        # Windows file URLs arrive as /C:/... - strip the leading slash.
        if len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            candidate = Path(raw_path[1:])
        resolved = candidate.resolve()
    else:
        marker = "/static/"
        relative = raw_path.split(marker, 1)[-1] if marker in raw_path else raw_path
        resolved = (root / relative.lstrip("/")).resolve()

    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


# ── Engines ─────────────────────────────────────────────────────────────────


class WeasyPrintEngine:
    name = "weasyprint"
    label = "WeasyPrint"
    _probe: bool | None = None

    @classmethod
    def available(cls) -> bool:
        if cls._probe is None:
            try:
                import weasyprint  # noqa: F401

                cls._probe = True
            except (ImportError, OSError) as exc:
                # OSError is what a missing GTK/Pango/Cairo library raises on
                # Windows; ImportError covers the package simply being absent.
                logger.info("WeasyPrint indisponível: %s", type(exc).__name__)
                cls._probe = False
        return cls._probe

    @staticmethod
    def _url_fetcher(url: str):
        from weasyprint.urls import default_url_fetcher

        resolved = _resolve_local_asset(url)
        if resolved is None:
            logger.warning("Recurso bloqueado durante a geração de PDF: %s", url[:120])
            raise ValueError("Recurso externo bloqueado por política de segurança.")
        return default_url_fetcher(resolved.as_uri())

    @classmethod
    def render(cls, html: str) -> bytes:
        from weasyprint import HTML

        return HTML(
            string=html,
            base_url=_static_root().as_uri(),
            url_fetcher=cls._url_fetcher,
        ).write_pdf()


class Xhtml2PdfEngine:
    name = "xhtml2pdf"
    label = "xhtml2pdf (ReportLab)"

    @classmethod
    def available(cls) -> bool:
        try:
            from xhtml2pdf import pisa  # noqa: F401

            return True
        except (ImportError, OSError):  # pragma: no cover
            return False

    @staticmethod
    def _link_callback(uri: str, _rel: str) -> str:
        resolved = _resolve_local_asset(uri)
        if resolved is None:
            logger.warning("Recurso bloqueado durante a geração de PDF: %s", uri[:120])
            # ReportLab tolerates a missing asset; an exception would abort the
            # whole document because of one image.
            return ""
        return str(resolved)

    @classmethod
    def render(cls, html: str) -> bytes:
        from xhtml2pdf import pisa

        buffer = io.BytesIO()
        result = pisa.CreatePDF(
            src=io.StringIO(html),
            dest=buffer,
            encoding="utf-8",
            link_callback=cls._link_callback,
        )
        if result.err:
            raise PdfGenerationError(
                "O gerador de PDF não conseguiu processar este documento."
            )
        return buffer.getvalue()


ENGINES = {engine.name: engine for engine in (WeasyPrintEngine, Xhtml2PdfEngine)}


def select_engine():
    """Pick an engine honouring ``PDF_ENGINE`` and actual availability."""
    preference = (current_app.config.get("PDF_ENGINE") or "auto").lower()

    if preference in ENGINES:
        engine = ENGINES[preference]
        if engine.available():
            return engine
        logger.warning(
            "Motor de PDF '%s' foi solicitado mas não está disponível.", preference
        )

    for engine in (WeasyPrintEngine, Xhtml2PdfEngine):
        if engine.available():
            return engine

    raise PdfGenerationError(
        "Nenhum motor de PDF está disponível. Instale as dependências do "
        "WeasyPrint ou o pacote xhtml2pdf."
    )


def engine_info() -> dict[str, object]:
    """Diagnostics for the Settings screen."""
    active = None
    try:
        active = select_engine()
    except PdfGenerationError:
        pass
    return {
        "active": getattr(active, "name", None),
        "active_label": getattr(active, "label", "Indisponível"),
        "weasyprint_available": WeasyPrintEngine.available(),
        "xhtml2pdf_available": Xhtml2PdfEngine.available(),
        "configured": current_app.config.get("PDF_ENGINE", "auto"),
    }


# ── Public API ──────────────────────────────────────────────────────────────


def build_context(document: Document, overrides: dict | None = None) -> RenderContext:
    settings = SettingsService.all()
    overrides = overrides or {}

    theme = overrides.get("theme") or document.pdf_theme or settings["pdf_theme"]
    page_size = overrides.get("page_size") or document.page_size or settings["pdf_page_size"]

    return RenderContext(
        document=document,
        theme=theme if theme in {"classic", "minimal", "academic", "modern"} else "classic",
        page_size=page_size if page_size in {"A4", "Letter"} else "A4",
        margin=settings["pdf_margin"],
        font=settings["pdf_font"],
        header=settings["pdf_header"],
        footer=settings["pdf_footer"],
        show_page_numbers=settings["pdf_show_page_numbers"],
        show_generated_date=settings["pdf_show_generated_date"],
        generated_at=format_datetime(utcnow(), settings["timezone"]),
        app_name=settings["app_name"],
    )


_VIDEO_TAG_RE = re.compile(r"<video\b[^>]*>.*?</video>|<video\b[^>]*/?>", re.S | re.I)


def replace_videos_for_print(html: str) -> str:
    """Swap ``<video>`` for a printable note.

    A video cannot exist on paper. Leaving the tag in produces an empty gap
    that reads as a rendering failure; a labelled placeholder is honest.
    """
    return _VIDEO_TAG_RE.sub(
        '<p class="media-placeholder">[vídeo — disponível na versão digital]</p>',
        html or "",
    )


_ATTACHMENT_CARD_RE = re.compile(
    r'<a class="attachment(?P<missing> attachment-missing)?"[^>]*>'
    r'\s*<span class="attachment-badge">(?P<badge>[^<]*)</span>'
    r'\s*<span class="attachment-body">'
    r'\s*<span class="attachment-name">(?P<name>[^<]*)</span>'
    r'\s*<span class="attachment-meta">(?P<meta>[^<]*)</span>'
    r"\s*</span>\s*</a>",
    re.S | re.I,
)


def replace_attachments_for_print(html: str) -> str:
    """Flatten attachment cards into one printable line each.

    The card on screen is a flex layout; neither PDF engine can be trusted
    with that, and xhtml2pdf ignores most of it outright. A single labelled
    paragraph is engine-independent and says exactly what the reader needs to
    know: this document carries a file, here is its name and size.
    """

    def replace(match: re.Match[str]) -> str:
        badge = (match.group("badge") or "").strip()
        name = (match.group("name") or "").strip()
        meta = (match.group("meta") or "").strip()
        prefix = "Anexo indisponível" if match.group("missing") else "Anexo"
        detail = f" — {meta}" if meta else ""
        return (
            f'<p class="attachment-print"><strong>{prefix}:</strong> '
            f"{name}{detail}</p>"
        )

    return _ATTACHMENT_CARD_RE.sub(replace, html or "")


def prepare_for_print(html: str) -> str:
    """Everything the rendered HTML needs before it becomes paper."""
    return replace_attachments_for_print(replace_videos_for_print(html))


VARIANT_RENDERED = "rendered"
VARIANT_SOURCE = "source"
VARIANTS = (VARIANT_RENDERED, VARIANT_SOURCE)

# A single document should not be able to produce a thousand-page listing.
MAX_SOURCE_LINES = 20_000


def render_document_pdf(
    document: Document,
    overrides: dict | None = None,
    variant: str = VARIANT_RENDERED,
) -> tuple[bytes, str]:
    """Render ``document`` and return ``(pdf_bytes, filename)``.

    ``variant`` selects between the formatted document and a numbered listing
    of its Markdown source.
    """
    engine = select_engine()
    context = build_context(document, overrides)
    is_source = variant == VARIANT_SOURCE

    if is_source:
        template = "pdf/source.html"
    elif engine is WeasyPrintEngine:
        template = "pdf/document.html"
    else:
        template = "pdf/document_fallback.html"

    html = render_template(
        template,
        ctx=context,
        document=document,
        margin=MARGIN_VALUES.get(context.margin, "22mm"),
        font_stack=FONT_STACKS.get(context.font, FONT_STACKS["serif"]),
        fallback_font=FALLBACK_FONTS.get(context.font, "Times-Roman"),
        fallback=engine is not WeasyPrintEngine,
        lines=(document.content_markdown or "").splitlines()[:MAX_SOURCE_LINES],
        printable_html=prepare_for_print(document.rendered_html),
    )

    try:
        pdf_bytes = engine.render(html)
    except PdfGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate third-party boundary
        # WeasyPrint and ReportLab raise a wide, undocumented range of types
        # for malformed content (font, image and layout errors among them).
        # Enumerating them would be guesswork that silently breaks on upgrade.
        # The exception is logged in full and converted to a domain error - it
        # is never swallowed.
        logger.exception("Falha ao gerar PDF com o motor %s", engine.name)
        raise PdfGenerationError(
            "Não foi possível gerar o PDF deste documento."
        ) from exc

    if not pdf_bytes:
        raise PdfGenerationError("O PDF gerado ficou vazio.")

    # The marker goes through the slug, not the extension argument: passing
    # "-markdown.pdf" as an extension yields "titulo.-markdown.pdf".
    stem = f"{document.title} markdown" if is_source else document.title
    return pdf_bytes, safe_filename(stem, ".pdf")
