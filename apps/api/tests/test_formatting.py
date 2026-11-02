from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.formatting import local_timestamp, month_day


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-03-06T05:03:00", "Mar 6, 12:03 AM"),
        ("2026-03-06T17:09:00", "Mar 6, 12:09 PM"),
        ("2026-03-06T18:05:00", "Mar 6, 1:05 PM"),
        ("2026-03-08T06:59:00", "Mar 8, 1:59 AM"),
        ("2026-03-08T07:00:00", "Mar 8, 3:00 AM"),
        ("2026-11-01T06:05:00", "Nov 1, 1:05 AM"),
        ("2026-10-23T03:07:00", "Oct 22, 11:07 PM"),
    ],
)
def test_local_timestamps_preserve_clock_and_unpadded_dates(instant: str, expected: str) -> None:
    value = datetime.fromisoformat(instant).replace(tzinfo=UTC)
    assert local_timestamp(value.astimezone(ZoneInfo("America/New_York"))) == expected


def test_notification_dates_preserve_weekday_and_month_boundaries() -> None:
    assert month_day(date(2026, 3, 6), weekday=True) == "Fri Mar 6"
    assert month_day(date(2026, 12, 31)) == "Dec 31"
    assert month_day(date(2027, 1, 1)) == "Jan 1"
