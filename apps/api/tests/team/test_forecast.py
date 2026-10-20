"""Forecast (median of the last 4 same weekdays) and shift projection math."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.integrations.providers.square import fake_data as fd
from app.team.forecast import basis_dates, forecast_for, median_cents
from app.team.schedule import paid_hours, shift_by_weeks, shift_cost

NY = ZoneInfo("America/New_York")
FRI = date(2026, 3, 13)
TODAY = date(2026, 3, 10)  # Tuesday


def test_median() -> None:
    assert median_cents([4, 1, 3, 2]) == 3  # (2 + 3) / 2, half up
    assert median_cents([5, 1, 3]) == 3
    assert median_cents([2, 4]) == 3
    assert median_cents([7]) == 7
    with pytest.raises(ValueError):
        median_cents([])


def test_basis_is_the_last_four_same_weekdays_before_today() -> None:
    assert basis_dates(FRI, TODAY) == [
        date(2026, 3, 6),
        date(2026, 2, 27),
        date(2026, 2, 20),
        date(2026, 2, 13),
    ]
    # Today and later are never in the basis, even for today's own forecast.
    assert basis_dates(TODAY, TODAY)[0] == date(2026, 3, 3)
    # A past date is forecast from the weeks before it, not from its own actuals.
    assert basis_dates(date(2026, 3, 2), TODAY)[0] == date(2026, 2, 23)
    assert all(d < TODAY and d.weekday() == FRI.weekday() for d in basis_dates(FRI, TODAY))


def _sales(values: list[int]) -> dict[date, int]:
    return {d: v for d, v in zip(basis_dates(FRI, TODAY), values, strict=False)}


def test_forecast_points() -> None:
    four = forecast_for(FRI, TODAY, _sales([100, 400, 200, 300]), date(2025, 1, 1))
    assert (four.sales, len(four.basis), four.reason) == (250, 4, None)
    three = forecast_for(FRI, TODAY, _sales([100, 400, 200]), date(2026, 2, 20))
    assert (three.sales, len(three.basis)) == (200, 3)
    two = forecast_for(FRI, TODAY, _sales([100, 400]), date(2026, 2, 27))
    assert (two.sales, len(two.basis)) == (250, 2)
    one = forecast_for(FRI, TODAY, _sales([100]), date(2026, 3, 6))
    assert (one.sales, one.reason) == (None, "not_enough_history")
    none = forecast_for(FRI, TODAY, {}, None)
    assert (none.sales, none.reason) == (None, "not_enough_history")


def test_closed_days_count_as_zero() -> None:
    sales = _sales([500, 600])  # the two older dates have no rows but are inside history
    f = forecast_for(FRI, TODAY, sales, date(2025, 1, 1))
    assert f.sales == 250  # median of [0, 0, 500, 600]


def _sim_net(d: date) -> int:
    """Net sales of the simulated seller's day, proportional to orders (enough for the median)."""
    plan = fd.day_plan(d)
    return plan.total * 4000 + (480_000 if plan.private_event else 0)


@pytest.mark.parametrize(
    ("target", "today"),
    [
        (date(2026, 2, 7), date(2026, 2, 3)),  # snowstorm Saturday Jan 24 2026 is in the window
        (date(2026, 5, 26), date(2026, 5, 25)),  # the May 12 2026 buyout Tuesday is in the window
    ],
)
def test_one_off_days_move_the_forecast_at_most_the_middle_gap(target: date, today: date) -> None:
    basis = basis_dates(target, today)
    sales = {d: _sim_net(d) for d in basis}
    assert any(fd.is_snowstorm(d) or fd.is_private_event(d) for d in basis)
    f = forecast_for(target, today, sales, date(2025, 1, 1))
    normal = sorted(
        v for d, v in sales.items() if not (fd.is_snowstorm(d) or fd.is_private_event(d))
    )
    # The outlier lands at one end, so the median stays between the normal days' values.
    assert f.sales is not None and normal[0] <= f.sales <= normal[-1]
    mid = sorted(sales.values())
    assert abs(f.sales - median_cents(normal)) <= mid[2] - mid[1]


def _ny(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def test_paid_hours_breaks_midnight_and_dst() -> None:
    assert paid_hours(_ny(FRI, 16), _ny(FRI, 23), 30) == Decimal("6.5")
    assert paid_hours(_ny(FRI, 22), _ny(FRI, 22) + timedelta(hours=6), 0) == Decimal(
        6
    )  # crosses midnight
    fall = date(2026, 10, 31)  # clocks go back on Nov 1
    assert paid_hours(_ny(fall, 22), _ny(fall + timedelta(days=1), 6), 0) == Decimal(9)
    spring = date(2026, 3, 7)  # clocks go forward on Mar 8
    assert paid_hours(_ny(spring, 22), _ny(spring + timedelta(days=1), 6), 0) == Decimal(7)


def test_cost_and_missing_rate() -> None:
    assert shift_cost(Decimal("6.5"), 1600) == 10400
    assert shift_cost(Decimal("1") / 3, 100) == 33
    assert shift_cost(Decimal(8), None) is None


def test_copy_keeps_local_wall_time_across_dst() -> None:
    before = _ny(date(2026, 3, 2), 16)  # EST
    after = shift_by_weeks(before, 1, NY)  # Mar 9 is EDT
    assert after.astimezone(NY).hour == 16
    assert (after - before) == timedelta(days=7, hours=-1)
