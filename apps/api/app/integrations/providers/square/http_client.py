"""HttpSquareClient: the real Square sandbox (or production via base_url) over httpx."""

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.providers.square.client import (
    SquareAuthError,
    SquareClientError,
    SquareRateLimited,
    SquareTransientError,
    build_page,
)
from app.integrations.providers.square.types import Credentials, Page, format_ts, parse_ts

SANDBOX_BASE_URL = "https://connect.squareupsandbox.com"
DEFAULT_SQUARE_VERSION = "2025-10-16"


class HttpSquareClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = SANDBOX_BASE_URL,
        square_version: str = DEFAULT_SQUARE_VERSION,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._version = square_version
        self._http = httpx.Client(base_url=self._base_url, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HttpSquareClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ plumbing

    def _request(
        self,
        method: str,
        path: str,
        *,
        creds: Credentials | None = None,
        body: dict[str, Any] | None = None,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Square-Version": self._version, "Content-Type": "application/json"}
        if creds is not None:
            headers["Authorization"] = f"Bearer {creds.access_token}"
        elif auth_header is not None:
            headers["Authorization"] = auth_header
        try:
            resp = self._http.request(method, path, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SquareTransientError(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            self._raise_for(resp)
        if not resp.content:
            return {}
        data = resp.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        status = resp.status_code
        detail = ""
        try:
            errors = resp.json().get("errors") or []
            detail = "; ".join(f"{e.get('code')}: {e.get('detail')}" for e in errors)
        except ValueError, AttributeError:
            detail = resp.text[:200]
        message = f"Square {status}: {detail}".strip()
        if status in (401, 403):
            raise SquareAuthError(message)
        if status == 429:
            retry: float | None
            try:
                retry = float(resp.headers["Retry-After"])
            except KeyError, ValueError:
                retry = None
            raise SquareRateLimited(message, retry_after=retry)
        if status >= 500:
            raise SquareTransientError(message)
        raise SquareClientError(message, status_code=status)

    @staticmethod
    def _creds_from(data: dict[str, Any], fallback_refresh: str = "") -> Credentials:
        return Credentials(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token") or fallback_refresh),
            expires_at=parse_ts(str(data["expires_at"])),
            merchant_id=str(data["merchant_id"]),
        )

    # ------------------------------------------------------------ OAuth

    def authorize_url(self, state: str, redirect_uri: str, scopes: list[str]) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "scope": " ".join(scopes),
                "session": "false",
                "state": state,
                "redirect_uri": redirect_uri,
            }
        )
        return f"{self._base_url}/oauth2/authorize?{query}"

    def obtain_token(self, code: str, redirect_uri: str) -> Credentials:
        data = self._request(
            "POST",
            "/oauth2/token",
            body={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        return self._creds_from(data)

    def refresh_token(self, creds: Credentials) -> Credentials:
        data = self._request(
            "POST",
            "/oauth2/token",
            body={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": creds.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        data.setdefault("merchant_id", creds.merchant_id)
        return self._creds_from(data, fallback_refresh=creds.refresh_token)

    def revoke(self, creds: Credentials) -> None:
        self._request(
            "POST",
            "/oauth2/revoke",
            body={"client_id": self._client_id, "access_token": creds.access_token},
            auth_header=f"Client {self._client_secret}",
        )

    # ------------------------------------------------------------ data

    def list_locations(self, creds: Credentials) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/locations", creds=creds)
        return list(data.get("locations") or [])

    def search_catalog(
        self, creds: Credentials, begin_time: datetime | None, cursor: str | None
    ) -> Page:
        body: dict[str, Any] = {"object_types": ["ITEM", "CATEGORY"]}
        if begin_time is not None:
            body["begin_time"] = format_ts(begin_time)
            body["include_deleted_objects"] = True
        if cursor:
            body["cursor"] = cursor
        data = self._request("POST", "/v2/catalog/search", creds=creds, body=body)
        return build_page(list(data.get("objects") or []), data.get("cursor"))

    def search_orders(
        self,
        creds: Credentials,
        location_ids: list[str],
        updated_from: datetime,
        updated_to: datetime,
        cursor: str | None,
        limit: int = 500,
    ) -> Page:
        body: dict[str, Any] = {
            "location_ids": location_ids,
            "limit": limit,
            "query": {
                "filter": {
                    "state_filter": {"states": ["COMPLETED"]},
                    "date_time_filter": {
                        "updated_at": {
                            "start_at": format_ts(updated_from),
                            "end_at": format_ts(updated_to),
                        }
                    },
                },
                "sort": {"sort_field": "UPDATED_AT", "sort_order": "ASC"},
            },
        }
        if cursor:
            body["cursor"] = cursor
        data = self._request("POST", "/v2/orders/search", creds=creds, body=body)
        return build_page(list(data.get("orders") or []), data.get("cursor"))

    def batch_retrieve_orders(
        self, creds: Credentials, location_id: str, order_ids: list[str]
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            "/v2/orders/batch-retrieve",
            creds=creds,
            body={"location_id": location_id, "order_ids": order_ids},
        )
        return list(data.get("orders") or [])

    def search_team_members(
        self, creds: Credentials, location_ids: list[str], cursor: str | None
    ) -> Page:
        body: dict[str, Any] = {
            "query": {"filter": {"location_ids": location_ids}},
            "limit": 200,
        }
        if cursor:
            body["cursor"] = cursor
        data = self._request("POST", "/v2/team-members/search", creds=creds, body=body)
        return build_page(list(data.get("team_members") or []), data.get("cursor"))

    def search_timecards(
        self,
        creds: Credentials,
        location_ids: list[str],
        workday_from: date,
        workday_to: date,
        cursor: str | None,
    ) -> Page:
        body: dict[str, Any] = {
            "query": {
                "filter": {
                    "location_ids": location_ids,
                    "workday": {
                        "date_range": {
                            "start_date": workday_from.isoformat(),
                            "end_date": workday_to.isoformat(),
                        },
                        "match_timecards_by": "START_AT",
                    },
                },
                "sort": {"field": "START_AT", "order": "ASC"},
            },
            "limit": 200,
        }
        if cursor:
            body["cursor"] = cursor
        data = self._request("POST", "/v2/labor/timecards/search", creds=creds, body=body)
        return build_page(list(data.get("timecards") or []), data.get("cursor"))
