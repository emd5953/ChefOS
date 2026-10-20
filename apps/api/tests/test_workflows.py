import inspect
import typing
import uuid
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import text
from temporalio import workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from app.config import get_settings
from app.db import scoped_session
from app.identity.schemas import SignupRequest
from app.identity.service import register_account
from app.workflows import api as workflows_api
from app.workflows.client import TASK_QUEUE
from app.workflows.ping import PingInput, PingResult, PingWorkflow
from app.workflows.worker import WORKFLOWS, build_worker
from tests.conftest import SignupFn

QUEUE = TASK_QUEUE


def _run_input_type(wf: type[Any]) -> Any:
    run = next(
        fn
        for _, fn in inspect.getmembers(wf, inspect.isfunction)
        if hasattr(fn, "__temporal_workflow_run")
    )
    hints = typing.get_type_hints(run)
    params = [p for p in inspect.signature(run).parameters if p != "self"]
    assert params, f"{wf.__name__}.run takes no input"
    return hints[params[0]]


def test_every_workflow_input_starts_with_org_id() -> None:
    """Scope convention: workflow input is a Pydantic model whose first field is org_id: UUID."""
    assert WORKFLOWS, "no workflows registered"
    for wf in WORKFLOWS:
        assert workflow._Definition.must_from_class(wf)  # is a real workflow
        input_type = _run_input_type(wf)
        assert isinstance(input_type, type) and issubclass(input_type, BaseModel), wf.__name__
        fields = list(input_type.model_fields)
        assert fields[0] == "org_id", f"{wf.__name__} input must start with org_id"
        assert input_type.model_fields["org_id"].annotation is uuid.UUID


@pytest.fixture
async def temporal_env() -> Any:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    yield env
    await env.shutdown()


async def test_ping_workflow_runs_under_rls(temporal_env: WorkflowEnvironment) -> None:
    reg = await register_account(
        SignupRequest(
            email=f"wf-{uuid.uuid4().hex[:8]}@example.com",
            password="correct-horse-battery-9",
            display_name="WF",
            org_name="Workflow Org",
            location_name="L",
            timezone="UTC",
        )
    )
    async with build_worker(temporal_env.client):
        result = await temporal_env.client.execute_workflow(
            PingWorkflow.run,
            PingInput(org_id=reg.org.id),
            id=f"ping-{reg.org.id}-{uuid.uuid4().hex[:8]}",
            task_queue=QUEUE,
        )
    assert result == PingResult(org_id=reg.org.id, org_name="Workflow Org")
    async with scoped_session(reg.org.id) as db:
        assert await db.scalar(text("SELECT count(*) FROM audit_events WHERE action = 'ping'")) == 1


async def test_ping_activity_cannot_see_other_orgs(temporal_env: WorkflowEnvironment) -> None:
    from app.workflows.ping import ping_activity

    with pytest.raises(Exception):  # noqa: B017  (NoResultFound: org not visible without scope)
        await ping_activity(PingInput(org_id=uuid.uuid4()))


async def test_dev_routes_404_outside_local(signup: SignupFn) -> None:
    acct = await signup()
    c = acct["client"]
    assert get_settings().env == "test"
    r = await c.post("/v1/dev/ping-workflow", headers=acct["headers"])
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert (await c.get("/v1/dev/ping-workflow/anything")).status_code == 404


async def test_dev_ping_start_and_status_with_fake_temporal(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch, temporal_env: WorkflowEnvironment
) -> None:
    monkeypatch.setattr(get_settings(), "env", "local")

    async def fake_client() -> Any:
        return temporal_env.client

    monkeypatch.setattr(workflows_api, "get_temporal_client", fake_client)
    acct = await signup()
    c, h = acct["client"], acct["headers"]
    async with build_worker(temporal_env.client):
        r = await c.post("/v1/dev/ping-workflow", headers=h)
        assert r.status_code == 202
        wid = r.json()["workflow_id"]
        await temporal_env.client.get_workflow_handle(wid).result()
        status = await c.get(f"/v1/dev/ping-workflow/{wid}")
    assert status.json()["status"] == "COMPLETED"
    assert status.json()["result"]["org_name"] == "Test Org"

    other = await signup()
    assert (await other["client"].get(f"/v1/dev/ping-workflow/{wid}")).status_code == 404


async def test_dev_ping_503_when_temporal_down(
    signup: SignupFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "env", "local")

    async def down() -> Any:
        raise RuntimeError("Failed client connect")

    monkeypatch.setattr(workflows_api, "get_temporal_client", down)
    acct = await signup()
    r: httpx.Response = await acct["client"].post("/v1/dev/ping-workflow", headers=acct["headers"])
    assert r.status_code == 503 and r.json()["code"] == "unavailable"
