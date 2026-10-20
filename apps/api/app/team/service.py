"""Team use cases: employees, positions, stations, the POS import hook, and merge.

Scope rules (docs/specs/team.md): owner/admin manage every employee; a manager manages
employees assigned to a location they have access to. Employees never reach this module's
manage paths (`require_staff_role` on the routes).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import ProblemError, forbidden, not_found, validation_error
from app.identity.models import Location
from app.security.sessions import AuthContext
from app.team import notify
from app.team.models import (
    EmployeeLocation,
    EmployeePosition,
    Invitation,
    Position,
    StaffMember,
    Station,
)
from app.team.schemas import (
    EmployeeCreate,
    EmployeeOut,
    EmployeePositionOut,
    EmployeeUpdate,
    LoginStatus,
    PositionCreate,
    PositionRef,
    PositionUpdate,
    StationCreate,
    StationUpdate,
)

ADMIN_ROLES = frozenset({"owner", "admin"})
# Positions created from POS job titles get the next color in this cycle.
PALETTE = (
    "#2861f5", "#138a4b", "#b26a00", "#d12f2f", "#7c3aed", "#0e7490", "#be185d", "#4d7c0f",
)  # fmt: skip
POS_OWNED = ("display_name", "active", "job_title", "hourly_rate")


@dataclass(frozen=True)
class Scope:
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    location_ids: frozenset[uuid.UUID]

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES


async def scope_for(ctx: AuthContext) -> Scope:
    return Scope(ctx.org_id, ctx.user_id, ctx.role, frozenset(await ctx.allowed_location_ids()))


def require_admin(scope: Scope) -> None:
    if not scope.is_admin:
        raise forbidden()


async def get_location(db: AsyncSession, scope: Scope, location_id: uuid.UUID) -> Location:
    """A location the caller may manage; anything else reads as missing."""
    loc = await db.get(Location, location_id) if location_id in scope.location_ids else None
    if loc is None:
        raise not_found("Location not found")
    return loc


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- employees


async def employee_location_ids(
    db: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    ids = list(ids)
    if ids:
        rows = await db.execute(
            select(EmployeeLocation.employee_id, EmployeeLocation.location_id).where(
                EmployeeLocation.employee_id.in_(ids)
            )
        )
        for r in rows:
            out[r.employee_id].add(r.location_id)
    return out


async def _employee_positions(
    db: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[EmployeePositionOut]]:
    out: dict[uuid.UUID, list[EmployeePositionOut]] = defaultdict(list)
    if ids:
        rows = await db.execute(
            select(EmployeePosition.employee_id, EmployeePosition.is_primary, Position)
            .join(Position, Position.id == EmployeePosition.position_id)
            .where(EmployeePosition.employee_id.in_(ids))
            .order_by(EmployeePosition.is_primary.desc(), Position.name)
        )
        for r in rows:
            p = r.Position
            out[r.employee_id].append(
                EmployeePositionOut(id=p.id, name=p.name, color=p.color, is_primary=r.is_primary)
            )
    return out


async def _invited(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Employees with a live pending invitation."""
    if not ids:
        return set()
    rows = await db.scalars(
        select(Invitation.employee_id).where(
            Invitation.employee_id.in_(ids),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > func.now(),
        )
    )
    return {r for r in rows if r is not None}


def effective_rate(emp: StaffMember) -> int | None:
    """rate_override ?? POS hourly_rate ?? None. None is shown as "no rate", never guessed."""
    return emp.rate_override if emp.rate_override is not None else emp.hourly_rate


async def employees_out(db: AsyncSession, emps: Sequence[StaffMember]) -> list[EmployeeOut]:
    ids = [e.id for e in emps]
    positions = await _employee_positions(db, ids)
    locations = await employee_location_ids(db, ids)
    invited = await _invited(db, [e.id for e in emps if e.user_id is None])
    out = []
    for e in emps:
        login: LoginStatus = (
            "active" if e.user_id is not None else "invited" if e.id in invited else "none"
        )
        out.append(
            EmployeeOut(
                id=e.id,
                display_name=e.display_name,
                email=e.email,
                phone=e.phone,
                source="manual" if e.source == "manual" else "square",
                active=e.active,
                job_title=e.job_title,
                pos_hourly_rate=e.hourly_rate,
                rate_override=e.rate_override,
                effective_rate=effective_rate(e),
                positions=positions.get(e.id, []),
                location_ids=sorted(locations.get(e.id, set())),
                login=login,
                merged_into_id=e.merged_into_id,
                updated_at=e.updated_at,
            )
        )
    return out


async def get_employee(db: AsyncSession, scope: Scope, employee_id: uuid.UUID) -> StaffMember:
    """An employee the caller may manage (any of their locations); otherwise 404."""
    emp = await db.scalar(
        select(StaffMember).where(StaffMember.id == employee_id, StaffMember.org_id == scope.org_id)
    )
    if emp is not None and not scope.is_admin:
        locs = (await employee_location_ids(db, [emp.id])).get(emp.id, set())
        if not locs & scope.location_ids:
            emp = None
    if emp is None:
        raise not_found("Employee not found")
    return emp


async def list_employees(
    db: AsyncSession,
    scope: Scope,
    *,
    location_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[EmployeeOut]:
    stmt = select(StaffMember).where(StaffMember.org_id == scope.org_id)
    if not include_inactive:
        stmt = stmt.where(StaffMember.active, StaffMember.merged_into_id.is_(None))
    at = select(EmployeeLocation.employee_id).where(EmployeeLocation.employee_id == StaffMember.id)
    if location_id is not None:
        await get_location(db, scope, location_id)
        stmt = stmt.where(exists(at.where(EmployeeLocation.location_id == location_id)))
    elif not scope.is_admin:
        stmt = stmt.where(exists(at.where(EmployeeLocation.location_id.in_(scope.location_ids))))
    emps = list(await db.scalars(stmt.order_by(StaffMember.display_name, StaffMember.id)))
    return await employees_out(db, emps)


async def check_locations(
    db: AsyncSession, scope: Scope, ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    wanted = set(ids)
    found = set(
        await db.scalars(
            select(Location.id).where(Location.org_id == scope.org_id, Location.id.in_(wanted))
        )
    )
    if wanted - (found & scope.location_ids):
        raise validation_error({"location_ids": "Unknown location"})
    return wanted


async def _check_positions(db: AsyncSession, org_id: uuid.UUID, refs: list[PositionRef]) -> None:
    wanted = {r.position_id for r in refs}
    if not wanted:
        return
    found = set(
        await db.scalars(
            select(Position.id).where(
                Position.org_id == org_id, Position.id.in_(wanted), Position.archived_at.is_(None)
            )
        )
    )
    if wanted - found:
        raise validation_error({"positions": "Unknown position"})


async def _set_positions(
    db: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, refs: list[PositionRef]
) -> None:
    await db.execute(delete(EmployeePosition).where(EmployeePosition.employee_id == employee_id))
    await db.flush()
    for r in refs:
        db.add(
            EmployeePosition(
                org_id=org_id,
                employee_id=employee_id,
                position_id=r.position_id,
                is_primary=r.is_primary,
            )
        )
    await db.flush()


async def _snapshot(db: AsyncSession, emp: StaffMember) -> dict[str, Any]:
    """Audit before/after view of an employee (team-owned fields plus name and active)."""
    positions = (await _employee_positions(db, [emp.id])).get(emp.id, [])
    locations = (await employee_location_ids(db, [emp.id])).get(emp.id, set())
    return {
        "display_name": emp.display_name,
        "active": emp.active,
        "email": emp.email,
        "phone": emp.phone,
        "rate_override": emp.rate_override,
        "location_ids": sorted(str(i) for i in locations),
        "positions": [{"id": str(p.id), "is_primary": p.is_primary} for p in positions],
    }


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = [k for k in after if before.get(k) != after[k]]
    return {"before": {k: before.get(k) for k in keys}, "after": {k: after[k] for k in keys}}


async def create_employee(db: AsyncSession, scope: Scope, data: EmployeeCreate) -> EmployeeOut:
    locations = await check_locations(db, scope, data.location_ids)
    await _check_positions(db, scope.org_id, data.positions)
    emp = StaffMember(
        org_id=scope.org_id,
        display_name=data.display_name,
        email=data.email,
        phone=data.phone,
        rate_override=data.rate_override,
        source="manual",
    )
    db.add(emp)
    await db.flush()
    for loc in sorted(locations):
        db.add(EmployeeLocation(org_id=scope.org_id, employee_id=emp.id, location_id=loc))
    await _set_positions(db, scope.org_id, emp.id, data.positions)
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="employee",
        entity_id=emp.id,
        action="employee.created",
        details={"after": await _snapshot(db, emp)},
    )
    return (await employees_out(db, [emp]))[0]


def _pos_owned(field: str) -> ProblemError:
    return ProblemError(
        422,
        "pos_owned_field",
        "This field is synced from Square. Change it in Square.",
        errors={field: "Synced from Square"},
        title="Field is managed by the POS",
    )


async def update_employee(
    db: AsyncSession, scope: Scope, employee_id: uuid.UUID, data: EmployeeUpdate
) -> EmployeeOut:
    emp = await get_employee(db, scope, employee_id)
    fields = data.model_fields_set
    if emp.source == "square":
        for field in POS_OWNED:
            if field in fields:
                raise _pos_owned(field)
    else:
        for field in ("job_title", "hourly_rate"):
            if field in fields:
                raise validation_error(
                    {field: "Manual employees use positions and rate_override instead"}
                )
        for field in ("display_name", "active"):
            if field in fields and getattr(data, field) is None:
                raise validation_error({field: "Must not be null"})
    if emp.merged_into_id is not None:
        raise ProblemError(409, "conflict", "This employee was merged into another record")

    before = await _snapshot(db, emp)
    if "location_ids" in fields and data.location_ids is not None:
        wanted = await check_locations(db, scope, data.location_ids)
        current = (await employee_location_ids(db, [emp.id])).get(emp.id, set())
        # A manager edits only the locations they can see; the others are kept as they are.
        target = wanted if scope.is_admin else (current - scope.location_ids) | wanted
        await db.execute(
            delete(EmployeeLocation).where(
                EmployeeLocation.employee_id == emp.id,
                EmployeeLocation.location_id.not_in(target),
            )
        )
        for loc in sorted(target - current):
            db.add(EmployeeLocation(org_id=scope.org_id, employee_id=emp.id, location_id=loc))
    if "positions" in fields and data.positions is not None:
        await _check_positions(db, scope.org_id, data.positions)
        await _set_positions(db, scope.org_id, emp.id, data.positions)
    for field in ("display_name", "active", "email", "phone", "rate_override"):
        if field in fields:
            setattr(emp, field, getattr(data, field))
    emp.updated_at = _now()
    await db.flush()
    after = await _snapshot(db, emp)
    if after != before:
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="employee",
            entity_id=emp.id,
            action="employee.updated",
            details=_changes(before, after),
        )
    return (await employees_out(db, [emp]))[0]


# Tables whose employee reference moves to the surviving record on merge. Later migrations
# add their tables here.
MERGE_REPOINT: list[tuple[str, str]] = [
    ("timecards", "staff_member_id"),
    ("shifts", "employee_id"),
    ("published_shifts", "employee_id"),
]


async def merge_employees(
    db: AsyncSession, scope: Scope, from_id: uuid.UUID, into_id: uuid.UUID
) -> EmployeeOut:
    """Fold a duplicate into the surviving record in one transaction (owner/admin)."""
    require_admin(scope)
    if from_id == into_id:
        raise validation_error({"into_id": "Choose a different employee"})
    src = await get_employee(db, scope, from_id)
    dst = await get_employee(db, scope, into_id)
    await db.execute(
        select(StaffMember.id)
        .where(StaffMember.id.in_([src.id, dst.id]))
        .order_by(StaffMember.id)
        .with_for_update()
    )
    await db.refresh(src)
    await db.refresh(dst)
    if src.merged_into_id is not None or dst.merged_into_id is not None:
        raise ProblemError(409, "conflict", "One of these employees was already merged")
    if src.user_id is not None and dst.user_id is not None:
        raise ProblemError(
            409, "conflict", "Both employees have logins. Remove one login before merging."
        )

    params = {"src": src.id, "dst": dst.id}
    await db.execute(
        text(
            "UPDATE external_refs SET entity_id = :dst "
            "WHERE entity_type = 'staff_member' AND entity_id = :src"
        ),
        params,
    )
    moved: dict[str, int] = {}
    for table, column in MERGE_REPOINT:
        res = await db.execute(
            text(f"UPDATE {table} SET {column} = :dst WHERE {column} = :src"),  # noqa: S608
            params,
        )
        moved[table] = int(getattr(res, "rowcount", 0) or 0)
    await db.execute(
        text(
            "INSERT INTO employee_locations (org_id, employee_id, location_id) "
            "SELECT org_id, :dst, location_id FROM employee_locations WHERE employee_id = :src "
            "ON CONFLICT DO NOTHING"
        ),
        params,
    )
    has_primary = await db.scalar(
        select(func.count())
        .select_from(EmployeePosition)
        .where(EmployeePosition.employee_id == dst.id, EmployeePosition.is_primary)
    )
    await db.execute(
        text(
            "INSERT INTO employee_positions (org_id, employee_id, position_id, is_primary) "
            "SELECT org_id, :dst, position_id, is_primary AND NOT :has_primary "
            "FROM employee_positions WHERE employee_id = :src ON CONFLICT DO NOTHING"
        ),
        {**params, "has_primary": bool(has_primary)},
    )
    # A POS-linked record makes the survivor POS-linked: sync owns its name, rate, and status.
    if src.source == "square":
        dst.source = "square"
        dst.display_name = src.display_name
        dst.job_title = src.job_title
        dst.hourly_rate = src.hourly_rate
    for field in ("email", "phone", "rate_override"):
        if getattr(dst, field) is None:
            setattr(dst, field, getattr(src, field))
    user_id = src.user_id
    src.user_id = None
    src.active = False
    src.merged_into_id = dst.id
    src.updated_at = dst.updated_at = _now()
    await db.flush()
    if user_id is not None:
        dst.user_id = user_id
        await db.flush()
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="employee",
        entity_id=dst.id,
        action="employee.merged",
        details={"from_id": str(src.id), "into_id": str(dst.id), "moved": moved},
    )
    return (await employees_out(db, [dst]))[0]


# ---------------------------------------------------------------- positions


async def list_positions(
    db: AsyncSession, org_id: uuid.UUID, *, include_archived: bool = False
) -> list[Position]:
    stmt = select(Position).where(Position.org_id == org_id)
    if not include_archived:
        stmt = stmt.where(Position.archived_at.is_(None))
    return list(await db.scalars(stmt.order_by(func.lower(Position.name))))


async def _next_color(db: AsyncSession, org_id: uuid.UUID) -> str:
    n = await db.scalar(select(func.count()).select_from(Position).where(Position.org_id == org_id))
    return PALETTE[int(n or 0) % len(PALETTE)]


def _name_taken() -> ProblemError:
    return ProblemError(
        409, "conflict", "A position with this name exists", errors={"name": "Already exists"}
    )


async def _flush_unique(db: AsyncSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise _name_taken() from None


async def create_position(db: AsyncSession, scope: Scope, data: PositionCreate) -> Position:
    taken = await db.scalar(
        select(Position.id).where(
            Position.org_id == scope.org_id, func.lower(Position.name) == data.name.lower()
        )
    )
    if taken is not None:
        raise _name_taken()
    pos = Position(
        org_id=scope.org_id,
        name=data.name,
        color=data.color or await _next_color(db, scope.org_id),
    )
    db.add(pos)
    await _flush_unique(db)
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="position",
        entity_id=pos.id,
        action="position.created",
        details={"after": {"name": pos.name, "color": pos.color}},
    )
    return pos


async def update_position(
    db: AsyncSession, scope: Scope, position_id: uuid.UUID, data: PositionUpdate
) -> Position:
    pos = await db.scalar(
        select(Position).where(Position.id == position_id, Position.org_id == scope.org_id)
    )
    if pos is None:
        raise not_found("Position not found")
    before = {"name": pos.name, "color": pos.color, "archived": pos.archived_at is not None}
    if data.name is not None:
        pos.name = data.name
    if data.color is not None:
        pos.color = data.color
    if data.archived is not None and data.archived != before["archived"]:
        pos.archived_at = _now() if data.archived else None
    await _flush_unique(db)
    after = {"name": pos.name, "color": pos.color, "archived": pos.archived_at is not None}
    if after != before:
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="position",
            entity_id=pos.id,
            action="position.archived"
            if after["archived"] and not before["archived"]
            else "position.updated",
            details=_changes(before, after),
        )
    return pos


# ---------------------------------------------------------------- stations


async def list_stations(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID, *, include_archived: bool = False
) -> list[Station]:
    await get_location(db, scope, location_id)
    stmt = select(Station).where(Station.org_id == scope.org_id, Station.location_id == location_id)
    if not include_archived:
        stmt = stmt.where(Station.archived_at.is_(None))
    return list(await db.scalars(stmt.order_by(func.lower(Station.name))))


async def create_station(db: AsyncSession, scope: Scope, data: StationCreate) -> Station:
    await get_location(db, scope, data.location_id)
    station = Station(org_id=scope.org_id, location_id=data.location_id, name=data.name)
    db.add(station)
    await db.flush()
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="station",
        entity_id=station.id,
        action="station.created",
        location_id=station.location_id,
        details={"after": {"name": station.name}},
    )
    return station


async def update_station(
    db: AsyncSession, scope: Scope, station_id: uuid.UUID, data: StationUpdate
) -> Station:
    station = await db.scalar(
        select(Station).where(Station.id == station_id, Station.org_id == scope.org_id)
    )
    if station is None or station.location_id not in scope.location_ids:
        raise not_found("Station not found")
    before = {"name": station.name, "archived": station.archived_at is not None}
    if data.name is not None:
        station.name = data.name
    if data.archived is not None and data.archived != before["archived"]:
        station.archived_at = _now() if data.archived else None
    await db.flush()
    after = {"name": station.name, "archived": station.archived_at is not None}
    if after != before:
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="station",
            entity_id=station.id,
            action="station.archived"
            if after["archived"] and not before["archived"]
            else "station.updated",
            location_id=station.location_id,
            details=_changes(before, after),
        )
    return station


# ---------------------------------------------------------------- POS import hook


@dataclass(frozen=True)
class ImportedStaff:
    """One team member as the POS reported it, after the pipeline resolved its locations."""

    employee_id: uuid.UUID
    email: str | None
    job_title: str | None
    location_ids: tuple[uuid.UUID, ...]
    deactivated: bool = False  # went from active to inactive in this sync


_SET_EMAIL = text("""
UPDATE staff_members s SET email = k.email, updated_at = now()
FROM unnest(CAST(:ids AS uuid[]), CAST(:emails AS text[])) AS k(id, email)
WHERE s.id = k.id AND s.email IS NULL AND k.email IS NOT NULL
""")
_POSITION_IDS = text(
    "SELECT id, lower(name) AS key FROM positions WHERE org_id = :org AND lower(name) = ANY(:keys)"
)


async def on_staff_imported(
    db: AsyncSession, org_id: uuid.UUID, members: Sequence[ImportedStaff]
) -> None:
    """Fill team-owned fields from the POS without overwriting manager edits.

    Sets an empty email, adds (never removes) assigned linked locations, and creates one
    position per job title, assigned as primary to employees that have no positions.
    """
    if not members:
        return
    await db.execute(
        _SET_EMAIL, {"ids": [m.employee_id for m in members], "emails": [m.email for m in members]}
    )
    loc_rows = [
        {"org_id": org_id, "employee_id": m.employee_id, "location_id": loc}
        for m in members
        for loc in sorted(set(m.location_ids))
    ]
    if loc_rows:
        await db.execute(pg_insert(EmployeeLocation).values(loc_rows).on_conflict_do_nothing())

    titles: dict[str, str] = {}
    for m in members:
        title = (m.job_title or "").strip()
        if title:
            titles.setdefault(title.lower(), title)
    if titles:
        positions = {
            r.key: r.id for r in await db.execute(_POSITION_IDS, {"org": org_id, "keys": [*titles]})
        }
        n = len(positions)
        for key, title in sorted(titles.items()):
            if key in positions:
                continue
            await db.execute(
                pg_insert(Position)
                .values(org_id=org_id, name=title, color=PALETTE[n % len(PALETTE)])
                .on_conflict_do_nothing()
            )
            n += 1
        positions = {
            r.key: r.id for r in await db.execute(_POSITION_IDS, {"org": org_id, "keys": [*titles]})
        }
        with_positions = set(
            await db.scalars(
                select(EmployeePosition.employee_id).where(
                    EmployeePosition.employee_id.in_([m.employee_id for m in members])
                )
            )
        )
        assign = [
            {
                "org_id": org_id,
                "employee_id": m.employee_id,
                "position_id": positions[(m.job_title or "").strip().lower()],
                "is_primary": True,
            }
            for m in members
            if m.employee_id not in with_positions
            and (m.job_title or "").strip().lower() in positions
        ]
        if assign:
            await db.execute(pg_insert(EmployeePosition).values(assign).on_conflict_do_nothing())

    gone = [m for m in members if m.deactivated]
    if gone:
        rows = await db.execute(
            select(StaffMember.id, StaffMember.display_name).where(
                StaffMember.id.in_([m.employee_id for m in gone])
            )
        )
        names = {r.id: r.display_name for r in rows}
        locations = await employee_location_ids(db, [m.employee_id for m in gone])
    for m in gone:
        await audit.record(
            db,
            org_id=org_id,
            actor_user_id=None,
            entity_type="employee",
            entity_id=m.employee_id,
            action="employee.deactivated_by_pos",
        )
        # The login is not revoked automatically; managers decide.
        name = names.get(m.employee_id, "An employee")
        await notify.notify(
            db,
            org_id,
            await notify.managers_for(db, org_id, locations.get(m.employee_id, set())),
            kind="employee_deactivated",
            title=f"{name} was deactivated in Square",
            body="Their draft shifts are flagged. Published shifts stay until you republish.",
            link=f"/app/team?employee={m.employee_id}",
            entity_type="employee",
            entity_id=m.employee_id,
        )
