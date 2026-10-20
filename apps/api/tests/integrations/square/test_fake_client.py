import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.integrations.providers.square import (
    Credentials,
    FakeSquareClient,
    MappingContext,
    SquareAuthError,
    SquareClientError,
    normalize_catalog_object,
    normalize_order,
    normalize_timecard,
)
from app.integrations.providers.square.types import CanonicalMenuItem, parse_ts
from tests.integrations.square.conftest import (
    LOC,
    NOW,
    History,
    midnight,
    payload_hash,
    walk_orders,
    walk_timecards,
)

# ------------------------------------------------------------------ determinism


def test_same_window_twice_identical_hashes(client: FakeSquareClient, creds: Credentials) -> None:
    start = midnight(date(2026, 3, 1))
    end = start + timedelta(days=10)
    a = payload_hash(walk_orders(client, creds, start, end))
    b = payload_hash(walk_orders(client, creds, start, end))
    fresh = FakeSquareClient(clock=lambda: NOW)
    c = payload_hash(walk_orders(fresh, fresh.obtain_token("fake-z", "x"), start, end))
    assert a and a == b == c


def test_clock_advance_only_adds_orders() -> None:
    t = {"now": datetime(2026, 5, 20, 22, 0, tzinfo=UTC)}
    c = FakeSquareClient(clock=lambda: t["now"])
    cr = c.obtain_token("fake-1", "x")
    start = midnight(date(2026, 5, 1))
    horizon = midnight(date(2026, 6, 30))
    before = {i.external_id: i for i in walk_orders(c, cr, start, horizon)}
    t1 = t["now"]
    t["now"] = t1 + timedelta(days=4, hours=7)
    after = {i.external_id: i for i in walk_orders(c, cr, start, horizon)}

    assert set(before) <= set(after)
    changed = 0
    for oid, old in before.items():
        new = after[oid]
        if new.payload != old.payload:
            changed += 1
            assert int(new.version or 0) == int(old.version or 0) + 1  # refund version only
            assert new.payload["refunds"] and new.payload["updated_at"] > old.payload["updated_at"]
            assert {
                k: v
                for k, v in new.payload.items()
                if k in ("line_items", "total_money", "closed_at")
            } == {
                k: v
                for k, v in old.payload.items()
                if k in ("line_items", "total_money", "closed_at")
            }
    for oid in set(after) - set(before):
        assert parse_ts(after[oid].payload["closed_at"]) > t1  # new orders closed after old "now"
    assert len(after) > len(before)
    assert changed >= 1  # 4 days of advance surfaces at least one refund version


def test_order_ids_stable_unique(history: History) -> None:
    ids = [o.external_id for o in history.orders]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("fake-ord-") for i in ids)


# ------------------------------------------------------------------ money


def _money(m: dict[str, int]) -> int:
    return int(m["amount"])


def test_money_consistency_2000_orders(history: History) -> None:
    rng = random.Random(7)
    sample = rng.sample(history.raw_orders, 2000)
    for item in sample:
        o = item.payload
        gross = sum(_money(li["gross_sales_money"]) for li in o["line_items"])
        disc = sum(_money(li["total_discount_money"]) for li in o["line_items"])
        tax = sum(_money(li["total_tax_money"]) for li in o["line_items"])
        tip = _money(o["total_tip_money"])
        sc = _money(o["total_service_charge_money"])
        assert disc == _money(o["total_discount_money"])
        assert tax == _money(o["total_tax_money"])
        assert _money(o["total_money"]) == gross - disc + tax + tip + sc
        for li in o["line_items"]:
            assert _money(li["gross_sales_money"]) == _money(li["base_price_money"]) * int(
                li["quantity"]
            )
            assert _money(li["total_money"]) == (
                _money(li["gross_sales_money"])
                - _money(li["total_discount_money"])
                + _money(li["total_tax_money"])
            )
        assert sum(_money(t["amount_money"]) for t in o["tenders"]) == _money(o["total_money"])
        assert sum(_money(t["tip_money"]) for t in o["tenders"]) == tip
        net = o["net_amounts"]
        ret = o.get("return_amounts")
        r_tot = _money(ret["total_money"]) if ret else 0
        r_tax = _money(ret["tax_money"]) if ret else 0
        r_disc = _money(ret["discount_money"]) if ret else 0
        assert _money(net["total_money"]) == _money(o["total_money"]) - r_tot
        assert _money(net["tax_money"]) == tax - r_tax
        assert _money(net["discount_money"]) == disc - r_disc
        assert _money(net["tip_money"]) == tip and _money(net["service_charge_money"]) == sc
        assert o["state"] == "COMPLETED" and o["version"] >= 1
        created, closed = parse_ts(o["created_at"]), parse_ts(o["closed_at"])
        assert timedelta(minutes=30) <= closed - created <= timedelta(minutes=90)
    by_id = {o.external_id: o for o in history.orders}
    for item in sample[:300]:
        c = by_id[item.external_id]
        assert c.net_sales + c.tax + c.tips + c.service_charges == c.total


def test_refunds_shape(history: History) -> None:
    refunded = [i.payload for i in history.raw_orders if i.payload.get("refunds")]
    assert refunded
    for o in refunded:
        closed, upd = parse_ts(o["closed_at"]), parse_ts(o["updated_at"])
        assert timedelta(days=1) <= upd - closed <= timedelta(days=3)
        assert o["returns"][0]["return_line_items"]
        assert parse_ts(o["refunds"][0]["created_at"]) == upd
        assert upd <= NOW
        assert o["version"] >= 2


# ------------------------------------------------------------------ pagination


def test_pagination_exactly_once_sorted(client: FakeSquareClient, creds: Credentials) -> None:
    start = midnight(date(2026, 4, 1))
    end = start + timedelta(days=30)
    small = walk_orders(client, creds, start, end, limit=37)
    big = walk_orders(client, creds, start, end, limit=5000)
    ids = [i.external_id for i in small]
    assert len(ids) == len(set(ids)) > 1000
    assert ids == [i.external_id for i in big]
    keys = [(i.updated_at, i.external_id) for i in small]
    assert keys == sorted(keys)
    assert all(i.updated_at is not None and start <= i.updated_at < end for i in small)
    # page size honored
    page = client.search_orders(creds, LOC, start, end, None, limit=37)
    assert len(page.items) == 37 and page.next_cursor
    assert page.max_updated_at == page.items[-1].updated_at


def test_window_boundaries_partition(client: FakeSquareClient, creds: Credentials) -> None:
    a, b, c = midnight(date(2026, 4, 1)), midnight(date(2026, 4, 10)), midnight(date(2026, 4, 20))
    whole = [i.external_id for i in walk_orders(client, creds, a, c)]
    parts = [i.external_id for i in walk_orders(client, creds, a, b)] + [
        i.external_id for i in walk_orders(client, creds, b, c)
    ]
    assert whole == parts


def test_refund_version_found_by_updated_at_window(
    history: History, client: FakeSquareClient, creds: Credentials
) -> None:
    item = next(i for i in history.raw_orders if i.payload.get("refunds") and i.updated_at)
    assert item.updated_at is not None
    got = walk_orders(client, creds, item.updated_at, item.updated_at + timedelta(seconds=1))
    assert item.external_id in {g.external_id for g in got}
    again = client.batch_retrieve_orders(
        creds, "L_JUNIPER_BK", [item.external_id, "fake-ord-20260101-9999"]
    )
    assert [o["id"] for o in again] == [item.external_id]
    assert again[0] == item.payload


def test_unknown_location_and_bad_cursor(client: FakeSquareClient, creds: Credentials) -> None:
    page = client.search_orders(
        creds, ["L_NOPE"], midnight(date(2026, 4, 1)), midnight(date(2026, 4, 2)), None
    )
    assert page.items == []
    with pytest.raises(SquareClientError):
        client.search_orders(
            creds, LOC, midnight(date(2026, 4, 1)), midnight(date(2026, 4, 2)), "%%%"
        )


# ------------------------------------------------------------------ aggregates


def test_aggregates_over_simulated_year(history: History) -> None:
    import collections
    import statistics

    orders, first = history.orders, history.first_day
    last_year_start = history.last_day - timedelta(days=364)
    counts: collections.Counter[date] = collections.Counter(o.business_date for o in orders)
    from app.integrations.providers.square.fake_data import is_private_event, is_snowstorm

    per_wd: dict[int, list[int]] = collections.defaultdict(list)
    for d, n in counts.items():
        if d >= last_year_start and not is_snowstorm(d) and not is_private_event(d):
            per_wd[d.weekday()].append(n)
    mean = {w: statistics.mean(v) for w, v in per_wd.items()}

    assert 45 <= mean[0] <= 85, (0, mean[0])  # light Monday dinner
    for w in (1, 2, 3):
        assert 65 <= mean[w] <= 125, (w, mean[w])
    for w in (4, 5):
        assert 150 <= mean[w] <= 240, (w, mean[w])
    assert 100 <= mean[6] <= 190

    # snowstorm Saturday vs typical Saturday
    snow = date(2026, 1, 24)
    sats = [
        n for d, n in counts.items() if d.weekday() == 5 and abs((d - snow).days) in (7, 14, 21)
    ]
    assert counts[snow] < 0.55 * statistics.mean(sats)
    # private event: one $4,800 order
    big = [o for o in orders if o.gross_sales == 480_000]
    assert len(big) == 1 and big[0].business_date == date(2026, 5, 12)

    # channel mix
    ch = collections.Counter(o.channel for o in orders)
    share = {k: 100 * v / len(orders) for k, v in ch.items()}
    for k, target in (("dine_in", 76), ("pickup", 11), ("delivery", 9), ("online", 4)):
        assert abs(share.get(k, 0) - target) <= 3, (k, share)
    assert "other" not in share

    # refund rate (only orders old enough to have had their refund window elapse)
    cutoff = NOW - timedelta(days=4)
    mature = [o for o in orders if o.closed_at <= cutoff]
    refund_rate = 100 * sum(1 for o in mature if o.returns) / len(mature)
    assert 0.9 <= refund_rate <= 1.6

    # discount rate and dine-in avg check
    disc_rate = 100 * sum(1 for o in orders if o.discounts) / len(orders)
    dine = [o.net_sales for o in orders if o.channel == "dine_in" and o.gross_sales != 480_000]
    avg_check = statistics.mean(dine) / 100
    assert 5 <= disc_rate <= 11 and 52 <= avg_check <= 68

    # labor % of net sales over the trailing 365 days
    lo, hi = history.last_day - timedelta(days=364), history.last_day
    net = sum(v for d, v in history.net_by_date.items() if lo <= d <= hi)
    labor = sum(v for d, v in history.labor_by_date.items() if lo <= d <= hi)
    labor_pct = 100 * labor / net
    assert 28 <= labor_pct <= 33, labor_pct
    by_wd: dict[int, tuple[int, int]] = {}
    for w in range(1, 7):
        ds = [
            d
            for d in history.net_by_date
            if lo <= d <= hi and d.weekday() == w and history.net_by_date[d] > 0
        ]
        by_wd[w] = (
            sum(history.labor_by_date.get(d, 0) for d in ds),
            sum(history.net_by_date[d] for d in ds),
        )
    pct_wd = {w: 100 * a / b for w, (a, b) in by_wd.items()}
    assert pct_wd[2] > pct_wd[4]  # slow Wednesday is worse than busy Friday

    # YoY: same 35 days a year apart
    def window(end: date) -> tuple[int, int]:
        ds = [end - timedelta(days=k) for k in range(35)]
        return sum(counts.get(d, 0) for d in ds), sum(history.net_by_date.get(d, 0) for d in ds)

    c_now, s_now = window(history.last_day)
    c_prev, s_prev = window(history.last_day - timedelta(days=364))
    assert first <= history.last_day - timedelta(days=364 + 34)
    yoy_orders, yoy_sales = 100 * (c_now / c_prev - 1), 100 * (s_now / s_prev - 1)
    assert 3 <= yoy_orders <= 11 and 3 <= yoy_sales <= 11

    # unmapped items ~0.3% of lines
    n_lines = sum(len(o.lines) for o in orders)
    unmapped = sum(1 for o in orders for ln in o.lines if ln.menu_item_variant_external_id is None)
    assert 0.001 <= unmapped / n_lines <= 0.007
    assert any(s.kind == "unmapped_item" for s in history.signals)

    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("\n=== Juniper House simulated stats ===")
    print(
        f"history {first}..{history.last_day}: {len(orders)} orders, {len(history.returns)} returns, "
        f"{len(history.timecards)} timecards"
    )
    print("avg orders/day  " + "  ".join(f"{dow[w]}={mean.get(w, 0):.0f}" for w in range(7)))
    print("channel share % " + "  ".join(f"{k}={v:.1f}" for k, v in sorted(share.items())))
    print(
        f"dine-in avg check ${avg_check:.2f}; discounted {disc_rate:.1f}%; refund rate {refund_rate:.2f}%"
    )
    print(
        f"labor {labor_pct:.1f}% of net sales  by weekday: "
        + " ".join(f"{dow[w]}={p:.1f}" for w, p in pct_wd.items())
    )
    print(f"YoY (35d): orders {yoy_orders:+.1f}%  net sales {yoy_sales:+.1f}%")
    print(
        f"unmapped lines {100 * unmapped / n_lines:.2f}%  snowstorm {counts[snow]} orders (typical Sat "
        f"{statistics.mean(sats):.0f})"
    )


# ------------------------------------------------------------------ menu rules


def test_brunch_items_only_weekend_10_to_15(history: History) -> None:
    from app.integrations.providers.square.fake_data import MENU

    brunch_vars = {v.id for m in MENU if m.category == "Brunch" for v in m.variations}
    seen = 0
    for o in history.orders:
        if any(ln.external_item_ref in brunch_vars for ln in o.lines):
            seen += 1
            lt = o.closed_at.astimezone(ZoneInfo("America/New_York"))
            assert lt.weekday() in (5, 6) and 10 <= lt.hour + lt.minute / 60 <= 15
    assert seen > 100


def test_catalog_shape_and_incremental(client: FakeSquareClient, creds: Credentials) -> None:
    full = client.search_catalog(creds, None, None)
    items = [i for i in full.items if i.payload["type"] == "ITEM"]
    cats = [i for i in full.items if i.payload["type"] == "CATEGORY"]
    assert len(cats) == 9 and 28 <= len(items) <= 36
    assert all(not i.payload["is_deleted"] for i in full.items)
    names = {c.payload["id"]: c.payload["category_data"]["name"] for c in cats}
    prices = []
    multi = 0
    for i in items:
        norm = normalize_catalog_object(i.payload, names)
        assert isinstance(norm, CanonicalMenuItem) and norm.category_name in names.values()
        prices += [v.price_cents for v in norm.variants if v.price_cents]
        multi += len(norm.variants) > 1
        for v in i.payload["item_data"]["variations"]:
            assert (
                v["type"] == "ITEM_VARIATION"
                and v["item_variation_data"]["price_money"]["currency"] == "USD"
            )
    assert min(prices) >= 600 and max(prices) <= 5800 and multi >= 3
    # incremental: only the changed items (+ deleted one) after a recent watermark
    inc = client.search_catalog(creds, NOW - timedelta(days=60), None)
    kinds = {i.payload["id"]: i.payload["is_deleted"] for i in inc.items}
    assert len(kinds) == 3 and sum(kinds.values()) == 1
    assert client.search_catalog(creds, NOW, None).items == []


def test_catalog_pagination() -> None:
    c = FakeSquareClient(clock=lambda: NOW, catalog_page_size=10)
    cr = c.obtain_token("fake-p", "x")
    seen: list[str] = []
    cur = None
    while True:
        p = c.search_catalog(cr, None, cur)
        seen += [i.external_id for i in p.items]
        cur = p.next_cursor
        if not cur:
            break
    assert len(seen) == len(set(seen)) == 40


# ------------------------------------------------------------------ staff / timecards


def test_staff_and_timecard_only_member(history: History) -> None:
    c, cr = history.client, history.creds
    members = c.search_team_members(cr, LOC, None)
    ids = {m.external_id for m in members.items}
    assert len(ids) == 18 and members.next_cursor is None
    rates = [
        m.payload["wage_setting"]["job_assignments"][0]["hourly_rate"]["amount"]
        for m in members.items
    ]
    assert min(rates) >= 1600 and max(rates) <= 3400
    tc_members = {t.team_member_external_id for t in history.timecards}
    assert tc_members - ids == {"TM_TEMP_EVENT"}
    unmapped = [s for s in history.signals if s.kind == "unmapped_team_member"]
    assert unmapped and {s.external_id for s in unmapped} == {"TM_TEMP_EVENT"}


def test_timecards_follow_demand(history: History) -> None:
    import collections

    n: collections.Counter[date] = collections.Counter(t.business_date for t in history.timecards)
    lo = history.last_day - timedelta(days=364)
    by_wd: dict[int, list[int]] = collections.defaultdict(list)
    for d, k in n.items():
        if d >= lo:
            by_wd[d.weekday()].append(k)
    avg = {w: sum(v) / len(v) for w, v in by_wd.items()}
    assert avg[0] < avg[4]  # light Monday crew vs Friday
    assert avg[4] > avg[2] and avg[5] > avg[2]
    closed = [t for t in history.timecards if not t.open]
    assert all(
        t.cost_cents > 0 and 4.5 <= t.paid_hours + t.unpaid_break_minutes / 60 <= 9.5
        for t in closed
    )
    assert all(
        t.unpaid_break_minutes == 30
        for t in closed
        if (t.end_at and t.start_at and (t.end_at - t.start_at) > timedelta(hours=6))
    )
    assert all(
        t.unpaid_break_minutes == 0
        for t in closed
        if t.end_at and (t.end_at - t.start_at) <= timedelta(hours=6)
    )


def test_open_timecards_follow_clock() -> None:
    now = datetime(2026, 6, 26, 23, 30, tzinfo=UTC)  # Friday 19:30 EDT, mid-service
    c = FakeSquareClient(clock=lambda: now, history_days=30)
    cr = c.obtain_token("fake-o", "x")
    items = walk_timecards(c, cr, date(2026, 6, 26), date(2026, 6, 26))
    open_ = [i for i in items if i.payload["status"] == "OPEN"]
    closed = [i for i in items if i.payload["status"] == "CLOSED"]
    assert len(open_) >= 8 and all("end_at" not in i.payload for i in open_)
    assert all(parse_ts(i.payload["start_at"]) <= now for i in items)
    ctx = MappingContext("America/New_York")
    for i in open_:
        tc = normalize_timecard(i.payload, ctx).timecard
        assert tc.open and tc.cost_cents == 0
    # later the same shifts close with a new version
    later = now + timedelta(hours=8)
    c2 = FakeSquareClient(clock=lambda: later, history_days=30)
    cr2 = c2.obtain_token("fake-o", "x")
    items2 = {
        i.external_id: i for i in walk_timecards(c2, cr2, date(2026, 6, 26), date(2026, 6, 26))
    }
    for i in open_:
        assert items2[i.external_id].payload["status"] == "CLOSED"
        assert int(items2[i.external_id].version or 0) > int(i.version or 0)
    assert closed  # some had already finished (lunch crew)


def test_timecard_pagination() -> None:
    c = FakeSquareClient(clock=lambda: NOW, timecard_page_size=25)
    cr = c.obtain_token("fake-t", "x")
    a = walk_timecards(c, cr, date(2026, 5, 1), date(2026, 5, 31))
    ids = [i.external_id for i in a]
    assert len(ids) == len(set(ids)) > 100
    keys = [(i.payload["start_at"], i.external_id) for i in a]
    assert [parse_ts(k[0]) for k in keys] == sorted(parse_ts(k[0]) for k in keys)
    assert a[0].payload["wage"]["hourly_rate"]["currency"] == "USD"


# ------------------------------------------------------------------ auth / locations


def test_oauth_and_locations(client: FakeSquareClient) -> None:
    assert (
        client.authorize_url("S1", "https://x/cb", ["ORDERS_READ"])
        == "/v1/integrations/square/fake-consent?state=S1"
    )
    with pytest.raises(SquareClientError):
        client.obtain_token("real-code", "x")
    cr = client.obtain_token("fake-abc", "x")
    assert cr.merchant_id == "MLJUNIPER" and cr.expires_at - NOW == timedelta(days=30)
    (loc,) = client.list_locations(cr)
    assert loc["id"] == "L_JUNIPER_BK" and loc["timezone"] == "America/New_York"
    r = client.refresh_token(cr)
    assert r.refresh_token == cr.refresh_token and r.merchant_id == cr.merchant_id
    client.revoke(cr)
    with pytest.raises(SquareAuthError):
        client.list_locations(cr)
    with pytest.raises(SquareAuthError):
        client.search_orders(
            Credentials("bogus", "", NOW + timedelta(days=1), "M"), LOC, NOW, NOW, None
        )


def test_orders_normalize_end_to_end(history: History) -> None:
    item = history.raw_orders[0]
    res = normalize_order(item.payload, history.ctx)
    assert res.order.external_id == item.external_id
    assert res.order.location_external_id == "L_JUNIPER_BK"


def test_performance_full_history(history: History) -> None:
    print(
        f"\nPERF: generated+paged+normalized {len(history.orders)} orders "
        f"({(history.last_day - history.first_day).days} days) and {len(history.timecards)} timecards "
        f"in {history.seconds:.2f}s"
    )
    assert history.seconds < 60
    assert (history.last_day - history.first_day).days >= 399  # 400-day history
