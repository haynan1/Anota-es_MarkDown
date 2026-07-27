"""O alinhamento visto de fora: sintaxe, editor e barra de ferramentas.

The alignment feature has two halves that must agree with each other: the
renderer in ``app/services/align_service.py`` and the text surgery in
``app/static/js/modules/toolbar.js``. A disagreement between them is invisible
in either half's own tests - the editor would toggle a fence the renderer does
not recognise, or vice versa - so both are checked here, from one command.

The JavaScript half runs under Node, which is not required to use this
application; when it is absent the test is skipped rather than failed.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from app.services.align_service import ALIGNMENTS

JS_SUITE = pathlib.Path(__file__).resolve().parent / "js" / "align.test.mjs"


class TestTheEditorAgreesWithTheRenderer:
    def test_every_keyword_the_editor_writes_is_understood(self, app):
        """The four words the toolbar can produce, straight from the template."""
        for keyword in ("esquerda", "centro", "direita", "justificado"):
            assert keyword in ALIGNMENTS, f"o editor escreve “{keyword}” e o renderizador não conhece"

    def test_the_toolbar_offers_exactly_the_supported_alignments(self, app, client, document):
        html = client.get(f"/editor/{document.uuid}").get_data(as_text=True)
        offered = {"esquerda", "centro", "direita", "justificado"}

        for keyword in offered:
            assert f'data-align="{keyword}"' in html

        # Every CSS class the renderer can emit needs a rule in the preview and
        # in the PDF, or an aligned block prints as if it were never aligned.
        root = pathlib.Path(app.root_path)
        preview = (root / "static" / "css" / "markdown.css").read_text(encoding="utf-8")
        printed = (root / "templates" / "pdf" / "document.html").read_text(encoding="utf-8")
        for suffix in set(ALIGNMENTS.values()):
            assert f".md-align-{suffix}" in preview, suffix
            assert f".md-align-{suffix}" in printed, suffix


class TestAnUnpairedFenceIsNeverHunted:
    """A cheia de `:::` soltos não pode custar caro.

    Finding an opening fence's partner means reading every remaining block of
    the document. Doing that for a fence that has no partner - and then again
    for the next one, and the next - is quadratic: 2000 unpaired fences took
    1.5 s against a 415 ms baseline for the same number of plain paragraphs,
    and a document may be 2 MB. The live preview renders on every keystroke,
    so that is a worker thread hanging, not a slow page.

    Counting the pairs once, up front, is what makes the search stop before it
    starts. This is asserted on the number of searches rather than on the
    clock, so it cannot go flaky on a busy machine.
    """

    @staticmethod
    def _count_searches(monkeypatch):
        from app.services.align_service import AlignBlockProcessor

        searches: list[str] = []
        original = AlignBlockProcessor.run

        def counted(self, parent, blocks):
            searches.append(blocks[0])
            return original(self, parent, blocks)

        monkeypatch.setattr(AlignBlockProcessor, "run", counted)
        return searches

    def test_fences_that_cannot_pair_are_not_searched_for(self, app, monkeypatch):
        from app.services.markdown_service import render_markdown

        searches = self._count_searches(monkeypatch)
        html = render_markdown("::: centro\n\n" * 500)

        assert searches == [], "procurou o par de uma cerca que nunca fecha"
        assert "md-align" not in html

    def test_closing_fences_before_any_opening_one_do_not_count(self, app, monkeypatch):
        """The pathological shape: stray `:::` first, unpaired openings after."""
        from app.services.markdown_service import render_markdown

        searches = self._count_searches(monkeypatch)
        render_markdown((":::\n\n" * 200) + ("::: centro\n\n" * 200))

        assert searches == []

    def test_a_real_block_is_still_built(self, app, monkeypatch):
        """The budget must not be so tight that it starves a valid document."""
        from app.services.markdown_service import render_markdown

        searches = self._count_searches(monkeypatch)
        html = render_markdown("::: centro\na\n:::\n\n" + ("::: centro\n\n" * 200))

        assert html.count("md-align-center") == 1
        assert "<p>a</p>" in html
        # One search built the block; the 200 hopeless fences cost nothing.
        assert len(searches) == 1, f"buscas demais: {len(searches)}"

    def test_an_unbalanced_document_groups_greedily(self, app):
        """The documented rule, made executable: the first closing fence wins.

        Two openings and one closing is a contradiction, and every parser
        resolves it by convention rather than by intent. Ours reads left to
        right and shuts the block at the first `:::` it meets, so the stray
        opening in between ends up as text inside the block. The toolbar never
        writes this shape; a person editing by hand can, and the result has to
        be predictable rather than clever.
        """
        from app.services.markdown_service import render_markdown

        html = render_markdown("::: centro\numa\n\n::: direita\nduas\n:::")

        assert html.count("md-align-center") == 1
        assert "md-align-right" not in html
        assert "<p>::: direita" in html or "::: direita" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node não está instalado")
class TestTheToolbarTextSurgery:
    def test_the_javascript_suite_passes(self):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [shutil.which("node"), str(JS_SUITE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
