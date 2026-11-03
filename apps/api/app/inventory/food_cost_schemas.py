"""API contract for operational food cost variance reports."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FoodCostVarianceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: uuid.UUID
    opening_count_session_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)
    closing_count_session_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)


class FoodCostItem(BaseModel):
    item_id: uuid.UUID
    item_name: str
    base_unit: str
    opening_quantity: Decimal | None
    receipts_quantity: Decimal
    closing_quantity: Decimal | None
    actual_depletion_quantity: Decimal | None
    theoretical_usage_quantity: Decimal | None
    weighted_average_unit_cost_minor: Decimal | None
    actual_cost_minor: int | None
    theoretical_cost_minor: int | None
    variance_minor: int | None
    variance_pct: Decimal | None
    recorded_waste_quantity: Decimal
    status: Literal[
        "covered", "missing_count", "negative_depletion", "missing_cost", "missing_recipe_history"
    ]


class FoodCostCoverage(BaseModel):
    count_items_expected: int
    count_items_covered: int
    sales_lines: int
    mapped_sales_lines: int
    mapped_sales_quantity: Decimal
    total_sales_quantity: Decimal
    sales_quantity_coverage_pct: Decimal | None
    excluded_sales_lines: dict[str, int]
    has_returns: bool
    orders_freshness: str
    orders_as_of: datetime | None


class FoodCostVarianceOut(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    location_id: uuid.UUID
    opening_count_date: date
    closing_count_date: date
    period_from: date
    period_to: date
    opening_submitted_at: list[datetime]
    closing_submitted_at: list[datetime]
    currency: str
    valuation_policy: Literal["period_weighted_average_received_cost"] = (
        "period_weighted_average_received_cost"
    )
    yield_assumption: str = "One sold menu unit equals one recipe yield unit."
    totals_are_complete: bool
    actual_cost_minor: int | None
    theoretical_cost_minor: int | None
    variance_minor: int | None
    variance_pct: Decimal | None
    coverage: FoodCostCoverage
    items: list[FoodCostItem]
    limitations: list[str]
