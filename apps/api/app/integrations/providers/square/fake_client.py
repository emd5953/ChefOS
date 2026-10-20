"""FakeSquareClient: a deterministic simulated Square sandbox for "Juniper House".

Determinism: order ``i`` of business date ``d`` is a pure function of ``random.Random(f"{d}:{i}")``.
The injectable ``clock`` only decides which orders/versions/timecards are *visible*: orders
with ``closed_at <= now`` exist, a refund version exists once ``now >= its updated_at``, and
timecards are open while ``start <= now < end``. History begins 400 days before the clock's
date at construction time (the epoch is frozen at construction so advancing the clock only
adds data). Returned payloads are deep copies; callers may mutate them freely.

Price note: orders are always priced at the current catalog price (including the two items
whose catalog ``updated_at`` is 20 days ago), so past orders never change as time passes.
"""

import base64
import copy
import hashlib
import json
import random
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.integrations.providers.square import fake_data as fd
from app.integrations.providers.square.client import (
    SquareAuthError,
    SquareClientError,
    build_page,
)
from app.integrations.providers.square.fake_data import MenuItem, Variation
from app.integrations.providers.square.types import Credentials, Page, format_ts, parse_ts

TAX_RATE_PER_100K = 8875  # 8.875% NYC
HISTORY_DAYS = 400
_DAY_CACHE_SIZE = 64

_CHANNEL_CUM = (0.76, 0.87, 0.96, 1.0)  # dine_in, pickup, delivery, online
_PARTY = (1, 2, 3, 4, 5, 6, 8, 10)
_PARTY_W = (40, 40, 8, 6, 2.5, 1.5, 1.5, 0.5)
_REFUND_RATE = 0.012
_ADHOC_RATE = 0.003
_REFUND_REASONS = ("Guest complaint", "Wrong item", "Cold food", "Overcharge")
_DELIVERY_SOURCES = ("DoorDash", "Uber Eats")


def _m(amount: int) -> dict[str, Any]:
    return {"amount": amount, "currency": "USD"}


def _pct(amount: int, per100k: int) -> int:
    return (amount * per100k + 50_000) // 100_000


def _split(total: int, weights: list[int]) -> list[int]:
    w = sum(weights)
    if w <= 0:
        return [total] + [0] * (len(weights) - 1)
    out: list[int] = []
    cum = prev = 0
    for x in weights:
        cum += x
        cur = (total * cum * 2 + w) // (2 * w)
        out.append(cur - prev)
        prev = cur
    return out


def _local_seconds(d: date, seconds: int) -> datetime:
    return (datetime(d.year, d.month, d.day, tzinfo=fd.TZ) + timedelta(seconds=seconds)).astimezone(
        UTC
    )


# ---------------------------------------------------------------- order synthesis


@dataclass(slots=True)
class _OrderRec:
    order_id: str
    closed_at: datetime
    v1: dict[str, Any]
    overlay: dict[str, Any] | None  # fields that change when the refund version exists
    refund_at: datetime | None

    def updated_at(self, now: datetime) -> datetime:
        if self.refund_at is not None and self.refund_at <= now:
            return self.refund_at
        return self.closed_at

    def payload(self, now: datetime) -> dict[str, Any]:
        if self.overlay is not None and self.refund_at is not None and self.refund_at <= now:
            return {**self.v1, **self.overlay}
        return self.v1


_CATALOG_POOLS: dict[str, tuple[MenuItem, ...]] = {c: fd.active_items(c) for _, c in fd.CATEGORIES}


def _compose(
    rng: random.Random, daypart: str, channel: str, party: int
) -> list[tuple[MenuItem | None, Variation | None, int]]:
    """Choose (item, variation, qty) lines. item None => ad-hoc 'Open Food'."""
    P = _CATALOG_POOLS
    take_out = channel != "dine_in"
    if take_out:
        p_main, p_start, p_side, p_dess, p_drink = 0.68, 0.26, 0.26, 0.12, 0.0
        main_pool = P["Mains"]
    elif daypart == "brunch":
        p_main, p_start, p_side, p_dess, p_drink = 0.60, 0.04, 0.08, 0.0, 0.85
        main_pool = P["Brunch"]
    elif daypart == "lunch":
        p_main, p_start, p_side, p_dess, p_drink = 0.50, 0.22, 0.12, 0.0, 0.50
        main_pool = P["Mains"][:5]
    else:
        p_main, p_start, p_side, p_dess, p_drink = 0.39, 0.22, 0.13, 0.07, 0.52
        main_pool = P["Mains"]

    sel: dict[str, list[Any]] = {}

    def add(item: MenuItem, var: Variation) -> None:
        cur = sel.get(var.id)
        if cur is None:
            sel[var.id] = [item, var, 1]
        else:
            cur[2] += 1

    r = rng.random
    for _ in range(party):
        if r() < p_main:
            it = rng.choice(main_pool)
            add(it, it.variations[0])
        if r() < p_start:
            it = rng.choice(P["Starters"])
            add(it, it.variations[0])
        if r() < p_side:
            it = rng.choice(P["Sides"])
            add(it, it.variations[0])
        if r() < p_dess:
            it = rng.choice(P["Desserts"])
            add(it, it.variations[0])
        if take_out:
            if r() < 0.2:
                it = rng.choice(P["N/A Beverages"])
                add(it, it.variations[0])
            continue
        for k in range(2):
            if r() < (p_drink if k == 0 else p_drink * 0.45):
                u = r()
                brunch = daypart == "brunch"
                if u < (0.55 if brunch else 0.38):
                    pool = P["Cocktails"]
                elif u < (0.65 if brunch else 0.68):
                    pool = P["Wine"]
                elif u < (0.75 if brunch else 0.85):
                    pool = P["Beer"]
                else:
                    pool = P["N/A Beverages"]
                it = rng.choice(pool)
                var = (
                    it.variations[-1]
                    if (len(it.variations) > 1 and r() < 0.12)
                    else it.variations[0]
                )
                add(it, var)
    if not sel:
        it = rng.choice(main_pool)
        add(it, it.variations[0])

    out: list[tuple[MenuItem | None, Variation | None, int]] = []
    for it, var, qty in sel.values():
        if r() < _ADHOC_RATE:
            # ad-hoc line, priced like the line it replaces
            out.append((None, Variation("", "Open Food", var.price_cents), qty))
        else:
            out.append((it, var, qty))
    return out


def _build_order(d: date, i: int, closed: datetime, daypart: str, plan: fd.DayPlan) -> _OrderRec:
    rng = random.Random(f"{d}:{i}")
    r = rng.random
    order_id = f"fake-ord-{d:%Y%m%d}-{i:04d}"
    event = daypart == "event"

    u = r()
    channel = "dine_in" if u < _CHANNEL_CUM[0] else (
        "pickup" if u < _CHANNEL_CUM[1] else ("delivery" if u < _CHANNEL_CUM[2] else "online")
    )  # fmt: skip
    if event:
        channel = "dine_in"
    if channel == "dine_in":
        party = rng.choices(_PARTY, _PARTY_W)[0]
    else:
        party = rng.choice((1, 1, 2, 2, 3))
    if event:
        party = 40

    local_close = closed.astimezone(fd.TZ)
    created = closed - timedelta(seconds=rng.randint(30 * 60, 90 * 60))

    # ---- lines
    raw_lines: list[tuple[str, str, str | None, str, int, int]] = []
    # (name, variation_name, catalog_id, item_type, unit_price, qty)
    if event:
        raw_lines.append(
            ("Private Event Buyout (40 guests)", "", None, "CUSTOM_AMOUNT", 480_000, 1)
        )
    else:
        for item, var, qty in _compose(rng, daypart, channel, party):
            if item is None and var is not None:
                raw_lines.append(
                    ("Open Food", "Regular", fd.ADHOC_CATALOG_ID, "ITEM", var.price_cents, qty)
                )
            elif item is not None and var is not None:
                raw_lines.append((item.name, var.name, var.id, "ITEM", var.price_cents, qty))
    gross_each = [unit * qty for *_, unit, qty in raw_lines]
    gross_total = sum(gross_each)

    # ---- discounts
    weekday_hh = (
        daypart == "dinner"
        and local_close.weekday() in (1, 2, 3, 4)
        and 17 <= local_close.hour < 18
    )
    discounts: list[dict[str, Any]] = []
    disc_total = 0
    if not event and r() < (0.35 if weekday_hh else 0.065):
        if weekday_hh and r() < 0.8:
            pct = 20
            disc_total = _pct(gross_total, pct * 1000)
            discounts.append({"uid": "d1", "name": "Happy Hour 20%", "type": "FIXED_PERCENTAGE",
                              "percentage": str(pct), "scope": "ORDER"})  # fmt: skip
        elif r() < 0.4:
            comp_unit = raw_lines[rng.randrange(len(raw_lines))][4]
            disc_total = min(comp_unit, gross_total)
            discounts.append({"uid": "d1", "name": "Manager Comp", "type": "FIXED_AMOUNT",
                              "amount_money": _m(disc_total), "scope": "ORDER"})  # fmt: skip
        else:
            pct = rng.choice((10, 15, 20))
            name = "Industry Discount" if pct == 20 else f"Loyalty {pct}%"
            disc_total = _pct(gross_total, pct * 1000)
            discounts.append({"uid": "d1", "name": name, "type": "FIXED_PERCENTAGE",
                              "percentage": str(pct), "scope": "ORDER"})  # fmt: skip
        discounts[0]["applied_money"] = _m(disc_total)
    line_disc = _split(disc_total, gross_each) if disc_total else [0] * len(gross_each)
    net_each = [g - x for g, x in zip(gross_each, line_disc, strict=True)]
    net_total = gross_total - disc_total

    # ---- tax / service charge / tip
    tax_total = _pct(net_total, TAX_RATE_PER_100K)
    line_tax = _split(tax_total, net_each)
    sc_total = _pct(net_total, 3000) if (channel == "dine_in" and party >= 6) else 0
    if channel == "dine_in":
        tender_type = "CASH" if r() < 0.12 else "CARD"
    elif channel == "delivery":
        tender_type = "OTHER"
    else:
        tender_type = "CASH" if (channel == "pickup" and r() < 0.2) else "CARD"
    tip_total = 0
    if channel == "dine_in" and tender_type == "CARD":
        tip_total = _pct(net_total, int(rng.uniform(18.0, 22.0) * 1000))
    total = net_total + tax_total + tip_total + sc_total

    # ---- payload
    line_items: list[dict[str, Any]] = []
    for n, (name, vname, cat_id, itype, unit, qty) in enumerate(raw_lines):
        li: dict[str, Any] = {
            "uid": f"l{n + 1}",
            "name": name,
            "quantity": str(qty),
            "item_type": itype,
            "base_price_money": _m(unit),
            "gross_sales_money": _m(gross_each[n]),
            "total_discount_money": _m(line_disc[n]),
            "total_tax_money": _m(line_tax[n]),
            "total_money": _m(net_each[n] + line_tax[n]),
        }
        if vname:
            li["variation_name"] = vname
        if cat_id:
            li["catalog_object_id"] = cat_id
        if line_disc[n]:
            li["applied_discounts"] = [
                {"uid": f"ad{n + 1}", "discount_uid": "d1", "applied_money": _m(line_disc[n])}
            ]
        li["applied_taxes"] = [
            {"uid": f"at{n + 1}", "tax_uid": "t1", "applied_money": _m(line_tax[n])}
        ]
        line_items.append(li)

    tender: dict[str, Any] = {
        "id": f"{order_id}-tdr",
        "type": tender_type,
        "amount_money": _m(total),
        "tip_money": _m(tip_total),
    }
    if tender_type == "CARD":
        tender["card_details"] = {
            "status": "CAPTURED",
            "card": {
                "card_brand": "VISA",
                "last_4": f"{rng.randrange(10000):04d}",
            },
        }

    fulfillments: list[dict[str, Any]] = []
    if channel in ("pickup", "online"):
        fulfillments.append({"uid": "f1", "type": "PICKUP", "state": "COMPLETED"})
    elif channel == "delivery":
        fulfillments.append({"uid": "f1", "type": "DELIVERY", "state": "COMPLETED"})
    source = {
        "dine_in": "Square Point of Sale",
        "pickup": "Square Point of Sale",
        "online": "Square Online",
        "delivery": rng.choice(_DELIVERY_SOURCES),
    }[channel]

    v1: dict[str, Any] = {
        "id": order_id,
        "location_id": fd.LOCATION_ID,
        "created_at": format_ts(created),
        "updated_at": format_ts(closed),
        "closed_at": format_ts(closed),
        "state": "COMPLETED",
        "version": 1 + rng.randrange(3),
        "source": {"name": source},
        "line_items": line_items,
        "taxes": [
            {
                "uid": "t1",
                "name": "NYC Sales Tax",
                "type": "ADDITIVE",
                "percentage": "8.875",
                "scope": "ORDER",
                "applied_money": _m(tax_total),
            }
        ],  # fmt: skip
        "total_money": _m(total),
        "total_tax_money": _m(tax_total),
        "total_discount_money": _m(disc_total),
        "total_tip_money": _m(tip_total),
        "total_service_charge_money": _m(sc_total),
        "net_amounts": {
            "total_money": _m(total),
            "tax_money": _m(tax_total),
            "discount_money": _m(disc_total),
            "tip_money": _m(tip_total),
            "service_charge_money": _m(sc_total),
        },
        "tenders": [tender],
    }
    if discounts:
        v1["discounts"] = discounts
    if sc_total:
        v1["service_charges"] = [{"uid": "sc1", "name": "Large Party Service Charge",
                                  "percentage": "3", "calculation_phase": "SUBTOTAL_PHASE",
                                  "taxable": False, "applied_money": _m(sc_total),
                                  "total_money": _m(sc_total),
                                  "total_tax_money": _m(0)}]  # fmt: skip
    if fulfillments:
        v1["fulfillments"] = fulfillments

    # ---- optional partial refund (a later version of the same order)
    overlay: dict[str, Any] | None = None
    refund_at: datetime | None = None
    if r() < _REFUND_RATE and not event:
        k = rng.randrange(len(raw_lines))
        name, vname, cat_id, itype, unit, qty = raw_lines[k]
        r_disc = _pct(line_disc[k], 100_000 // qty) if qty else 0
        r_tax = _pct(line_tax[k], 100_000 // qty) if qty else 0
        r_total = unit - r_disc + r_tax
        refund_at = closed + timedelta(seconds=rng.randint(86_400, 3 * 86_400))
        # Refunds are booked on their processing date. Keep generated refunds after service
        # has started so an early-evening dashboard does not show returns before any sales.
        refund_day = refund_at.astimezone(fd.TZ).date()
        dinner_start = datetime(refund_day.year, refund_day.month, refund_day.day, 18, tzinfo=fd.TZ)
        refund_at = max(refund_at, dinner_start.astimezone(UTC))
        rli: dict[str, Any] = {
            "uid": "r1",
            "source_line_item_uid": f"l{k + 1}",
            "name": name,
            "quantity": "1",
            "item_type": itype,
            "base_price_money": _m(unit),
            "gross_return_money": _m(unit),
            "total_discount_money": _m(r_disc),
            "total_tax_money": _m(r_tax),
            "total_money": _m(r_total),
        }
        if cat_id:
            rli["catalog_object_id"] = cat_id
        amounts = {"total_money": _m(r_total), "tax_money": _m(r_tax), "discount_money": _m(r_disc),
                   "tip_money": _m(0), "service_charge_money": _m(0)}  # fmt: skip
        overlay = {
            "version": v1["version"] + 1,
            "updated_at": format_ts(refund_at),
            "returns": [
                {
                    "uid": "ret1",
                    "source_order_id": order_id,
                    "return_line_items": [rli],
                    "return_amounts": amounts,
                }
            ],  # fmt: skip
            "refunds": [
                {
                    "id": f"fake-rf-{order_id[9:]}",
                    "location_id": fd.LOCATION_ID,
                    "transaction_id": f"{order_id}-tx",
                    "tender_id": tender["id"],
                    "created_at": format_ts(refund_at),
                    "reason": rng.choice(_REFUND_REASONS),
                    "amount_money": _m(r_total),
                    "status": "COMPLETED",
                }
            ],  # fmt: skip
            "return_amounts": amounts,
            "net_amounts": {
                "total_money": _m(total - r_total),
                "tax_money": _m(tax_total - r_tax),
                "discount_money": _m(disc_total - r_disc),
                "tip_money": _m(tip_total),
                "service_charge_money": _m(sc_total),
            },
        }
    return _OrderRec(order_id, closed, v1, overlay, refund_at)


def _generate_day(d: date) -> list[_OrderRec]:
    plan = fd.day_plan(d)
    if not plan.is_open:
        return []
    trng = random.Random(f"{d}:times")
    slots: list[tuple[datetime, str]] = []
    for _ in range(plan.brunch):
        slots.append(
            (
                _local_seconds(d, int(trng.triangular(10.5 * 3600, 15 * 3600, 12.75 * 3600))),
                "brunch",
            )
        )
    for _ in range(plan.lunch):
        slots.append(
            (_local_seconds(d, int(trng.triangular(12 * 3600, 14.5 * 3600, 13 * 3600))), "lunch")
        )
    for _ in range(plan.dinner):
        u = trng.random()
        if u < 0.20:
            s = trng.uniform(17 * 3600, 19 * 3600)
        elif u < 0.75:
            s = trng.uniform(19 * 3600, 20.5 * 3600)  # peak 19:00-20:30
        else:
            s = trng.uniform(20.5 * 3600, 22.5 * 3600)
        slots.append((_local_seconds(d, int(s)), "dinner"))
    if plan.private_event:
        slots.append((_local_seconds(d, 22 * 3600), "event"))
    slots.sort(key=lambda s: s[0])
    return [_build_order(d, i, t, dp, plan) for i, (t, dp) in enumerate(slots)]


# ---------------------------------------------------------------- cursors


def _enc(obj: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()


def _dec(cursor: str) -> Any:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, TypeError) as exc:
        raise SquareClientError("INVALID_CURSOR: bad cursor", 400) from exc


# ---------------------------------------------------------------- client


class FakeSquareClient:
    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        *,
        history_days: int = HISTORY_DAYS,
        catalog_page_size: int = 100,
        team_page_size: int = 100,
        timecard_page_size: int = 200,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        start = self._now()
        self.epoch: date = start.astimezone(fd.TZ).date() - timedelta(days=history_days)
        self._catalog_updated = datetime(
            self.epoch.year, self.epoch.month, self.epoch.day, 12, tzinfo=UTC
        )
        recent = start.astimezone(fd.TZ).date() - timedelta(days=20)
        self._catalog_recent = datetime(recent.year, recent.month, recent.day, 15, tzinfo=UTC)
        self._catalog_page_size = catalog_page_size
        self._team_page_size = team_page_size
        self._timecard_page_size = timecard_page_size
        self._cache: OrderedDict[date, list[_OrderRec]] = OrderedDict()
        self._revoked: set[str] = set()

    # ------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        n = self._clock()
        return n.replace(tzinfo=UTC) if n.tzinfo is None else n.astimezone(UTC)

    def _auth(self, creds: Credentials) -> None:
        if (
            not creds.access_token.startswith("fake-")
            or creds.access_token in self._revoked
            or creds.expires_at <= self._now()
        ):
            raise SquareAuthError("Square 401: UNAUTHORIZED: token invalid, expired or revoked")

    def _day(self, d: date) -> list[_OrderRec]:
        cached = self._cache.get(d)
        if cached is not None:
            self._cache.move_to_end(d)
            return cached
        orders = _generate_day(d) if d >= self.epoch else []
        self._cache[d] = orders
        if len(self._cache) > _DAY_CACHE_SIZE:
            self._cache.popitem(last=False)
        return orders

    def _make_creds(self, seed: str) -> Credentials:
        now = self._now()
        digest = hashlib.sha256(f"{seed}:{now.isoformat()}".encode()).hexdigest()[:16]
        return Credentials(
            access_token=f"fake-at-{digest}",
            refresh_token=f"fake-rt-{hashlib.sha256(seed.encode()).hexdigest()[:16]}",
            expires_at=now + timedelta(days=30),
            merchant_id=fd.MERCHANT_ID,
        )

    # ------------------------------------------------------------ OAuth

    def authorize_url(self, state: str, redirect_uri: str, scopes: list[str]) -> str:
        return f"/v1/integrations/square/fake-consent?state={state}"

    def obtain_token(self, code: str, redirect_uri: str) -> Credentials:
        if not code.startswith("fake-"):
            raise SquareClientError(
                "Square 400: INVALID_REQUEST_ERROR: invalid authorization code", 400
            )
        return self._make_creds(code)

    def refresh_token(self, creds: Credentials) -> Credentials:
        if not creds.refresh_token.startswith("fake-rt-") or creds.access_token in self._revoked:
            raise SquareAuthError("Square 401: refresh token invalid or revoked")
        new = self._make_creds(creds.refresh_token)
        return Credentials(new.access_token, creds.refresh_token, new.expires_at, creds.merchant_id)

    def revoke(self, creds: Credentials) -> None:
        self._revoked.add(creds.access_token)

    # ------------------------------------------------------------ locations / catalog

    def list_locations(self, creds: Credentials) -> list[dict[str, Any]]:
        self._auth(creds)
        hours = [{"day_of_week": dow, "start_local_time": s, "end_local_time": e}
                 for dow, s, e in (("TUE", "17:00:00", "22:30:00"), ("WED", "17:00:00", "22:30:00"),
                                   ("THU", "11:30:00", "22:30:00"), ("FRI", "11:30:00", "22:30:00"),
                                   ("SAT", "10:00:00", "22:30:00"),
                                   ("SUN", "10:00:00", "22:30:00"))]  # fmt: skip
        return [{
            "id": fd.LOCATION_ID,
            "name": fd.BUSINESS_NAME,
            "business_name": fd.BUSINESS_NAME,
            "merchant_id": fd.MERCHANT_ID,
            "status": "ACTIVE",
            "type": "PHYSICAL",
            "timezone": fd.TZ_NAME,
            "country": "US",
            "language_code": "en-US",
            "currency": "USD",
            "mcc": "5812",
            "capabilities": ["CREDIT_CARD_PROCESSING"],
            "address": {"address_line_1": "214 Atlantic Ave", "locality": "Brooklyn",
                        "administrative_district_level_1": "NY", "postal_code": "11201",
                        "country": "US"},  # fmt: skip
            "business_hours": {"periods": hours},
            "created_at": format_ts(self._catalog_updated),
        }]  # fmt: skip

    def _catalog_objects(self) -> list[dict[str, Any]]:
        objs: list[dict[str, Any]] = []
        base_ts = format_ts(self._catalog_updated)
        base_ver = int(self._catalog_updated.timestamp() * 1000)
        for cid, name in fd.CATEGORIES:
            objs.append({"type": "CATEGORY", "id": cid, "updated_at": base_ts, "version": base_ver,
                         "is_deleted": False, "present_at_all_locations": True,
                         "category_data": {"name": name}})  # fmt: skip
        for it in fd.MENU:
            upd = (
                self._catalog_recent if (it.price_changed or it.deleted) else self._catalog_updated
            )
            ts, ver = format_ts(upd), int(upd.timestamp() * 1000)
            cat_id = fd.CATEGORY_ID_BY_NAME[it.category]
            variations = [{
                "type": "ITEM_VARIATION", "id": v.id, "updated_at": ts, "version": ver,
                "is_deleted": it.deleted, "present_at_all_locations": True,
                "item_variation_data": {"item_id": it.id, "name": v.name, "ordinal": n,
                                        "pricing_type": "FIXED_PRICING",
                                        "price_money": _m(v.price_cents)},
            } for n, v in enumerate(it.variations)]  # fmt: skip
            objs.append({
                "type": "ITEM", "id": it.id, "updated_at": ts, "version": ver,
                "is_deleted": it.deleted, "present_at_all_locations": True,
                "item_data": {"name": it.name, "product_type": "FOOD_AND_BEV",
                              "category_id": cat_id, "categories": [{"id": cat_id, "ordinal": 0}],
                              "reporting_category": {"id": cat_id, "ordinal": 0},
                              "variations": variations},
            })  # fmt: skip
        return objs

    def search_catalog(
        self, creds: Credentials, begin_time: datetime | None, cursor: str | None
    ) -> Page:
        self._auth(creds)
        objs = self._catalog_objects()
        if begin_time is None:
            objs = [o for o in objs if not o["is_deleted"]]
        else:
            objs = [o for o in objs if parse_ts(o["updated_at"]) > begin_time]
        off = int(_dec(cursor)) if cursor else 0
        chunk = objs[off : off + self._catalog_page_size]
        nxt = (
            _enc(off + self._catalog_page_size)
            if off + self._catalog_page_size < len(objs)
            else None
        )
        return build_page(copy.deepcopy(chunk), nxt)

    # ------------------------------------------------------------ orders

    def search_orders(
        self,
        creds: Credentials,
        location_ids: list[str],
        updated_from: datetime,
        updated_to: datetime,
        cursor: str | None,
        limit: int = 500,
    ) -> Page:
        """SearchOrders semantics: state COMPLETED, ``updated_at`` in ``[from, to)``,
        sorted ``UPDATED_AT ASC`` (ties by id), opaque cursors, ``limit`` honored."""
        self._auth(creds)
        if fd.LOCATION_ID not in location_ids or limit <= 0:
            return Page([], None, None)
        now = self._now()
        lo, hi = (
            updated_from.astimezone(UTC),
            min(updated_to.astimezone(UTC), now + timedelta(microseconds=1)),
        )
        after: tuple[str, str] | None = None
        if cursor:
            c_u, c_id = _dec(cursor)
            after = (c_u, c_id)
            lo = max(lo, parse_ts(c_u))
        if lo >= hi:
            return Page([], None, None)

        # An order's updated_at is in [closed_at, closed_at + 3 days], so look back 4 days.
        first = lo.astimezone(fd.TZ).date() - timedelta(days=4)
        last = (hi - timedelta(microseconds=1)).astimezone(fd.TZ).date()
        first = max(first, self.epoch)
        buf: list[tuple[datetime, str, _OrderRec]] = []
        d = first
        one = timedelta(days=1)
        while d <= last:
            if len(buf) > limit:
                buf.sort(key=lambda t: (t[0], t[1]))
                day_floor = _local_seconds(d, 0)  # nothing from day d can update earlier
                if buf[limit - 1][0] < day_floor:
                    break
            for rec in self._day(d):
                if rec.closed_at > now:
                    break  # recs are ordered by closed_at
                upd = rec.updated_at(now)
                if not (lo <= upd < hi):
                    continue
                if after is not None and (format_ts(upd), rec.order_id) <= after:
                    continue
                buf.append((upd, rec.order_id, rec))
            d += one
        buf.sort(key=lambda t: (t[0], t[1]))
        page = buf[:limit]
        nxt = _enc([format_ts(page[-1][0]), page[-1][1]]) if len(buf) > limit else None
        payloads = [copy.deepcopy(rec.payload(now)) for _, _, rec in page]
        return build_page(payloads, nxt)

    def batch_retrieve_orders(
        self, creds: Credentials, location_id: str, order_ids: list[str]
    ) -> list[dict[str, Any]]:
        self._auth(creds)
        if location_id != fd.LOCATION_ID:
            return []
        now = self._now()
        out: list[dict[str, Any]] = []
        for oid in order_ids:
            try:
                d = datetime.strptime(oid.split("-")[2], "%Y%m%d").date()
            except IndexError, ValueError:
                continue
            for rec in self._day(d):
                if rec.order_id == oid and rec.closed_at <= now:
                    out.append(copy.deepcopy(rec.payload(now)))
                    break
        return out

    # ------------------------------------------------------------ team

    def search_team_members(
        self, creds: Credentials, location_ids: list[str], cursor: str | None
    ) -> Page:
        self._auth(creds)
        if location_ids and fd.LOCATION_ID not in location_ids:
            return Page([], None, None)
        ts = format_ts(self._catalog_updated)
        members = []
        for s in fd.STAFF:
            members.append({
                "id": s.id, "status": "ACTIVE", "is_owner": False,
                "given_name": s.given_name, "family_name": s.family_name,
                "email_address": f"{s.given_name}.{s.family_name}@juniperhouse.example".lower(),
                "created_at": ts, "updated_at": ts,
                "assigned_locations": {"assignment_type": "EXPLICIT_LOCATIONS",
                                       "location_ids": [fd.LOCATION_ID]},
                "wage_setting": {"team_member_id": s.id, "is_overtime_exempt": False, "version": 1,
                                 "created_at": ts, "updated_at": ts,
                                 "job_assignments": [{"job_title": s.role, "pay_type": "HOURLY",
                                                      "hourly_rate": _m(s.hourly_cents),
                                                      "weekly_hours": 40}]},
            })  # fmt: skip
        off = int(_dec(cursor)) if cursor else 0
        size = self._team_page_size
        nxt = _enc(off + size) if off + size < len(members) else None
        return build_page(members[off : off + size], nxt)

    # ------------------------------------------------------------ timecards

    def _timecard(
        self, d: date, idx: int, sh: fd.ShiftSpec, now: datetime
    ) -> dict[str, Any] | None:
        if sh.start.astimezone(UTC) > now:
            return None
        staff = fd.STAFF_BY_ID[sh.staff_id]
        tc_id = f"fake-tc-{d:%Y%m%d}-{idx:03d}"
        closed = sh.end.astimezone(UTC) <= now

        def iso(dt: datetime) -> str:
            return dt.isoformat(timespec="seconds")

        breaks: list[dict[str, Any]] = []
        if sh.break_start is not None and sh.break_start.astimezone(UTC) <= now:
            b_end = sh.break_start + timedelta(minutes=30)
            brk: dict[str, Any] = {"id": f"{tc_id}-b1", "start_at": iso(sh.break_start),
                                   "break_type_id": "BT_MEAL", "name": "Meal Break",
                                   "expected_duration": "PT30M", "is_paid": False}  # fmt: skip
            if b_end.astimezone(UTC) <= now:
                brk["end_at"] = iso(b_end)
            breaks.append(brk)
        tc: dict[str, Any] = {
            "id": tc_id,
            "location_id": fd.LOCATION_ID,
            "team_member_id": staff.id,
            "timezone": fd.TZ_NAME,
            "start_at": iso(sh.start),
            "status": "CLOSED" if closed else "OPEN",
            "wage": {
                "title": staff.role,
                "hourly_rate": _m(staff.hourly_cents),
                "job_id": f"JOB_{staff.role.upper().replace(' ', '_')}",
                "tip_eligible": staff.tipped,
            },  # fmt: skip
            "breaks": breaks,
            "version": 2 if closed else 1,
            "created_at": iso(sh.start),
            "updated_at": iso(sh.end if closed else sh.start),
        }
        if closed:
            tc["end_at"] = iso(sh.end)
        return tc

    def search_timecards(
        self,
        creds: Credentials,
        location_ids: list[str],
        workday_from: date,
        workday_to: date,
        cursor: str | None,
    ) -> Page:
        """Timecards whose start_at (local) date is in ``[workday_from, workday_to]``, sorted by
        start_at then id. Open timecards (no end_at, status OPEN) are shifts in progress."""
        self._auth(creds)
        if fd.LOCATION_ID not in location_ids:
            return Page([], None, None)
        now = self._now()
        after: tuple[str, str] | None = None
        if cursor:
            c_s, c_id = _dec(cursor)
            after = (c_s, c_id)
        found: list[tuple[str, str, dict[str, Any]]] = []
        d = max(workday_from, self.epoch)
        last = min(workday_to, now.astimezone(fd.TZ).date())
        while d <= last:
            for idx, sh in enumerate(fd.shifts_for_day(d)):
                tc = self._timecard(d, idx, sh, now)
                if tc is None:
                    continue
                key = (format_ts(sh.start), tc["id"])
                if after is not None and key <= after:
                    continue
                found.append((key[0], key[1], tc))
            d += timedelta(days=1)
        found.sort(key=lambda t: (t[0], t[1]))
        size = self._timecard_page_size
        page = found[:size]
        nxt = _enc([page[-1][0], page[-1][1]]) if len(found) > size else None
        return build_page([t[2] for t in page], nxt)
