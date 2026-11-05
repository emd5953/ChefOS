"""Business-date calculation (spec: local date of closed_at / clock_in, shifted by day start)."""

from datetime import date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=32)
def get_zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def business_date(ts: datetime, tz: str | tzinfo, day_start: time = time(4, 0)) -> date:
    """Local date of ``ts`` shifted so the business day begins at ``day_start``.

    Works on local wall-clock time, so it is DST-safe: with the default 04:00 start an
    instant at 01:30 local belongs to the previous date, including on the 23h/25h days.
    """
    if ts.tzinfo is None:
        raise ValueError("business_date requires an aware datetime")
    zone = get_zone(tz) if isinstance(tz, str) else tz
    local = ts.astimezone(zone)
    if local.time() < day_start:
        return local.date() - timedelta(days=1)
    return local.date()
