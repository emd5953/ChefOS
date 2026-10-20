"""Commerce read API (docs/api.md, Commerce section)."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.commerce import service
from app.commerce.schemas import DailyOut, ItemsOut, SummaryOut, TodayOut
from app.identity.models import Location
from app.security.sessions import OrgSession, StaffDep

router = APIRouter(prefix="/v1/commerce", tags=["commerce"])

LocationId = Annotated[uuid.UUID, Query()]
From = Annotated[date, Query(alias="from")]
To = Annotated[date, Query(alias="to")]


async def _location(ctx: StaffDep, db: OrgSession, location_id: uuid.UUID) -> Location:
    return await service.get_location(db, await ctx.allowed_location_ids(), location_id)


@router.get("/daily", response_model=DailyOut)
async def daily(
    ctx: StaffDep, db: OrgSession, location_id: LocationId, from_: From, to: To
) -> DailyOut:
    loc = await _location(ctx, db, location_id)
    return await service.daily(db, ctx.org_id, loc, from_, to)


@router.get("/summary", response_model=SummaryOut)
async def summary(
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    from_: From,
    to: To,
    compare: Literal["previous_period", "previous_year"] = "previous_period",
) -> SummaryOut:
    loc = await _location(ctx, db, location_id)
    return await service.summary(db, ctx.org_id, loc, from_, to, compare)


@router.get("/items", response_model=ItemsOut)
async def items(
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    from_: From,
    to: To,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ItemsOut:
    loc = await _location(ctx, db, location_id)
    return await service.items(db, ctx.org_id, loc, from_, to, limit)


@router.get("/today", response_model=TodayOut)
async def today(ctx: StaffDep, db: OrgSession, location_id: LocationId) -> TodayOut:
    loc = await _location(ctx, db, location_id)
    return await service.today(db, ctx.org_id, loc)
