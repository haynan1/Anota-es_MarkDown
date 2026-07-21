"""Pure geometry for the report charts.

No database, no Flask — data in, coordinates out. Keeping the arithmetic here
means the templates only ever emit attributes, and every scale decision is
unit-testable.

Charts are server-rendered on purpose: the strict CSP forbids inline styles and
scripts, so colour comes from CSS classes bound to custom properties
(theme-aware). Nothing is fetched, and the charts render with JavaScript
disabled.

The line chart is inline SVG, where geometry rides on plain attributes the
policy does not restrict. The bar chart is HTML: an SVG sized at ``width:100%``
scales its own text with the container, which left chart labels with no fixed
size at all — specified 12px, rendered near 9px in a narrow card — and outside
the reach of the accessibility font scale. Only proportions are computed for
bars; CSS lays them out.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Point:
    x: float
    y: float
    label: str
    value: int


@dataclass(slots=True)
class LineSeries:
    name: str
    slot: int
    points: list[Point] = field(default_factory=list)

    @property
    def polyline(self) -> str:
        return " ".join(f"{p.x:.1f},{p.y:.1f}" for p in self.points)

    @property
    def area_path(self) -> str:
        """Closed path for the 10%-opacity wash under the line."""
        if not self.points:
            return ""
        first, last = self.points[0], self.points[-1]
        body = " ".join(f"L{p.x:.1f},{p.y:.1f}" for p in self.points[1:])
        return (
            f"M{first.x:.1f},{first.y:.1f} {body} "
            f"L{last.x:.1f},{BASE_Y} L{first.x:.1f},{BASE_Y} Z"
        )

    @property
    def end_point(self) -> Point | None:
        return self.points[-1] if self.points else None

    @property
    def last_value(self) -> int:
        """The value the legend reports beside the series name.

        This is the direct label. It used to float inside the plot as SVG
        text, which meant it scaled with the container and became unreadable
        on a narrow screen. In the legend it is real HTML text, and it sits
        against the name it belongs to instead of needing a colour match.
        """
        end = self.end_point
        return end.value if end else 0


@dataclass(slots=True)
class LineChart:
    width: int
    height: int
    series: list[LineSeries]
    # One entry per bucket, in order; an empty string means "no label here",
    # which keeps the axis grid aligned while thinning a crowded axis.
    x_labels: list[str]
    y_ticks: list[tuple[float, str]]
    max_value: int

    @property
    def columns(self) -> int:
        return len(self.x_labels)

    @property
    def is_empty(self) -> bool:
        return self.max_value <= 0


@dataclass(slots=True)
class Bar:
    label: str
    value: int
    display: str
    ratio: float
    # Whole percent of the longest bar. A class name, not an inline style:
    # the Content-Security-Policy forbids inline styles, so the width has to
    # arrive through a stylesheet rule. One percent of a bar is imperceptible.
    percent: int


@dataclass(slots=True)
class BarChart:
    bars: list[Bar]

    @property
    def is_empty(self) -> bool:
        return not self.bars


# Line-chart plot box. The SVG is now the plot and nothing else — axis text
# lives in HTML beside it — so the only padding left is the room a marker
# needs not to be clipped at the edges.
LINE_WIDTH = 720
LINE_HEIGHT = 240
PAD_X = 6
PAD_TOP = 8
PAD_BOTTOM = 8
BASE_Y = LINE_HEIGHT - PAD_BOTTOM


def _nice_ceiling(value: int) -> int:
    """Round a maximum up to a clean axis top.

    Always even: the axis draws a midpoint tick at half the top, and an odd
    ceiling put that gridline at x.5 while the label rounded to an integer —
    the line and its number disagreed.
    """
    if value <= 4:
        return 4

    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step * 4:
            top = step * ((value + step - 1) // step)
            return top + 1 if top % 2 else top

    magnitude = 10 ** (len(str(value)) - 1)
    top = magnitude * ((value + magnitude - 1) // magnitude)
    return top + 1 if top % 2 else top


# Above this many buckets the axis is thinned; below it every bucket is named.
MAX_X_LABELS = 7

# Separation applied to a series that traces exactly over an earlier one.
# Three units of a 224-unit plot: far below the resolution of the axis, and
# the difference between reading one line and reading two.
DODGE = 3.0


def _separate_identical_series(series: list[LineSeries]) -> None:
    """Nudge a series apart when it repeats another one exactly.

    Two series holding the same value in every bucket land on the same pixels,
    and the upper one hides the lower one completely — the chart then reports
    one series where there are two. This is the common case early on, when
    every document has exactly one saved revision.

    Only an *exact* duplicate is moved, and it is moved as a whole: shifting
    single points would put a kink in the line where the series met, which
    would misdescribe the shape. The legend, the tooltips and the table all
    continue to report the true values.
    """
    seen: dict[tuple[float, ...], int] = {}
    for item in series:
        shape = tuple(round(point.y, 3) for point in item.points)
        if not shape:
            continue

        duplicates = seen.get(shape, 0)
        if duplicates:
            for point in item.points:
                point.y -= DODGE * duplicates
        seen[shape] = duplicates + 1


def _thin_labels(labels: list[str]) -> list[str]:
    """Blank out labels until the axis is no longer crowded.

    The blanks are kept rather than dropped: the axis is a grid with one cell
    per bucket, so an empty cell is what holds the remaining labels over the
    points they describe. Filtering the list instead would silently respace
    everything and put each label above the wrong month.
    """
    count = len(labels)
    if count <= MAX_X_LABELS:
        return list(labels)

    stride = -(-count // MAX_X_LABELS)  # ceiling division
    # Anchored to the end so the most recent bucket is always named.
    return [
        label if (count - 1 - index) % stride == 0 else ""
        for index, label in enumerate(labels)
    ]


def build_line_chart(
    labels: list[str], series_values: list[tuple[str, list[int]]]
) -> LineChart:
    """Multi-series line chart on a single shared axis.

    One axis by construction: every series here counts documents, so there is
    never a second scale to invent a correlation with.

    Points sit at *band centres* rather than at the edges of the plot. Each
    bucket is an interval — a whole month, a whole day — not an instant, so a
    centre is the honest position for it. It also lets the axis labels be laid
    out as a plain CSS grid of one cell per bucket, which is what allows them
    to be real HTML text: aligned by construction, at a fixed readable size,
    instead of SVG text that shrank with the container.
    """
    columns = len(labels)
    top = _nice_ceiling(max((max(v) for _, v in series_values if v), default=0))

    plot_width = LINE_WIDTH - 2 * PAD_X
    plot_height = BASE_Y - PAD_TOP
    band = plot_width / max(columns, 1)

    def y_for(value: int) -> float:
        return BASE_Y - (value / top) * plot_height if top else BASE_Y

    series: list[LineSeries] = []
    for slot, (name, values) in enumerate(series_values, start=1):
        points = [
            Point(
                x=PAD_X + (index + 0.5) * band,
                y=y_for(value),
                label=labels[index] if index < len(labels) else "",
                value=value,
            )
            for index, value in enumerate(values)
        ]
        series.append(LineSeries(name=name, slot=slot, points=points))

    _separate_identical_series(series)

    x_labels = _thin_labels(labels)

    # The tick value is computed once and drives both the line's position and
    # its label, so the two can never disagree.
    y_ticks = [
        (y_for(tick), f"{tick:,}".replace(",", "."))
        for tick in (0, top // 2, top)
    ]

    return LineChart(
        width=LINE_WIDTH,
        height=LINE_HEIGHT,
        series=series,
        x_labels=x_labels,
        y_ticks=y_ticks,
        max_value=top if any(any(v) for _, v in series_values) else 0,
    )


# A bar shorter than this is invisible; it still has to read as "some, but
# little" rather than as nothing at all.
MIN_VISIBLE_PERCENT = 1


def build_bar_chart(rows: list[tuple[str, int]], formatter=None) -> BarChart:
    """Ranked horizontal bars.

    A single colour for every bar: length already encodes magnitude, so a
    value-ramp would double-encode it and burn the one free channel.

    Only the *proportion* is computed here. The bars are laid out in HTML
    rather than SVG, because an SVG sized at ``width: 100%`` scales its own
    text with the container: a label specified as 12px rendered at roughly
    9px inside a narrow card and larger inside a wide one, so chart type had
    no fixed size and ignored the accessibility font scale. In HTML the label
    is real text wearing the real token.
    """
    formatter = formatter or (lambda value: f"{value:,}".replace(",", "."))
    top = max((value for _, value in rows), default=0)

    bars: list[Bar] = []
    for label, value in rows:
        ratio = (value / top) if top else 0
        percent = round(ratio * 100)
        if value > 0:
            percent = max(percent, MIN_VISIBLE_PERCENT)
        bars.append(
            Bar(
                label=label,
                value=value,
                display=formatter(value),
                ratio=ratio,
                percent=percent,
            )
        )

    return BarChart(bars=bars)
