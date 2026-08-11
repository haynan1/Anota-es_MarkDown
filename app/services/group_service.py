"""Regras de negócio dos grupos de documentos.

A group is a curated list, so two rules matter more than the rest:

* **Membership is idempotent.** Adding a document that is already in the group
  changes nothing and is not an error - bulk actions and drag-and-drop both
  produce duplicates constantly, and refusing them would only surface as noise.
* **Order is rewritten, never patched.** Reordering assigns 1..N to the whole
  group in one statement instead of shifting neighbours; there is no
  arrangement of concurrent edits that can leave two documents fighting over
  one slot.

Deleting a group never touches a document. The membership rows go, the
documents stay - the same promise categories already make.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import bindparam
from sqlalchemy import inspect as sa_inspect

from app.extensions import db
from app.models import Document, Group, document_groups
from app.models.group import (
    DEFAULT_GROUP_COLOR,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
)
from app.repositories.group_repository import MAX_GROUPS, GroupRepository
from app.services.exceptions import NotFoundError, ValidationError
from app.services.sanitizer import sanitize_plain_text
from app.utils.dates import utcnow
from app.utils.files import safe_slug

# One request may not move an unbounded number of documents.
MAX_DOCUMENTS_PER_OPERATION = 200

# A group is a curated sequence that is rendered and reordered as one page, so
# it is bounded on the way in. Without this, a single group could grow to the
# size of the library and turn its page into an unbounded query and an
# unbounded render.
MAX_DOCUMENTS_PER_GROUP = 500

# Ceiling for memberships restored from a file. A document belongs to a
# handful of groups; a header claiming thousands is malformed input.
MAX_GROUPS_PER_DOCUMENT = 50


def _identity_of(document: Document) -> int:
    """The primary key of ``document`` without ever going back to the database.

    SQLAlchemy expires every instance on commit, so reading ``document.id`` in
    a loop after any earlier commit issues one SELECT per document - an N+1
    that appears and disappears depending on what the caller did first. The
    identity key is already in the session for any persisted object.
    """
    identity = sa_inspect(document).identity
    return identity[0] if identity else document.id


class GroupService:
    # ── Lifecycle ───────────────────────────────────────────────────────────

    @staticmethod
    def require(public_uuid: str) -> Group:
        group = GroupRepository.get_by_uuid(public_uuid)
        if group is None:
            raise NotFoundError("Grupo não encontrado.")
        return group

    @staticmethod
    def _clean_name(name: str) -> str:
        clean = sanitize_plain_text(name or "", max_length=MAX_NAME_LENGTH)
        if not clean:
            raise ValidationError("Informe um nome para o grupo.")
        return clean

    @staticmethod
    def _unique_slug(name: str, exclude_id: int | None = None) -> str:
        base = safe_slug(name, fallback="grupo", max_length=100)
        candidate = base
        suffix = 2
        while True:
            existing = GroupRepository.get_by_slug(candidate)
            if existing is None or existing.id == exclude_id:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 1000:  # pragma: no cover - defensive
                raise ValidationError("Não foi possível gerar um endereço único.")

    @staticmethod
    def create(name: str, description: str = "", color: str | None = None) -> Group:
        clean_name = GroupService._clean_name(name)

        if GroupRepository.get_by_name(clean_name) is not None:
            raise ValidationError(f"Já existe um grupo chamado “{clean_name}”.")
        if len(GroupRepository.all(limit=MAX_GROUPS)) >= MAX_GROUPS:
            raise ValidationError(
                f"Limite de {MAX_GROUPS} grupos atingido. Remova algum antes de criar outro."
            )

        group = Group(
            name=clean_name,
            slug=GroupService._unique_slug(clean_name),
            description=sanitize_plain_text(
                description or "", max_length=MAX_DESCRIPTION_LENGTH
            ),
            color=GroupService._clean_color(color),
        )
        db.session.add(group)
        db.session.commit()
        return group

    @staticmethod
    def update(
        group: Group,
        name: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> Group:
        if name is not None:
            clean_name = GroupService._clean_name(name)
            existing = GroupRepository.get_by_name(clean_name)
            if existing is not None and existing.id != group.id:
                raise ValidationError(f"Já existe um grupo chamado “{clean_name}”.")
            if clean_name != group.name:
                group.name = clean_name
                group.slug = GroupService._unique_slug(clean_name, exclude_id=group.id)

        if description is not None:
            group.description = sanitize_plain_text(
                description, max_length=MAX_DESCRIPTION_LENGTH
            )
        if color is not None:
            group.color = GroupService._clean_color(color)

        db.session.commit()
        return group

    @staticmethod
    def delete(group: Group) -> None:
        """Remove the group. Its documents are kept, only the grouping is lost."""
        db.session.execute(
            document_groups.delete().where(document_groups.c.group_id == group.id)
        )
        db.session.delete(group)
        db.session.commit()

    @staticmethod
    def _clean_color(color: str | None) -> str:
        value = (color or "").strip()
        if not value:
            return DEFAULT_GROUP_COLOR
        if len(value) in {4, 7} and value.startswith("#"):
            try:
                int(value[1:], 16)
            except ValueError:
                return DEFAULT_GROUP_COLOR
            return value
        return DEFAULT_GROUP_COLOR

    # ── Membership ──────────────────────────────────────────────────────────

    @staticmethod
    def add_documents(group: Group, documents: list[Document], commit: bool = True) -> int:
        """Put ``documents`` at the end of the group. Returns how many were new.

        Three statements regardless of how many documents arrive: read the
        current members, read the last position, insert the rest in one go.
        Asking "is this one already here?" per document turned a 200-document
        bulk action into 400 round trips.
        """
        wanted = documents[:MAX_DOCUMENTS_PER_OPERATION]
        if not wanted:
            return 0

        existing = GroupRepository.member_ids(group.id)
        position = GroupRepository.next_position(group.id)

        rows = []
        seen: set[int] = set()
        now = utcnow()
        for document in wanted:
            document_id = _identity_of(document)
            if document_id in existing or document_id in seen:
                continue
            seen.add(document_id)
            rows.append(
                {
                    "document_id": document_id,
                    "group_id": group.id,
                    "position": position,
                    "added_at": now,
                }
            )
            position += 1

        if not rows:
            if commit:
                db.session.commit()
            return 0

        remaining = MAX_DOCUMENTS_PER_GROUP - len(existing)
        if len(rows) > remaining:
            raise ValidationError(
                f"Um grupo comporta até {MAX_DOCUMENTS_PER_GROUP} documentos. "
                f"Este já tem {len(existing)}."
            )

        db.session.execute(document_groups.insert(), rows)
        group.updated_at = now
        if commit:
            db.session.commit()
        return len(rows)

    @staticmethod
    def attach_by_names(document: Document, names: Sequence[str]) -> int:
        """Put ``document`` into each named group, creating the missing ones.

        The entry point for anything restoring memberships from outside the
        database - a backup archive, a Markdown front matter block. Groups
        travel by name in both, because a numeric id means nothing in a file
        that may be restored into a different installation.

        A name that cannot become a group (empty, too long, colliding after
        sanitising) is skipped rather than raised: one unusable name must not
        cost the document its other memberships.
        """
        attached = 0
        for raw in list(names)[:MAX_GROUPS_PER_DOCUMENT]:
            if not isinstance(raw, str) or not raw.strip():
                continue
            group = GroupRepository.get_by_name(raw)
            if group is None:
                try:
                    group = GroupService.create(raw)
                except ValidationError:
                    continue
            attached += GroupService.add_documents(group, [document])
        return attached

    @staticmethod
    def remove_documents(group: Group, documents: list[Document]) -> int:
        ids = [_identity_of(document) for document in documents[:MAX_DOCUMENTS_PER_OPERATION]]
        if not ids:
            return 0

        result = db.session.execute(
            document_groups.delete().where(
                document_groups.c.group_id == group.id,
                document_groups.c.document_id.in_(ids),
            )
        )
        group.updated_at = utcnow()
        db.session.commit()
        return result.rowcount or 0

    @staticmethod
    def set_groups_for(document: Document, group_uuids: list[str]) -> list[Group]:
        """Replace the groups of one document, from the editor's panel."""
        wanted: list[Group] = []
        seen: set[str] = set()
        for public_uuid in group_uuids[:MAX_DOCUMENTS_PER_OPERATION]:
            if public_uuid in seen:
                continue
            seen.add(public_uuid)
            group = GroupRepository.get_by_uuid(public_uuid)
            if group is not None:
                wanted.append(group)

        current = {group.id for group in document.groups}
        target = {group.id for group in wanted}

        # One transaction for the whole panel: a document must never come back
        # from a half-applied save belonging to two groups it was moved out of.
        for group in wanted:
            if group.id not in current:
                GroupService.add_documents(group, [document], commit=False)

        removed = current - target
        if removed:
            db.session.execute(
                document_groups.delete().where(
                    document_groups.c.document_id == document.id,
                    document_groups.c.group_id.in_(removed),
                )
            )

        db.session.commit()
        db.session.refresh(document)
        return wanted

    # ── Order ───────────────────────────────────────────────────────────────

    @staticmethod
    def reorder(group: Group, document_uuids: list[str]) -> int:
        """Arrange the group in the given order. Unlisted members go last."""
        documents = GroupRepository.documents_of(group)
        by_uuid = {document.uuid: document for document in documents}

        ordered: list[Document] = []
        for public_uuid in document_uuids[:MAX_DOCUMENTS_PER_OPERATION]:
            document = by_uuid.pop(public_uuid, None)
            if document is not None:
                ordered.append(document)
        # Anything the client did not mention keeps its relative order at the
        # end: a stale page must not silently drop a document from the group.
        ordered.extend(by_uuid.values())
        if not ordered:
            return 0

        # One executemany rather than one UPDATE per document: reordering a
        # 500-document group was 500 round trips inside a single transaction.
        statement = (
            document_groups.update()
            .where(
                document_groups.c.group_id == bindparam("target_group"),
                document_groups.c.document_id == bindparam("target_document"),
            )
            .values(position=bindparam("new_position"))
        )
        db.session.execute(
            statement,
            [
                {
                    "target_group": group.id,
                    "target_document": _identity_of(document),
                    "new_position": position,
                }
                for position, document in enumerate(ordered, start=1)
            ],
        )

        group.updated_at = utcnow()
        db.session.commit()
        return len(ordered)

    @staticmethod
    def move(group: Group, document: Document, offset: int) -> bool:
        """Move one document up (-1) or down (+1). The keyboard path to order."""
        documents = GroupRepository.documents_of(group)
        uuids = [item.uuid for item in documents]

        try:
            index = uuids.index(document.uuid)
        except ValueError:
            raise NotFoundError("Este documento não está neste grupo.") from None

        target = index + offset
        if target < 0 or target >= len(uuids):
            return False

        uuids[index], uuids[target] = uuids[target], uuids[index]
        GroupService.reorder(group, uuids)
        return True
