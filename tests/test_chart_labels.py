"""Chart text must stay readable at any card width.

Found in the design pass: chart text lived inside an SVG sized at
`width: 100%`, so it was multiplied by the viewBox scale. A label specified as
12px rendered near 17px on a wide dashboard and near 5px on a phone — an
accessibility failure, and outside the reach of the font-scale setting.

The fix was structural: no text inside the SVG at all. These tests hold that
line, since the failure is invisible in any assertion about the markup alone.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.chart_geometry import (
    DODGE,
    MAX_X_LABELS,
    build_bar_chart,
    build_line_chart,
)

CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css"
MONTHS = [f"m{i}" for i in range(12)]
SERIES = [("Criados", [0] * 11 + [5]), ("Edições", [0] * 11 + [5])]


class TestNoTextInsideTheSvg:
    def test_the_line_chart_renders_no_svg_text(self, app, client):
        html = client.get("/").get_data(as_text=True)
        svgs = re.findall(r"<svg\b.*?</svg>", html, re.S)

        assert svgs, "nenhum gráfico renderizado"
        for svg in svgs:
            assert "<text" not in svg, "texto dentro do SVG volta a escalar"

    def test_no_chart_specific_font_tokens_remain(self):
        """They encoded the false belief that px is immune to the viewBox."""
        base = (CSS / "base.css").read_text(encoding="utf-8")

        assert "--fs-chart" not in base


class TestXAxisAlignment:
    def test_one_cell_per_bucket(self):
        """The axis is a grid; a label must sit over the band it names."""
        chart = build_line_chart(MONTHS, SERIES)

        assert chart.columns == len(MONTHS)
        assert len(chart.x_labels) == len(MONTHS)

    def test_a_crowded_axis_is_thinned_not_resized(self):
        chart = build_line_chart([f"d{i}" for i in range(30)], [("s", [0] * 30)])
        shown = [label for label in chart.x_labels if label]

        assert len(shown) <= MAX_X_LABELS
        assert len(chart.x_labels) == 30, "as células em branco mantêm o alinhamento"

    def test_the_most_recent_bucket_is_always_named(self):
        """Whatever the stride, the reader must see where the series ends."""
        for count in (7, 12, 13, 30, 31):
            chart = build_line_chart(
                [f"b{i}" for i in range(count)], [("s", [0] * count)]
            )
            assert chart.x_labels[-1], f"último rótulo vazio com {count} colunas"

    def test_a_short_axis_names_every_bucket(self):
        chart = build_line_chart([f"d{i}" for i in range(7)], [("s", [0] * 7)])

        assert all(chart.x_labels)

    def test_every_column_count_has_a_grid_rule(self):
        """A missing .viz-cols-N rule collapses the axis to one column."""
        css = (CSS / "charts.css").read_text(encoding="utf-8")

        from app.services.reports_service import PERIODS

        for period in PERIODS.values():
            assert f".viz-cols-{period['count']} " in css, period


class TestPointsSitAtBandCentres:
    def test_points_are_centred_in_their_band(self):
        """A month is an interval, not an instant."""
        chart = build_line_chart(["a", "b"], [("s", [1, 2])])
        first, second = chart.series[0].points

        assert first.x > 0, "o primeiro ponto não fica colado na borda"
        assert second.x < chart.width
        # Two bands, so the centres are symmetric about the middle of the plot.
        assert abs((first.x + second.x) / 2 - chart.width / 2) < 1

    def test_bands_are_evenly_spaced(self):
        chart = build_line_chart(list("abcde"), [("s", [1, 2, 3, 4, 5])])
        xs = [p.x for p in chart.series[0].points]
        gaps = [round(b - a, 3) for a, b in zip(xs, xs[1:])]

        assert len(set(gaps)) == 1, gaps


class TestLegendCarriesTheValue:
    def test_both_series_report_their_latest_value(self):
        """Coincident lines used to hide one another completely; the legend
        states both numbers regardless of what the marks do."""
        chart = build_line_chart(MONTHS, SERIES)

        assert [s.last_value for s in chart.series] == [5, 5]

    def test_an_empty_series_reports_zero(self):
        chart = build_line_chart([], [("Criados", [])])

        assert chart.series[0].last_value == 0

    def test_the_second_series_is_dashed(self):
        """Identity must not rest on colour alone — and a dash is what lets a
        line underneath read through where two series coincide."""
        css = (CSS / "charts.css").read_text(encoding="utf-8")
        block = css.split(".viz-stroke-2")[1].split("}")[0]

        assert "stroke-dasharray" in block

    def test_the_legend_key_mirrors_the_dash(self):
        """A solid swatch for a dashed series is a legend that lies."""
        css = (CSS / "charts.css").read_text(encoding="utf-8")
        block = css.split(".viz-key.viz-series-2")[1].split("}")[0]

        assert "repeating-linear-gradient" in block


class TestOverlappingSeries:
    """Two series with the same values drew on the same pixels, and the upper
    one erased the lower one — the chart showed one line where there were two.
    """

    def test_an_exact_duplicate_is_separated(self):
        chart = build_line_chart(MONTHS, SERIES)
        first, second = chart.series

        assert [p.y for p in first.points] != [p.y for p in second.points]

    def test_the_separation_is_far_below_axis_resolution(self):
        """Visible as two lines, negligible as a value."""
        chart = build_line_chart(MONTHS, SERIES)
        first, second = chart.series
        drift = max(abs(a.y - b.y) for a, b in zip(first.points, second.points))

        assert 0 < drift <= DODGE
        assert drift < chart.height * 0.02

    def test_the_reported_values_are_untouched(self):
        """Only the drawing moves; every readout stays exact."""
        chart = build_line_chart(MONTHS, SERIES)

        for series in chart.series:
            assert [p.value for p in series.points] == [0] * 11 + [5]
            assert series.last_value == 5

    def test_series_that_differ_are_left_alone(self):
        """No nudge unless the lines genuinely coincide."""
        labels = list("abc")
        chart = build_line_chart(labels, [("A", [1, 2, 3]), ("B", [3, 2, 1])])
        first, second = chart.series

        assert first.points[1].y == second.points[1].y, "o cruzamento é real"

    def test_the_line_shape_is_preserved(self):
        """A whole-series shift, never a per-point one: nudging single points
        would put a kink in the line and misdescribe the trend."""
        chart = build_line_chart(MONTHS, SERIES)
        first, second = chart.series
        offsets = {round(a.y - b.y, 6) for a, b in zip(first.points, second.points)}

        assert len(offsets) == 1, offsets

    def test_a_third_identical_series_clears_both(self):
        values = [0, 4]
        chart = build_line_chart(
            ["a", "b"], [("A", values), ("B", values), ("C", values)]
        )
        tops = [series.points[-1].y for series in chart.series]

        assert len(set(tops)) == 3


class TestBarChartText:
    def test_bar_text_uses_the_shared_type_scale(self):
        css = (CSS / "charts.css").read_text(encoding="utf-8")

        for selector in (".viz-barlabel", ".viz-barvalue"):
            block = css.split(selector)[1].split("}")[0]
            assert "--fs-" in block, f"{selector} sem token de tipografia"

    def test_a_long_label_truncates_instead_of_starving_the_track(self):
        css = (CSS / "charts.css").read_text(encoding="utf-8")
        block = css.split(".viz-barlabel")[1].split("}")[0]

        assert "max-width" in block
        assert "text-overflow" in block

    def test_the_full_label_survives_truncation(self, app):
        """Truncated on screen, complete in the tooltip and the table."""
        long_title = "Arquitetura de conteúdo para a plataforma inteira"
        chart = build_bar_chart([(long_title, 10)])

        assert chart.bars[0].label == long_title
