from datetime import date, datetime, time
from decimal import Decimal

import pytest

from app.integrations.providers.square import (
    NORMALIZER_VERSION,
    MappingContext,
    normalize_catalog_object,
    normalize_order,
    normalize_team_member,
    normalize_timecard,
)
from app.integrations.providers.square.normalizer import derive_channel
from app.integrations.providers.square.types import CanonicalMenuCategory, CanonicalMenuItem
from tests.integrations.square.conftest import TZ, make_order


def ctx(known: set[str] | None = None) -> MappingContext:
    known = known if known is not None else {"VAR_BURGER"}
    return MappingContext(TZ, resolve_catalog=lambda c: c if c in known else None)


def test_version() -> None:
    assert NORMALIZER_VERSION == "1"


def test_order_amounts_exclude_tax_tips_service() -> None:
    res = normalize_order(make_order(), ctx())
    o = res.order
    assert (o.gross_sales, o.discounts, o.net_sales) == (4800, 480, 4320)
    assert (o.tax, o.tips, o.service_charges, o.total) == (383, 800, 130, 5633)
    assert o.net_sales + o.tax + o.tips + o.service_charges == o.total
    assert o.channel == "dine_in" and o.source_version == 3 and o.returns == 0
    assert res.signals == [] and res.returns == []
    line = o.lines[0]
    assert line.menu_item_variant_external_id == "VAR_BURGER" and line.quantity == Decimal(2)
    assert line.net_cents == 4320


def test_cross_midnight_business_date() -> None:
    # 05:30Z = 01:30 EDT on Mar 11 -> belongs to Mar 10
    res = normalize_order(make_order(closed_at="2026-03-11T05:30:00.000Z"), ctx())
    assert res.order.business_date == date(2026, 3, 10)
    res = normalize_order(make_order(closed_at="2026-03-11T08:30:00.000Z"), ctx())  # 04:30 EDT
    assert res.order.business_date == date(2026, 3, 11)
    res = normalize_order(
        make_order(closed_at="2026-03-11T05:30:00.000Z"),
        MappingContext(TZ, business_day_start=time(0, 0), resolve_catalog=lambda _: None),
    )
    assert res.order.business_date == date(2026, 3, 11)


def test_return_lands_on_processing_date_not_sale_date() -> None:
    refund_at = "2026-03-14T18:00:00.000Z"  # Mar 14 14:00 EDT
    payload = make_order(
        closed_at="2026-03-11T02:00:00.000Z",  # Mar 10 22:00 EDT
        updated_at=refund_at,
        version=4,
        returns=[{"uid": "r", "return_line_items": [{
            "uid": "rl", "source_line_item_uid": "a", "name": "Burger", "quantity": "1",
            "catalog_object_id": "VAR_BURGER",
            "base_price_money": {"amount": 2400, "currency": "USD"},
            "gross_return_money": {"amount": 2400, "currency": "USD"},
            "total_discount_money": {"amount": 240, "currency": "USD"},
            "total_tax_money": {"amount": 192, "currency": "USD"},
            "total_money": {"amount": 2352, "currency": "USD"},
        }]}],
        refunds=[{"id": "RF1", "created_at": refund_at, "status": "COMPLETED", "reason": "Cold",
                  "amount_money": {"amount": 2352, "currency": "USD"}}],
    )  # fmt: skip
    res = normalize_order(payload, ctx())
    o, (ret,) = res.order, res.returns
    assert o.business_date == date(2026, 3, 10)
    assert o.net_sales == 4320  # sale-date contribution is NOT reduced by the return
    assert o.returns == 2160 and o.net_sales_after_returns == 2160
    assert ret.business_date == date(2026, 3, 14)
    assert (ret.gross_cents, ret.discount_cents, ret.net_cents) == (2400, 240, 2160)
    assert (ret.tax_cents, ret.total_cents, ret.external_id) == (192, 2352, "RF1")
    assert ret.reason == "Cold" and ret.order_external_id == "O1"


def test_multiple_refunds_split_exactly() -> None:
    payload = make_order(
        returns=[{"return_line_items": [{
            "quantity": "2", "gross_return_money": {"amount": 4801}, "total_discount_money": {"amount": 0},
            "total_tax_money": {"amount": 101},
        }]}],
        refunds=[
            {"id": "A", "created_at": "2026-03-12T15:00:00Z", "amount_money": {"amount": 1000}},
            {"id": "B", "created_at": "2026-03-13T15:00:00Z", "amount_money": {"amount": 3902}},
        ],
    )  # fmt: skip
    res = normalize_order(payload, ctx())
    assert sum(r.gross_cents for r in res.returns) == 4801
    assert sum(r.tax_cents for r in res.returns) == 101
    assert [r.business_date for r in res.returns] == [date(2026, 3, 12), date(2026, 3, 13)]


def test_unmapped_item_signal_and_still_counts_toward_sales() -> None:
    res = normalize_order(make_order(), ctx(known=set()))
    line = res.order.lines[0]
    assert line.menu_item_variant_external_id is None
    assert line.external_item_ref == "VAR_BURGER"
    assert res.order.net_sales == 4320
    (sig,) = res.signals
    assert (sig.kind, sig.external_id, sig.label) == ("unmapped_item", "VAR_BURGER", "Burger")


def test_custom_amount_line_uses_name_as_ref() -> None:
    p = make_order()
    p["line_items"].append({"uid": "z", "name": "Open Food", "quantity": "1", "item_type": "CUSTOM_AMOUNT",
                            "base_price_money": {"amount": 1000}})  # fmt: skip
    res = normalize_order(p, ctx())
    assert res.order.lines[1].external_item_ref == "Open Food"
    assert res.order.gross_sales == 5800
    assert [s.external_id for s in res.signals] == ["Open Food"]


def test_gift_cards_excluded() -> None:
    p = make_order()
    p["line_items"].append({"uid": "g", "name": "Gift Card", "quantity": "1", "item_type": "GIFT_CARD",
                            "base_price_money": {"amount": 5000}, "gross_sales_money": {"amount": 5000}})  # fmt: skip
    res = normalize_order(p, ctx())
    assert res.order.gross_sales == 4800 and len(res.order.lines) == 1
    assert res.signals == []


@pytest.mark.parametrize(
    ("source", "fulfillments", "expected"),
    [
        ("Square Point of Sale", [], "dine_in"),
        ("Square Point of Sale", ["PICKUP"], "pickup"),
        ("Square Online", ["PICKUP"], "online"),
        ("Square Online", [], "online"),
        ("DoorDash", ["DELIVERY"], "delivery"),
        ("Uber Eats", [], "delivery"),
        ("Square Point of Sale", ["DELIVERY"], "delivery"),
        ("Mystery Kiosk", [], None),
        ("Square Point of Sale", ["SHIPMENT"], None),
        (None, [], None),
    ],
)
def test_channel_mapping(source: str | None, fulfillments: list[str], expected: str | None) -> None:
    assert derive_channel(source, fulfillments) == expected


def test_unknown_channel_signal() -> None:
    p = make_order(source={"name": "Mystery Kiosk"})
    res = normalize_order(p, ctx())
    assert res.order.channel == "other"
    (sig,) = res.signals
    assert sig.kind == "unknown_channel" and sig.external_id == "Mystery Kiosk|"


def tc(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "TC1", "team_member_id": "TM1", "location_id": "L1",
        "start_at": "2026-03-10T16:00:00-04:00", "end_at": "2026-03-11T00:00:00-04:00",
        "status": "CLOSED", "version": 2, "updated_at": "2026-03-11T00:00:00-04:00",
        "wage": {"title": "Server", "hourly_rate": {"amount": 2000, "currency": "USD"}, "tip_eligible": True},
        "breaks": [{"id": "b", "start_at": "2026-03-10T19:30:00-04:00", "end_at": "2026-03-10T20:00:00-04:00",
                    "is_paid": False, "name": "Meal"}],
    }  # fmt: skip
    base.update(kw)
    return base


def test_timecard_break_math() -> None:
    res = normalize_timecard(tc(), ctx())
    t = res.timecard
    assert t.paid_hours == 7.5 and t.unpaid_break_minutes == 30
    assert t.hourly_rate_cents == 2000 and t.cost_cents == 15000
    assert not t.open and t.tip_eligible and t.business_date == date(2026, 3, 10)
    assert res.signals == []


def test_timecard_paid_break_not_deducted_and_rounding() -> None:
    p = tc(breaks=[{"start_at": "2026-03-10T19:30:00-04:00", "end_at": "2026-03-10T20:00:00-04:00", "is_paid": True}],
           end_at="2026-03-10T21:20:00-04:00", wage={"hourly_rate": {"amount": 1650}})  # fmt: skip
    t = normalize_timecard(p, ctx()).timecard
    # 5h20m * $16.50 = $88.00
    assert t.paid_hours == pytest.approx(5.3333, abs=1e-3) and t.cost_cents == 8800


def test_open_timecard() -> None:
    p = tc(status="OPEN")
    del p["end_at"]
    t = normalize_timecard(p, ctx()).timecard
    assert t.open and t.end_at is None and t.cost_cents == 0 and t.paid_hours == 0.0


def test_timecard_unmapped_team_member() -> None:
    c = MappingContext(TZ, is_known_team_member=lambda m: m != "TM1")
    res = normalize_timecard(tc(), c)
    assert [(s.kind, s.external_id) for s in res.signals] == [("unmapped_team_member", "TM1")]
    assert res.timecard.cost_cents == 15000  # still counts toward location labor


def test_timecard_business_date_cross_midnight() -> None:
    p = tc(start_at="2026-03-11T01:15:00-04:00", end_at="2026-03-11T06:15:00-04:00", breaks=[])
    assert normalize_timecard(p, ctx()).timecard.business_date == date(2026, 3, 10)


def test_catalog_and_team_normalization() -> None:
    cat = {"type": "CATEGORY", "id": "CAT1", "category_data": {"name": "Mains"}}
    c = normalize_catalog_object(cat, {})
    assert c == CanonicalMenuCategory("CAT1", "Mains", False)
    item = {
        "type": "ITEM", "id": "I1", "updated_at": "2026-01-01T00:00:00Z", "version": 5, "is_deleted": False,
        "item_data": {"name": "Pinot", "category_id": "CAT1", "variations": [
            {"id": "V1", "item_variation_data": {"name": "Glass", "price_money": {"amount": 1500, "currency": "USD"}}},
            {"id": "V2", "is_deleted": True, "item_variation_data": {"name": "Bottle"}},
        ]},
    }  # fmt: skip
    m = normalize_catalog_object(item, {"CAT1": "Mains"})
    assert isinstance(m, CanonicalMenuItem)
    assert m.category_name == "Mains" and m.source_version == 5 and not m.deleted
    assert [(v.external_id, v.price_cents, v.deleted) for v in m.variants] == [
        ("V1", 1500, False),
        ("V2", None, True),
    ]
    assert normalize_catalog_object({"type": "TAX", "id": "T"}, {}) is None
    gone = normalize_catalog_object({**item, "is_deleted": True}, {})
    assert (
        isinstance(gone, CanonicalMenuItem)
        and gone.deleted
        and all(v.deleted for v in gone.variants)
    )

    staff = normalize_team_member({
        "id": "TM1", "status": "INACTIVE", "given_name": "A", "family_name": "B",
        "updated_at": "2026-01-01T00:00:00Z",
        "wage_setting": {"job_assignments": [{"job_title": "Server", "hourly_rate": {"amount": 1700}}]},
    })  # fmt: skip
    assert (staff.status, staff.job_title, staff.hourly_rate_cents) == ("inactive", "Server", 1700)
    assert staff.source_updated_at == datetime.fromisoformat("2026-01-01T00:00:00+00:00")
