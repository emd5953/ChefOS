"""Failed-login throttling: fixed 15-minute windows per email and per client IP.

A key that reaches its limit is refused (429) until its window ends, before the password is
checked. Unknown emails count the same as known ones, so the limit doesn't reveal accounts.
Callers pass an unscoped session (login runs before an org scope exists).
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ProblemError
from app.security.models import LoginThrottle

WINDOW = timedelta(minutes=15)
EMAIL_LIMIT = 5
# Higher than the email limit: an office or NAT can share one address.
IP_LIMIT = 30

_RECORD_FAILURE = text("""
INSERT INTO login_throttle (key, failures, window_started_at)
SELECT k, 1, :now FROM unnest(CAST(:keys AS text[])) AS k
ON CONFLICT (key) DO UPDATE SET
  failures = CASE WHEN login_throttle.window_started_at <= :cutoff THEN 1
                  ELSE login_throttle.failures + 1 END,
  window_started_at = CASE WHEN login_throttle.window_started_at <= :cutoff THEN :now
                           ELSE login_throttle.window_started_at END
""")


def keys(email: str, ip: str | None) -> dict[str, int]:
    """Throttle keys for one attempt, with their limits."""
    out = {f"email:{email}": EMAIL_LIMIT}
    if ip:
        out[f"ip:{ip}"] = IP_LIMIT
    return out


async def check(db: AsyncSession, limits: dict[str, int], now: datetime) -> None:
    """Raise 429 if any key has used up its window."""
    rows = await db.scalars(
        select(LoginThrottle).where(
            LoginThrottle.key.in_(limits), LoginThrottle.window_started_at > now - WINDOW
        )
    )
    blocked = [r for r in rows if r.failures >= limits[r.key]]
    if blocked:
        until = max(r.window_started_at for r in blocked) + WINDOW
        retry_after = max(1, math.ceil((until - now).total_seconds()))
        raise ProblemError(
            429,
            "rate_limited",
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def record_failure(db: AsyncSession, limits: dict[str, int], now: datetime) -> None:
    await db.execute(_RECORD_FAILURE, {"keys": list(limits), "now": now, "cutoff": now - WINDOW})


async def clear_email(db: AsyncSession, email: str) -> None:
    """A successful login resets the account's counter (not the IP's)."""
    await db.execute(delete(LoginThrottle).where(LoginThrottle.key == f"email:{email}"))
