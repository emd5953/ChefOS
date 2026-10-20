import uuid
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


class POCreate(BaseModel):
    location_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: str = "USD"
    lines: list[dict[str, object]] = Field(min_length=1)


class ReceiveRequest(BaseModel):
    lines: list[dict[str, object]] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class RecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    yield_quantity: Decimal = Field(gt=0)
    yield_unit: str
    ingredients: list[dict[str, object]]
