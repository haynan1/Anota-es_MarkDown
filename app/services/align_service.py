"""Alinhamento de blocos: ``::: centro`` … ``:::``.

Markdown has no alignment of its own - the format describes structure, not
appearance - so every implementation invents one. The two usual inventions are
raw ``<div align="center">``, which this application forbids on purpose (no
inline styles, no unrestricted HTML), and one-off attribute lists, which apply
to a single block and break the moment the text grows a second paragraph.

This extension uses the *fenced div*, the convention Pandoc established::

    ::: centro
    Um parágrafo centralizado.

    E outro, no mesmo bloco.
    :::

Everything between the fences is still ordinary Markdown - headings, lists,
images, tables - and comes out wrapped in ``<div class="md-align-center">``.
The class is the same one table cells already use, so the rendered document,
the live preview and all four PDF themes align it without a single new rule.

Design notes
------------
* Keywords are accepted in Portuguese and in English; the toolbar writes the
  Portuguese ones. An unknown keyword (``::: aviso``) is **not** consumed - the
  text stays visible instead of vanishing into a container nobody asked for.
* An unclosed fence renders literally, for the same reason.
* Up to three spaces of indentation, like every other block construct: a fourth
  makes it indented code, so a code sample containing ``:::`` stays a code
  sample.
* Nesting is not supported. The first closing fence closes the block, and the
  remainder goes back to the parser as ordinary content - alignment inside
  alignment has no meaning anyway, since the inner one would simply win.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as etree
from dataclasses import dataclass

from markdown.blockprocessors import BlockProcessor
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

# What the writer may type -> the CSS class suffix it produces.
ALIGNMENTS: dict[str, str] = {
    "centro": "center",
    "centralizado": "center",
    "center": "center",
    "direita": "right",
    "right": "right",
    "esquerda": "left",
    "left": "left",
    "justificado": "justify",
    "justificar": "justify",
    "justify": "justify",
}

FENCE_OPEN_RE = re.compile(r"^ {0,3}:{3,}[ \t]*([A-Za-z]+)[ \t]*(?:\n|$)")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}:{3,}[ \t]*$", re.MULTILINE)

# Above every built-in block processor, so the opening fence is seen before
# `paragraph` swallows it as ordinary text.
PROCESSOR_PRIORITY = 175

# Below `pymdownx.superfences` (25) and `html_block` (20), so the count below is
# taken from the same lines the block parser will end up seeing: whatever is
# inside a code fence has already been replaced by a placeholder.
PREPROCESSOR_PRIORITY = 16


@dataclass(slots=True)
class _Budget:
    """How many fence pairs are still worth looking for in this document."""

    remaining: int = 0


class AlignFencePreprocessor(Preprocessor):
    """Counts the fence pairs in the document, once, in a single pass.

    Without this the block processor is quadratic. An opening fence with no
    partner only learns that by reading every remaining block of the document,
    and a file with thousands of stray ``:::`` lines makes that search happen
    thousands of times: 2000 unpaired fences took 1.5 s, and the ceiling on a
    document is 2 MB. The live preview runs on every keystroke, so that is a
    worker thread hanging, not a slow render.

    Pairing greedily here - first opening fence, then the first closing one
    after it - mirrors exactly what the block processor does, so the count is
    an upper bound on the number of blocks it can possibly build. Once they
    are used up, an opening fence is known to be unpaired without looking.
    """

    def __init__(self, md, budget: _Budget) -> None:
        super().__init__(md)
        self._budget = budget

    def run(self, lines: list[str]) -> list[str]:
        pairs = 0
        open_fence = False

        for line in lines:
            if open_fence:
                if FENCE_CLOSE_RE.match(line):
                    pairs += 1
                    open_fence = False
                continue
            match = FENCE_OPEN_RE.match(line)
            if match and match.group(1).lower() in ALIGNMENTS:
                open_fence = True

        self._budget.remaining = pairs
        return lines


class AlignBlockProcessor(BlockProcessor):
    """Turns a ``:::`` fence pair into an aligned ``div``."""

    def __init__(self, parser, budget: _Budget) -> None:
        super().__init__(parser)
        self._budget = budget

    def test(self, parent: etree.Element, block: str) -> bool:  # noqa: N802 - Python-Markdown API
        if self._budget.remaining <= 0:
            return False
        match = FENCE_OPEN_RE.match(block)
        return bool(match) and match.group(1).lower() in ALIGNMENTS

    def run(self, parent: etree.Element, blocks: list[str]) -> bool | None:  # noqa: N802
        original = blocks[0]
        opening = FENCE_OPEN_RE.match(original)
        # `test` already guaranteed both of these; mypy and the next reader
        # should not have to take that on faith.
        if opening is None or opening.group(1).lower() not in ALIGNMENTS:
            return False

        alignment = ALIGNMENTS[opening.group(1).lower()]
        blocks[0] = original[opening.end():]

        for index, block in enumerate(blocks):
            closing = FENCE_CLOSE_RE.search(block)
            if closing is None:
                continue

            # `rstrip` only of newlines: two trailing spaces are a hard line
            # break, and the closing fence must not eat one.
            inside = [*blocks[:index], block[: closing.start()].rstrip("\n")]
            # Anything written after the closing fence without a blank line in
            # between belongs to the document, not to the aligned block.
            trailing = block[closing.end():].lstrip("\n")

            container = etree.SubElement(parent, "div")
            container.set("class", f"md-align-{alignment}")
            # A fresh list: `parseBlocks` consumes the one it is given, and the
            # blocks still queued for the document must survive this call.
            self.parser.parseBlocks(container, [part for part in inside if part.strip()])

            del blocks[: index + 1]
            if trailing:
                blocks.insert(0, trailing)
            self._budget.remaining -= 1
            return None

        # No closing fence anywhere: put the block back exactly as it came and
        # let the ordinary processors render it as text. One of the pairs the
        # preprocessor counted has turned out not to exist at this level, so
        # the budget pays for the search that just failed - which is what keeps
        # a run of unpaired fences from searching again, and again, and again.
        blocks[0] = original
        self._budget.remaining -= 1
        return False


class AlignExtension(Extension):
    """Registers the ``:::`` block and the counting pass in front of it."""

    def extendMarkdown(self, md) -> None:  # noqa: N802 - Python-Markdown API
        # One budget per Markdown instance, created here rather than on the
        # extension: `extendMarkdown` runs once per instance, so two renderers
        # sharing this extension object still count their own documents.
        budget = _Budget()
        md.preprocessors.register(
            AlignFencePreprocessor(md, budget), "block-align-budget", PREPROCESSOR_PRIORITY
        )
        md.parser.blockprocessors.register(
            AlignBlockProcessor(md.parser, budget), "block-align", PROCESSOR_PRIORITY
        )
