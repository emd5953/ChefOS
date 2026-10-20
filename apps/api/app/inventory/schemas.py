import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None


class StorageAreaCreate(BaseModel):
    location_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = 0


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    active: bool | None = None


class StorageAreaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = None
    active: bool | None = None


class PackUpdate(BaseModel):
    name: str | None = None
    purchase_unit: str | None = None
    base_quantity: Decimal | None = Field(default=None, gt=0)
    last_price_minor: int | None = Field(default=None, ge=0)
    active: bool | None = None


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = ""
    base_unit: str
    dimension: str
    default_supplier_id: uuid.UUID | None = None
    currency: str = "USD"
    conversions: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unit_dimension(self) -> ItemCreate:
        base = {"mass": "g", "volume": "ml", "count": "ea"}
        if self.dimension not in base or self.base_unit != base[self.dimension]:
            raise ValueError("base unit must match dimension (mass=g, volume=ml, count=ea)")
        units = {"mass": {"g", "kg", "oz", "lb"}, "volume": {"ml", "l", "fl_oz"}, "count": {"ea"}}
        if any(unit not in units[self.dimension] for unit in self.conversions):
            raise ValueError("conversion units must match the item's dimension")
        if any(not value.is_finite() or value <= 0 for value in self.conversions.values()):
            raise ValueError("conversion factors must be finite and positive")
        if self.currency != "USD":
            raise ValueError("currency must match the organization currency (USD)")
        return self


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    active: bool | None = None
    default_supplier_id: uuid.UUID | None = None


class ConversionUpdate(BaseModel):
    conversions: dict[str, Decimal]


class PackCreate(BaseModel):
    item_id: uuid.UUID
    supplier_id: uuid.UUID
    name: str
    purchase_unit: str
    base_quantity: Decimal = Field(gt=0)
    last_price_minor: int | None = Field(default=None, ge=0)
    currency: str = "USD"


class ParUpdate(BaseModel):
    location_id: uuid.UUID
    par_base_quantity: Decimal = Field(ge=0)


class ItemOut(Out):
    id: uuid.UUID
    name: str
    category: str
    base_unit: str
    dimension: str
    active: bool
    default_supplier_id: uuid.UUID | None
    currency: str
    on_hand_base_quantity: Decimal = Decimal(0)
    par_base_quantity: Decimal | None = None
    below_par: bool = False


class CountStart(BaseModel):
    location_id: uuid.UUID
    storage_area_id: uuid.UUID


class CountEntry(BaseModel):
    quantity: Decimal = Field(ge=0)
    unit: str
    note: str | None = None


class CountSubmit(BaseModel):
    pass


class PurchaseOrderLineCreate(BaseModel):
    pack_id: uuid.UUID
    ordered_packs: Decimal = Field(gt=0)
    unit_price_minor: int = Field(ge=0)


class POCreate(BaseModel):
    location_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: str = Field(default="USD", pattern="^USD$")
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1)
    expected_at: date | None = None
    note: str | None = None


class POUpdate(BaseModel):
    expected_at: date | None = None
    note: str | None = None
    lines: list[PurchaseOrderLineCreate] | None = Field(default=None, min_length=1)


class RecipeIngredientCreate(BaseModel):
    item_id: uuid.UUID
    quantity_base: Decimal = Field(gt=0)


class RecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    yield_quantity: Decimal = Field(gt=0)
    yield_unit: str
    ingredients: list[RecipeIngredientCreate] = Field(min_length=1)


class RecipeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    yield_quantity: Decimal | None = Field(default=None, gt=0)
    yield_unit: str | None = None
    active: bool | None = None
    ingredients: list[RecipeIngredientCreate] | None = Field(default=None, min_length=1)


class ReceiveLine(BaseModel):
    line_id: uuid.UUID
    received_base_quantity: Decimal = Field(gt=0)
    total_cost_minor: int = Field(ge=0)
    accept_overage: bool = False
    reason: str | None = None


class ReceiveRequest(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
