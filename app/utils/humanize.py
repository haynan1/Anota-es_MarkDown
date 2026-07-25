"""Human-readable formatting of machine values.

Sizes are written the way a Brazilian reader expects them: decimal comma, one
decimal place, and no decimals at all for byte counts - "1,4 MB", "820 KB",
"512 bytes".
"""

from __future__ import annotations

_UNITS = ("KB", "MB", "GB", "TB")


def format_bytes(size_bytes: int | float | None) -> str:
    """Return ``size_bytes`` as a short, readable size."""
    size = float(size_bytes or 0)
    if size < 1024:
        count = int(size)
        return f"{count} byte" if count == 1 else f"{count} bytes"

    for unit in _UNITS:
        size /= 1024
        if size < 1024 or unit == _UNITS[-1]:
            # One decimal, comma-separated: "1,4 MB". Whole values lose the
            # trailing ",0" - "2 MB" reads better than "2,0 MB".
            rounded = round(size, 1)
            if rounded.is_integer():
                return f"{int(rounded)} {unit}"
            return f"{rounded:.1f}".replace(".", ",") + f" {unit}"

    return f"{size:.1f} TB"  # pragma: no cover - unreachable, loop always returns
