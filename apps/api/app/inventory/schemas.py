"""Inventory request and response models (docs/api.md, Inventory section).

Quantities are `numeric(18,6)` in the item's base unit and serialize as decimal strings. Money is
integer minor units; derived per-base-unit costs are decimal minor units (6 places).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ORG_CURRENCY = "USD"
BASE_UNIT = {"mass": "g", "volume": "ml", "count": "ea"}
# Stable, item-independent units per dimension. Supplier cases belong in packs, not here, and
# there are no cross-dimension conversions (that would need an item-specific density).
CONVERSION_UNITS: dict[str, frozenset[str]] = {
    "mass": frozenset({"kg", "oz", "lb"}),
    "volume": frozenset({"l", "fl_oz"}),
    "count": frozenset({"dozen"}),
}
# numeric(18,6): 12 integer digits.
MAX_QUANTITY = Decimal("999999999999")


def _text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Must not be blank")
    return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _currency(value: str) -> str:
    if value != ORG_CURRENCY:
        raise ValueError(f"Currency must match the organization currency ({ORG_CURRENCY})")
    return value


Name = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_text)]
Label = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_text)]
OptionalText = Annotated[str | None, Field(max_length=500), AfterValidator(_optional_text)]
Reason = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_text)]
Currency = Annotated[str, AfterValidator(_currency)]
# Positive quantities that fit numeric(18,6) exactly; anything finer is rejected (422), never
# silently rounded by the database.
Quantity = Annotated[Decimal, Field(gt=0, le=MAX_QUANTITY, max_digits=18, decimal_places=6)]
NonNegQuantity = Annotated[Decimal, Field(ge=0, le=MAX_QUANTITY, max_digits=18, decimal_places=6)]
Factor = Quantity
Minor = Annotated[int, Field(ge=0, le=10**12)]
Dimension = Literal["mass", "volume", "count"]
BaseUnit = Literal["g", "ml", "ea"]


def check_conversions(dimension: str, conversions: dict[str, Decimal]) -> None:
    allowed = CONVERSION_UNITS[dimension]
    bad = sorted(unit for unit in conversions if unit not in allowed)
    if bad:
        raise ValueError(
            f"Units {', '.join(bad)} do not belong to the {dimension} dimension "
            f"(allowed: {', '.join(sorted(allowed))})"
        )


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    data: list[T]


# ---------------------------------------------------------------- suppliers


class SupplierCreate(BaseModel):
    name: Name
    contact_name: OptionalText = None
    email: OptionalText = None
    phone: OptionalText = None
    notes: OptionalText = None


class SupplierUpdate(BaseModel):
    name: Name | None = None
    contact_name: OptionalText = None
    email: OptionalText = None
    phone: OptionalText = None
    notes: OptionalText = None
    active: bool | None = None


class SupplierOut(Out):
    id: uuid.UUID
    name: str
    contact_name: str | None
    email: str | None
    phone: str | None
    notes: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- storage areas


class StorageAreaCreate(BaseModel):
    location_id: uuid.UUID
    name: Label
    sort_order: int = Field(default=0, ge=0, le=10_000)


class StorageAreaUpdate(BaseModel):
    name: Label | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    active: bool | None = None


class StorageAreaOut(Out):
    id: uuid.UUID
    location_id: uuid.UUID
    name: str
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- items


class ItemCreate(BaseModel):
    name: Name
    category: Annotated[str, Field(max_length=120)] = ""
    base_unit: BaseUnit
    dimension: Dimension
    default_supplier_id: uuid.UUID | None = None
    currency: Currency = ORG_CURRENCY
    conversions: dict[str, Factor] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _units(self) -> ItemCreate:
        if self.base_unit != BASE_UNIT[self.dimension]:
            raise ValueError("Base unit must match dimension (mass=g, volume=ml, count=ea)")
        check_conversions(self.dimension, self.conversions)
        self.category = self.category.strip()
        return self


class ItemUpdate(BaseModel):
    """Only fields present in the body change; `default_supplier_id: null` clears it."""

    name: Name | None = None
    category: Annotated[str, Field(max_length=120)] | None = None
    active: bool | None = None
    default_supplier_id: uuid.UUID | None = None


class ConversionUpdate(BaseModel):
    conversions: dict[str, Factor]


class ConversionOut(BaseModel):
    unit: str
    base_units_per_unit: Decimal


class ConversionsOut(BaseModel):
    item_id: uuid.UUID
    base_unit: str
    conversions: dict[str, Decimal]


class ItemOut(Out):
    id: uuid.UUID
    name: str
    category: str
    base_unit: str
    dimension: str
    active: bool
    default_supplier_id: uuid.UUID | None
    currency: str
    created_at: datetime
    updated_at: datetime
    conversions: list[ConversionOut] = []


class StockItemOut(ItemOut):
    """An item with its stock at one location."""

    on_hand_base_quantity: Decimal
    par_base_quantity: Decimal | None
    below_par: bool
    storage_area_id: uuid.UUID | None
    # Minor units per base unit from the latest posted receipt line (any location), 6 places.
    last_received_cost_minor: Decimal | None
    cost_as_of: datetime | None


class ParUpdate(BaseModel):
    location_id: uuid.UUID
    par_base_quantity: NonNegQuantity


class ParOut(Out):
    location_id: uuid.UUID
    item_id: uuid.UUID
    par_base_quantity: Decimal


class WasteCreate(BaseModel):
    location_id: uuid.UUID
    item_id: uuid.UUID
    quantity: Quantity
    unit: str | None = None
    reason: Reason
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]


class WasteOut(Out):
    id: uuid.UUID
    location_id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    base_unit: str
    quantity_base: Decimal
    unit_cost_minor: int | None
    reason: str
    station_id: uuid.UUID | None
    shift_id: uuid.UUID | None
    business_date: date
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AssignmentOut(Out):
    location_id: uuid.UUID
    storage_area_id: uuid.UUID
    item_id: uuid.UUID


# ---------------------------------------------------------------- packs


class PackCreate(BaseModel):
    item_id: uuid.UUID
    supplier_id: uuid.UUID
    name: Label
    purchase_unit: Label
    base_quantity: Quantity
    last_price_minor: Minor | None = None
    currency: Currency = ORG_CURRENCY


class PackUpdate(BaseModel):
    name: Label | None = None
    purchase_unit: Label | None = None
    base_quantity: Quantity | None = None
    last_price_minor: Minor | None = None
    active: bool | None = None


class PackOut(Out):
    id: uuid.UUID
    item_id: uuid.UUID
    supplier_id: uuid.UUID
    name: str
    purchase_unit: str
    base_quantity: Decimal
    last_price_minor: int | None
    currency: str
    active: bool
    created_at: datetime
    updated_at: datetime
    item_name: str | None = None
    base_unit: str | None = None


# ---------------------------------------------------------------- counts


class CountStart(BaseModel):
    location_id: uuid.UUID
    storage_area_id: uuid.UUID


class CountEntry(BaseModel):
    quantity: NonNegQuantity
    unit: Annotated[str, Field(min_length=1, max_length=20)]
    note: OptionalText = None


class CountReview(BaseModel):
    note: Reason


class CountSubmit(BaseModel):
    pass


ExceptionState = Literal["clear", "review_required", "confirmed"]


class CountLineOut(Out):
    id: uuid.UUID
    session_id: uuid.UUID
    location_id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    base_unit: str
    expected_base_quantity: Decimal
    expected_level_version: int
    counted_base_quantity: Decimal | None
    entered_quantity: Decimal | None
    entered_unit: str | None
    exception_state: ExceptionState
    note: str | None
    # Units a count may be entered in: the base unit first, then configured conversions.
    units: list[str] = []


class CountSessionOut(Out):
    id: uuid.UUID
    location_id: uuid.UUID
    storage_area_id: uuid.UUID
    storage_area_name: str
    status: Literal["draft", "submitted"]
    started_by: uuid.UUID
    submitted_by: uuid.UUID | None
    started_at: datetime
    submitted_at: datetime | None
    note: str | None
    line_count: int = 0
    counted_count: int = 0
    exception_count: int = 0


class CountSessionDetail(CountSessionOut):
    lines: list[CountLineOut]


# ---------------------------------------------------------------- purchasing


class PurchaseOrderLineCreate(BaseModel):
    pack_id: uuid.UUID
    ordered_packs: Quantity
    unit_price_minor: Minor


class POCreate(BaseModel):
    location_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: Currency = ORG_CURRENCY
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1, max_length=200)
    expected_at: date | None = None
    note: OptionalText = None


class POUpdate(BaseModel):
    expected_at: date | None = None
    note: OptionalText = None
    lines: list[PurchaseOrderLineCreate] | None = Field(default=None, min_length=1, max_length=200)


class ReasonIn(BaseModel):
    reason: Reason


POStatus = Literal["open", "partially_received", "closed"]


class POLineOut(Out):
    id: uuid.UUID
    purchase_order_id: uuid.UUID
    item_id: uuid.UUID
    pack_id: uuid.UUID | None
    ordered_packs: Decimal
    ordered_pack_name: str
    ordered_base_quantity_per_pack: Decimal
    ordered_unit_price_minor: int
    ordered_quantity: Decimal
    received_quantity: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal
    item_name: str
    base_unit: str


class PurchaseOrderOut(Out):
    id: uuid.UUID
    location_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    status: POStatus
    ordered_at: datetime
    expected_at: date | None
    created_by: uuid.UUID
    currency: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    total_ordered_minor: int = 0


class PurchaseOrderDetail(PurchaseOrderOut):
    lines: list[POLineOut]


class ReceiveLine(BaseModel):
    line_id: uuid.UUID
    received_base_quantity: Quantity
    total_cost_minor: Minor
    accept_overage: bool = False
    reason: OptionalText = None

    @model_validator(mode="after")
    def _reason(self) -> ReceiveLine:
        if self.accept_overage and not self.reason:
            raise ValueError("A reason is required to accept an overage")
        return self


class ReceiveRequest(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1, max_length=200)
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _unique(self) -> ReceiveRequest:
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Each purchase order line may appear once per receipt")
        return self


class ReceiptLineOut(Out):
    id: uuid.UUID
    receipt_id: uuid.UUID
    purchase_order_line_id: uuid.UUID
    item_id: uuid.UUID
    received_base_quantity: Decimal
    total_cost_minor: int
    item_name: str
    base_unit: str


class ReceiptOut(Out):
    id: uuid.UUID
    location_id: uuid.UUID
    purchase_order_id: uuid.UUID
    received_by: uuid.UUID
    received_at: datetime
    business_date: date
    idempotency_key: str
    created_at: datetime
    lines: list[ReceiptLineOut]


# ---------------------------------------------------------------- recipes


class RecipeIngredientIn(BaseModel):
    item_id: uuid.UUID
    quantity_base: Quantity


RecipeIngredientCreate = RecipeIngredientIn


def _unique_ingredients(ingredients: list[RecipeIngredientIn] | None) -> None:
    if ingredients is not None and len({i.item_id for i in ingredients}) != len(ingredients):
        raise ValueError("An inventory item may only appear once in a recipe")


class RecipeCreate(BaseModel):
    name: Name
    yield_quantity: Quantity
    yield_unit: Label
    ingredients: list[RecipeIngredientIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> RecipeCreate:
        _unique_ingredients(self.ingredients)
        return self


class RecipeUpdate(BaseModel):
    name: Name | None = None
    yield_quantity: Quantity | None = None
    yield_unit: Label | None = None
    active: bool | None = None
    ingredients: list[RecipeIngredientIn] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> RecipeUpdate:
        _unique_ingredients(self.ingredients)
        return self


class RecipeOut(Out):
    id: uuid.UUID
    name: str
    yield_quantity: Decimal
    yield_unit: str
    active: bool
    created_at: datetime
    updated_at: datetime


class RecipeIngredientOut(Out):
    item_id: uuid.UUID
    quantity_base: Decimal
    item_name: str
    base_unit: str
    item_active: bool
    # Minor units per base unit from the latest receipt; null when never received.
    unit_cost_minor: Decimal | None
    line_cost_minor: Decimal | None


class RecipeDetail(RecipeOut):
    ingredients: list[RecipeIngredientOut]
    plate_cost_minor: int | None
    cost_incomplete: bool
    cost_as_of: datetime | None


class MenuRecipeCost(BaseModel):
    menu_item_id: uuid.UUID
    menu_item_name: str
    menu_item_active: bool
    recipe_id: uuid.UUID | None
    recipe_name: str | None
    # Cost of one yield unit, rounded half-up to whole minor units; null when unmapped or any
    # ingredient has no received cost (`cost_incomplete`).
    plate_cost_minor: int | None
    cost_incomplete: bool
    missing_cost_items: list[str]
    cost_as_of: datetime | None


class MenuItemOut(Out):
    id: uuid.UUID
    name: str
    category: str
    active: bool
    recipe_id: uuid.UUID | None


class MenuRecipeLink(BaseModel):
    recipe_id: uuid.UUID | None = None


class MenuRecipeLinkOut(BaseModel):
    menu_item_id: uuid.UUID
    recipe_id: uuid.UUID | None
