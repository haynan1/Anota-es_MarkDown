"""Regression tests for specific published vulnerabilities.

Each test here maps to a CVE/GHSA found by ``pip-audit`` against an earlier
pin of this project. They exist so that a downgrade, a lockfile mistake or a
change in the sanitizer configuration fails loudly instead of silently
reopening a known hole.

Re-audit with::

    pip install pip-audit && python -m pip_audit
"""

from __future__ import annotations

import time

import pytest

from app.services.markdown_service import render_markdown
from app.services.sanitizer import sanitize_plain_text


class TestDependencyFloor:
    """Minimum versions for the packages on the security path."""

    def test_bleach_is_patched(self):
        # GHSA-8rfp-98v4-mmr6: scheme bypass in href, exactly our configuration.
        import bleach

        assert tuple(int(p) for p in bleach.__version__.split(".")[:2]) >= (6, 4)

    def test_markdown_is_patched(self):
        # PYSEC-2026-89: unhandled AssertionError on malformed HTML (DoS).
        import markdown

        major, minor = (int(p) for p in markdown.__version__.split(".")[:2])
        assert (major, minor) >= (3, 8)

    def test_pygments_is_patched(self):
        # PYSEC-2026-2987: ReDoS in AdlLexer.
        import pygments

        major, minor = (int(p) for p in pygments.__version__.split(".")[:2])
        assert (major, minor) >= (2, 20)


class TestObfuscatedSchemeBypass:
    """GHSA-8rfp-98v4-mmr6 - javascript: hidden inside an allowed href.

    Bleach below 6.4 emitted URIs whose scheme it should have stripped, using
    control characters and entities to break the match. Our allowlist enables
    both ``a`` and ``href``, which is the affected combination.
    """

    PAYLOADS = [
        '<a href="java\tscript:alert(1)">x</a>',
        '<a href="java\nscript:alert(1)">x</a>',
        '<a href="java\rscript:alert(1)">x</a>',
        '<a href="java\x00script:alert(1)">x</a>',
        '<a href="java\x01script:alert(1)">x</a>',
        '<a href="java​script:alert(1)">x</a>',
        '<a href="javascript&#58;alert(1)">x</a>',
        '<a href="javascript&#x3A;alert(1)">x</a>',
        '<a href="jav&#x09;ascript:alert(1)">x</a>',
        '<a href=" javascript:alert(1)">x</a>',
        '<a href="JAVASCRIPT:alert(1)">x</a>',
        "[markdown](java\tscript:alert(1))",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_no_executable_scheme_survives(self, app, payload):
        rendered = render_markdown(payload)

        # Normalise the tricks away before looking for the scheme, so an
        # obfuscated variant cannot slip past the assertion itself.
        flat = rendered.lower()
        for noise in ("\t", "\n", "\r", "\x00", "\x01", "​", " "):
            flat = flat.replace(noise, "")

        assert "javascript:" not in flat
        assert "javascript&#" not in flat
        assert "alert(1)" not in flat.replace("</a>", "")

    def test_the_link_text_is_preserved(self, app):
        """Stripping the scheme must not swallow the user's content."""
        rendered = render_markdown('<a href="javascript:alert(1)">clique aqui</a>')
        assert "clique aqui" in rendered


class TestMalformedHtmlDoesNotCrash:
    """PYSEC-2026-89 - malformed HTML raised an unhandled AssertionError.

    Any of these in a document body would have taken down the request that
    rendered it: the editor preview, a save, or a PDF export.
    """

    PAYLOADS = [
        "<a<b>",
        "<<<<<<>>>>>>",
        "<!",
        "<!--",
        "<![CDATA[",
        "<?php echo 1; ?>",
        "<a href=",
        "<" * 200,
        "<a " + "x" * 500,
        "<!DOCTYPE",
        "</>",
        "<a<a<a<a<a>",
        "<p<p<p>>",
        "text <!-- comentário nunca fechado",
        "<svg/onload=alert(1)>",
        "<math><mi//xlink:href='data:x,<script>alert(1)</script>'>",
        "<a\x00href='x'>y</a>",
        "<![if !IE]>",
        "<%\n%>",
        "<a b=`c`>",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_renders_without_raising(self, app, payload):
        rendered = render_markdown(payload)
        assert isinstance(rendered, str)
        assert "<script" not in rendered.lower()
        assert "onload" not in rendered.lower()

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_plain_text_sanitizer_survives_too(self, app, payload):
        # Titles run through a different code path than document bodies.
        assert isinstance(sanitize_plain_text(payload), str)

    def test_preview_endpoint_survives_malformed_input(self, client, app):
        """The crash would have been remotely reachable through this endpoint."""
        for payload in self.PAYLOADS:
            response = client.post("/api/preview", json={"content_markdown": payload})
            assert response.status_code == 200, payload

    def test_saving_malformed_content_works(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": "Malformado",
                "content_markdown": "\n\n".join(self.PAYLOADS),
                "revision": document.revision,
            },
        )
        assert response.status_code == 200
        assert response.get_json()["ok"] is True


class TestRedosResistance:
    """PYSEC-2026-2987 / PYSEC-2026-1825 - pathological regex inputs.

    A document is user-controlled input on a single-threaded dev server; a
    rendering pass that takes minutes is a denial of service in practice.
    """

    BUDGET_SECONDS = 5.0

    PAYLOADS = [
        # Deeply nested emphasis - classic backtracking trigger.
        "*" * 400 + "texto" + "*" * 400,
        "_" * 400 + "texto",
        "[" * 300 + "x" + "]" * 300,
        "(" * 300 + "x" + ")" * 300,
        "`" * 500,
        "#" * 500 + " título",
        "> " * 500 + "citação",
        "|" + "a|" * 500,
        "- " * 800 + "item",
        "~" * 400 + "x" + "~" * 400,
        # A named lexer is the only way user content reaches a Pygments lexer,
        # since guess_lang is disabled.
        "```adl\n" + "a" * 5000 + "\n```",
        "```python\n" + ("x = " + "(" * 200 + ")" * 200 + "\n") * 5 + "```",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_renders_within_budget(self, app, payload):
        started = time.perf_counter()
        render_markdown(payload)
        elapsed = time.perf_counter() - started

        assert elapsed < self.BUDGET_SECONDS, (
            f"Renderização levou {elapsed:.2f}s, acima do orçamento de "
            f"{self.BUDGET_SECONDS}s — possível ReDoS."
        )

    def test_deeply_nested_lists_degrade_instead_of_crashing(self, app):
        """Found by this suite, not by any advisory.

        Python-Markdown recurses once per nesting level, so a single line of
        "- " repeated a few hundred times exhausts the stack. Unhandled, that
        was a 500 on every save and preview of the document - the user could
        not store their own text.
        """
        payload = "- " * 800 + "item"

        rendered = render_markdown(payload)

        assert "md-render-warning" in rendered
        assert "item" in rendered
        assert "<pre>" in rendered

    def test_the_renderer_recovers_after_a_nesting_failure(self, app):
        """The parser instance is discarded, so the next render is normal."""
        render_markdown("- " * 800 + "item")

        rendered = render_markdown("# Título normal\n\nCorpo.")
        assert "<h1" in rendered
        assert "md-render-warning" not in rendered

    def test_a_deeply_nested_document_can_still_be_saved(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": "Aninhado",
                "content_markdown": "- " * 800 + "item",
                "revision": document.revision,
            },
        )
        assert response.status_code == 200
        assert response.get_json()["saved"] is True

    def test_a_deeply_nested_document_still_exports_to_pdf(self, app, make_document):
        from app.services.pdf_service import render_document_pdf

        deep = make_document(title="Aninhado", content="- " * 800 + "item")
        pdf_bytes, _ = render_document_pdf(deep)
        assert pdf_bytes.startswith(b"%PDF")

    def test_a_large_realistic_document_is_fast(self, app):
        body = "\n\n".join(
            f"## Seção {index}\n\nParágrafo com **negrito** e `código`.\n\n"
            f"- item um\n- item dois\n\n| A | B |\n|---|---|\n| 1 | 2 |"
            for index in range(200)
        )
        started = time.perf_counter()
        rendered = render_markdown(body)
        elapsed = time.perf_counter() - started

        assert "<h2" in rendered
        assert elapsed < self.BUDGET_SECONDS, f"{elapsed:.2f}s para 200 seções"


class TestPdfEngineResourcePolicy:
    """PYSEC-2026-2034 - SSRF bypass through redirects in WeasyPrint.

    Not exploitable here: our fetcher rejects http/https before WeasyPrint's
    default fetcher is ever reached, so there is no request to redirect. This
    test pins that ordering in place.
    """

    def test_http_never_reaches_the_default_fetcher(self, app):
        from app.services.pdf_service import _resolve_local_asset

        with app.test_request_context():
            for url in (
                "http://169.254.169.254/latest/meta-data/",
                "https://evil.test/redirect-to-internal",
                "http://localhost:8080/admin",
                "http://[::1]:6379/",
            ):
                assert _resolve_local_asset(url) is None

    def test_presentational_hints_stay_disabled(self, app, document):
        """PYSEC-2026-3412 - CSS injection needs presentational hints enabled."""
        import inspect

        from app.services import pdf_service

        # presentational_hints=True makes WeasyPrint honour HTML presentation
        # attributes from the document, which is attacker-controlled content.
        module_source = inspect.getsource(pdf_service)
        assert "presentational_hints" not in module_source
