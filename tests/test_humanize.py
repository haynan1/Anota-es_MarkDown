"""Tamanhos legíveis.

This string is read in three places - the attachment card, the upload tray and
the backup table - so it is worth pinning: Brazilian decimal comma, one decimal
place, and no ",0" hanging off a round number.
"""

from __future__ import annotations

import pytest

from app.utils.humanize import format_bytes


@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 bytes"),
        (1, "1 byte"),
        (999, "999 bytes"),
        (1023, "1023 bytes"),
        (1024, "1 KB"),
        (1536, "1,5 KB"),
        (10 * 1024, "10 KB"),
        (1024 * 1024, "1 MB"),
        (int(2.36 * 1024 * 1024), "2,4 MB"),
        (50 * 1024 * 1024, "50 MB"),
        (1024 ** 3, "1 GB"),
        (int(1.28 * 1024 ** 4), "1,3 TB"),
    ],
)
def test_sizes_are_written_the_way_a_reader_expects(size, expected):
    assert format_bytes(size) == expected


def test_missing_values_do_not_break_a_card():
    assert format_bytes(None) == "0 bytes"


def test_the_javascript_twin_agrees_with_the_server():
    """The tray formats sizes in the browser; the card formats them here.

    The same file must not be "1,5 MB" in one and "1.5 MB" in the other, so
    the JavaScript implementation is held to the table above.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "modules" / "uploads.js"
    ).read_text(encoding="utf-8")

    assert "export function formatBytes" in source
    # Decimal comma and the same unit ladder as format_bytes.
    assert "replace('.', ',')" in source
    assert re.search(r"\['KB', 'MB', 'GB'\]", source)
    assert "bytes" in source
