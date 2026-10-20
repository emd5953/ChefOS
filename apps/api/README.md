# ChefOS API

FastAPI modular monolith plus Temporal worker. One image, two processes (`api`, `worker`).

```
make up        # from the repo root: stack + migrations; waits for /readyz
make seed      # demo login: owner@chefos.local / ChefOS-demo-2026!
make test      # pytest as chefos_app against chefos_test (never touches dev data)
make lint      # ruff, mypy --strict
```

Config lives in `app/config.py` (`Settings`, read from env and the repo-root `.env`). Compose
overrides service addresses (`postgres:5432`, `temporal:7233`, ...) via `environment:`.

## Conventions

- Every org-scoped table has `org_id` and an RLS policy on `app_org_id()`; the app connects as
  `chefos_app` (no `BYPASSRLS`). A test fails if an `org_id` table lacks RLS.
- Request handlers: `ctx: AuthDep` + `db: OrgSession` (from `app.security.sessions`).
  `ctx` is an `AuthContext` (`user_id`, `org_id`, `role`, `session_id`, `await ctx.allowed_location_ids()`).
  Use `require_role(ctx, {"owner", "admin"})` for admin routes. Unsafe methods get CSRF for free.
- Activities: `async with activity_org_session(input.org_id) as db:` (from `app.db`).
- Errors: raise `ProblemError(status, code, detail, errors=...)` (or `not_found()`, `forbidden()`, ...).
- Never import `unscoped_session` outside the auth code (a test enforces it).

## Add a module

1. Create `app/<module>/` with `models.py`, `schemas.py`, `service.py`, `api.py` (`router = APIRouter(...)`).
2. Register the router: one `include_router` line in `create_app()` in `app/main.py`.
3. Register models: one import line in `app/models.py` (Alembic autogenerate and the drift test see `Base`).
4. Migration: `uv run alembic revision --autogenerate -m "add <module>"`, then edit it by hand to add
   `ENABLE ROW LEVEL SECURITY` and a policy (`USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())`)
   for each org-scoped table. Run `make migrate`, and `alembic check` must report no drift.
   `chefos_app` gets DML on new tables automatically (default privileges).
5. Workflows/activities: add them to `WORKFLOWS` / `ACTIVITIES` in `app/workflows/worker.py`.
   Workflow input is a Pydantic model whose first field is `org_id: UUID`.
6. CLI commands: decorate a function with `@command("name")` in `app/cli.py`.
7. If the module has unsafe-method routes without a session (webhooks), add them to `PUBLIC_UNSAFE`
   in `tests/test_auth.py` and verify them another way (e.g. HMAC).
