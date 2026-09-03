"""Quando uma meta acontece, e quem é dono do estado de cada dia.

A expansão de ocorrências é a peça de aritmética mais carregada da jornada:
dela dependem a esteira, o plano, o histórico, a sequência e metade das
conquistas. Ela é uma função pura sobre datas, então é testada como uma.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.goal import STATUS_DONE, STATUS_PENDING
from app.repositories.goal_repository import GoalRepository
from app.services.exceptions import ValidationError
from app.services.goal_schedule import (
    MAX_WINDOW_DAYS,
    clamp_window,
    occurrence_dates,
    rows_between,
    rows_for_day,
    today,
)
from app.services.goal_service import GoalInput, GoalService
from app.services.progress_service import (
    XP_PER_COMPLETION,
    build_progress,
    current_streak,
    level_for,
    longest_streak,
)

# Uma segunda-feira, para que "dias úteis" e "fins de semana" tenham um
# ancoradouro que não muda com o dia em que a suíte roda.
MONDAY = date(2026, 3, 2)


@pytest.fixture()
def make_goal(app):
    def _make(title="Meta", **kwargs):
        return GoalService.create(GoalInput(title=title, **kwargs))

    return _make


class TestExpansion:
    def test_a_single_goal_falls_on_its_own_day_only(self, make_goal):
        goal = make_goal(date=today())

        assert occurrence_dates(goal, today(), today() + timedelta(days=7)) == [today()]
        assert occurrence_dates(goal, today() + timedelta(days=1), today() + timedelta(days=7)) == []

    def test_weekdays_skips_the_weekend(self, app, db, make_goal):
        goal = make_goal(recurrence_type="weekdays")
        goal.date = MONDAY
        db.session.commit()

        days = occurrence_dates(goal, MONDAY, MONDAY + timedelta(days=6))

        assert [day.weekday() for day in days] == [0, 1, 2, 3, 4]

    def test_weekends_keeps_only_the_weekend(self, app, db, make_goal):
        goal = make_goal(recurrence_type="weekends")
        goal.date = MONDAY
        db.session.commit()

        days = occurrence_dates(goal, MONDAY, MONDAY + timedelta(days=6))

        assert [day.weekday() for day in days] == [5, 6]

    def test_a_count_stops_after_the_number_of_days(self, app, db, make_goal):
        goal = make_goal(recurrence_type="count", recurrence_days=3)
        goal.date = MONDAY
        db.session.commit()

        days = occurrence_dates(goal, MONDAY, MONDAY + timedelta(days=30))

        assert days == [MONDAY, MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)]

    def test_forever_fills_the_window(self, app, db, make_goal):
        goal = make_goal(recurrence_type="forever")
        goal.date = MONDAY
        db.session.commit()

        days = occurrence_dates(goal, MONDAY, MONDAY + timedelta(days=9))

        assert len(days) == 10

    def test_an_end_date_closes_the_series(self, app, db, make_goal):
        goal = make_goal(
            recurrence_type="weekdays",
            date=MONDAY,
            recurrence_end_date=MONDAY + timedelta(days=2),
        )
        goal.date = MONDAY
        db.session.commit()

        days = occurrence_dates(goal, MONDAY, MONDAY + timedelta(days=30))

        assert days[-1] <= MONDAY + timedelta(days=2)

    def test_a_series_that_started_before_the_window_still_shows_up(self, make_goal):
        """O corte à esquerda vale para metas avulsas, nunca para séries."""
        make_goal(date=today() - timedelta(days=100), recurrence_type="forever")

        rows = rows_for_day(today())

        assert len(rows) == 1

    def test_a_window_larger_than_the_ceiling_is_trimmed(self):
        start = date(2026, 1, 1)
        trimmed_start, trimmed_end = clamp_window(start, start + timedelta(days=5000))

        assert (trimmed_end - trimmed_start).days == MAX_WINDOW_DAYS

    def test_an_inverted_window_is_put_back_in_order(self):
        start, end = clamp_window(date(2026, 3, 10), date(2026, 3, 1))

        assert start < end


class TestOccurrenceState:
    def test_completing_one_day_leaves_the_others_alone(self, make_goal):
        goal = make_goal(recurrence_type="forever")
        tomorrow = today() + timedelta(days=1)

        GoalService.set_status(goal, STATUS_DONE, today())

        rows = {row.date: row.status for row in rows_between(today(), tomorrow)}
        assert rows[today()] == STATUS_DONE
        assert rows[tomorrow] == STATUS_PENDING

    def test_only_the_exceptional_day_becomes_a_row(self, make_goal):
        goal = make_goal(recurrence_type="forever")

        GoalService.set_status(goal, STATUS_DONE, today())

        assert GoalRepository.occurrence(goal.id, today()) is not None
        assert GoalRepository.occurrence(goal.id, today() + timedelta(days=1)) is None

    def test_a_series_cannot_be_born_completed(self, app):
        """Marcar a série inteira concluiria terças que ainda não chegaram."""
        goal = GoalService.create(
            GoalInput(title="Correr", recurrence_type="forever", status=STATUS_DONE)
        )

        assert goal.status == STATUS_PENDING

    def test_turning_a_series_into_a_single_goal_drops_the_rules(self, make_goal):
        goal = make_goal(recurrence_type="count", recurrence_days=5)

        GoalService.update(goal, GoalInput(title="Correr", recurrence_type="none"))

        assert goal.recurrence_days is None
        assert goal.recurrence_end_date is None

    def test_a_count_without_a_number_is_refused(self, app):
        with pytest.raises(ValidationError):
            GoalService.create(GoalInput(title="Correr", recurrence_type="count"))

    def test_an_end_date_before_the_start_is_refused(self, app):
        with pytest.raises(ValidationError):
            GoalService.create(
                GoalInput(
                    title="Correr",
                    recurrence_type="weekdays",
                    date=today(),
                    recurrence_end_date=today() - timedelta(days=1),
                )
            )


class TestProgress:
    def test_each_completion_is_worth_its_xp(self, make_goal):
        goal = make_goal()
        GoalService.set_status(goal, STATUS_DONE, goal.date)

        assert build_progress().xp == XP_PER_COMPLETION

    def test_a_habit_kept_thirty_times_is_worth_thirty(self, make_goal):
        """Um hábito não vale um. Cada dia dele conta sozinho."""
        goal = make_goal(date=today() - timedelta(days=4), recurrence_type="forever")
        for offset in range(5):
            GoalService.set_status(goal, STATUS_DONE, today() - timedelta(days=offset))

        assert build_progress().completed == 5

    def test_the_level_costs_more_as_it_rises(self):
        assert level_for(0)[0] == 1
        assert level_for(150)[0] == 2
        # O nível 2 custa 300, e não 150 de novo.
        assert level_for(150 + 299)[0] == 2
        assert level_for(150 + 300)[0] == 3

    def test_the_streak_survives_a_day_not_yet_worked(self, app):
        """Hoje em branco não derruba a sequência - o dia ainda não acabou."""
        days = {today() - timedelta(days=1), today() - timedelta(days=2)}

        assert current_streak(days) == 2

    def test_the_streak_breaks_on_a_blank_day(self, app):
        days = {today() - timedelta(days=1), today() - timedelta(days=3)}

        assert current_streak(days) == 1

    def test_the_record_reads_the_longest_run_ever(self, app):
        base = date(2026, 1, 1)
        days = {base, base + timedelta(days=1), base + timedelta(days=2),
                base + timedelta(days=10)}

        assert longest_streak(days) == 3

    def test_a_goal_without_a_deadline_cannot_sustain_a_streak(self, make_goal):
        goal = make_goal(has_deadline=False)
        GoalService.set_status(goal, STATUS_DONE)

        assert build_progress().streak == 0
        assert build_progress().completed == 1
