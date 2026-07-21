"""Aggregations behind the reports dashboard.

Every figure is produced by a grouped SQL query — never by walking documents in
Python. A library of ten thousand notes must cost the same number of queries as
one of ten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, func, select

from app.extensions import db
from app.models import Category, Document, DocumentVersion
from app.utils.dates import utcnow

MONTH_ABBR = (
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
)

DEFAULT_MONTHS = 12

# Presets for the period filter. `bucket` is the SQLite strftime format the
# rows are grouped by; `count` is how many buckets to show.
PERIODS: dict[str, dict] = {
    "7d": {"label": "7 dias", "unit": "day", "count": 7},
    "30d": {"label": "30 dias", "unit": "day", "count": 30},
    "12s": {"label": "12 semanas", "unit": "week", "count": 12},
    "6m": {"label": "6 meses", "unit": "month", "count": 6},
    "12m": {"label": "12 meses", "unit": "month", "count": 12},
}
DEFAULT_PERIOD = "12m"

ACTIVITY_SERIES = ("Documentos criados", "Edições salvas")

BUCKET_FORMAT = {"day": "%Y-%m-%d", "week": "%Y-%W", "month": "%Y-%m"}


def resolve_period(key: str | None) -> str:
    """Validate a period key coming from the query string."""
    return key if key in PERIODS else DEFAULT_PERIOD


@dataclass(slots=True)
class ActivityReport:
    labels: list[str]
    created: list[int]
    edited: list[int]
    period: str = DEFAULT_PERIOD

    @property
    def total_created(self) -> int:
        return sum(self.created)

    @property
    def total_edited(self) -> int:
        return sum(self.edited)

    @property
    def has_data(self) -> bool:
        return self.total_created > 0 or self.total_edited > 0


def _month_keys(months: int) -> list[tuple[str, str]]:
    """`[(YYYY-MM, 'mai/26'), …]` ending at the current month."""
    now = utcnow()
    keys: list[tuple[str, str]] = []
    year, month = now.year, now.month

    for _ in range(months):
        keys.append((f"{year:04d}-{month:02d}", f"{MONTH_ABBR[month - 1]}/{year % 100:02d}"))
        month -= 1
        if month == 0:
            month, year = 12, year - 1

    return list(reversed(keys))


def _day_keys(days: int) -> list[tuple[str, str]]:
    """`[(YYYY-MM-DD, '21/07'), …]` ending today."""
    today = utcnow().date()
    return [
        (
            (today - timedelta(days=offset)).strftime("%Y-%m-%d"),
            (today - timedelta(days=offset)).strftime("%d/%m"),
        )
        for offset in range(days - 1, -1, -1)
    ]


def _week_keys(weeks: int) -> list[tuple[str, str]]:
    """`[(YYYY-WW, '21/07'), …]` labelled by the Monday of each week.

    SQLite's `%W` counts weeks from the first Monday of the year, which is what
    `date.strftime('%W')` produces too — so the keys line up.
    """
    today = utcnow().date()
    monday = today - timedelta(days=today.weekday())

    keys: list[tuple[str, str]] = []
    for offset in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=offset)
        keys.append((start.strftime("%Y-%W"), start.strftime("%d/%m")))
    return keys


def _period_keys(period: str) -> list[tuple[str, str]]:
    config = PERIODS[period]
    if config["unit"] == "day":
        return _day_keys(config["count"])
    if config["unit"] == "week":
        return _week_keys(config["count"])
    return _month_keys(config["count"])


def _window_start(period: str) -> datetime:
    """First instant the chart can display.

    Used to bound the aggregation. Without it, drawing seven days still
    grouped every row ever written and threw almost all of it away — which is
    why the shortest period measured as the slowest.
    """
    config = PERIODS[period]
    now = utcnow()

    if config["unit"] == "day":
        start = now.date() - timedelta(days=config["count"] - 1)
    elif config["unit"] == "week":
        monday = now.date() - timedelta(days=now.weekday())
        start = monday - timedelta(weeks=config["count"] - 1)
    else:
        year, month = now.year, now.month
        month -= config["count"] - 1
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)

    return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)


def _counts_by_bucket(column, table, fmt: str, extra_filters=()) -> dict[str, int]:
    """`{bucket: count}` for a timestamp column, grouped in the database."""
    bucket = func.strftime(fmt, column).cast(String).label("bucket")
    stmt = select(bucket, func.count()).group_by(bucket)
    for condition in extra_filters:
        stmt = stmt.where(condition)
    return {row[0]: row[1] for row in db.session.execute(stmt.select_from(table)).all()}


def activity_report(period: str = DEFAULT_PERIOD) -> ActivityReport:
    """Documents created and revisions saved, bucketed by the chosen period."""
    period = resolve_period(period)
    keys = _period_keys(period)
    fmt = BUCKET_FORMAT[PERIODS[period]["unit"]]
    # Bounded by the window the chart actually shows, so the index range scan
    # touches only the rows in view instead of the whole history.
    since = _window_start(period)

    created = _counts_by_bucket(
        Document.created_at,
        Document,
        fmt,
        (Document.is_deleted.is_(False), Document.created_at >= since),
    )
    edited = _counts_by_bucket(
        DocumentVersion.created_at,
        DocumentVersion,
        fmt,
        (DocumentVersion.created_at >= since,),
    )

    return ActivityReport(
        labels=[label for _, label in keys],
        created=[created.get(key, 0) for key, _ in keys],
        edited=[edited.get(key, 0) for key, _ in keys],
        period=period,
    )


def words_by_category(limit: int = 6) -> list[tuple[str, int]]:
    """Total words per category, biggest first, with uncategorised folded in."""
    rows = db.session.execute(
        select(
            func.coalesce(Category.name, "Sem categoria"),
            func.coalesce(func.sum(Document.word_count), 0),
        )
        .select_from(Document)
        .outerjoin(Category, Document.category_id == Category.id)
        .where(Document.is_deleted.is_(False))
        .group_by(Category.id)
        .order_by(func.sum(Document.word_count).desc())
        .limit(limit)
    ).all()

    return [(name, int(total)) for name, total in rows if total]


def longest_documents(limit: int = 5) -> list[tuple[str, int]]:
    rows = db.session.execute(
        select(Document.title, Document.word_count)
        .where(Document.is_deleted.is_(False), Document.word_count > 0)
        .order_by(Document.word_count.desc())
        .limit(limit)
    ).all()

    return [(title, int(words)) for title, words in rows]


@dataclass(slots=True)
class ReportSummary:
    activity: ActivityReport
    words_per_category: list[tuple[str, int]]
    longest: list[tuple[str, int]]
    busiest_month: tuple[str, int] | None
    average_words: int
    period: str = DEFAULT_PERIOD

    @property
    def period_label(self) -> str:
        return PERIODS[self.period]["label"]

    @property
    def has_any_data(self) -> bool:
        return (
            self.activity.has_data
            or bool(self.words_per_category)
            or bool(self.longest)
        )


@dataclass(slots=True)
class ReportView:
    """A summary plus the charts drawn from it.

    Assembling the charts belongs here, not in the route: a blueprint should
    translate HTTP to a service call and back, and three chart builders in a
    view function is presentation logic living in the wrong layer.
    """

    summary: ReportSummary
    activity: object
    categories: object
    longest: object


def build_report_view(period: str = DEFAULT_PERIOD) -> ReportView:
    from app.services.chart_geometry import build_bar_chart, build_line_chart

    summary = build_summary(period)

    return ReportView(
        summary=summary,
        # One series on the plot. Edits are still counted — the summary
        # sentence below the heading reports the busiest period from them —
        # but two lines that track each other closely made the chart harder to
        # read, not richer.
        activity=build_line_chart(
            summary.activity.labels,
            [(ACTIVITY_SERIES[0], summary.activity.created)],
        ),
        categories=build_bar_chart(summary.words_per_category),
        longest=build_bar_chart(summary.longest),
    )


def build_summary(period: str = DEFAULT_PERIOD) -> ReportSummary:
    """Everything the reports section needs, in a fixed number of queries."""
    period = resolve_period(period)
    activity = activity_report(period)

    average = db.session.scalar(
        select(func.coalesce(func.avg(Document.word_count), 0)).where(
            Document.is_deleted.is_(False), Document.word_count > 0
        )
    )

    busiest = None
    if activity.has_data:
        peak = max(range(len(activity.labels)), key=lambda i: activity.edited[i])
        if activity.edited[peak]:
            busiest = (activity.labels[peak], activity.edited[peak])

    return ReportSummary(
        activity=activity,
        words_per_category=words_by_category(),
        longest=longest_documents(),
        busiest_month=busiest,
        average_words=int(average or 0),
        period=period,
    )
