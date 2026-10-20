"""Team HTTP API (docs/api.md, Team section).

Every /v1/team route requires owner, admin, or manager (`StaffDep`); employees get 403.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.security.sessions import OrgSession, StaffDep
from app.team import service
from app.team.schemas import (
    EmployeeCreate,
    EmployeeList,
    EmployeeOut,
    EmployeeUpdate,
    MergeRequest,
    PositionCreate,
    PositionList,
    PositionOut,
    PositionUpdate,
    StationCreate,
    StationList,
    StationOut,
    StationUpdate,
)

router = APIRouter()
team = APIRouter(prefix="/v1/team", tags=["team"])

LocationId = Annotated[uuid.UUID, Query()]


# ---------------------------------------------------------------- employees


@team.get("/employees", response_model=EmployeeList)
async def list_employees(
    ctx: StaffDep,
    db: OrgSession,
    location_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> EmployeeList:
    scope = await service.scope_for(ctx)
    return EmployeeList(
        data=await service.list_employees(
            db, scope, location_id=location_id, include_inactive=include_inactive
        )
    )


@team.post("/employees", status_code=201, response_model=EmployeeOut)
async def create_employee(body: EmployeeCreate, ctx: StaffDep, db: OrgSession) -> EmployeeOut:
    return await service.create_employee(db, await service.scope_for(ctx), body)


@team.patch("/employees/{id}", response_model=EmployeeOut)
async def update_employee(
    id: uuid.UUID, body: EmployeeUpdate, ctx: StaffDep, db: OrgSession
) -> EmployeeOut:
    return await service.update_employee(db, await service.scope_for(ctx), id, body)


@team.post("/employees/{id}/merge", response_model=EmployeeOut)
async def merge_employee(
    id: uuid.UUID, body: MergeRequest, ctx: StaffDep, db: OrgSession
) -> EmployeeOut:
    return await service.merge_employees(db, await service.scope_for(ctx), id, body.into_id)


# ---------------------------------------------------------------- positions


@team.get("/positions", response_model=PositionList)
async def list_positions(
    ctx: StaffDep, db: OrgSession, include_archived: bool = False
) -> PositionList:
    rows = await service.list_positions(db, ctx.org_id, include_archived=include_archived)
    return PositionList(data=[PositionOut.model_validate(r) for r in rows])


@team.post("/positions", status_code=201, response_model=PositionOut)
async def create_position(body: PositionCreate, ctx: StaffDep, db: OrgSession) -> PositionOut:
    pos = await service.create_position(db, await service.scope_for(ctx), body)
    return PositionOut.model_validate(pos)


@team.patch("/positions/{id}", response_model=PositionOut)
async def update_position(
    id: uuid.UUID, body: PositionUpdate, ctx: StaffDep, db: OrgSession
) -> PositionOut:
    pos = await service.update_position(db, await service.scope_for(ctx), id, body)
    return PositionOut.model_validate(pos)


# ---------------------------------------------------------------- stations


@team.get("/stations", response_model=StationList)
async def list_stations(
    ctx: StaffDep, db: OrgSession, location_id: LocationId, include_archived: bool = False
) -> StationList:
    rows = await service.list_stations(
        db, await service.scope_for(ctx), location_id, include_archived=include_archived
    )
    return StationList(data=[StationOut.model_validate(r) for r in rows])


@team.post("/stations", status_code=201, response_model=StationOut)
async def create_station(body: StationCreate, ctx: StaffDep, db: OrgSession) -> StationOut:
    station = await service.create_station(db, await service.scope_for(ctx), body)
    return StationOut.model_validate(station)


@team.patch("/stations/{id}", response_model=StationOut)
async def update_station(
    id: uuid.UUID, body: StationUpdate, ctx: StaffDep, db: OrgSession
) -> StationOut:
    station = await service.update_station(db, await service.scope_for(ctx), id, body)
    return StationOut.model_validate(station)


router.include_router(team)
