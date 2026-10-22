"""Display dates without platform-specific strftime flags."""

from datetime import date, datetime


def month_day(value: date, *, weekday: bool = False) -> str:
    prefix = value.strftime("%a %b" if weekday else "%b")
    return f"{prefix} {value.day}"


def local_timestamp(value: datetime) -> str:
    """Format an already-localized time, preserving midnight/noon and minute padding."""
    return f"{month_day(value)}, {value.hour % 12 or 12}:{value:%M %p}"
