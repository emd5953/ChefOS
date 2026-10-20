"""Demo seeding for Team (`python -m app.cli demo`): logins through the real invitation path.

Idempotent: anything that already exists is left alone.
"""

import uuid

from sqlalchemy import select

from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location, User
from app.team import invitations
from app.team.models import StaffMember
from app.team.schemas import InvitationCreate
from app.team.service import Scope

GM_EMAIL = "gm@chefos.local"
EMPLOYEE_EMAIL = "maya@chefos.local"
EMPLOYEE_NAME = "Maya Okafor"


async def _invite_and_accept(
    owner: Scope, data: InvitationCreate, display_name: str, password: str
) -> bool:
    async with scoped_session(owner.org_id) as db:
        if await db.scalar(select(User.id).where(User.email == data.email)) is not None:
            return False
        _, token = await invitations.create_invitation(db, owner, data)
    await invitations.accept(token, display_name, password, user_agent="cli-demo", ip=None)
    return True


async def seed_logins(org_id: uuid.UUID, owner_user_id: uuid.UUID, password: str) -> list[str]:
    """A manager (gm@) for every location and an employee login (maya@) linked to the imported
    Square team member Maya Okafor. Returns the emails it created."""
    async with scoped_session(org_id) as db:
        locations = list(
            await db.scalars(
                select(Location.id).where(Location.org_id == org_id, Location.archived_at.is_(None))
            )
        )
        maya = await db.scalar(
            select(StaffMember).where(
                StaffMember.display_name == EMPLOYEE_NAME, StaffMember.merged_into_id.is_(None)
            )
        )
        maya_id = maya.id if maya is not None and maya.user_id is None else None
    owner = Scope(org_id, owner_user_id, "owner", frozenset(locations))
    created: list[str] = []
    gm = InvitationCreate(role="manager", email=GM_EMAIL, location_ids=locations)
    if await _invite_and_accept(owner, gm, "Jordan Reyes", password):
        created.append(GM_EMAIL)
    if maya_id is not None:
        emp = InvitationCreate(
            role="employee", employee_id=maya_id, email=EMPLOYEE_EMAIL, location_ids=locations
        )
        try:
            if await _invite_and_accept(owner, emp, EMPLOYEE_NAME, password):
                created.append(EMPLOYEE_EMAIL)
        except ProblemError as exc:  # e.g. the employee isn't assigned to a location yet
            print(f"Skipped the employee login: {exc.detail}")
    return created
