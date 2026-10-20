"""SquareConnector: adapts a SquareClient (fake or HTTP) to the integration layer's Connector.

The connector owns `fetch` and webhook handling. Normalization is the pure functions in
`normalizer.py`, called by the shared pipeline.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.providers.square.business_date import get_zone
from app.integrations.providers.square.client import SquareClient
from app.integrations.providers.square.normalizer import NORMALIZER_VERSION
from app.integrations.providers.square.types import (
    Credentials,
    Page,
    RawItem,
    WebhookEvent,
    Window,
    parse_ts,
)
from app.integrations.providers.square.webhooks import (
    SIGNATURE_HEADER,
    parse_event,
    verify_signature,
)

SCOPES = [
    "MERCHANT_PROFILE_READ",
    "ITEMS_READ",
    "ORDERS_READ",
    "EMPLOYEES_READ",
    "TIMECARDS_READ",
]
STREAMS = ("locations", "catalog", "orders", "team_members", "timecards")
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
_WEBHOOK_STREAMS = {
    "order": "orders",
    "catalog": "catalog",
    "team_member": "team_members",
    "labor": "timecards",
    "timecard": "timecards",
}


def _wrap(obj: Mapping[str, Any]) -> RawItem:
    updated = obj.get("updated_at")
    version = obj.get("version")
    return RawItem(
        external_id=str(obj["id"]),
        updated_at=parse_ts(updated) if isinstance(updated, str) else None,
        version=None if version is None else str(version),
        payload=dict(obj),
    )


class SquareConnector:
    provider = "square"
    streams = STREAMS
    normalizer_version = NORMALIZER_VERSION

    def __init__(self, client: SquareClient, *, mode: str, signature_key: str = "") -> None:
        self.client = client
        self.mode = mode
        self._signature_key = signature_key

    # --- OAuth
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return self.client.authorize_url(state, redirect_uri, SCOPES)

    def exchange_code(self, code: str, redirect_uri: str) -> Credentials:
        return self.client.obtain_token(code, redirect_uri)

    def refresh(self, creds: Credentials) -> Credentials:
        return self.client.refresh_token(creds)

    def revoke(self, creds: Credentials) -> None:
        self.client.revoke(creds)

    def list_locations(self, creds: Credentials) -> list[RawItem]:
        return [_wrap(loc) for loc in self.client.list_locations(creds)]

    # --- fetch
    def fetch_page(
        self,
        creds: Credentials,
        stream: str,
        window: Window,
        cursor: str | None,
        location_ids: list[str],
        *,
        tz: str = "America/New_York",
    ) -> Page:
        if stream == "locations":
            items = self.list_locations(creds)
            return Page(items, None, None)
        if stream == "catalog":
            return self.client.search_catalog(creds, _EPOCH, cursor)
        if stream == "team_members":
            return self.client.search_team_members(creds, location_ids, cursor)
        if stream == "orders":
            return self.client.search_orders(
                creds, location_ids, window.start, window.end, cursor, 500
            )
        if stream == "timecards":
            zone = get_zone(tz)
            return self.client.search_timecards(
                creds,
                location_ids,
                window.start.astimezone(zone).date(),
                (window.end - timedelta(microseconds=1)).astimezone(zone).date(),
                cursor,
            )
        raise ValueError(f"unknown stream {stream!r}")

    def fetch_by_ids(
        self, creds: Credentials, stream: str, ids: list[str], location_ids: list[str]
    ) -> list[RawItem]:
        if stream != "orders":
            return []
        out: list[RawItem] = []
        for loc in location_ids:
            out.extend(_wrap(o) for o in self.client.batch_retrieve_orders(creds, loc, ids))
        return out

    # --- webhooks
    def verify_webhook(self, headers: Mapping[str, str], body: bytes, url: str) -> bool:
        lowered = {k.lower(): v for k, v in headers.items()}
        return bool(self._signature_key) and verify_signature(
            self._signature_key, url, body, lowered.get(SIGNATURE_HEADER)
        )

    def parse_webhook(self, body: bytes) -> WebhookEvent:
        return parse_event(body)

    @staticmethod
    def stream_for_event(event: WebhookEvent) -> str | None:
        head = event.type.split(".", 1)[0]
        return _WEBHOOK_STREAMS.get(head)
