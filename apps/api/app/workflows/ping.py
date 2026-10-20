"""PingWorkflow: proves org scoping, DB access under RLS, audit writes, and tracing end to end."""

import uuid
from datetime import timedelta

from pydantic import BaseModel
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from app.audit import service as audit
    from app.db import activity_org_session
    from app.identity.models import Organization


class PingInput(BaseModel):
    org_id: uuid.UUID  # scope convention: org_id is always the first field


class PingResult(BaseModel):
    org_id: uuid.UUID
    org_name: str


@activity.defn
async def ping_activity(data: PingInput) -> PingResult:
    async with activity_org_session(data.org_id) as db:
        org = await db.get_one(Organization, data.org_id)
        await audit.record(
            db,
            org_id=data.org_id,
            actor_user_id=None,
            entity_type="organization",
            entity_id=data.org_id,
            action="ping",
        )
        return PingResult(org_id=org.id, org_name=org.name)


@workflow.defn
class PingWorkflow:
    @workflow.run
    async def run(self, data: PingInput) -> PingResult:
        return await workflow.execute_activity(
            ping_activity, data, start_to_close_timeout=timedelta(seconds=30)
        )
