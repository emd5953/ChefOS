from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.integrations.providers.square import business_date

NY = ZoneInfo("America/New_York")


def test_cross_midnight_belongs_to_previous_day() -> None:
    ts = datetime(2026, 3, 11, 1, 30, tzinfo=NY)
    assert business_date(ts, "America/New_York") == date(2026, 3, 10)
    assert business_date(datetime(2026, 3, 11, 4, 0, tzinfo=NY), NY) == date(2026, 3, 11)
    assert business_date(datetime(2026, 3, 11, 3, 59, tzinfo=NY), NY) == date(2026, 3, 10)


def test_utc_input_uses_local_date() -> None:
    # 03:30Z on Jan 5 is 22:30 EST on Jan 4
    assert business_date(datetime(2026, 1, 5, 3, 30, tzinfo=UTC), "America/New_York") == date(
        2026, 1, 4
    )


def test_spring_forward_dst() -> None:
    # 2026-03-08: 02:00 EST jumps to 03:00 EDT. 06:59Z = 01:59 EST, 07:00Z = 03:00 EDT.
    assert business_date(datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "America/New_York") == date(
        2026, 3, 7
    )
    assert business_date(datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "America/New_York") == date(
        2026, 3, 7
    )
    # 07:59Z = 03:59 EDT (still before 04:00), 08:00Z = 04:00 EDT
    assert business_date(datetime(2026, 3, 8, 7, 59, tzinfo=UTC), "America/New_York") == date(
        2026, 3, 7
    )
    assert business_date(datetime(2026, 3, 8, 8, 0, tzinfo=UTC), "America/New_York") == date(
        2026, 3, 8
    )


def test_fall_back_dst_both_1_30_belong_to_previous_day() -> None:
    # 2026-11-01: 01:30 happens twice (EDT then EST)
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST
    assert business_date(first, "America/New_York") == date(2026, 10, 31)
    assert business_date(second, "America/New_York") == date(2026, 10, 31)
    # 04:00 EST = 09:00Z starts the new business day
    assert business_date(datetime(2026, 11, 1, 8, 59, tzinfo=UTC), "America/New_York") == date(
        2026, 10, 31
    )
    assert business_date(datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "America/New_York") == date(
        2026, 11, 1
    )


def test_custom_day_start_and_midnight() -> None:
    ts = datetime(2026, 5, 2, 0, 30, tzinfo=NY)
    assert business_date(ts, NY, time(0, 0)) == date(2026, 5, 2)
    assert business_date(ts, NY, time(6, 0)) == date(2026, 5, 1)


def test_naive_rejected() -> None:
    with pytest.raises(ValueError):
        business_date(datetime(2026, 1, 1, 12), "America/New_York")
