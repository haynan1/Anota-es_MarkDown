"""UTC storage, local presentation.

Every timestamp in the database is timezone-aware UTC. Rendering converts to
the timezone chosen in Settings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Sao_Paulo"


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive datetimes read back from SQLite."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def to_local(value: datetime | None, tz_name: str | None = None) -> datetime | None:
    utc_value = as_utc(value)
    if utc_value is None:
        return None
    return utc_value.astimezone(resolve_zone(tz_name))


def format_datetime(
    value: datetime | None, tz_name: str | None = None, fmt: str = "%d/%m/%Y %H:%M"
) -> str:
    local = to_local(value, tz_name)
    return local.strftime(fmt) if local else "—"


def format_date(value: datetime | None, tz_name: str | None = None) -> str:
    return format_datetime(value, tz_name, "%d/%m/%Y")


def humanize(value: datetime | None, tz_name: str | None = None) -> str:
    """Relative label such as "ha 5 minutos", falling back to an absolute date."""
    utc_value = as_utc(value)
    if utc_value is None:
        return "—"

    seconds = (utcnow() - utc_value).total_seconds()
    if seconds < 0:
        return format_datetime(value, tz_name)
    if seconds < 60:
        return "agora mesmo"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"há {minutes} minuto{'s' if minutes > 1 else ''}"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"há {hours} hora{'s' if hours > 1 else ''}"
    if seconds < 604800:
        days = int(seconds // 86400)
        return f"há {days} dia{'s' if days > 1 else ''}"
    return format_date(value, tz_name)
