"""Shared dataclasses: transport types, canonical records, mapping signals.

Money is always integer cents. Timestamps are timezone-aware datetimes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

Channel = Literal["dine_in", "pickup", "delivery", "online", "other"]
MappingIssueKind = Literal[
    "unmapped_location", "unmapped_item", "unmapped_team_member", "unknown_channel"
]


def parse_ts(value: str) -> datetime:
    """Parse an RFC 3339 timestamp (``Z`` or numeric offset) into an aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def format_ts(dt: datetime) -> str:
    """Format as Square does for UTC timestamps: ``2025-01-05T23:15:00.000Z``."""
    u = dt.astimezone(UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------- transport


@dataclass(frozen=True, slots=True)
class Credentials:
    access_token: str
    refresh_token: str
    expires_at: datetime
    merchant_id: str


@dataclass(frozen=True, slots=True)
class Window:
    """Half-open time window ``[start, end)``."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class RawItem:
    external_id: str
    updated_at: datetime | None
    version: str | None
    payload: dict[str, Any]


@dataclass(slots=True)
class Page:
    items: list[RawItem]
    next_cursor: str | None
    max_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    event_id: str
    type: str
    merchant_id: str
    object_type: str | None
    object_id: str | None
    created_at: datetime | None = None
    location_id: str | None = None


# ---------------------------------------------------------------- signals / context


@dataclass(frozen=True, slots=True)
class MappingIssueSignal:
    """Emitted by normalizers; the integrator upserts these into ``mapping_issues``.

    ``external_id`` is the dedupe key (catalog object id or item name for unmapped_item,
    team member id for unmapped_team_member, ``"<source>|<fulfillment types>"`` for
    unknown_channel). ``label`` is human readable.
    """

    kind: MappingIssueKind
    external_id: str
    label: str


def _no_catalog(_: str) -> str | None:
    return None


def _any_member(_: str) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class MappingContext:
    """Per-location context for the pure normalizers."""

    timezone: str
    business_day_start: time = time(4, 0)
    # Square catalog_object_id -> platform menu item variant external id, None if unknown.
    resolve_catalog: Callable[[str], str | None] = _no_catalog
    # Square team_member_id -> True if the staff member is known.
    is_known_team_member: Callable[[str], bool] = _any_member


# ---------------------------------------------------------------- canonical records


@dataclass(frozen=True, slots=True)
class CanonicalSalesLine:
    line_uid: str
    name: str
    variation_name: str | None
    external_item_ref: str  # catalog_object_id, or the item name for ad-hoc lines
    menu_item_variant_external_id: str | None  # None => unmapped
    quantity: Decimal
    gross_cents: int
    discount_cents: int
    net_cents: int  # gross - discount
    tax_cents: int


@dataclass(frozen=True, slots=True)
class CanonicalReturn:
    """A refund, attributed to the business date it was *processed*."""

    external_id: str
    order_external_id: str
    location_external_id: str
    business_date: date  # from refund created_at, NOT the original order's date
    processed_at: datetime
    gross_cents: int
    discount_cents: int
    net_cents: int  # gross - discount: the amount that reduces net sales
    tax_cents: int
    total_cents: int  # what the guest got back (net + tax)
    reason: str | None


@dataclass(frozen=True, slots=True)
class CanonicalSalesOrder:
    """One sale, on the business date it closed.

    ``net_sales`` is the SALE-DATE contribution (gross - discounts). Returns are NOT
    subtracted here; they are reported as separate CanonicalReturn records on their own
    business date. ``returns`` is a memo total of those records. Daily net sales =
    sum(order.net_sales for the date) - sum(return.net_cents for the date).
    Tax, tips, service charges and gift cards are excluded from all sales figures.
    """

    external_id: str
    location_external_id: str
    business_date: date
    closed_at: datetime
    created_at: datetime | None
    source_updated_at: datetime
    source_version: int
    channel: Channel
    source_name: str | None
    currency: str
    gross_sales: int
    discounts: int
    returns: int  # memo: sum of this order's CanonicalReturn.net_cents
    net_sales: int  # gross_sales - discounts (before returns)
    tax: int
    tips: int
    service_charges: int
    total: int
    lines: list[CanonicalSalesLine] = field(default_factory=list)
    normalizer_version: str = "1"

    @property
    def net_sales_after_returns(self) -> int:
        return self.net_sales - self.returns


@dataclass(frozen=True, slots=True)
class CanonicalMenuVariant:
    external_id: str
    name: str
    price_cents: int | None
    currency: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class CanonicalMenuItem:
    external_id: str
    name: str
    category_external_id: str | None
    category_name: str | None
    deleted: bool
    source_updated_at: datetime | None
    source_version: int | None
    variants: list[CanonicalMenuVariant] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CanonicalMenuCategory:
    external_id: str
    name: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class CanonicalStaff:
    external_id: str
    given_name: str | None
    family_name: str | None
    status: str  # "active" | "inactive"
    job_title: str | None
    hourly_rate_cents: int | None
    source_updated_at: datetime | None
    email: str | None = None
    # Square location ids the member is assigned to; None means all locations.
    assigned_location_ids: tuple[str, ...] | None = ()


@dataclass(frozen=True, slots=True)
class CanonicalTimecard:
    external_id: str
    location_external_id: str
    team_member_external_id: str
    business_date: date  # from start_at (clock in)
    start_at: datetime
    end_at: datetime | None
    paid_hours: float  # excludes unpaid breaks; 0.0 while open
    unpaid_break_minutes: int
    hourly_rate_cents: int
    cost_cents: int  # 0 while open (cost lands when the timecard closes)
    open: bool
    tip_eligible: bool
    job_title: str | None
    source_updated_at: datetime | None
    source_version: int | None
