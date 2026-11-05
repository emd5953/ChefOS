# Backend module boundaries

## Problem

`apps/api` is a modular monolith (`app/<module>/{api,models,schemas,service}.py`), but the
layout is a convention and nothing enforces it. Three places have drifted:

- `inventory/api.py` is 1,687 lines with 107 raw-SQL calls inside route handlers. It has no
  service layer, so inventory logic can't be reused from Sous, workflows, or the CLI.
- Any module imports any other module's internals. `identity` and `team` import each other,
  and package-level cycles also run through `security`, `integrations`, `commerce` and
  `workflows`. Nothing stops new cycles from forming.
- `team` (13 flat files, e.g. `requests.py` at 1,078 lines and `schedule.py` at 980) and `sous`
  (14 flat files) have no internal grouping.

## Goals / Non-goals

Goals
1. Inventory gets a service layer. Route handlers keep only HTTP concerns: role checks,
   location authorization, query and body parsing, and response shape.
2. Module boundaries are enforced by a test in `make test`. That test covers the platform
   layer, the composition roots, cross-module route imports, a declared public surface for
   each module, and a ratchet on package-level cycles.
3. Break the `identity → team` edge.
4. Group `team` and `sous` into subpackages.

Non-goals
- Microservices, separate deploys, or separate databases. There is one Postgres and RLS
  stays exactly as it is.
- Any behavior change. URLs, response bodies, status codes, SQL, audit records and
  notifications stay byte-identical. There are no migrations.
- Breaking the remaining cycles that run through `integrations` and `security`. They get
  allowlisted and ratcheted, not fixed, here.
- Rewriting inventory's raw SQL in the ORM. The SQL moves as-is.
- Splitting `sous/tools.py` or `team/schemas.py`.

## Approach

Make the boundaries checkable, then fix the worst violations.

The checks go in an AST test (`tests/test_boundaries.py`). This follows the existing
`tests/test_structure.py` guardrail and avoids `import-linter`. The rules we need are
"public submodules only" and "no new cycles, with an allowlist that shrinks". Encoding those
in import-linter means wildcard contracts plus a separate cycle check. Forty lines of `ast`
do it directly and run with the rest of the suite.

Each module declares its public surface in `__init__.py` as a `PUBLIC` tuple of submodule
names. Eager re-exports in `__init__` were the rejected alternative: with the existing
package cycles (`identity ↔ security`, for example), importing a submodule would run the
package `__init__` mid-cycle and fail.

## Design

### Module classes (`tests/test_boundaries.py`)

| Class | Modules | May import |
|---|---|---|
| platform | `business_date config db errors ids context formatting telemetry` | platform only |
| domain | `audit security identity commerce integrations inventory sous team workflows` | platform + other domains' public surface |
| root | `main cli health models` | anything; nothing imports a root |

### Rules
1. Platform modules import no domain or root module.
2. No module imports a root module.
3. A domain never imports another domain's `api` module, or `integrations.push` (which is a
   router).
4. A cross-domain import must target `app.<mod>` or `app.<mod>.<name>…` where `<name>` is in
   `app/<mod>/__init__.py`'s `PUBLIC`. Importing a `_private` name across modules is a
   failure.
5. Take the package-level graph of domain → domain edges and remove `ALLOWED_BACK_EDGES`.
   The result must be acyclic. Every allowlisted edge must still exist, so the allowlist
   only shrinks.

### Step 1: inventory service layer

```
inventory/
  api.py          routes: require_role, _location (ctx.allowed_location_ids), call service
  service.py      shared helpers (row(), business_date_for) + catalog: items, suppliers,
                  storage areas, par, packs, unit conversions
  waste.py        waste create/log
  counts.py       count sessions, lines, confirm, submit
  purchasing.py   purchase orders, close/reopen, receive
  recipes.py      recipes, snapshots, menu-item links and costs
  food_cost.py    (unchanged)
```

Service functions take `db: AsyncSession` plus explicit ids (`org_id`, `user_id`), a
`Location` or id, and the request body. They never take `AuthContext`.

### Step 3: identity → team

`GET /v1/auth/me` returns `has_employee`, which is team knowledge. The handler moves to
team's `mine` router (`/v1` prefix, same path, same `SessionOut`) and calls `identity.service`
for the user and org. After the move, `identity` imports nothing from `team`.

### Step 4: subpackages

```
team/
  api.py models.py schemas.py service.py demo.py invitations.py me.py notify.py
  scheduling/  builder.py (was schedule.py)  forecast.py  attendance.py
  requests/    availability.py (time off + availability + builder conflicts, from requests.py)
               coverage.py     (open-shift coverage, from requests.py)
               trades.py
sous/
  api.py models.py schemas.py service.py conversations.py proposals.py retention.py
  agent/  orchestrator.py llm.py prompt.py validator.py claims.py fallback.py tools.py
```

These are moves plus import rewrites only, across `app/`, `tests/`, `evals/` and string
targets such as `monkeypatch.setattr("app.team.schedule…")`. The side-effect registration of
builder conflicts, previously done by importing `team.requests`, must still happen when
`team.api` loads.

## Behavior

No observable change. The OpenAPI schema is identical except that `/v1/auth/me` moves from
the `identity` tag to `team`.

## Verification

- `make lint`: ruff, ruff format and `mypy --strict`, clean.
- `make test`: the full suite passes, including the new `tests/test_boundaries.py`.
- The OpenAPI paths/methods set is diffed before and after: it must be identical.
- Line counts: `inventory/api.py` drops below about 600 lines, with no `text(` SQL left in
  it.

## Risks

- Moving Sous files may break eval-harness imports or the `evals/sous/results` replay. Evals
  get grepped and imported in a smoke test.
- The allowlist means the remaining cycles are tolerated, not fixed. Each one is a real two-way
  domain link (auth, sync scheduling, freshness on commerce and team pages); break one only when
  extracting that module. `business_date` moved to the platform layer (`app/business_date.py`).
