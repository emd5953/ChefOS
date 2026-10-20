import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import check_db
from app.workflows.client import get_temporal_client

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check_temporal() -> None:
    client = await asyncio.wait_for(get_temporal_client(), timeout=3)
    await asyncio.wait_for(client.service_client.check_health(), timeout=3)


@router.get("/readyz")
async def readyz() -> JSONResponse:
    checks: dict[str, str] = {}
    for name, check in (("db", check_db), ("temporal", _check_temporal)):
        try:
            await asyncio.wait_for(check(), timeout=5)
            checks[name] = "ok"
        except Exception:
            checks[name] = "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503
    )
