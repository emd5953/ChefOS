"""Purchase orders: create, edit, close/reopen, and receiving."""
# ruff: noqa: E501

import uuid
from collections.abc import Collection
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.inventory import service
from app.inventory.schemas import (
    POCreate,
    POUpdate,
    ReceiveRequest,
)


async def list_orders(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT p.*,s.name supplier_name FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id AND s.org_id=p.org_id WHERE p.org_id=:org AND p.location_id=:loc ORDER BY p.ordered_at DESC"
        ),
        {"org": org_id, "loc": location_id},
    )
    return {"data": [service.as_dict(r) for r in rows]}


async def update_order(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    po_id: uuid.UUID,
    body: POUpdate,
) -> dict[str, Any]:
    order = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": org_id},
    )
    if not (po := order.first()):
        raise not_found("Purchase order not found")
    await service.authorized_location(db, allowed_location_ids, po.location_id)
    if po.status == "closed":
        raise service.conflict("Reopen a closed purchase order before editing")
    if body.lines is not None:
        existing_lines = await db.execute(
            text(
                "SELECT received_quantity FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id FOR UPDATE"
            ),
            {"org": org_id, "id": po_id},
        )
        if any(line.received_quantity > 0 for line in existing_lines):
            raise service.conflict("Orders with posted receipts cannot have their lines replaced")
        await db.execute(
            text("DELETE FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id"),
            {"org": org_id, "id": po_id},
        )
        for line_request in body.lines:
            pack = await db.execute(
                text(
                    "SELECT p.*,i.name item_name,i.base_unit FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.id=:id AND p.org_id=:org AND p.active AND i.active AND p.supplier_id=:supplier"
                ),
                {"id": line_request.pack_id, "org": org_id, "supplier": po.supplier_id},
            )
            if not (pack_row := pack.first()):
                raise not_found("Active pack for this supplier not found")
            await db.execute(
                text(
                    "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) VALUES(:id,:org,:loc,:po,:item,:pack,:packs,:pack_name,:base,:price,:qty,:item_name,:unit,:pack)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": org_id,
                    "loc": po.location_id,
                    "po": po_id,
                    "item": pack_row.item_id,
                    "pack": pack_row.id,
                    "packs": line_request.ordered_packs,
                    "pack_name": pack_row.name,
                    "base": pack_row.base_quantity,
                    "price": line_request.unit_price_minor,
                    "qty": line_request.ordered_packs * pack_row.base_quantity,
                    "item_name": pack_row.item_name,
                    "unit": pack_row.base_unit,
                },
            )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET expected_at=COALESCE(:expected,expected_at),note=COALESCE(:note,note),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {"expected": body.expected_at, "note": body.note, "id": po_id, "org": org_id},
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=po.location_id,
        action="updated",
    )
    return service.as_dict(result.one())


async def create_order(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: POCreate
) -> dict[str, Any]:
    po_id = uuid.uuid7()
    supplier = await db.scalar(
        text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
        {"id": body.supplier_id, "org": org_id},
    )
    if supplier is None:
        raise not_found("Supplier not found")
    await db.execute(
        text(
            "INSERT INTO purchase_orders(id,org_id,location_id,supplier_id,created_by,currency,expected_at,note) VALUES(:id,:org,:loc,:supplier,:user,:currency,:expected,:note)"
        ),
        {
            "id": po_id,
            "org": org_id,
            "loc": body.location_id,
            "supplier": body.supplier_id,
            "user": user_id,
            "currency": body.currency,
            "expected": body.expected_at,
            "note": body.note,
        },
    )
    for line_request in body.lines:
        raw = line_request.model_dump()
        pack_id = uuid.UUID(str(raw["pack_id"]))
        packs = await db.execute(
            text(
                "SELECT p.*,i.name item_name,i.base_unit FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.id=:id AND p.org_id=:org AND p.active AND i.active AND p.supplier_id=:supplier"
            ),
            {"id": pack_id, "org": org_id, "supplier": body.supplier_id},
        )
        if not (pack := packs.first()):
            raise not_found("Pack not found for this supplier")
        packs_count = Decimal(str(raw["ordered_packs"]))
        unit_price = int(str(raw["unit_price_minor"]))
        await db.execute(
            text(
                "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) VALUES(:id,:org,:loc,:po,:item,:pack,:packs,:name,:base,:price,:qty,:item_name,:unit,:pack)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": body.location_id,
                "po": po_id,
                "item": pack.item_id,
                "pack": pack.id,
                "packs": packs_count,
                "name": pack.name,
                "base": pack.base_quantity,
                "price": unit_price,
                "qty": packs_count * pack.base_quantity,
                "item_name": pack.item_name,
                "unit": pack.base_unit,
            },
        )
    result = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org"),
        {"id": po_id, "org": org_id},
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=body.location_id,
        action="created",
    )
    return {**service.as_dict(result.one()), "lines": []}


async def order_detail(
    db: AsyncSession,
    org_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    po_id: uuid.UUID,
) -> dict[str, Any]:
    result = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org"),
        {"id": po_id, "org": org_id},
    )
    if not (po := result.first()):
        raise not_found("Purchase order not found")
    await service.authorized_location(db, allowed_location_ids, po.location_id)
    lines = await db.execute(
        text(
            "SELECT * FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id ORDER BY item_name"
        ),
        {"org": org_id, "id": po_id},
    )
    return {**service.as_dict(po), "lines": [service.as_dict(r) for r in lines]}


async def close_order(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    po_id: uuid.UUID,
    body: dict[str, str],
) -> dict[str, Any]:
    if not body.get("reason", "").strip():
        raise validation_error({"reason": "A close reason is required"})
    po = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": org_id},
    )
    if not (order := po.first()):
        raise not_found("Purchase order not found")
    if order.status == "closed":
        raise service.conflict("Purchase order is already closed")
    await service.authorized_location(db, allowed_location_ids, order.location_id)
    await db.execute(
        text(
            "UPDATE purchase_order_lines SET closed_quantity=ordered_quantity-received_quantity WHERE org_id=:org AND purchase_order_id=:id"
        ),
        {"org": org_id, "id": po_id},
    )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET status='closed',updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {"id": po_id, "org": org_id},
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=order.location_id,
        action="closed",
        details={"reason": body["reason"]},
    )
    return service.as_dict(result.one())


async def reopen_order(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    po_id: uuid.UUID,
    body: dict[str, str],
) -> dict[str, Any]:
    if not body.get("reason", "").strip():
        raise validation_error({"reason": "A reopen reason is required"})
    existing = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": org_id},
    )
    if not (closed_order := existing.first()):
        raise not_found("Purchase order not found")
    await service.authorized_location(db, allowed_location_ids, closed_order.location_id)
    if closed_order.status != "closed":
        raise service.conflict("Only a closed purchase order can be reopened")
    await db.execute(
        text(
            "UPDATE purchase_order_lines SET closed_quantity=0 WHERE org_id=:org AND purchase_order_id=:id"
        ),
        {"org": org_id, "id": po_id},
    )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET status=CASE WHEN EXISTS(SELECT 1 FROM purchase_order_lines l WHERE l.org_id=:org AND l.purchase_order_id=:id AND l.received_quantity>0) THEN 'partially_received' ELSE 'open' END,updated_at=now() WHERE id=:id AND org_id=:org AND status='closed' RETURNING *"
        ),
        {"id": po_id, "org": org_id},
    )
    if not (order := result.first()):
        raise not_found("Closed purchase order not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=order.location_id,
        action="reopened",
        details={"reason": body["reason"]},
    )
    return service.as_dict(order)


async def receive_order(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    allowed_location_ids: Collection[uuid.UUID],
    po_id: uuid.UUID,
    body: ReceiveRequest,
) -> dict[str, Any]:
    po = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": org_id},
    )
    if not (order := po.first()):
        raise not_found("Purchase order not found")
    await service.authorized_location(db, allowed_location_ids, order.location_id)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{org_id}:{body.idempotency_key}"},
    )
    previous = await db.execute(
        text("SELECT * FROM receipts WHERE org_id=:org AND idempotency_key=:key"),
        {"org": org_id, "key": body.idempotency_key},
    )
    if prior := previous.first():
        if prior.purchase_order_id != po_id:
            raise service.conflict(
                "This idempotency key was already used for another purchase order"
            )
        original_lines = await db.execute(
            text("SELECT * FROM receipt_lines WHERE org_id=:org AND receipt_id=:id ORDER BY id"),
            {"org": org_id, "id": prior.id},
        )
        saved_lines = list(original_lines)
        saved = {
            row.purchase_order_line_id: (row.received_base_quantity, row.total_cost_minor)
            for row in saved_lines
        }
        requested = {
            line.line_id: (line.received_base_quantity, line.total_cost_minor)
            for line in body.lines
        }
        # The posted quantities/costs and approval reasons are durable, including receipts
        # created before this check existed. Compare semantics, independent of line order.
        details = await db.scalar(
            text(
                "SELECT details FROM audit_events WHERE org_id=:org AND entity_id=:id "
                "AND entity_type='receipt' AND action='posted'"
            ),
            {"org": org_id, "id": prior.id},
        )
        saved_overages = {
            entry["line_id"]: entry["reason"] for entry in (details or {}).get("overages", [])
        }
        requested_overages = {
            str(line.line_id): str(line.reason) for line in body.lines if line.accept_overage
        }
        if saved != requested or saved_overages != requested_overages:
            raise service.conflict("This idempotency key was already used with a different request")
        return {**service.as_dict(prior), "lines": [service.as_dict(row) for row in saved_lines]}
    if order.status == "closed":
        raise service.conflict("Reopen this purchase order before receiving")
    business_day = service.business_date_for(
        await service.authorized_location(db, allowed_location_ids, order.location_id)
    )
    receipt_id = uuid.uuid7()
    await db.execute(
        text(
            "INSERT INTO receipts(id,org_id,location_id,purchase_order_id,received_by,business_date,idempotency_key) VALUES(:id,:org,:loc,:po,:user,:date,:key)"
        ),
        {
            "id": receipt_id,
            "org": org_id,
            "loc": order.location_id,
            "po": po_id,
            "user": user_id,
            "date": business_day,
            "key": body.idempotency_key,
        },
    )
    for line_request in body.lines:
        raw = line_request.model_dump()
        line_id = uuid.UUID(str(raw["line_id"]))
        q = Decimal(str(raw["received_base_quantity"]))
        cost = int(str(raw["total_cost_minor"]))
        rows = await db.execute(
            text(
                "SELECT * FROM purchase_order_lines WHERE id=:id AND org_id=:org AND purchase_order_id=:po FOR UPDATE"
            ),
            {"id": line_id, "org": org_id, "po": po_id},
        )
        if not (line := rows.first()):
            raise not_found("Purchase order line not found")
        over = bool(raw.get("accept_overage", False))
        if over and not str(raw.get("reason", "")).strip():
            raise validation_error({"reason": "A reason is required to accept an overage"})
        if q <= 0 or (
            q > line.ordered_quantity - line.received_quantity - line.closed_quantity
            and not (over and role in service.MANAGERS)
        ):
            raise service.conflict(
                "Received quantity exceeds the remaining order. Manager approval is required for overage."
            )
        await db.execute(
            text(
                "UPDATE purchase_order_lines SET received_quantity=received_quantity+:q WHERE id=:id AND org_id=:org"
            ),
            {"q": q, "id": line_id, "org": org_id},
        )
        await db.execute(
            text(
                "INSERT INTO receipt_lines(id,org_id,location_id,receipt_id,purchase_order_line_id,item_id,received_base_quantity,total_cost_minor,item_name,base_unit) VALUES(:id,:org,:loc,:receipt,:line,:item,:q,:cost,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": order.location_id,
                "receipt": receipt_id,
                "line": line_id,
                "item": line.item_id,
                "q": q,
                "cost": cost,
                "name": line.item_name,
                "unit": line.base_unit,
            },
        )
        await db.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version,updated_at) VALUES(:org,:loc,:item,:q,1,now()) ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET on_hand_base_quantity=inventory_levels.on_hand_base_quantity+excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
            ),
            {"org": org_id, "loc": order.location_id, "item": line.item_id, "q": q},
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'receipt',:source,:q,:date,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": order.location_id,
                "item": line.item_id,
                "source": receipt_id,
                "q": q,
                "date": business_day,
                "name": line.item_name,
                "unit": line.base_unit,
            },
        )
    await db.execute(
        text(
            "UPDATE purchase_orders SET status=CASE WHEN EXISTS(SELECT 1 FROM purchase_order_lines l WHERE l.org_id=:org AND l.purchase_order_id=:id AND l.ordered_quantity>l.received_quantity+l.closed_quantity) THEN 'partially_received' ELSE 'closed' END,updated_at=now() WHERE id=:id AND org_id=:org"
        ),
        {"id": po_id, "org": org_id},
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="receipt",
        entity_id=receipt_id,
        location_id=order.location_id,
        action="posted",
        details={
            "overages": [
                {"line_id": str(line.line_id), "reason": str(line.reason)}
                for line in body.lines
                if line.accept_overage
            ]
        },
    )
    receipt = await db.execute(
        text("SELECT * FROM receipts WHERE id=:id AND org_id=:org"),
        {"id": receipt_id, "org": org_id},
    )
    lines = await db.execute(
        text("SELECT * FROM receipt_lines WHERE receipt_id=:id AND org_id=:org"),
        {"id": receipt_id, "org": org_id},
    )
    return {**service.as_dict(receipt.one()), "lines": [service.as_dict(r) for r in lines]}
