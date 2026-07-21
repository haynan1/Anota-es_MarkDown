"""What a listing shows about a document.

Excerpts are the only text most documents ever show outside the editor, so
syntax leaking into them is a visible defect, not a cosmetic one.
"""

from __future__ import annotations

from app.utils.text import build_excerpt, count_words, strip_markdown


class TestWikilinksInExcerpts:
    """Found in a design pass: raw [[brackets]] were reaching every card."""

    def test_wikilink_keeps_only_its_label(self, app):
        assert strip_markdown("Veja o [[Guia de estilo]] agora") == (
            "Veja o Guia de estilo agora"
        )

    def test_wikilink_with_custom_label(self, app):
        assert strip_markdown("Veja o [[Guia de estilo|nosso guia]]") == (
            "Veja o nosso guia"
        )

    def test_excerpt_has_no_brackets(self, app, make_document):
        document = make_document(
            title="Com links",
            content="# Com links\n\nVeja [[Guia de estilo]] e [[Outro documento]].",
        )
        assert "[[" not in document.excerpt
        assert "]]" not in document.excerpt
        assert "Guia de estilo" in document.excerpt

    def test_wikilinks_count_as_their_label(self, app):
        assert count_words("[[Guia de estilo]]") == 3
        assert count_words("[[Guia de estilo|guia]]") == 1


class TestExcerptShape:
    def test_leading_heading_is_dropped(self, app):
        excerpt = build_excerpt("# Título do documento\n\nO corpo começa aqui.")
        assert excerpt.startswith("O corpo")
        assert "Título do documento" not in excerpt

    def test_document_that_is_only_a_heading_still_shows_something(self, app):
        """Dropping the heading must not leave an empty card."""
        assert build_excerpt("# Só um título") == "Só um título"

    def test_excerpt_stays_within_the_column_budget(self, app, make_document):
        document = make_document(title="Longo", content="palavra " * 500)
        assert len(document.excerpt) <= 320

    def test_excerpt_has_no_markdown_syntax(self, app, make_document):
        document = make_document(
            title="Sintaxe",
            content=(
                "# Sintaxe\n\n**negrito** _itálico_ `código` ~~riscado~~\n\n"
                "- item\n\n> citação\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                "[link](https://exemplo.com) ![img](/x.png)"
            ),
        )
        for token in ["**", "~~", "`", "|", ">", "!["]:
            assert token not in document.excerpt, token
