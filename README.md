# ChefOS

A learning project: a local, demo-able restaurant operations platform built with production-grade practices, at zero cost. It runs alongside the POS. It brings sales, labor, and inventory data into one place and builds the daily workflows on top of it: scheduling, reporting, ordering, and an assistant that answers operating questions.

Modules: **Team**, **Commerce**, **Inventory**. Shared capabilities: **Integrations** (POS, labor, and supplier data) and **Sous** (assistant).

## Status

| Area | State |
| --- | --- |
| Product requirements | Draft ([PRD](docs/prd.md)) |
| Production services | Not started. Phase 0 and the Square integration come first ([roadmap](docs/roadmap.md)) |
| Prototype workspace | Runnable UI/workflow sandbox (`server.py`, `product.js`); not the production codebase |
| Marketing site | Static reproduction of the ChefOS public site, kept as design reference |

## Documentation

| Doc | Purpose |
| --- | --- |
| [PRD](docs/prd.md) | Problem, users, requirements, scope |
| [Architecture](docs/architecture.md) | System design, modules, data model, tenancy |
| [Roadmap](docs/roadmap.md) | Phases and exit criteria (integration-first) |
| [ADRs](docs/adr/) | Recorded decisions |
| [Spec: Integration layer](docs/specs/integration-layer.md) | Square connector, ingestion pipeline, freshness |
| [Spec: Sous](docs/specs/sous.md) | Assistant design, tools, approval path |
| [Research: ChefOS](docs/research/chefos-public-site.md) | Competitive research on the reference product |

## Run the prototype

Requires Python 3.11+. No third-party packages.

```sh
python3 server.py
```

- Marketing site: http://127.0.0.1:8000/
- Workspace: http://127.0.0.1:8000/app
- Health: http://127.0.0.1:8000/api/v1/health

The first run seeds a fictional restaurant and prints the demo sign-in to the console (`owner@chefos.local` / `ChefOS-demo-2026!`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHEFOS_DB_PATH` | `~/.chefos/chefos.sqlite3` | SQLite database file (kept outside the web root) |
| `PORT` | `8000` | Bind port |
| `CHEFOS_HOST` | `127.0.0.1` | Bind address |

### What the prototype covers

SQLite-backed API (`/api/v1`) and screens for inventory adjustments, purchase orders and receiving, recipe costing, waste, employees, weekly shifts, shift requests, time clock, tasks, sales summaries, team channels, and a restricted sales-ingest API key. Sous is a deterministic keyword-based stand-in.

Endpoints: `/auth/{register,login,logout,me,accept-invitation}`, `/inventory`, `/purchase-orders`, `/recipes`, `/waste`, `/employees`, `/invitations`, `/shifts`, `/time-entries`, `/tasks`, `/sales`, `/reports/summary`, `/channels`, `/api-keys`, `/sous`.

**Sales ingest:** create a key in **Workspace → Integrations**, then `POST /api/v1/sales/ingest` with `Authorization: Bearer ck_live_…`. Include a stable `external_id` so retries update the same record. Keys are shown once, stored hashed, and limited to sales writes.

### Security boundary

PBKDF2 password hashing, HttpOnly SameSite session cookies, CSRF tokens, org-scoped queries, input validation at the API boundary. It's a single-process development server: don't expose it to the internet.
