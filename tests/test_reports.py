"""Reports dashboard: aggregation, chart geometry and rendering."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.extensions import db
from app.repositories.taxonomy_repository import CategoryRepository
from app.services.chart_geometry import (
    MIN_VISIBLE_PERCENT,
    build_bar_chart,
    build_line_chart,
)
from app.services.document_service import DocumentService
from app.services.reports_service import (
    activity_report,
    build_summary,
    longest_documents,
    words_by_category,
)


class TestAggregation:
    def test_activity_covers_twelve_months_ending_now(self, app):
        report = activity_report()
        assert len(report.labels) == 12
        assert len(report.created) == 12
        assert len(report.edited) == 12
        # Months run oldest to newest.
        assert report.labels[-1].split("/")[1] >= report.labels[0].split("/")[1]

    def test_new_documents_land_in_the_current_month(self, app, make_document):
        make_document(title="Agora")
        report = activity_report()
        assert report.created[-1] >= 1
        assert report.total_created >= 1

    def test_edits_are_counted_from_versions(self, app, document):
        before = activity_report().edited[-1]
        DocumentService.save(document, document.title, "Conteúdo bem diferente.")
        assert activity_report().edited[-1] == before + 1

    def test_trashed_documents_leave_the_report(self, app, make_document):
        document = make_document(title="Some do relatório")
        assert activity_report().total_created >= 1

        DocumentService.move_to_trash(document)
        assert activity_report().created[-1] == 0

    def test_words_by_category_groups_and_ranks(self, app, make_document):
        guias = CategoryRepository.get_or_create("Guias")
        notas = CategoryRepository.get_or_create("Notas")
        db.session.commit()

        make_document(title="A", content="palavra " * 100, category_id=guias.id)
        make_document(title="B", content="palavra " * 40, category_id=guias.id)
        make_document(title="C", content="palavra " * 10, category_id=notas.id)

        rows = words_by_category()
        assert rows[0][0] == "Guias"
        assert rows[0][1] == 140
        assert rows[1] == ("Notas", 10)

    def test_uncategorised_documents_are_labelled(self, app, make_document):
        make_document(title="Solto", content="palavra " * 5)
        assert ("Sem categoria", 5) in words_by_category()

    def test_longest_documents_are_ordered(self, app, make_document):
        make_document(title="Curto", content="palavra " * 5)
        make_document(title="Longo", content="palavra " * 200)

        rows = longest_documents()
        assert rows[0] == ("Longo", 200)

    def test_empty_library_produces_an_empty_summary(self, app):
        summary = build_summary()
        assert summary.has_any_data is False
        assert summary.average_words == 0
        assert summary.busiest_month is None

    def test_summary_query_count_is_flat(self, app, make_document):
        """Reports must not scale queries with the library."""
        from tests.test_performance import QueryCounter

        for index in range(4):
            make_document(title=f"Poucos {index}", content="palavra " * 20)
        db.session.expire_all()
        with QueryCounter() as small:
            build_summary()

        for index in range(30):
            make_document(title=f"Muitos {index}", content="palavra " * 20)
        db.session.expire_all()
        with QueryCounter() as large:
            build_summary()

        assert large.count == small.count, (
            f"consultas subiram de {small.count} para {large.count}"
        )


class TestLineGeometry:
    def test_axis_top_is_even_so_the_midpoint_tick_is_whole(self, app):
        """A 2.5 gridline labelled '2' is a lie about where the line sits."""
        for peak in range(1, 60):
            chart = build_line_chart(["a", "b"], [("s", [0, peak])])
            top = chart.max_value or 4
            assert top % 2 == 0, f"topo ímpar para pico {peak}: {top}"

    def test_tick_labels_match_their_own_positions(self, app):
        chart = build_line_chart(["a", "b", "c"], [("s", [0, 3, 7])])
        values = [int(label.replace(".", "")) for _, label in chart.y_ticks]

        assert values[0] == 0
        assert values[1] * 2 == values[2]
        # Higher value -> smaller y (SVG grows downward).
        ys = [y for y, _ in chart.y_ticks]
        assert ys[0] > ys[1] > ys[2]

    def test_series_share_one_axis(self, app):
        """Two series of the same unit are never given two scales."""
        chart = build_line_chart(
            ["a", "b"], [("um", [0, 10]), ("dois", [0, 5])]
        )
        first, second = chart.series
        # Half the value must sit at half the height on the same scale.
        span_first = first.points[0].y - first.points[1].y
        span_second = second.points[0].y - second.points[1].y
        assert abs(span_first / 2 - span_second) < 0.5

    def test_labels_are_thinned_when_crowded(self, app):
        """Thinned by blanking, not by dropping: the axis is a grid of one
        cell per bucket, so a shorter list would respace every label onto the
        wrong month."""
        labels = [f"m{i}" for i in range(12)]
        chart = build_line_chart(labels, [("s", [1] * 12)])

        assert len(chart.x_labels) == 12
        assert len([label for label in chart.x_labels if label]) < 12
        # The most recent month is always labelled.
        assert chart.x_labels[-1] == "m11"

    def test_all_zero_data_reports_empty(self, app):
        chart = build_line_chart(["a", "b"], [("s", [0, 0])])
        assert chart.is_empty is True

    def test_area_path_closes_on_the_baseline(self, app):
        chart = build_line_chart(["a", "b"], [("s", [1, 2])])
        assert chart.series[0].area_path.endswith("Z")


class TestBarGeometry:
    def test_bars_are_capped_and_proportional(self, app):
        chart = build_bar_chart([("A", 100), ("B", 50), ("C", 0)])

        assert len(chart.bars) == 3
        assert chart.bars[0].ratio == 1.0
        assert chart.bars[1].ratio == 0.5
        assert chart.bars[2].ratio == 0

    def test_percent_tracks_the_longest_bar(self, app):
        chart = build_bar_chart([("A", 200), ("B", 50)])

        assert chart.bars[0].percent == 100
        assert chart.bars[1].percent == 25

    def test_a_tiny_value_stays_visible(self, app):
        """1 next to 10.000 must still read as "a little", not as nothing."""
        chart = build_bar_chart([("Enorme", 10_000), ("Mínimo", 1)])

        assert chart.bars[1].percent >= MIN_VISIBLE_PERCENT
        assert chart.bars[1].percent > 0

    def test_zero_is_the_only_empty_bar(self, app):
        """Zero genuinely means zero — the floor must not lift it off the axis."""
        chart = build_bar_chart([("Cheio", 10), ("Vazio", 0)])

        assert chart.bars[1].percent == 0

    def test_percent_is_a_whole_number(self, app):
        """It becomes a class name; a float would match no rule at all."""
        chart = build_bar_chart([("A", 7), ("B", 3), ("C", 1)])

        for bar in chart.bars:
            assert isinstance(bar.percent, int)
            assert 0 <= bar.percent <= 100

    def test_every_percent_has_a_matching_css_rule(self, app):
        """The width arrives by class because the CSP forbids inline styles.
        A generated class with no rule is an invisible bar."""
        css = (
            Path(__file__).resolve().parents[1]
            / "app" / "static" / "css" / "charts.css"
        ).read_text(encoding="utf-8")

        chart = build_bar_chart([("A", 9), ("B", 5), ("C", 1), ("D", 0)])
        for bar in chart.bars:
            assert f".viz-w-{bar.percent} {{" in css, f"falta .viz-w-{bar.percent}"

    def test_values_are_formatted_for_pt_br(self, app):
        chart = build_bar_chart([("Grande", 12345)])
        assert chart.bars[0].display == "12.345"

    def test_empty_input_is_empty(self, app):
        assert build_bar_chart([]).is_empty is True


class TestRendering:
    def test_dashboard_renders_the_reports(self, client, make_document):
        make_document(title="Com dados", content="palavra " * 50)
        response = client.get("/")

        assert response.status_code == 200
        body = response.data.decode("utf-8")
        assert "Relatórios" in body
        assert "Atividade — últimos 12 meses" in body
        assert "<svg" in body

    def test_the_document_starts_with_the_doctype(self, client):
        """A byte-order mark before <!DOCTYPE> drops the page into quirks mode."""
        body = client.get("/").data.decode("utf-8")
        assert body.startswith("<!DOCTYPE html>"), repr(body[:20])

    def test_charts_carry_no_inline_styles(self, client, make_document):
        """The CSP forbids them; colour must come from classes."""
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")

        assert " style=" not in body
        # Series colour is applied through a class, never a fill literal.
        assert "viz-stroke-1" in body
        assert not re.search(r'fill="#', body)

    def test_every_chart_has_a_table_view(self, client, make_document):
        """No value may be reachable only by hovering."""
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")

        assert body.count("Ver os dados em tabela") >= 3

    def test_the_activity_chart_plots_one_series(self, client, make_document):
        """Two lines that tracked each other made the chart harder to read,
        not richer. Edits are still counted for the summary sentence."""
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")

        assert 'class="viz-legend"' in body
        assert "Documentos criados" in body
        assert body.count('class="viz-legend-item"') == 1

    def test_the_legend_carries_the_latest_value(self, client, make_document):
        """The direct label. It cannot live inside the SVG — it would scale."""
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")

        assert 'class="viz-legend-value tabular"' in body

    def test_marks_expose_accessible_titles(self, client, make_document):
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")
        assert "<title>" in body

    def test_dashboard_without_data_omits_the_reports(self, client, app):
        body = client.get("/").data.decode("utf-8")
        assert "Atividade —" not in body


class TestPeriodFilter:
    """One filter row scoping the whole reports section."""

    @pytest.mark.parametrize("period", ["7d", "30d", "12s", "6m", "12m"])
    def test_every_preset_renders(self, client, make_document, period):
        make_document(title="Com dados", content="palavra " * 50)
        response = client.get(f"/?periodo={period}")
        assert response.status_code == 200

    def test_bucket_count_matches_the_preset(self, app, make_document):
        from app.services.reports_service import PERIODS, activity_report

        for key, config in PERIODS.items():
            report = activity_report(key)
            assert len(report.labels) == config["count"], key
            assert len(report.created) == config["count"], key

    def test_an_unknown_period_falls_back(self, app):
        from app.services.reports_service import DEFAULT_PERIOD, resolve_period

        for bogus in ["../../etc", "999y", "", None, "<script>"]:
            assert resolve_period(bogus) == DEFAULT_PERIOD

    def test_daily_buckets_label_by_day(self, app, make_document):
        from app.services.reports_service import activity_report

        make_document(title="Hoje", content="palavra")
        report = activity_report("7d")

        assert len(report.labels) == 7
        assert re.fullmatch(r"\d{2}/\d{2}", report.labels[-1]), report.labels[-1]
        # Today's document lands in the last bucket.
        assert report.created[-1] >= 1

    def test_weekly_buckets_label_by_monday(self, app, make_document):
        from app.services.reports_service import activity_report

        make_document(title="Esta semana", content="palavra")
        report = activity_report("12s")

        assert len(report.labels) == 12
        assert re.fullmatch(r"\d{2}/\d{2}", report.labels[-1])
        assert report.created[-1] >= 1

    def test_the_active_preset_is_marked(self, client, make_document):
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/?periodo=7d").data.decode("utf-8")

        active = re.search(r'<a class="chip is-active"[^>]*>\s*([^<]+?)\s*</a>', body)
        assert active, "nenhum período marcado como ativo"
        assert active.group(1) == "7 dias"
        assert 'aria-current="true"' in body

    def test_the_filter_sits_outside_the_chart_cards(self, client, make_document):
        """A per-chart filter is the anti-pattern; one row scopes them all."""
        make_document(title="Com dados", content="palavra " * 50)
        body = client.get("/").data.decode("utf-8")

        filter_at = body.index('class="period-filter"')
        first_card = body.index('class="card card-pad section-gap"')
        assert filter_at < first_card

    def test_the_aggregation_is_bounded_by_the_window(self, app, make_document):
        """Found in QA: showing seven days grouped the entire history.

        Without a lower bound the shortest period was the slowest — 30 ms
        against 12k rows, versus 8 ms once the scan is limited to the window
        actually on screen.
        """
        from tests.test_performance import QueryCounter

        make_document(title="Recente", content="palavra")
        db.session.expire_all()

        with QueryCounter() as counter:
            build_summary("7d")

        aggregations = [
            s for s in counter.statements
            if "strftime" in s and "GROUP BY" in s.upper()
        ]
        assert aggregations, "nenhuma agregação encontrada"
        for statement in aggregations:
            assert "created_at >=" in statement.replace("\n", " "), statement[:160]

    def test_the_window_start_matches_the_first_bucket(self, app):
        from app.services.reports_service import PERIODS, _period_keys, _window_start

        for key, config in PERIODS.items():
            start = _window_start(key)
            first_bucket = _period_keys(key)[0][0]

            if config["unit"] == "day":
                assert start.strftime("%Y-%m-%d") == first_bucket, key
            elif config["unit"] == "month":
                assert start.strftime("%Y-%m") == first_bucket, key
            else:
                assert start.strftime("%Y-%W") == first_bucket, key

    def test_changing_the_period_changes_the_buckets(self, client, make_document):
        make_document(title="Com dados", content="palavra " * 50)

        weekly = client.get("/?periodo=12s").data.decode("utf-8")
        monthly = client.get("/?periodo=12m").data.decode("utf-8")

        assert "últimos 12 semanas" in weekly
        assert "últimos 12 meses" in monthly

    def test_pluralisation_of_the_summary_line(self, client, app, make_document, document):
        """A broken plural read 'ediçãoões' on the live page."""
        DocumentService.save(document, document.title, "Texto novo para gerar versão.")
        body = client.get("/").data.decode("utf-8")

        assert "ediçãoões" not in body
        assert "edições salvas" in body or "edição salva" in body

    @pytest.mark.parametrize("path", ["/static/css/charts.css"])
    def test_chart_stylesheet_is_served(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert "--viz-series-1" in response.data.decode("utf-8")
