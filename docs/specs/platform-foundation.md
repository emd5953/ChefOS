# Platform Foundation (Phase 0)

**Status:** Proposed · **Last updated:** 2026-10-19
**Related:** [ADR 0002: Production stack](../adr/0002-production-stack.md), [Architecture](../architecture.md), [Roadmap](../roadmap.md), [Integration layer](integration-layer.md)

## Problem

No production code exists yet. Every later phase needs the same base: a local environment that starts with one command, a database where tenant isolation is enforced, authentication, durable background workers, secret encryption, tracing, and CI. If each feature builds its own piece, the pieces end up inconsistent and isolation leaks. Phase 0 builds the base once, so Square M1 can start on solid ground.

## Goals

- `make up` from a clean checkout starts the full local stack at $0.
- FastAPI service with health checks, structured errors, and config from environment.
- PostgreSQL schema managed by migrations, with row-level security on every org-scoped table, enforced by a database role that can't bypass it.
- Identity: sign up (creates user, org, owner membership, first location), log in/out, session cookies with CSRF, location management.
- Temporal worker that runs a workflow scoped to an org, traced end to end.
- `KeyProvider` envelope encryption, ready for OAuth tokens in Square M1.
- OpenTelemetry traces from web request → API → DB → workflow, visible in Jaeger.
- Next.js web shell: sign up, log in, manage locations.
- GitHub Actions CI: lint, type check, migrations, tests, web build.

## Non-goals

- Mobile app (Phase 3).
- Moving the marketing site into Next.js, or deleting the prototype (Phase 2).
- Invitations, password reset, email delivery, MFA, social login.
- Roles beyond `owner` and `admin` in practice. `manager` and `employee` exist in the enum but get no screens until Phase 3.
- Rate limiting and account lockout. Noted as a risk below.
- Any domain module (commerce, team, inventory) or integration code.
- Cloud deployment.

## Approach

A monorepo with one Python package for the API and workers, one Next.js app, and a Compose file for infrastructure. The API and worker share code and run as two processes from the same image.

Tenant isolation uses two layers. The service layer passes explicit scope to every query, and PostgreSQL RLS is the backstop. The app connects as `chefos_app`, a role without `BYPASSRLS`. Every request transaction runs `SET LOCAL app.org_id = '<uuid>'`, and policies compare against it. A missing scope matches no rows, so failures are closed, not open.

**Alternative considered: schema-per-tenant.** Rejected. Migrations get multiplied per tenant, cross-tenant tooling gets harder, and the payoff (stronger isolation) matters for large enterprise tenants, not this product.

**Alternative considered: an auth library** (fastapi-users, or a hosted provider). Rejected. Hosted providers cost money or lock you in, and session auth done properly is a core skill this project is meant to practice. The prototype already does it in stdlib, so the new version is a port with stronger primitives.

## Design

### Tooling

| Concern | Choice |
| --- | --- |
| Python | 3.14 (native `uuid.uuid7`), managed with `uv` |
| API | FastAPI, Pydantic v2, `pydantic-settings` |
| DB access | SQLAlchemy 2.0 (async) + `asyncpg`; Alembic migrations |
| Passwords | Argon2id (`argon2-cffi`) |
| Crypto | `cryptography` (AES-256-GCM) |
| Workers | `temporalio` Python SDK |
| Telemetry | `opentelemetry-sdk`, FastAPI/SQLAlchemy/asyncpg instrumentations, `temporalio.contrib.opentelemetry`; `structlog` JSON logs with trace IDs |
| Python quality | `ruff` (lint + format), `mypy --strict` on `app/`, `pytest` + `pytest-asyncio` |
| Web | Next.js (current stable, App Router), TypeScript, pnpm, Node 24 LTS |
| Task runner | `Makefile` (no extra install) |

### Repository layout

```
apps/api/
  pyproject.toml
  Dockerfile                  one image; CMD selects api or worker
  alembic.ini
  migrations/
  app/
    main.py                   FastAPI app factory, middleware, routers
    config.py                 Settings (pydantic-settings)
    db.py                     engine, session dependency, SET LOCAL scope
    errors.py                 RFC 9457 problem+json handlers
    telemetry.py              OTel + structlog setup
    security/
      passwords.py
      sessions.py             create/lookup/revoke, CSRF
      keys.py                 KeyProvider protocol + LocalKeyProvider
    identity/
      models.py  schemas.py  service.py  api.py
    audit/
      models.py  service.py
    workflows/
      worker.py               worker entrypoint, interceptors
      ping.py                 PingWorkflow + activity
  tests/
apps/web/                     Next.js app
infra/
  docker-compose.yml
  otel-collector.yaml
  postgres/init.sql           roles: chefos_owner, chefos_app
.env.example
Makefile
.github/workflows/ci.yml
```

`server.py`, `product.js`, and the static site files stay at the repo root, untouched, until Phase 2.

### Local services (`infra/docker-compose.yml`)

| Service | Image | Ports (host) | Notes |
| --- | --- | --- | --- |
| postgres | `postgres:17` | 5432 | Volume-backed; `init.sql` creates roles |
| temporal | `temporalio/temporal` (`server start-dev`) | 7233, 8233 (UI) | SQLite file on a volume so history survives restarts |
| minio | `minio/minio` | 9000, 9001 (console) | Bucket `chefos-raw` created on startup |
| otel-collector | `otel/opentelemetry-collector-contrib` | 4317, 4318 | Exports traces to Jaeger, logs to stdout |
| jaeger | `jaegertracing/jaeger` (v2) | 16686 (UI) | In-memory storage |
| api | built from `apps/api` | 8080 | `uvicorn --reload` with a source mount |
| worker | built from `apps/api` | none | Same image, `python -m app.workflows.worker` |

Images are pinned to exact versions in the Compose file. The web app runs on the host (`pnpm dev`, port 3000), not in Compose, for fast reloads. The prototype keeps port 8000.

### Makefile targets

```
make up          docker compose up -d --wait, then alembic upgrade head
make down        stop containers (data kept)
make reset       stop and delete volumes
make migrate     alembic upgrade head
make test        API tests against the compose Postgres
make lint        ruff + mypy + web lint/typecheck
make web         pnpm dev in apps/web
```

### Configuration (`.env.example`)

```
DATABASE_URL=postgresql+asyncpg://chefos_app:chefos_app@localhost:5432/chefos
MIGRATIONS_DATABASE_URL=postgresql+asyncpg://chefos_owner:chefos_owner@localhost:5432/chefos
TEMPORAL_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=chefos
S3_SECRET_KEY=chefos-dev-secret
CHEFOS_MASTER_KEY=            # base64, 32 bytes; `make up` generates one if empty
CHEFOS_MASTER_KEY_ID=local-1
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
SESSION_COOKIE_SECURE=false   # true outside localhost
ENV=local                     # local | test; dev-only routes require local
```

`.env` is gitignored. The app refuses to start if `CHEFOS_MASTER_KEY` is missing or the wrong length.

### Database

Conventions: UUIDv7 primary keys generated in the app, `timestamptz` everywhere, `created_at`/`updated_at` on mutable tables, snake_case.

**Roles.** `chefos_owner` owns the schema and runs migrations. `chefos_app` has DML only, no DDL, and no `BYPASSRLS`.

**Tables**

```
users(id, email citext unique, password_hash, display_name, created_at, disabled_at)
  -- global, no RLS (login must look up by email before org is known)

organizations(id, name, timezone, created_at)
  RLS: id = current_setting('app.org_id')::uuid

memberships(id, org_id, user_id, role, created_at)
  unique(org_id, user_id); role ∈ {owner, admin, manager, employee}
  RLS: org_id = app.org_id

locations(id, org_id, name, address, timezone, business_day_start time default '04:00',
  created_at, archived_at)
  RLS: org_id = app.org_id

location_access(org_id, user_id, location_id)       -- for manager/employee; unused in Phase 0
  RLS: org_id = app.org_id

sessions(id, token_hash unique, user_id, org_id, csrf_token, created_at, last_seen_at,
  expires_at, revoked_at, user_agent, ip)
  -- no RLS; accessed only by the auth service before scope exists

audit_events(id, org_id, actor_user_id, location_id null, entity_type, entity_id, action,
  details jsonb, request_id, created_at)
  RLS: org_id = app.org_id; chefos_app has INSERT and SELECT only (append-only)
```

Policies use `current_setting('app.org_id', true)`, which returns NULL when unset, so unscoped queries return no rows.

**Scoped session dependency**

```python
async def org_session(ctx: AuthContext = Depends(require_auth)) -> AsyncIterator[AsyncSession]: ...
    # BEGIN; SET LOCAL app.org_id = ctx.org_id; yield; COMMIT/ROLLBACK
async def unscoped_session() -> AsyncIterator[AsyncSession]: ...
    # only importable by app.security.sessions and app.identity.service (enforced by a lint rule test)
```

### Auth

- **Passwords:** Argon2id with library defaults. Minimum length 12, checked against a small bundled list of common passwords.
- **Sessions:** 256-bit random token in the `chefos_session` cookie (HttpOnly, SameSite=Lax, Secure when configured, Path=/). Only the SHA-256 hash is stored. 14-day absolute expiry, 24-hour idle expiry. A new token is issued at login, so session fixation can't carry over.
- **CSRF:** a per-session token is returned by `/auth/me` and `/auth/login`. Unsafe methods require an `X-CSRF-Token` header that matches it (compared in constant time).
- **Active org** is stored on the session. Phase 0 users have exactly one org. A switch endpoint is deferred.
- **Authorization helper:** `require_role(ctx, {"owner","admin"})`. Location scope helper: `ctx.allowed_location_ids()`, which returns all org locations for owner/admin.

### HTTP API

Errors use `application/problem+json` (RFC 9457) with `type`, `title`, `status`, `detail`, `request_id`, and an optional `errors` map of fields to messages. Every response carries `X-Request-ID`.

```
GET    /healthz                       → 200 { status: "ok" }       (process up)
GET    /readyz                        → 200 | 503                 (DB + Temporal reachable)

POST   /v1/auth/signup                ← { email, password, display_name, org_name, location_name, timezone }
                                      → 201 { user, org, location, csrf_token } + Set-Cookie
POST   /v1/auth/login                 ← { email, password } → 200 { user, org, csrf_token } + Set-Cookie
POST   /v1/auth/logout                → 204 (revokes session, clears cookie)
GET    /v1/auth/me                    → 200 { user, org, role, csrf_token }

GET    /v1/org                        → 200 Organization
PATCH  /v1/org                        ← { name?, timezone? }            (owner/admin)
GET    /v1/locations                  → 200 { data: Location[] }
POST   /v1/locations                  ← { name, address?, timezone, business_day_start? }  (owner/admin)
PATCH  /v1/locations/{id}             ← partial                          (owner/admin)
POST   /v1/locations/{id}/archive     → 200                              (owner/admin)

POST   /v1/dev/ping-workflow          → 202 { workflow_id }   (ENV=local only; returns 404 otherwise)
GET    /v1/dev/ping-workflow/{id}     → 200 { status, result }
```

Every mutating identity call writes an `audit_events` row in the same transaction.

### Workers

- One task queue, `chefos-default`. The worker registers workflows and activities from `app.workflows`.
- **Scope convention:** every workflow input is a Pydantic model with `org_id: UUID` as its first field. Activities open DB sessions only through `activity_org_session(org_id)`, which applies `SET LOCAL`. A unit test checks that every registered workflow's input type has `org_id`.
- `PingWorkflow(org_id)` runs one activity that reads the org name under RLS and writes an audit event (`action = "ping"`). It proves scoping, DB access, and tracing in a single run.
- The tracing interceptor is installed on both client and worker, so the API span that starts a workflow is the parent of the workflow and activity spans.

### KeyProvider

```python
class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes: ...
    def decrypt(self, envelope: bytes, aad: bytes) -> bytes: ...
```

`LocalKeyProvider` creates a random 256-bit data key per call, encrypts the plaintext with AES-GCM, wraps the data key with the master key (also AES-GCM), and returns a versioned envelope: `v1 | key_id | wrapped_dk | nonce | ciphertext`. AAD binds a ciphertext to its owner (for example `org_id|connection_id`), so a row copied to another tenant won't decrypt. Decryption picks the master key by `key_id`, which allows key rotation later.

### Web shell (`apps/web`)

- Routes: `/signup`, `/login`, `/app` (requires a session; shows org and locations), `/app/locations` (list/create/edit/archive).
- `next.config` rewrites `/api/*` → `http://localhost:8080/*`. The browser talks only to the Next.js origin, so cookies stay first-party and there's no CORS configuration.
- A small `api()` fetch wrapper attaches `X-CSRF-Token` and maps problem+json errors onto form fields.
- No design system yet: plain, accessible forms. Visual design comes in Phase 2 with the marketing site.

### CI (`.github/workflows/ci.yml`)

Runs on push and PR, using only free GitHub Actions minutes.

- **api:** `uv sync` → `ruff check` → `ruff format --check` → `mypy` → Postgres service container with `init.sql` → `alembic upgrade head` → `pytest`.
- **web:** `pnpm install --frozen-lockfile` → `lint` → `tsc --noEmit` → `next build`.
- **migrations:** `alembic upgrade head` → `downgrade base` → `upgrade head` (migrations must be reversible).

## Behavior

### Happy path: sign up and see a traced workflow

1. User opens `localhost:3000/signup` and submits email, password, name, restaurant name, location name, and timezone.
2. In one transaction, the API creates the user, generates the org ID, runs `SET LOCAL app.org_id`, inserts the org, the owner membership, the location, and audit events, then creates a session. The cookie is set.
3. `/app` loads, `/auth/me` returns the CSRF token, and the locations list shows the new location.
4. Developer calls `POST /api/v1/dev/ping-workflow`. The workflow shows up in the Temporal UI (localhost:8233). Jaeger (localhost:16686) shows one trace: HTTP request → workflow start → activity → SQL.

### Error and edge cases

| Case | Result |
| --- | --- |
| Sign up with an existing email | 409 problem, `errors.email`. The response doesn't reveal other account details |
| Weak or short password | 422 with `errors.password` |
| Invalid IANA timezone | 422 with `errors.timezone` |
| Wrong email or password | 401 with a generic message. Argon2 verification also runs against a dummy hash for unknown emails, so response timing doesn't reveal which emails exist |
| Expired, idle, or revoked session | 401 `session_expired`; web redirects to `/login` |
| Unsafe method without a valid CSRF header | 403 `csrf_failed` |
| Non-admin calls an owner/admin route | 403 `forbidden` (unreachable in Phase 0, still tested) |
| Location ID from another org | 404, not 403. RLS hides the row, so existence isn't revealed |
| Query runs without `SET LOCAL` | Zero rows returned, inserts fail the RLS check. Tests assert this |
| `CHEFOS_MASTER_KEY` missing or invalid | Process exits at startup with a clear message |
| Temporal down | `/readyz` returns 503, dev ping returns 503 problem, `/healthz` still 200 |
| Ciphertext decrypted with wrong AAD | `DecryptionError`, never partial plaintext |
| Duplicate location name in an org | Allowed. Names aren't identifiers |

## Verification

1. **RLS tests**, connecting as `chefos_app`:
   - Org A's scope can't read, update, or delete org B's rows in any RLS table.
   - Without scope, every RLS table returns zero rows.
   - `audit_events` rejects UPDATE and DELETE.
   - A test fails if any table with an `org_id` column lacks RLS. It introspects `pg_class.relrowsecurity` and `pg_policies`.
2. **Auth tests:** signup/login/logout round trip; cookie flags; idle and absolute expiry; CSRF required on every unsafe route (enumerated from the router so new routes can't skip it); login timing path for unknown emails executes Argon2.
3. **KeyProvider tests:** round trip; tampered ciphertext, wrong AAD, and wrong key ID all fail; envelope version parsing.
4. **Workflow tests:** `PingWorkflow` under the Temporal time-skipping test environment; a structural test that every workflow input has `org_id`.
5. **Smoke test** (`make smoke`, also a CI job): bring Compose up, sign up via HTTP, run the ping workflow, poll until complete, and query the Jaeger API for a trace containing both the HTTP and activity spans.
6. **Manual:** from a fresh clone on a clean machine, `cp .env.example .env && make up && make web` reaches a working sign-up in under 15 minutes.

## Risks / open questions

- **Python 3.14 ecosystem.** If a dependency (asyncpg, temporalio) lacks 3.14 wheels, fall back to 3.13 plus the `uuid-utils` package for UUIDv7. Check in F1.
- **Single-org sessions.** Users who belong to several orgs need an org switcher later. The schema already supports it.
- **No rate limiting.** Login is open to brute force. Acceptable while local-only. Add per-IP and per-account limits before anything is exposed, even via a tunnel (Square M4 webhooks use a tunnel, so this is due by then).
- **Temporal dev server is not production Temporal.** Workflow code is identical. Only the server differs, which is the intended boundary.
- **Docker resource use.** Seven containers is heavier than the prototype. Expect about 2–3 GB of RAM.

## Milestones

Each one leaves `main` working and green in CI.

1. **F1: Skeleton and local stack.** Monorepo layout, Compose with all infra services, API with `/healthz` and `/readyz`, Makefile, `.env.example`, telemetry wiring. Exit: `make up` brings all services up healthy, and all three UIs (Temporal, Jaeger, MinIO) load.
2. **F2: CI.** GitHub Actions for api, web placeholder, and migrations. Exit: green run on `main`.
3. **F3: Database and RLS.** Roles, Alembic, identity tables, policies, scoped session dependency, RLS test suite. Exit: all RLS tests pass in CI.
4. **F4: Auth and identity API.** Signup/login/logout/me, sessions, CSRF, org and locations endpoints, problem+json, audit events. Exit: auth tests pass. A curl session can sign up and create locations.
5. **F5: Workers and KeyProvider.** Worker process, scope convention, `PingWorkflow`, tracing interceptor, `LocalKeyProvider`. Exit: a ping trace appears in Jaeger, and KeyProvider tests pass.
6. **F6: Web shell and smoke test.** Next.js signup, login, and locations pages; `/api` rewrite; `make smoke` in CI. Exit: the Phase 0 roadmap exit criterion holds.
