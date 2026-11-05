"""Reproducible synthetic fixture for live Sous evals, without a Temporal dependency.

Run only against a dedicated database whose name ends in `_eval`, after migrations.
Uses the existing Square simulator, ingestion, schedule and inventory demo services.
No customer data or real provider credentials are used.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.business_date import business_date
from app.cli import DEMO_EMAIL, DEMO_PASSWORD, seed_demo, seed_inventory_demo
from app.config import get_settings
from app.db import scoped_session
from app.identity import service as identity
from app.integrations import links, pipeline, providers, sync
from app.integrations.models import IntegrationConnection
from app.integrations.providers.square.connector import SquareConnector
from app.integrations.providers.square.fake_client import FakeSquareClient
from app.integrations.secrets import encrypt_credentials
from app.security.sessions import revoke_session
from app.team.demo import seed_week

FIXTURE_VERSION = "square-simulator-400d-v1"


async def seed() -> None:
    settings = get_settings()
    if not (make_url(settings.database_url).database or "").endswith("_eval"):
        raise ValueError("Live eval fixtures require a dedicated database ending in _eval")
    now = datetime.now(UTC)
    connector = SquareConnector(FakeSquareClient(clock=lambda: now), mode="fake")
    providers.set_connector_override(connector)
    await seed_demo(argparse.Namespace())
    login = await identity.login(DEMO_EMAIL, DEMO_PASSWORD, user_agent="eval-fixture", ip=None)
    org_id = login.org.id
    try:
        async with scoped_session(org_id) as db:
            conn = await db.scalar(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider == "square",
                )
            )
            if conn is None:
                conn = IntegrationConnection(
                    org_id=org_id, provider="square", mode="fake", created_by=login.user.id
                )
                db.add(conn)
                await db.flush()
            elif conn.mode != "fake":
                raise ValueError("Eval fixture refuses a real provider connection")
            cid = conn.id
            creds = connector.exchange_code("fake-code", "http://localhost/eval")
            conn.credential_ciphertext = encrypt_credentials(creds, org_id, cid)
            conn.credential_expires_at = creds.expires_at
            conn.external_account_id = creds.merchant_id
            conn.status = "active"
            await sync.ensure_streams(db, org_id, cid)
            await pipeline.ingest(
                db,
                pipeline.ConnCtx(org_id, cid, {}),
                "locations",
                connector.list_locations(creds),
                None,
                now,
            )
            await links.auto_link(db, org_id, cid)
        await sync.sync_connection(
            org_id, cid, streams=["locations", "catalog", "team_members"], force=True, now=now
        )
        async with scoped_session(org_id) as db:
            state = await sync._load_state(db, org_id, cid)
        today = business_date(now, *state.zone)
        first = today - timedelta(days=399)
        workflow_id = f"live-eval-{cid}-{today}"
        await sync.begin_backfill(org_id, cid, first, today, workflow_id)
        last = today
        while last >= first:
            start = max(first, last - timedelta(days=6))
            await sync.backfill_chunk(org_id, cid, start, last, workflow_id, now=now)
            print(f"Loaded synthetic dates {start} through {last}", flush=True)
            last = start - timedelta(days=1)
        await sync.finish_backfill(org_id, cid, workflow_id, None)
        await seed_week(org_id, login.user.id)
        await seed_inventory_demo(argparse.Namespace())
        print(f"Fixture {FIXTURE_VERSION}, clock {now.isoformat()}", flush=True)
    finally:
        providers.set_connector_override(None)
        await revoke_session(login.record.id)


if __name__ == "__main__":
    asyncio.run(seed())
