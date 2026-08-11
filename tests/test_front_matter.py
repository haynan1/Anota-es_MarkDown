"""The front matter subset: what it reads, what it refuses, what it bounds.

This parser runs over every imported file, including files this application
never wrote. Its contract is that it always returns - a malformed header is
body text, never an exception - and that every dimension is bounded.
"""

from __future__ import annotations

from app.services import front_matter


class TestParsing:
    def test_a_file_without_a_block_is_all_body(self):
        fields, body = front_matter.parse("# Título\n\nCorpo.")
        assert fields == {}
        assert body == "# Título\n\nCorpo."

    def test_a_block_is_split_from_the_body(self):
        fields, body = front_matter.parse('---\ntitle: "Guia"\n---\n\n# Guia\n\nCorpo.')
        assert fields["title"] == "Guia"
        assert body.strip() == "# Guia\n\nCorpo."

    def test_a_horizontal_rule_is_not_a_block(self):
        """`---` further down the file is Markdown, not metadata."""
        source = "# Título\n\n---\n\nOutra seção."
        fields, body = front_matter.parse(source)
        assert fields == {}
        assert body == source

    def test_an_unterminated_block_is_body(self):
        source = "---\ntitle: Sem fim\n\nCorpo que nunca fecha."
        fields, body = front_matter.parse(source)
        assert fields == {}
        assert body == source

    def test_inline_lists_are_read(self):
        fields, _ = front_matter.parse('---\ntags: [alpha, "beta gama"]\n---\n\nx')
        assert fields["tags"] == ["alpha", "beta gama"]

    def test_block_lists_are_read(self):
        fields, _ = front_matter.parse("---\ntags:\n  - alpha\n  - beta\n---\n\nx")
        assert fields["tags"] == ["alpha", "beta"]

    def test_a_comma_inside_a_quoted_item_is_not_a_separator(self):
        fields, _ = front_matter.parse('---\ngroups: ["Vendas, Sul", Norte]\n---\n\nx')
        assert fields["groups"] == ["Vendas, Sul", "Norte"]

    def test_escaped_quotes_survive(self):
        fields, _ = front_matter.parse('---\ntitle: "Ele disse \\"oi\\""\n---\n\nx')
        assert fields["title"] == 'Ele disse "oi"'

    def test_comments_and_blank_lines_are_ignored(self):
        fields, _ = front_matter.parse("---\n# comentário\n\ntitle: Real\n---\n\nx")
        assert fields == {"title": "Real"}

    def test_crlf_input_is_normalized(self):
        fields, body = front_matter.parse("---\r\ntitle: Windows\r\n---\r\n\r\nCorpo.")
        assert fields["title"] == "Windows"
        assert "\r" not in body

    def test_keys_are_case_insensitive(self):
        fields, _ = front_matter.parse("---\nTitle: Maiúsculo\n---\n\nx")
        assert fields["title"] == "Maiúsculo"

    def test_unknown_keys_are_kept_not_rejected(self):
        """A file from Obsidian or Jekyll must still import."""
        fields, _ = front_matter.parse("---\ntitle: X\naliases: [a]\ndraft: true\n---\n\nx")
        assert fields["aliases"] == ["a"]
        assert fields["draft"] == "true"


class TestMalformedInput:
    """Nothing in here may raise. A bad header is a bad header, not a crash."""

    def test_a_line_that_is_not_a_field_is_stepped_over(self):
        fields, _ = front_matter.parse("---\ntitle: X\nisto nao e um campo\nuuid: u\n---\n\nc")
        assert fields["title"] == "X"
        assert fields["uuid"] == "u"

    def test_a_dash_item_without_an_open_key_is_ignored(self):
        fields, _ = front_matter.parse("---\ntitle: X\n  - orfao\n---\n\nc")
        assert fields["title"] == "X"
        assert "orfao" not in str(fields)

    def test_a_nested_mapping_does_not_capture_the_next_list(self):
        source = "---\nauthor:\n  name: X\ntags:\n  - real\n---\n\nc"
        fields, _ = front_matter.parse(source)
        assert front_matter.list_of(fields, "tags") == ["real"]

    def test_an_empty_key_stays_empty(self):
        fields, _ = front_matter.parse("---\ntitle: X\ncategory:\n---\n\nc")
        assert front_matter.text_of(fields, "category") == ""

    def test_text_of_on_a_list_takes_the_first_item(self):
        fields, _ = front_matter.parse("---\ntitle: [Primeiro, Segundo]\n---\n\nc")
        assert front_matter.text_of(fields, "title") == "Primeiro"

    def test_an_escaped_quote_inside_an_inline_list_survives(self):
        fields, _ = front_matter.parse('---\ntags: ["diz \\"oi\\"", outra]\n---\n\nc')
        assert fields["tags"] == ['diz "oi"', "outra"]

    def test_single_quoted_values_are_unwrapped(self):
        fields, _ = front_matter.parse("---\ntitle: 'Entre aspas simples'\n---\n\nc")
        assert fields["title"] == "Entre aspas simples"


class TestBounds:
    def test_the_field_count_is_capped(self):
        block = "\n".join(f"chave{index}: valor" for index in range(200))
        fields, _ = front_matter.parse(f"---\n{block}\n---\n\nx")
        assert len(fields) <= front_matter.MAX_FIELDS

    def test_list_length_is_capped(self):
        items = ", ".join(f"t{index}" for index in range(500))
        fields, _ = front_matter.parse(f"---\ntags: [{items}]\n---\n\nx")
        assert len(fields["tags"]) <= front_matter.MAX_LIST_ITEMS

    def test_value_length_is_capped(self):
        fields, _ = front_matter.parse(f"---\ntitle: {'x' * 5000}\n---\n\nx")
        assert len(fields["title"]) <= front_matter.MAX_VALUE_LENGTH


class TestTypedAccess:
    def test_text_of_falls_back(self):
        assert front_matter.text_of({}, "title", "padrão") == "padrão"

    def test_list_of_splits_a_plain_scalar(self):
        """`tags: alpha, beta` is how most hand-written headers state a list."""
        fields, _ = front_matter.parse("---\ntags: alpha, beta\n---\n\nx")
        assert front_matter.list_of(fields, "tags") == ["alpha", "beta"]

    def test_flag_of_reads_the_usual_spellings(self):
        for spelling in ("true", "yes", "on", "1", "sim"):
            fields, _ = front_matter.parse(f"---\nfavorite: {spelling}\n---\n\nx")
            assert front_matter.flag_of(fields, "favorite") is True

    def test_flag_of_defaults_to_false(self):
        assert front_matter.flag_of({}, "favorite") is False
        fields, _ = front_matter.parse("---\nfavorite: false\n---\n\nx")
        assert front_matter.flag_of(fields, "favorite") is False


class TestRoundTrip:
    def test_what_is_written_is_read_back(self):
        original = {
            "title": 'Relatório "2026" — 100% \\ pronto',
            "uuid": "3f2b1c9d-0000-4000-8000-000000000001",
            "tags": ["marketing, digital", "ads"],
            "favorite": True,
            "archived": False,
        }
        fields, body = front_matter.parse(front_matter.dump(original) + "\nCorpo.\n")

        assert fields["title"] == original["title"]
        assert fields["tags"] == original["tags"]
        assert front_matter.flag_of(fields, "favorite") is True
        assert front_matter.flag_of(fields, "archived") is False
        assert body.strip() == "Corpo."

    def test_none_and_empty_values_are_omitted(self):
        written = front_matter.dump({"title": "X", "category": None, "tags": []})
        assert "category" not in written
        assert "tags" not in written

    def test_a_newline_in_a_value_cannot_break_out_of_the_block(self):
        """A title carrying a newline must not be able to forge new keys."""
        written = front_matter.dump({"title": "Fim\n---\nuuid: forjado"})
        fields, _ = front_matter.parse(written + "\nCorpo.\n")

        assert "uuid" not in fields
        assert fields["title"] == "Fim\n---\nuuid: forjado"
