"""Document versioning.

A snapshot is only written when the content actually changed: the SHA-256 of
``title + body`` is compared against the newest stored version, so autosave
firing every few seconds does not fill the history with identical rows.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from app.extensions import db
from app.models import Document, DocumentVersion
from app.repositories.version_repository import VersionRepository
from app.utils.text import content_hash, count_words


@dataclass(slots=True)
class DiffRow:
    """One line in a rendered comparison."""

    tag: str  # equal | insert | delete | replace | skip
    old_number: int | None
    new_number: int | None
    old_text: str
    new_text: str


@dataclass(slots=True)
class DiffSummary:
    rows: list[DiffRow]
    added: int
    removed: int
    changed: int

    @property
    def is_identical(self) -> bool:
        return not (self.added or self.removed or self.changed)


class HistoryService:
    """Creates, lists and restores document versions."""

    @staticmethod
    def snapshot(
        document: Document,
        change_summary: str = "",
        force: bool = False,
    ) -> DocumentVersion | None:
        """Store the document's current state as a new version.

        Returns ``None`` when the content is unchanged since the last snapshot,
        which is the normal outcome of a no-op autosave.
        """
        current_hash = content_hash(document.title, document.content_markdown)

        if not force:
            latest = VersionRepository.latest(document.id)
            if latest is not None and latest.content_hash == current_hash:
                return None
            # A brand-new document with no body is not worth a version.
            if latest is None and not (document.content_markdown or "").strip():
                return None

        version = DocumentVersion(
            document_id=document.id,
            version_number=VersionRepository.latest_number(document.id) + 1,
            title=document.title,
            content_markdown=document.content_markdown,
            content_hash=current_hash,
            change_summary=(change_summary or "")[:200],
            word_count=document.word_count or count_words(document.content_markdown),
        )
        db.session.add(version)
        db.session.flush()
        return version

    @staticmethod
    def restore(document: Document, version_number: int) -> DocumentVersion:
        """Roll ``document`` back to ``version_number``.

        The pre-restore state is snapshotted first, so restoring is itself
        undoable and nothing is ever lost.
        """
        target = VersionRepository.get(document.id, version_number)
        if target is None:
            raise LookupError("Versão não encontrada.")

        HistoryService.snapshot(
            document,
            change_summary=f"Estado anterior à restauração da versão {version_number}",
        )

        document.title = target.title
        document.content_markdown = target.content_markdown
        return target

    @staticmethod
    def count(document_id: int) -> int:
        return VersionRepository.count(document_id)

    # ── Comparison ──────────────────────────────────────────────────────────

    @staticmethod
    def build_diff(
        old_text: str, new_text: str, context: int = 3
    ) -> DiffSummary:
        """Line-level comparison with collapsed unchanged regions."""
        old_lines = (old_text or "").splitlines()
        new_lines = (new_text or "").splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

        rows: list[DiffRow] = []
        added = removed = changed = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                span = i2 - i1

                def equal_row(offset: int, start_old: int = i1, start_new: int = j1) -> DiffRow:
                    line = old_lines[start_old + offset]
                    return DiffRow(
                        "equal", start_old + offset + 1, start_new + offset + 1, line, line
                    )

                if span <= context * 2 + 1:
                    rows.extend(equal_row(k) for k in range(span))
                else:
                    # Show a few lines of context, collapse the middle.
                    rows.extend(equal_row(k) for k in range(context))
                    rows.append(
                        DiffRow(
                            "skip", None, None, f"{span - context * 2} linhas iguais", ""
                        )
                    )
                    rows.extend(equal_row(k) for k in range(span - context, span))
                continue

            old_span = list(range(i1, i2))
            new_span = list(range(j1, j2))
            if tag == "replace":
                changed += max(len(old_span), len(new_span))
            elif tag == "delete":
                removed += len(old_span)
            else:
                added += len(new_span)

            for offset in range(max(len(old_span), len(new_span))):
                old_index = old_span[offset] if offset < len(old_span) else None
                new_index = new_span[offset] if offset < len(new_span) else None
                rows.append(
                    DiffRow(
                        tag=tag,
                        old_number=(old_index + 1) if old_index is not None else None,
                        new_number=(new_index + 1) if new_index is not None else None,
                        old_text=old_lines[old_index] if old_index is not None else "",
                        new_text=new_lines[new_index] if new_index is not None else "",
                    )
                )

        return DiffSummary(rows=rows, added=added, removed=removed, changed=changed)

    @staticmethod
    def describe_change(old_text: str, new_text: str) -> str:
        """Human-readable summary stored on the version row."""
        old_words = count_words(old_text)
        new_words = count_words(new_text)
        delta = new_words - old_words
        if delta > 0:
            return f"{delta} palavra{'s' if delta > 1 else ''} adicionada{'s' if delta > 1 else ''}"
        if delta < 0:
            magnitude = abs(delta)
            return (
                f"{magnitude} palavra{'s' if magnitude > 1 else ''} "
                f"removida{'s' if magnitude > 1 else ''}"
            )
        return "Conteúdo revisado"
