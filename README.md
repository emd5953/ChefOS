# ChefOS

A learning project: a local, demo-able restaurant operations platform built with production-grade practices, at zero cost. It runs alongside the POS. It brings sales, labor, and inventory data into one place and builds the daily workflows on top of it: scheduling, reporting, ordering, and an assistant that answers operating questions.

Modules: **Team**, **Commerce**, **Inventory**. Shared capabilities: **Integrations** (POS, labor, and supplier data) and **Sous** (assistant).

## Status

| Area | State |
| --- | --- |
| Product requirements | Draft ([PRD](docs/prd.md)) |
| Platform foundation (Phase 0) | Built: local stack, RLS, auth, Temporal workers, encryption, CI |
| Square integration (M1–M5) | Built: simulated sandbox by default, real sandbox optional |
| Commerce (Phase 2) | Built: overview, sales, menu items, freshness |
| Sous (read-only) | Built: local model via Ollama, 5 Commerce tools, grounded answers, 40-case eval (`make sous-eval`) |
| Team, Inventory | Not started ([roadmap](docs/roadmap.md)) |
| Prototype workspace | Legacy UX reference (`server.py`, `product.js`); removed once fully covered |
| Marketing site | Served by the web app at `/` |

## Run the demo

Requires Docker Desktop, [uv](https://docs.astral.sh/uv/), and Node 24. Everything runs locally for free. For Sous, also `brew install ollama && brew services start ollama && ollama pull qwen3:8b`.

```sh
make up                      # Postgres, Temporal, RustFS, OTel, Jaeger, API, worker
make demo                    # demo account + simulated Square + 13-month backfill (~30 s)
cd apps/web && npm install && npm run dev
```

Open http://localhost:3000/login and sign in as `owner@chefos.local` / `ChefOS-demo-2026!`.

**Walkthrough:** Overview (live today, last 7 days vs previous, 30-day trend) → Sales (any range, compare to previous period or last year, channels, labor %) → Menu items → Integrations (sync health, backfill, mapping issues, sync runs). New orders from the simulated POS arrive every minute during service hours (America/New_York).

| URL | What |
| --- | --- |
| http://localhost:3000 | Web app and marketing site |
| http://localhost:8080/docs | API (OpenAPI) |
| http://localhost:8233 | Temporal UI (workflows, schedules) |
| http://localhost:16686 | Jaeger (traces) |

Other targets: `make test`, `make lint`, `make smoke`, `make down` (keep data), `make reset` (wipe data). To use a real Square sandbox, set `SQUARE_MODE=sandbox` and the `SQUARE_*` credentials in `.env`.

## Documentation

| Doc | Purpose |
| --- | --- |
| [PRD](docs/prd.md) | Problem, users, requirements, scope |
| [Architecture](docs/architecture.md) | System design, modules, data model, tenancy |
| [Roadmap](docs/roadmap.md) | Phases and exit criteria (integration-first) |
| [ADRs](docs/adr/) | Recorded decisions |
| [Spec: Platform foundation](docs/specs/platform-foundation.md) | Phase 0: local stack, RLS, auth, workers, CI |
| [Spec: Integration layer](docs/specs/integration-layer.md) | Square connector, ingestion pipeline, freshness |
| [Spec: Sous](docs/specs/sous.md) | Assistant design, tools, approval path |
| [Research: ChefOS](docs/research/chefos-public-site.md) | Competitive research on the reference product |
| [Interview prep](docs/interview-prep.md) | Engineering topics mapped to this codebase, and production gaps |

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
