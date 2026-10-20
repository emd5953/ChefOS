"""SquareClient protocol, typed exceptions and shared page helpers.

All methods return raw Square API v2 shaped dicts (Money objects, RFC 3339 timestamps).
"""

from datetime import date, datetime
from typing import Any, Protocol

from app.integrations.providers.square.types import Credentials, Page, RawItem, parse_ts


class SquareError(Exception):
    """Base class for all Square client errors."""


class SquareAuthError(SquareError):
    """401/403: token invalid, expired, revoked, or missing scope."""


class SquareRateLimited(SquareError):
    """429. ``retry_after`` is seconds if Square sent a Retry-After header."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SquareTransientError(SquareError):
    """5xx, timeouts, connection errors: safe to retry."""


class SquareClientError(SquareError):
    """Other 4xx: the request is wrong; do not retry."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class SquareClient(Protocol):
    def authorize_url(self, state: str, redirect_uri: str, scopes: list[str]) -> str: ...

    def obtain_token(self, code: str, redirect_uri: str) -> Credentials: ...

    def refresh_token(self, creds: Credentials) -> Credentials: ...

    def revoke(self, creds: Credentials) -> None: ...

    def list_locations(self, creds: Credentials) -> list[dict[str, Any]]: ...

    def search_catalog(
        self, creds: Credentials, begin_time: datetime | None, cursor: str | None
    ) -> Page: ...

    def search_orders(
        self,
        creds: Credentials,
        location_ids: list[str],
        updated_from: datetime,
        updated_to: datetime,
        cursor: str | None,
        limit: int = 500,
    ) -> Page: ...

    def batch_retrieve_orders(
        self, creds: Credentials, location_id: str, order_ids: list[str]
    ) -> list[dict[str, Any]]: ...

    def search_team_members(
        self, creds: Credentials, location_ids: list[str], cursor: str | None
    ) -> Page: ...

    def search_timecards(
        self,
        creds: Credentials,
        location_ids: list[str],
        workday_from: date,
        workday_to: date,
        cursor: str | None,
    ) -> Page: ...


def build_page(objects: list[dict[str, Any]], next_cursor: str | None) -> Page:
    """Wrap raw Square objects as RawItems (id, updated_at, version) plus a Page."""
    items: list[RawItem] = []
    max_updated: datetime | None = None
    for obj in objects:
        raw_updated = obj.get("updated_at")
        updated = parse_ts(raw_updated) if isinstance(raw_updated, str) else None
        version = obj.get("version")
        items.append(
            RawItem(
                external_id=str(obj["id"]),
                updated_at=updated,
                version=None if version is None else str(version),
                payload=obj,
            )
        )
        if updated is not None and (max_updated is None or updated > max_updated):
            max_updated = updated
    return Page(items=items, next_cursor=next_cursor or None, max_updated_at=max_updated)
