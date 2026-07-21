"""Filesystem safety helpers.

User-supplied text never reaches the filesystem unfiltered: download names are
rebuilt from a slug, and archive members are validated before extraction.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from slugify import slugify

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_UNSAFE_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_slug(value: str, fallback: str = "documento", max_length: int = 80) -> str:
    """URL and filename safe slug, never empty."""
    slug = slugify(
        unicodedata.normalize("NFKC", value or ""),
        max_length=max_length,
        word_boundary=True,
        lowercase=True,
    )
    return slug or fallback


def safe_filename(title: str, extension: str, fallback: str = "documento") -> str:
    """Build a download filename from a title, discarding any path component."""
    ext = extension if extension.startswith(".") else f".{extension}"
    stem = safe_slug(title, fallback=fallback)
    stem = _UNSAFE_CHARS_RE.sub("", stem).strip(". ") or fallback
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"{stem}-arquivo"
    return f"{stem}{ext}"


def is_safe_archive_member(name: str) -> bool:
    """Reject absolute paths, drive letters and ``..`` traversal in ZIP members."""
    if not name or name.startswith(("/", "\\")):
        return False
    normalized = name.replace("\\", "/")
    if ":" in normalized.split("/", 1)[0]:
        return False
    return not any(part == ".." for part in normalized.split("/"))


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path inside ``directory`` for ``filename``."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Não foi possível gerar um nome de arquivo único.")
