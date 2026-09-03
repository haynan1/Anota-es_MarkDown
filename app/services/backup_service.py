"""Backup and restore.

A backup is a ZIP archive containing

``manifest.json``
    Format version, application version, creation timestamp and record counts.
``data.json``
    Structured export of documents, versions, categories, tags and settings.
``documents/<slug>.md``
    Plain Markdown copies, so the archive stays useful even without this app.

Restoring never overwrites silently: the caller chooses ``merge`` or
``replace``, and ``replace`` always writes a safety backup first.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from flask import current_app
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    AchievementUnlock,
    Category,
    Document,
    DocumentVersion,
    Goal,
    GoalOccurrence,
    GoalTemplate,
    Group,
    MotivationalPhrase,
    Setting,
    Tag,
    document_groups,
)
from app.repositories.group_repository import GroupRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.services.exceptions import ValidationError
from app.services.group_service import GroupService
from app.models.goal import (
    GOAL_CATEGORIES,
    GOAL_PRIORITIES,
    GOAL_STATUSES,
    RECURRENCE_TYPES,
)
from app.models.goal import new_uuid as new_goal_uuid
from app.services.markdown_service import render_markdown
from app.services.sanitizer import sanitize_plain_text
from app.services.search_service import search_index
from app.utils.dates import as_utc, parse_iso, utcnow
from app.utils.files import (
    ensure_directory,
    is_safe_archive_member,
    safe_filename,
    unique_path,
)
from app.utils.humanize import format_bytes
from app.utils.text import build_excerpt, content_hash, count_characters, count_words

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
DATA_NAME = "data.json"
DOCUMENTS_DIR = "documents"

# Guards against a zip bomb during restore.
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000

RESTORE_MODES = ("merge", "replace")


@dataclass(slots=True)
class BackupInfo:
    path: Path
    created_at: datetime
    size_bytes: int
    document_count: int = 0
    format_version: int = 1
    app_version: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_readable(self) -> str:
        return format_bytes(self.size_bytes)


@dataclass(slots=True)
class RestoreReport:
    mode: str
    documents_created: int = 0
    documents_skipped: int = 0
    categories_created: int = 0
    groups_created: int = 0
    goals_created: int = 0
    goal_templates_created: int = 0
    phrases_created: int = 0
    achievements_restored: int = 0
    tags_created: int = 0
    settings_applied: int = 0
    safety_backup: str | None = None
    warnings: list[str] = field(default_factory=list)


# ── Serialization ───────────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    utc_value = as_utc(value)
    return utc_value.isoformat() if utc_value else None


def _serialize_document(document: Document) -> dict:
    return {
        "uuid": document.uuid,
        "title": document.title,
        "slug": document.slug,
        "content_markdown": document.content_markdown,
        "excerpt": document.excerpt,
        "category": document.category.name if document.category else None,
        "tags": [tag.name for tag in document.tags],
        # Groups travel with the document, by name. The membership is what
        # matters; the order inside a group is rebuilt on restore, in the order
        # the documents are read back.
        "groups": [group.name for group in document.groups],
        "is_favorite": document.is_favorite,
        "is_locked": document.is_locked,
        "is_archived": document.is_archived,
        "is_deleted": document.is_deleted,
        "deleted_at": _iso(document.deleted_at),
        "created_at": _iso(document.created_at),
        "updated_at": _iso(document.updated_at),
        "page_size": document.page_size,
        "pdf_theme": document.pdf_theme,
        "revision": document.revision,
        "versions": [
            {
                "version_number": version.version_number,
                "title": version.title,
                "content_markdown": version.content_markdown,
                "content_hash": version.content_hash,
                "change_summary": version.change_summary,
                "word_count": version.word_count,
                "created_at": _iso(version.created_at),
            }
            for version in sorted(document.versions, key=lambda v: v.version_number)
        ],
    }


def _iso_date(value) -> str | None:
    return value.isoformat() if value else None


def _serialize_goal(goal: Goal) -> dict:
    """Uma meta com a regra dela e as exceções que ela acumulou.

    O documento ligado viaja pelo UUID, e não pelo id: um id só faz sentido no
    banco que o gerou, e um backup restaurado em outra instalação apontaria
    para um documento qualquer - ou para nenhum.
    """
    return {
        "uuid": goal.uuid,
        "title": goal.title,
        "description": goal.description,
        "link_url": goal.link_url,
        "document_uuid": goal.document.uuid if goal.document else None,
        "date": _iso_date(goal.date),
        "time": goal.time.isoformat() if goal.time else None,
        "has_deadline": goal.has_deadline,
        "show_on_board": goal.show_on_board,
        "priority": goal.priority,
        "category": goal.category,
        "status": goal.status,
        "completed_at": _iso(goal.completed_at),
        "recurrence_type": goal.recurrence_type,
        "recurrence_days": goal.recurrence_days,
        "recurrence_end_date": _iso_date(goal.recurrence_end_date),
        "created_at": _iso(goal.created_at),
        "updated_at": _iso(goal.updated_at),
        "occurrences": [
            {
                "date": _iso_date(occurrence.occurrence_date),
                "status": occurrence.status,
                "completed_at": _iso(occurrence.completed_at),
            }
            for occurrence in sorted(
                goal.occurrences, key=lambda item: item.occurrence_date
            )
        ],
    }


def _serialize_goal_template(template: GoalTemplate) -> dict:
    return {
        "uuid": template.uuid,
        "title": template.title,
        "description": template.description,
        "link_url": template.link_url,
        "document_uuid": template.document.uuid if template.document else None,
        "time": template.time.isoformat() if template.time else None,
        "show_on_board": template.show_on_board,
        "priority": template.priority,
        "category": template.category,
        "created_at": _iso(template.created_at),
    }


def build_export_payload() -> dict:
    from app.services.settings_service import SettingsService

    # selectinload is load-bearing: DocumentVersion is lazy by default, so
    # serialising each document's history would issue one query per document.
    documents = (
        db.session.scalars(
            db.select(Document).options(
                selectinload(Document.versions),
                selectinload(Document.tags),
                selectinload(Document.groups),
                joinedload(Document.category),
            )
        )
        .unique()
        .all()
    )
    return {
        "categories": [
            {"name": category.name, "slug": category.slug, "color": category.color}
            for category in db.session.scalars(db.select(Category))
        ],
        "tags": [
            {"name": tag.name, "slug": tag.slug}
            for tag in db.session.scalars(db.select(Tag))
        ],
        # Exported in full, not just as names on documents: an empty group is
        # still a decision the user made, and its colour and description would
        # otherwise be lost on every restore.
        "groups": [
            {
                "name": group.name,
                "slug": group.slug,
                "color": group.color,
                "description": group.description,
            }
            for group in db.session.scalars(db.select(Group))
        ],
        "settings": SettingsService.export(),
        "documents": [_serialize_document(document) for document in documents],
        # A jornada viaja junto. Um backup que salva os documentos e perde as
        # metas restaura metade da aplicação - e a metade perdida é justamente
        # a que não pode ser reconstruída relendo os arquivos .md do arquivo.
        "goals": [
            _serialize_goal(goal)
            for goal in db.session.scalars(
                db.select(Goal).options(
                    selectinload(Goal.occurrences), joinedload(Goal.document)
                )
            )
            .unique()
            .all()
        ],
        "goal_templates": [
            _serialize_goal_template(template)
            for template in db.session.scalars(
                db.select(GoalTemplate).options(joinedload(GoalTemplate.document))
            )
            .unique()
            .all()
        ],
        "motivational_phrases": [
            {"uuid": phrase.uuid, "text": phrase.text, "created_at": _iso(phrase.created_at)}
            for phrase in db.session.scalars(db.select(MotivationalPhrase))
        ],
        "achievements": [
            {"key": row.key, "unlocked_at": _iso(row.unlocked_at)}
            for row in db.session.scalars(db.select(AchievementUnlock))
        ],
    }


# ── Creation ────────────────────────────────────────────────────────────────


def backup_directory() -> Path:
    return ensure_directory(Path(current_app.config["BACKUP_DIR"]))


def create_backup(label: str = "") -> BackupInfo:
    """Write a new ZIP backup and prune older ones."""
    directory = backup_directory()
    payload = build_export_payload()
    now = utcnow()

    suffix = f"-{safe_filename(label, '').rstrip('.')}" if label else ""
    filename = f"markdown-studio-{now.strftime('%Y%m%d-%H%M%S')}{suffix}.zip"
    target = unique_path(directory, filename)

    manifest = {
        "backup_format": current_app.config["BACKUP_FORMAT_VERSION"],
        "app_version": current_app.config["APP_VERSION"],
        "app_name": current_app.config["APP_NAME"],
        "created_at": now.isoformat(),
        "counts": {
            "documents": len(payload["documents"]),
            "categories": len(payload["categories"]),
            "groups": len(payload["groups"]),
            "tags": len(payload["tags"]),
            "settings": len(payload["settings"]),
            "goals": len(payload["goals"]),
            "goal_templates": len(payload["goal_templates"]),
        },
    }

    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                DATA_NAME, json.dumps(payload, ensure_ascii=False, indent=2)
            )
            used_names: set[str] = set()
            for document in payload["documents"]:
                name = safe_filename(document["title"], ".md")
                stem, index = name[:-3], 2
                while name in used_names:
                    name = f"{stem}-{index}.md"
                    index += 1
                used_names.add(name)
                archive.writestr(
                    f"{DOCUMENTS_DIR}/{name}", document["content_markdown"] or ""
                )
    except OSError as exc:
        raise ValidationError(f"Não foi possível gravar o backup: {exc}") from exc

    size_bytes = target.stat().st_size
    prune_backups(keep_path=target)

    return BackupInfo(
        path=target,
        created_at=now,
        size_bytes=size_bytes,
        document_count=len(payload["documents"]),
        format_version=manifest["backup_format"],
        app_version=manifest["app_version"],
    )


def list_backups() -> list[BackupInfo]:
    """Every archive in the backup directory, newest first.

    Ordered by the recorded creation time rather than by filename: two backups
    taken in the same second differ only by their label, which sorts
    alphabetically and would otherwise scramble the order.
    """
    directory = backup_directory()
    infos: list[BackupInfo] = []
    for path in directory.glob("*.zip"):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover
            continue
        info = BackupInfo(
            path=path,
            created_at=as_utc(datetime.fromtimestamp(stat.st_mtime)),
            size_bytes=stat.st_size,
        )
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            info.document_count = manifest.get("counts", {}).get("documents", 0)
            info.format_version = manifest.get("backup_format", 1)
            info.app_version = manifest.get("app_version", "")
            parsed = parse_iso(manifest.get("created_at"))
            if parsed:
                info.created_at = parsed
        except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError):
            logger.warning("Backup ilegível ignorado na listagem: %s", path.name)
        infos.append(info)

    infos.sort(key=lambda item: item.created_at, reverse=True)
    return infos


def prune_backups(keep_path: Path | None = None) -> int:
    """Keep only the newest ``backup_keep_last`` archives.

    ``keep_path`` is never pruned. Filesystem timestamps on Windows are coarse
    enough that several backups taken in quick succession can share an mtime;
    without this guard, the archive that was just written could be the one
    selected for deletion.
    """
    from app.services.settings_service import SettingsService

    keep = SettingsService.get("backup_keep_last", 10)

    def sort_key(path: Path) -> tuple[float, str]:
        try:
            mtime = path.stat().st_mtime
        except OSError:  # pragma: no cover - file vanished
            mtime = 0.0
        # The name carries a timestamp, so it breaks mtime ties deterministically.
        return (mtime, path.name)

    archives = sorted(backup_directory().glob("*.zip"), key=sort_key, reverse=True)

    removed = 0
    for path in archives[keep:]:
        if keep_path is not None and path.resolve() == keep_path.resolve():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover
            logger.warning("Não foi possível remover o backup antigo %s", path.name)
    return removed


def resolve_backup(name: str) -> Path:
    """Resolve a backup filename supplied by the UI, refusing traversal."""
    if not name or not is_safe_archive_member(name) or "/" in name or "\\" in name:
        raise ValidationError("Nome de backup inválido.")
    directory = backup_directory()
    candidate = (directory / name).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError as exc:
        raise ValidationError("Nome de backup inválido.") from exc
    if not candidate.is_file():
        raise ValidationError("Backup não encontrado.")
    return candidate


# ── Restore ─────────────────────────────────────────────────────────────────


def read_archive(source) -> dict:
    """Validate a backup archive and return its ``data.json`` payload."""
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ValidationError("O arquivo enviado não é um ZIP válido.") from exc

    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValidationError("O backup contém arquivos demais.")

        total = 0
        for member in members:
            if not is_safe_archive_member(member.filename):
                raise ValidationError(
                    f"O backup contém um caminho inseguro: {member.filename[:60]}"
                )
            total += member.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValidationError("O backup é grande demais para ser restaurado.")

        names = {member.filename for member in members}
        if MANIFEST_NAME not in names or DATA_NAME not in names:
            raise ValidationError(
                "Backup inválido: manifest.json ou data.json não foi encontrado."
            )

        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            payload = json.loads(archive.read(DATA_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Backup corrompido: não foi possível ler os dados.") from exc

    fmt = manifest.get("backup_format")
    supported = current_app.config["BACKUP_FORMAT_VERSION"]
    if not isinstance(fmt, int) or fmt > supported:
        raise ValidationError(
            f"Formato de backup não suportado (versão {fmt}). "
            f"Esta aplicação lê até a versão {supported}."
        )

    if not isinstance(payload.get("documents"), list):
        raise ValidationError("Backup inválido: a lista de documentos está ausente.")

    payload["_manifest"] = manifest
    return payload


def _restore_document(entry: dict) -> Document:
    title = (entry.get("title") or "Documento importado")[:200]
    body = entry.get("content_markdown") or ""

    document = Document(
        title=title,
        content_markdown=body,
        rendered_html=render_markdown(body),
        excerpt=entry.get("excerpt") or build_excerpt(body),
        content_hash=content_hash(title, body),
        word_count=count_words(body),
        character_count=count_characters(body),
        is_favorite=bool(entry.get("is_favorite")),
        # Absent in archives written before the lock existed - those documents
        # were unprotected, so False is the correct reading.
        is_locked=bool(entry.get("is_locked")),
        is_archived=bool(entry.get("is_archived")),
        is_deleted=bool(entry.get("is_deleted")),
        page_size=entry.get("page_size") or "A4",
        pdf_theme=entry.get("pdf_theme") or "classic",
        revision=int(entry.get("revision") or 1),
    )
    document.estimated_reading_time = max(1, round(document.word_count / 200)) if document.word_count else 0

    if isinstance(entry.get("uuid"), str) and len(entry["uuid"]) <= 36:
        document.uuid = entry["uuid"]

    for field_name in ("created_at", "updated_at", "deleted_at"):
        parsed = parse_iso(entry.get(field_name))
        if parsed:
            setattr(document, field_name, parsed)

    from app.services.document_service import DocumentService

    # Slug first (it queries), then persist, and only then wire up
    # relationships - assigning them to a transient object makes SQLAlchemy
    # warn that the backref append cannot proceed.
    document.slug = DocumentService.build_slug(title)
    db.session.add(document)
    db.session.flush()

    if entry.get("category"):
        # Assigned by foreign key rather than by relationship: the owning side
        # avoids a backref append into a collection that is not loaded.
        document.category_id = CategoryRepository.get_or_create(entry["category"]).id
    if isinstance(entry.get("tags"), list):
        document.tags = TagRepository.resolve_many(
            [tag for tag in entry["tags"] if isinstance(tag, str)]
        )
    if isinstance(entry.get("groups"), list):
        # Archives written before groups existed simply carry no "groups" key,
        # and a document restored from one lands in no group - which is exactly
        # the state it was in when the archive was written.
        GroupService.attach_by_names(
            document, [name for name in entry["groups"] if isinstance(name, str)]
        )
    db.session.flush()

    for raw_version in entry.get("versions") or []:
        if not isinstance(raw_version, dict):
            continue
        version_body = raw_version.get("content_markdown") or ""
        version = DocumentVersion(
            document_id=document.id,
            version_number=int(raw_version.get("version_number") or 1),
            title=(raw_version.get("title") or title)[:200],
            content_markdown=version_body,
            content_hash=raw_version.get("content_hash")
            or content_hash(raw_version.get("title") or title, version_body),
            change_summary=(raw_version.get("change_summary") or "")[:200],
            word_count=int(raw_version.get("word_count") or count_words(version_body)),
        )
        created = parse_iso(raw_version.get("created_at"))
        if created:
            version.created_at = created
        db.session.add(version)

    return document


# ── Restauração da jornada ──────────────────────────────────────────────────


def _parse_date(value: object):
    """Uma data ISO vinda do arquivo, ou ``None``. Nunca levanta.

    O arquivo é entrada não confiável: qualquer campo pode ter o tipo errado
    ou uma data que não existe. Uma linha ruim vira "não sei", e quem chama
    decide se isso invalida a entrada inteira.
    """
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_time(value: object):
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def _one_of(value: object, allowed: tuple[str, ...], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def _restore_goal(entry: dict, documents: dict[str, int]) -> Goal:
    anchor = _parse_date(entry.get("date"))
    if anchor is None:
        raise ValidationError("Meta sem data válida.")

    goal = Goal(
        uuid=entry.get("uuid") or new_goal_uuid(),
        title=sanitize_plain_text(str(entry.get("title") or ""), max_length=160)
        or "Meta sem título",
        description=str(entry.get("description") or "")[:2000],
        link_url=str(entry.get("link_url") or "")[:500],
        document_id=documents.get(entry.get("document_uuid") or ""),
        date=anchor,
        time=_parse_time(entry.get("time")),
        has_deadline=bool(entry.get("has_deadline", True)),
        show_on_board=bool(entry.get("show_on_board", True)),
        priority=_one_of(entry.get("priority"), GOAL_PRIORITIES, "media"),
        category=_one_of(entry.get("category"), GOAL_CATEGORIES, "outros"),
        status=_one_of(entry.get("status"), GOAL_STATUSES, "pendente"),
        completed_at=parse_iso(entry.get("completed_at")),
        recurrence_type=_one_of(entry.get("recurrence_type"), RECURRENCE_TYPES, "none"),
        recurrence_end_date=_parse_date(entry.get("recurrence_end_date")),
    )
    days = entry.get("recurrence_days")
    goal.recurrence_days = days if isinstance(days, int) and 0 < days <= 365 else None

    created = parse_iso(entry.get("created_at"))
    if created:
        goal.created_at = created
    updated = parse_iso(entry.get("updated_at"))
    if updated:
        goal.updated_at = updated

    seen: set[date] = set()
    for raw in entry.get("occurrences") or []:
        if not isinstance(raw, dict):
            continue
        day = _parse_date(raw.get("date"))
        # A unicidade (meta, dia) é uma restrição do banco: um arquivo com o
        # mesmo dia duas vezes derrubaria a restauração inteira no commit.
        if day is None or day in seen:
            continue
        seen.add(day)
        goal.occurrences.append(
            GoalOccurrence(
                occurrence_date=day,
                status=_one_of(raw.get("status"), GOAL_STATUSES, "pendente"),
                completed_at=parse_iso(raw.get("completed_at")),
            )
        )

    db.session.add(goal)
    return goal


def _restore_goal_template(entry: dict, documents: dict[str, int]) -> GoalTemplate:
    template = GoalTemplate(
        uuid=entry.get("uuid") or new_goal_uuid(),
        title=sanitize_plain_text(str(entry.get("title") or ""), max_length=160)
        or "Predefinida sem título",
        description=str(entry.get("description") or "")[:2000],
        link_url=str(entry.get("link_url") or "")[:500],
        document_id=documents.get(entry.get("document_uuid") or ""),
        time=_parse_time(entry.get("time")),
        show_on_board=bool(entry.get("show_on_board", True)),
        priority=_one_of(entry.get("priority"), GOAL_PRIORITIES, "media"),
        category=_one_of(entry.get("category"), GOAL_CATEGORIES, "outros"),
    )
    created = parse_iso(entry.get("created_at"))
    if created:
        template.created_at = created
    db.session.add(template)
    return template


def _restore_journey(payload: dict, report: RestoreReport) -> None:
    """Metas, predefinidas, frases e conquistas.

    Chamado depois dos documentos, e não antes: uma meta aponta para um
    documento pelo UUID, e o mapa de UUIDs só está completo quando eles já
    foram restaurados.
    """
    documents = {
        row[0]: row[1]
        for row in db.session.execute(db.select(Document.uuid, Document.id)).all()
    }

    existing_goals = {row[0] for row in db.session.execute(db.select(Goal.uuid)).all()}
    for raw in payload.get("goals") or []:
        if not isinstance(raw, dict):
            report.warnings.append("Uma entrada de meta inválida foi ignorada.")
            continue
        if raw.get("uuid") in existing_goals:
            continue
        try:
            goal = _restore_goal(raw, documents)
        except Exception as exc:  # noqa: BLE001 - untrusted-input boundary
            logger.exception("Falha ao restaurar meta")
            report.warnings.append(f"Meta ignorada: {type(exc).__name__}")
            db.session.rollback()
            continue
        existing_goals.add(goal.uuid)
        report.goals_created += 1

    existing_templates = {
        row[0] for row in db.session.execute(db.select(GoalTemplate.uuid)).all()
    }
    for raw in payload.get("goal_templates") or []:
        if not isinstance(raw, dict) or raw.get("uuid") in existing_templates:
            continue
        try:
            template = _restore_goal_template(raw, documents)
        except Exception as exc:  # noqa: BLE001 - untrusted-input boundary
            logger.exception("Falha ao restaurar meta predefinida")
            report.warnings.append(f"Predefinida ignorada: {type(exc).__name__}")
            db.session.rollback()
            continue
        existing_templates.add(template.uuid)
        report.goal_templates_created += 1

    existing_phrases = {
        row[0] for row in db.session.execute(db.select(MotivationalPhrase.uuid)).all()
    }
    for raw in payload.get("motivational_phrases") or []:
        if not isinstance(raw, dict) or raw.get("uuid") in existing_phrases:
            continue
        text = sanitize_plain_text(str(raw.get("text") or ""), max_length=255)
        if not text:
            continue
        phrase = MotivationalPhrase(uuid=raw.get("uuid") or new_goal_uuid(), text=text)
        created = parse_iso(raw.get("created_at"))
        if created:
            phrase.created_at = created
        db.session.add(phrase)
        existing_phrases.add(phrase.uuid)
        report.phrases_created += 1

    unlocked = {
        row[0] for row in db.session.execute(db.select(AchievementUnlock.key)).all()
    }
    for raw in payload.get("achievements") or []:
        if not isinstance(raw, dict):
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not key or key in unlocked:
            continue
        row = AchievementUnlock(key=key[:80])
        when = parse_iso(raw.get("unlocked_at"))
        if when:
            row.unlocked_at = when
        db.session.add(row)
        unlocked.add(key)
        report.achievements_restored += 1

    db.session.commit()


def restore_backup(source, mode: str = "merge") -> RestoreReport:
    """Restore ``source`` (a path or file object) using ``mode``."""
    if mode not in RESTORE_MODES:
        raise ValidationError("Modo de restauração inválido.")

    payload = read_archive(source)
    report = RestoreReport(mode=mode)

    # Replacing is destructive - capture the current state first, always.
    if mode == "replace":
        safety = create_backup(label="antes-da-restauracao")
        report.safety_backup = safety.name

        # Association rows first: SQLite only enforces ON DELETE CASCADE when
        # foreign keys are switched on per connection, so nothing here relies
        # on it. Leaving them behind would point at documents that no longer
        # exist.
        # A jornada primeiro, e as ocorrências antes das metas: o SQLite só
        # honra ON DELETE CASCADE com as chaves estrangeiras ligadas por
        # conexão, então nada aqui depende delas.
        db.session.execute(db.delete(GoalOccurrence))
        db.session.execute(db.delete(Goal))
        db.session.execute(db.delete(GoalTemplate))
        db.session.execute(db.delete(MotivationalPhrase))
        db.session.execute(db.delete(AchievementUnlock))
        db.session.execute(document_groups.delete())
        db.session.execute(db.delete(Group))
        db.session.execute(db.delete(DocumentVersion))
        db.session.execute(db.delete(Document))
        db.session.execute(db.delete(Setting))
        db.session.commit()
        # A bulk DELETE does not update the identity map: objects deleted at the
        # database level would linger in loaded relationship collections and
        # confuse the next autoflush. Drop them all.
        db.session.expunge_all()
        TagRepository.delete_orphans()
        db.session.commit()

    for raw in payload.get("categories") or []:
        if isinstance(raw, dict) and raw.get("name"):
            before = CategoryRepository.get_by_name(raw["name"])
            CategoryRepository.get_or_create(raw["name"], raw.get("color"))
            if before is None:
                report.categories_created += 1

    # Restored before the documents, so a group keeps the colour and the
    # description it was given rather than the defaults a membership would
    # create it with.
    for raw in payload.get("groups") or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        if GroupRepository.get_by_name(raw["name"]) is not None:
            continue
        try:
            GroupService.create(
                raw["name"],
                description=raw.get("description") or "",
                color=raw.get("color"),
            )
            report.groups_created += 1
        except ValidationError:
            report.warnings.append(f"Grupo ignorado: {raw['name'][:40]}")

    existing_uuids = {
        row[0] for row in db.session.execute(db.select(Document.uuid)).all()
    }

    for raw in payload.get("documents") or []:
        if not isinstance(raw, dict):
            report.warnings.append("Uma entrada de documento inválida foi ignorada.")
            continue
        if mode == "merge" and raw.get("uuid") in existing_uuids:
            report.documents_skipped += 1
            continue
        try:
            document = _restore_document(raw)
        except Exception as exc:  # noqa: BLE001 - untrusted-input boundary
            # The archive is attacker-shaped data: any field can be the wrong
            # type, out of range or malformed. One bad entry must not abort a
            # restore of hundreds of good documents, so the failure is logged,
            # surfaced in the report and the loop continues.
            logger.exception("Falha ao restaurar documento")
            report.warnings.append(f"Documento ignorado: {type(exc).__name__}")
            db.session.rollback()
            continue
        existing_uuids.add(document.uuid)
        report.documents_created += 1

    db.session.commit()

    _restore_journey(payload, report)

    settings_payload = payload.get("settings")
    if isinstance(settings_payload, dict):
        from app.services.settings_service import SettingsService

        report.settings_applied = SettingsService.import_values(settings_payload)

    search_index.rebuild()
    report.tags_created = len(TagRepository.all())
    return report
