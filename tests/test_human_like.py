"""Tests for pacing: delay shape, session limits and activity windows."""

from __future__ import annotations

import statistics
from datetime import datetime, time

import pytest

from src.config import Delays
from src.utils.human_like import HumanBehavior, SessionBudget, within_active_hours

SAMPLES = 4000


@pytest.fixture
def behaviour():
    # No long breaks here; they are exercised separately.
    return HumanBehavior(Delays(min_seconds=1.5, max_seconds=3.5, long_pause_chance=0.0))


def draw(behaviour, count=SAMPLES):
    return [behaviour.delay() for _ in range(count)]


class TestDelayShape:
    def test_most_pauses_land_inside_the_configured_range(self, behaviour):
        sample = draw(behaviour)
        inside = [d for d in sample if 1.5 <= d <= 3.5]
        # The bounds are the 10th and 90th percentiles, so ~80% should fall in.
        assert 0.7 <= len(inside) / len(sample) <= 0.9

    def test_the_median_sits_between_the_bounds(self, behaviour):
        assert 1.5 < statistics.median(draw(behaviour)) < 3.5

    def test_produces_a_long_tail(self, behaviour):
        # A uniform draw can never exceed its maximum; a log-normal must.
        assert max(draw(behaviour)) > 3.5

    def test_spread_is_wider_than_a_uniform_draw(self, behaviour):
        # Uniform(1.5, 3.5) has a standard deviation of about 0.58. The real
        # run's 0.94 s was flagged as too metronomic, so beat it.
        assert statistics.stdev(draw(behaviour)) > 0.58

    def test_never_returns_a_negative_pause(self, behaviour):
        assert min(draw(behaviour)) > 0

    def test_respects_the_floor(self, behaviour):
        assert min(draw(behaviour)) >= 0.4


class TestLongPauses:
    def test_breaks_occur_at_roughly_the_configured_rate(self):
        behaviour = HumanBehavior(
            Delays(
                min_seconds=1.0,
                max_seconds=2.0,
                long_pause_chance=0.1,
                long_pause_min=30,
                long_pause_max=60,
            )
        )
        sample = draw(behaviour)
        long_ones = [d for d in sample if d > 25]
        assert 0.06 <= len(long_ones) / len(sample) <= 0.15

    def test_no_breaks_when_disabled(self):
        behaviour = HumanBehavior(Delays(min_seconds=1.0, max_seconds=2.0, long_pause_chance=0.0))
        assert max(draw(behaviour, 500)) < 25


class TestDegenerateRanges:
    def test_zero_delays_stay_zero(self):
        # Tests configure this to keep the suite fast.
        behaviour = HumanBehavior(Delays(0, 0, 0, 0, long_pause_chance=0.0))
        assert behaviour.delay() == 0.0

    def test_a_single_point_range_is_handled(self):
        behaviour = HumanBehavior(Delays(min_seconds=2, max_seconds=2, long_pause_chance=0.0))
        assert behaviour.delay() > 0


class TestSessionBudget:
    def test_unlimited_never_expires(self):
        budget = SessionBudget(0)
        assert budget.unlimited is True
        assert budget.expired() is False

    def test_a_fresh_budget_has_not_expired(self):
        assert SessionBudget(30).expired() is False

    def test_expires_once_the_time_is_used(self):
        budget = SessionBudget(30)
        budget.started -= 31 * 60  # pretend 31 minutes passed
        assert budget.expired() is True

    def test_reports_elapsed_time(self):
        budget = SessionBudget(30)
        budget.started -= 5 * 60
        assert 4.9 < budget.elapsed_minutes() < 5.1


class TestActiveHours:
    def test_no_window_always_allows(self):
        assert within_active_hours(None) is True

    @pytest.mark.parametrize(
        "now, expected",
        [("08:59", False), ("09:00", True), ("15:00", True), ("23:29", True), ("23:30", False)],
    )
    def test_daytime_window(self, now, expected):
        window = (time(9, 0), time(23, 30))
        moment = datetime.strptime(f"2026-08-18 {now}", "%Y-%m-%d %H:%M")
        assert within_active_hours(window, moment) is expected

    @pytest.mark.parametrize(
        "now, expected",
        [("21:59", False), ("22:00", True), ("23:59", True), ("01:00", True), ("02:00", False)],
    )
    def test_window_spanning_midnight(self, now, expected):
        window = (time(22, 0), time(2, 0))
        moment = datetime.strptime(f"2026-08-18 {now}", "%Y-%m-%d %H:%M")
        assert within_active_hours(window, moment) is expected
