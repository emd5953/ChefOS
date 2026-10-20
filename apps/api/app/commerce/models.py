"""Canonical commerce entities and read models.

Written by the integration layer (app.integrations.pipeline), read by the Commerce API.
Money is bigint minor units. Every table carries `org_id` and sits under RLS. Staff and
timecards live in app.team.models.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base
from app.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


_NOW = text("now()")


class MenuItem(Base):
    __tablename__ = "menu_items"
    __table_args__ = (Index("ix_menu_items_org_id", "org_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(default=None)


class MenuItemVariant(Base):
    __tablename__ = "menu_item_variants"
    __table_args__ = (Index("ix_menu_item_variants_menu_item_id", "menu_item_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    menu_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("menu_items.id"))
    name: Mapped[str] = mapped_column(String)
    price: Mapped[int | None] = mapped_column(BigInteger, default=None)
    currency: Mapped[str] = mapped_column(String, default="USD", server_default="USD")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class SalesOrder(Base):
    """One completed sale, on the business date it closed. Returns live in sales_order_returns."""

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_sales_orders_conn_external"),
        Index("ix_sales_orders_org_location_date", "org_id", "location_id", "business_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integration_connections.id"))
    external_id: Mapped[str] = mapped_column(String)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    business_date: Mapped[date] = mapped_column(Date)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String, default="completed", server_default="completed")
    channel: Mapped[str] = mapped_column(String)
    gross_sales: Mapped[int] = mapped_column(BigInteger)
    discounts: Mapped[int] = mapped_column(BigInteger)
    returns: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    net_sales: Mapped[int] = mapped_column(BigInteger)
    tax: Mapped[int] = mapped_column(BigInteger)
    tips: Mapped[int] = mapped_column(BigInteger)
    service_charges: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    source_version: Mapped[int] = mapped_column(BigInteger)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class SalesOrderLine(Base):
    """Lines denormalize location and business date so item reports never join orders."""

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        Index("ix_sales_order_lines_order_id", "order_id"),
        Index("ix_sales_order_lines_org_location_date", "org_id", "location_id", "business_date"),
        Index(
            "ix_sales_order_lines_unmapped",
            "external_item_ref",
            postgresql_where=text("menu_item_variant_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    business_date: Mapped[date] = mapped_column(Date)
    line_uid: Mapped[str] = mapped_column(String)
    menu_item_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_item_variants.id"), default=None
    )
    external_item_ref: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    gross: Mapped[int] = mapped_column(BigInteger)
    discounts: Mapped[int] = mapped_column(BigInteger)
    net: Mapped[int] = mapped_column(BigInteger)


class SalesOrderReturn(Base):
    """A refund, booked on the business date it was processed."""

    __tablename__ = "sales_order_returns"
    __table_args__ = (
        UniqueConstraint("order_id", "external_id", name="uq_sales_order_returns_order_external"),
        Index("ix_sales_order_returns_org_location_date", "org_id", "location_id", "business_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    business_date: Mapped[date] = mapped_column(Date)
    external_id: Mapped[str] = mapped_column(String)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String)
    amount: Mapped[int] = mapped_column(BigInteger)  # net sales reduction, cents
    tax: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String, default=None)


class DailySales(Base):
    __tablename__ = "daily_sales"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    channel: Mapped[str] = mapped_column(String, primary_key=True)
    orders: Mapped[int] = mapped_column(Integer)
    net_sales: Mapped[int] = mapped_column(BigInteger)
    gross_sales: Mapped[int] = mapped_column(BigInteger)
    discounts: Mapped[int] = mapped_column(BigInteger)
    returns: Mapped[int] = mapped_column(BigInteger)
    tax: Mapped[int] = mapped_column(BigInteger)
    tips: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyLabor(Base):
    __tablename__ = "daily_labor"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scheduled_hours: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    actual_hours: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    actual_cost: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
