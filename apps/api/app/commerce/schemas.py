import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["dine_in", "pickup", "delivery", "online", "other"]
CHANNELS: tuple[Channel, ...] = ("dine_in", "pickup", "delivery", "online", "other")
Currency = Literal["USD"]


class Totals(BaseModel):
    net_sales: int
    gross_sales: int
    discounts: int
    returns: int
    tax: int
    tips: int
    orders: int
    avg_check: int | None
    labor_cost: int
    labor_hours: float
    labor_pct: float | None


class FreshnessOut(BaseModel):
    stream: Literal["orders", "timecards", "catalog", "team_members"]
    location_id: uuid.UUID
    data_as_of: datetime | None
    status: Literal["fresh", "stale", "failing", "not_connected"]


class DayOut(Totals):
    business_date: date
    channels: dict[Channel, int]


class DailyOut(BaseModel):
    location_id: uuid.UUID
    currency: Currency = "USD"
    days: list[DayOut]
    freshness: list[FreshnessOut]


class ChangeOut(BaseModel):
    net_sales_pct: float | None
    orders_pct: float | None
    avg_check_pct: float | None
    labor_pct_pts: float | None


class ChannelShare(BaseModel):
    channel: Channel
    net_sales: int
    share_pct: float


class SummaryOut(BaseModel):
    currency: Currency = "USD"
    period: dict[str, date]
    compare_period: dict[str, date]
    current: Totals
    previous: Totals
    change: ChangeOut
    channels: list[ChannelShare]
    freshness: list[FreshnessOut]


class ItemOut(BaseModel):
    name: str
    category: str
    quantity: float
    net_sales: int
    share_pct: float


class CategoryMixOut(BaseModel):
    category: str
    net_sales: int
    share_pct: float


class ItemsOut(BaseModel):
    currency: Currency = "USD"
    total_net_sales: int = 0
    categories: list[CategoryMixOut] = Field(default_factory=list)
    items: list[ItemOut] = Field(default_factory=list)
    unmapped_net_sales: int = 0


class HourOut(BaseModel):
    hour: int
    net_sales: int
    orders: int


class TodayOut(BaseModel):
    currency: Currency = "USD"
    business_date: date
    net_sales: int
    orders: int
    avg_check: int | None
    last_order_at: datetime | None
    by_hour: list[HourOut]
    freshness: list[FreshnessOut]
