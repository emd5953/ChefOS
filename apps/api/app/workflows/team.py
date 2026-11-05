"""Temporal activities for Team.

attendance_check_activity  late/missed clock-in detection for a connection's locations. Runs at
                           the end of every ConnectionSync (scheduled per connection), right
                           after timecards are imported, so it is both periodic and current.
"""

import uuid

from pydantic import BaseModel
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from app.team.scheduling import attendance


class AttendanceInput(BaseModel):
    org_id: uuid.UUID  # scope convention: org_id is always the first field
    connection_id: uuid.UUID


@activity.defn
async def attendance_check_activity(data: AttendanceInput) -> int:
    return await attendance.check_connection(data.org_id, data.connection_id)
