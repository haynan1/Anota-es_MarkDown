"""Rendering and sanitization - the security-critical path."""

from __future__ import annotations

import pytest

from app.services.markdown_service import render_markdown
from app.services.sanitizer import sanitize_html, sanitize_plain_text
from app.utils.text import build_excerpt, content_hash, count_words


class TestRendering:
    def test_headings_and_emphasis(self, app):
        html = render_markdown("# Título\n\nTexto **forte** e *suave*.")
        assert "<h1" in html
        assert "<strong>forte</strong>" in html
        assert "<em>suave</em>" in html

    def test_lists_ordered_and_unordered(self, app):
        html = render_markdown("- a\n- b\n\n1. um\n2. dois")
        assert "<ul>" in html and "<ol>" in html

    def test_task_list(self, app):
        html = render_markdown("- [ ] pendente\n- [x] feito")
        assert 'type="checkbox"' in html
        assert "checked" in html

    def test_table_alignment_becomes_a_class_not_inline_style(self, app):
        html = render_markdown("| A | B |\n|:--|--:|\n| 1 | 2 |")
        assert "<table>" in html
        assert "md-align-right" in html
        # Inline styles would force 'unsafe-inline' into the CSP.
        assert "style=" not in html

    def test_fenced_code_is_highlighted(self, app):
        html = render_markdown("```python\nx = 1\n```")
        assert 'class="highlight"' in html
        assert "<pre>" in html

    def test_blockquote_and_horizontal_rule(self, app):
        html = render_markdown("> citação\n\n---\n")
        assert "<blockquote>" in html
        assert "<hr>" in html

    def test_footnotes(self, app):
        html = render_markdown("Texto[^1]\n\n[^1]: A nota.")
        assert "footnote" in html

    def test_inline_code_and_links(self, app):
        html = render_markdown("`codigo` e [link](https://exemplo.com)")
        assert "<code>codigo</code>" in html
        assert 'href="https://exemplo.com"' in html

    def test_empty_input_renders_nothing(self, app):
        assert render_markdown("") == ""
        assert render_markdown("   \n  ") == ""


class TestSanitization:
    def test_script_tag_is_removed_with_its_payload(self, app):
        html = render_markdown("Antes\n\n<script>alert('xss')</script>\n\nDepois")
        assert "<script" not in html.lower()
        assert "alert" not in html

    def test_event_handler_attribute_is_stripped(self, app):
        html = render_markdown('<img src="x" onerror="alert(1)">')
        assert "onerror" not in html.lower()

    def test_javascript_url_is_blocked(self, app):
        html = render_markdown("[clique](javascript:alert(1))")
        assert "javascript:" not in html.lower()
        assert "<a>clique</a>" in html or "href" not in html

    def test_data_url_is_blocked(self, app):
        html = render_markdown("[x](data:text/html;base64,PHNjcmlwdD4=)")
        assert "data:text/html" not in html.lower()

    def test_iframe_is_removed(self, app):
        html = render_markdown('<iframe src="https://evil.test"></iframe>')
        assert "<iframe" not in html.lower()

    def test_style_tag_is_removed(self, app):
        html = render_markdown("<style>body{display:none}</style>\n\nTexto")
        assert "<style" not in html.lower()
        assert "display:none" not in html

    def test_form_and_input_injection_is_neutralised(self, app):
        html = render_markdown('<form action="/x"><input name="p" type="password"></form>')
        assert "<form" not in html.lower()
        assert 'type="password"' not in html.lower()

    def test_external_links_get_safe_rel(self, app):
        html = render_markdown("[site](https://exemplo.com)")
        assert 'rel="noopener noreferrer nofollow"' in html
        assert 'target="_blank"' in html

    def test_svg_with_script_is_removed(self, app):
        html = render_markdown('<svg><script>alert(1)</script></svg>')
        assert "<svg" not in html.lower()
        assert "alert" not in html

    @pytest.mark.parametrize(
        "payload",
        [
            "<a href='vbscript:msgbox(1)'>x</a>",
            '<a href="JaVaScRiPt:alert(1)">x</a>',
            '<img src="javascript:alert(1)">',
        ],
    )
    def test_dangerous_schemes_variants(self, app, payload):
        html = render_markdown(payload)
        lowered = html.lower()
        assert "javascript:" not in lowered
        assert "vbscript:" not in lowered

    def test_sanitize_plain_text_strips_all_markup(self, app):
        assert sanitize_plain_text("<b>Olá</b> <script>x</script>") == "Olá"

    def test_sanitize_html_handles_empty(self, app):
        assert sanitize_html("") == ""


class TestTextMetrics:
    def test_word_count_ignores_code_fences(self, app):
        text = "Uma duas três\n\n```\nignorar isto tudo aqui\n```"
        assert count_words(text) == 3

    def test_word_count_handles_accents(self, app):
        assert count_words("ação código órgão") == 3

    def test_excerpt_is_truncated_on_a_word_boundary(self, app):
        excerpt = build_excerpt("palavra " * 100, limit=50)
        assert len(excerpt) <= 51
        assert excerpt.endswith("…")

    def test_content_hash_is_stable_and_sensitive(self, app):
        assert content_hash("t", "corpo") == content_hash("t", "corpo")
        assert content_hash("t", "corpo") != content_hash("t", "corpo ")
        assert content_hash("a", "x") != content_hash("b", "x")

    def test_content_hash_separator_cannot_be_forged(self, app):
        # Title+body must not collide with a body that mimics the separator.
        assert content_hash("ab", "c") != content_hash("a", "bc")
