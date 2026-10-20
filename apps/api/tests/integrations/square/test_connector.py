import json
from datetime import UTC, datetime
from typing import Any

from app.integrations.providers.square.connector import SquareConnector
from app.integrations.providers.square.types import Credentials, Page, RawItem, Window

CREDS = Credentials("tok", "rtok", datetime(2030, 1, 1, tzinfo=UTC), "M1")
WINDOW = Window(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))


class SearchClient:
    def __init__(self) -> None:
        self.order_calls: list[tuple[list[str], str | None]] = []
        self.timecard_calls: list[list[str]] = []

    def search_orders(
        self,
        creds: Credentials,
        location_ids: list[str],
        updated_from: datetime,
        updated_to: datetime,
        cursor: str | None,
        limit: int = 500,
    ) -> Page:
        self.order_calls.append((location_ids, cursor))
        ident = f"O{len(self.order_calls)}"
        item = RawItem(ident, datetime(2026, 1, 1, tzinfo=UTC), "1", {"id": ident})
        return Page(
            [item], f"C{len(self.order_calls)}" if cursor is None else None, item.updated_at
        )

    def search_timecards(
        self,
        creds: Credentials,
        location_ids: list[str],
        workday_from: Any,
        workday_to: Any,
        cursor: str | None,
    ) -> Page:
        self.timecard_calls.append(location_ids)
        return Page([], None, None)


def test_empty_locations_skip_orders_and_timecards() -> None:
    client = SearchClient()
    connector = SquareConnector(client, mode="sandbox")  # type: ignore[arg-type]

    assert connector.fetch_page(CREDS, "orders", WINDOW, None, []).items == []
    assert connector.fetch_page(CREDS, "timecards", WINDOW, None, []).items == []
    assert client.order_calls == []
    assert client.timecard_calls == []


def test_orders_search_batches_locations_and_keeps_cursors_per_batch() -> None:
    client = SearchClient()
    connector = SquareConnector(client, mode="sandbox")  # type: ignore[arg-type]
    locations = [f"L{i}" for i in range(21)]

    first = connector.fetch_page(CREDS, "orders", WINDOW, None, locations)
    assert [len(ids) for ids, _ in client.order_calls] == [10, 10, 1]
    assert [item.external_id for item in first.items] == ["O1", "O2", "O3"]
    assert json.loads(first.next_cursor or "") == ["C1", "C2", "C3"]

    second = connector.fetch_page(CREDS, "orders", WINDOW, first.next_cursor, locations)
    assert client.order_calls[3:] == [
        (locations[:10], "C1"),
        (locations[10:20], "C2"),
        (locations[20:], "C3"),
    ]
    assert [item.external_id for item in second.items] == ["O4", "O5", "O6"]
    assert second.next_cursor is None
