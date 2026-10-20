"""Management CLI: `python -m app.cli <command>`.

Add a command by decorating a function with `@command("name")`.
"""

import argparse
import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from app.config import get_settings

Handler = Callable[[argparse.Namespace], Coroutine[Any, Any, int]]
COMMANDS: dict[str, tuple[str, Handler]] = {}


def command(name: str, help_text: str = "") -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        COMMANDS[name] = (help_text or (fn.__doc__ or "").strip(), fn)
        return fn

    return register


DEMO_EMAIL = "owner@chefos.local"
DEMO_PASSWORD = "ChefOS-demo-2026!"  # noqa: S105  (local demo account)


@command("seed-demo", "Create the demo owner, org, and location (idempotent)")
async def seed_demo(_: argparse.Namespace) -> int:
    from app.errors import ProblemError
    from app.identity.schemas import SignupRequest
    from app.identity.service import register_account

    data = SignupRequest(
        email=DEMO_EMAIL,
        password=DEMO_PASSWORD,
        display_name="Morgan Lee",
        org_name="Juniper House",
        location_name="Juniper House · Brooklyn",
        timezone="America/New_York",
    )
    try:
        reg = await register_account(data)
    except ProblemError as exc:
        if exc.code == "conflict":
            print(f"Demo account already exists: {DEMO_EMAIL}")
            return 0
        raise
    # Defaults match the spec: address "Brooklyn, NY", business day starts 04:00.
    from app.db import scoped_session
    from app.identity.models import Location

    async with scoped_session(reg.org.id) as db:
        location = await db.get_one(Location, reg.location.id)
        location.address = "Brooklyn, NY"
    print(f"Seeded demo account: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    return 0


WARM_DAYS = 30
DEMO_URL = "http://localhost:3000/login"


@command("demo", "Seed the demo org, connect fake Square, backfill; wait for the last 30 days")
async def demo(args: argparse.Namespace) -> int:
    """Idempotent: re-running reuses the existing connection and backfill."""
    import time
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db import scoped_session
    from app.identity import service as identity
    from app.integrations import service, sync, temporal
    from app.integrations.models import IntegrationConnection
    from app.integrations.providers.square.business_date import business_date

    if get_settings().square_mode != "fake":
        print("`demo` connects the simulated Square; set SQUARE_MODE=fake.")
        return 2
    await seed_demo(args)
    login = await identity.login(DEMO_EMAIL, DEMO_PASSWORD, user_agent="cli-demo", ip=None)
    org_id, user_id = login.org.id, login.user.id

    async with scoped_session(org_id) as db:
        conn = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == "square",
                IntegrationConnection.status != "disconnected",
            )
        )
        if conn is None:
            conn = IntegrationConnection(
                org_id=org_id, provider="square", mode="fake", created_by=user_id
            )
            db.add(conn)
            await db.flush()
        connection_id, status = conn.id, conn.status
    try:
        if status == "active":
            print(f"Square already connected ({connection_id}); resuming.")
            await temporal.ensure_schedules(org_id, connection_id)
            await service.maybe_start_backfill(org_id, connection_id)
        else:
            print("Connecting simulated Square ...")
            await service.complete_oauth(
                service.sign_state(org_id, connection_id, user_id), "fake-code"
            )
    except (OSError, RuntimeError) as exc:
        print(f"Could not reach Temporal ({exc}). Is the stack up? Run `make up`.")
        return 1

    print(f"Backfilling; waiting for the last {WARM_DAYS} days ...")
    started = time.monotonic()
    while True:
        async with scoped_session(org_id) as db:
            state = await sync._load_state(db, org_id, connection_id)
        done_through = {
            s: (row.backfill_completed_through if row else None)
            for s, row in state.streams.items()
            if s in sync.FACT_STREAMS
        }
        tz, day_start = state.zone
        today = business_date(datetime.now(UTC), tz, day_start)
        target = today - timedelta(days=WARM_DAYS - 1)
        reached = [d for d in done_through.values() if d is not None and d <= target]
        loaded = max(
            (min(WARM_DAYS, (today - d).days + 1) for d in done_through.values() if d), default=0
        )
        if len(reached) == len(done_through) and done_through:
            break
        elapsed = int(time.monotonic() - started)
        print(f"  [{elapsed:>3}s] {loaded}/{WARM_DAYS} days loaded", flush=True)
        if elapsed > 600:
            print("Timed out. Check that the worker is running: `make logs`.")
            return 1
        await asyncio.sleep(2)
    print(f"Last {WARM_DAYS} days loaded in {int(time.monotonic() - started)}s.")
    print("The rest of the 13-month backfill continues in the background.")
    print(f"\nOpen {DEMO_URL}\n  email:    {DEMO_EMAIL}\n  password: {DEMO_PASSWORD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (help_text, _) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    get_settings()
    return asyncio.run(COMMANDS[args.command][1](args))


if __name__ == "__main__":
    raise SystemExit(main())
