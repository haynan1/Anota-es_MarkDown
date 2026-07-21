"""Text metrics and excerpt generation.

Metrics are computed from the raw markdown with the most obvious syntax noise
removed, so a document full of code fences does not report an absurd word
count.
"""

from __future__ import annotations

import hashlib
import re

_CODE_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Wiki links keep their visible label: [[Alvo]] -> Alvo, [[Alvo|texto]] -> texto.
# Without this the raw brackets leak into every excerpt and search snippet.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]+?))?\]\]")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"[*_~]{1,3}")
_HRULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_TABLE_PIPE_RE = re.compile(r"[|]")
_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Separator between title and body when hashing. A unit separator can never
# appear in normal prose, so it cannot be forged by document content.
_HASH_SEPARATOR = "\x1f"


def strip_markdown(markdown_text: str) -> str:
    """Return a plain-text approximation of ``markdown_text``."""
    if not markdown_text:
        return ""

    text = _CODE_FENCE_RE.sub(" ", markdown_text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _HRULE_RE.sub(" ", text)
    text = _HEADING_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _LIST_MARKER_RE.sub("", text)
    text = _TABLE_PIPE_RE.sub(" ", text)
    text = _EMPHASIS_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def count_words(markdown_text: str) -> int:
    return len(_WORD_RE.findall(strip_markdown(markdown_text)))


def count_characters(markdown_text: str) -> int:
    """Character count of the raw markdown."""
    return len(markdown_text or "")


def reading_time_minutes(word_count: int, wpm: int = 200) -> int:
    """Minutes needed to read ``word_count`` words, minimum 1 for any content."""
    if word_count <= 0:
        return 0
    return max(1, round(word_count / max(wpm, 1)))


_LEADING_HEADING_RE = re.compile(r"\A\s*#{1,6}\s+[^\n]*\n?")


def build_excerpt(markdown_text: str, limit: int = 240) -> str:
    """Short plain-text summary, cut on a word boundary.

    The opening heading is dropped: it almost always repeats the document
    title, and an excerpt that restates the title directly beneath it is
    wasted space in every listing.
    """
    body = _LEADING_HEADING_RE.sub("", markdown_text or "", count=1)
    plain = strip_markdown(body) or strip_markdown(markdown_text)
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "…"


def content_hash(title: str, markdown_text: str) -> str:
    """Stable fingerprint of a document revision.

    The title participates so a pure rename still produces a restorable
    version, while an unchanged save produces an identical hash and is skipped.
    """
    payload = f"{(title or '').strip()}{_HASH_SEPARATOR}{markdown_text or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
