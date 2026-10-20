"""Dev-only routes (ENV=local) for exercising the worker."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from app.config import get_settings
from app.errors import ProblemError, not_found, unavailable
from app.security.sessions import AuthDep
from app.workflows.client import TASK_QUEUE, get_temporal_client
from app.workflows.ping import PingInput, PingResult, PingWorkflow

router = APIRouter(prefix="/v1/dev", tags=["dev"])


class PingStarted(BaseModel):
    workflow_id: str


class PingStatus(BaseModel):
    status: str
    result: PingResult | None = None


def _require_local() -> None:
    if get_settings().env != "local":
        raise not_found()


@router.post("/ping-workflow", status_code=202, response_model=PingStarted)
async def start_ping(ctx: AuthDep) -> PingStarted:
    _require_local()
    workflow_id = f"ping-{ctx.org_id}-{uuid.uuid4().hex[:12]}"
    try:
        client = await get_temporal_client()
        await client.start_workflow(
            PingWorkflow.run, PingInput(org_id=ctx.org_id), id=workflow_id, task_queue=TASK_QUEUE
        )
    except (RPCError, OSError, RuntimeError) as exc:
        raise unavailable("Temporal is unreachable") from exc
    return PingStarted(workflow_id=workflow_id)


@router.get("/ping-workflow/{workflow_id}", response_model=PingStatus)
async def ping_status(workflow_id: str, ctx: AuthDep) -> PingStatus:
    _require_local()
    if not workflow_id.startswith(f"ping-{ctx.org_id}-"):
        raise not_found("Workflow not found")  # another org's workflow looks like it doesn't exist
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        status = desc.status
        result: PingResult | None = None
        if status == WorkflowExecutionStatus.COMPLETED:
            result = await handle.result(rpc_timeout=None)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise not_found("Workflow not found") from exc
        raise unavailable("Temporal is unreachable") from exc
    except OSError as exc:
        raise unavailable("Temporal is unreachable") from exc
    except ProblemError:
        raise
    return PingStatus(status=status.name if status else "UNKNOWN", result=result)
