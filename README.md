# ChefOS

A learning project: a local, demo-able restaurant operations platform built with production-grade practices, at zero cost. It runs alongside the POS. It brings sales, labor, and inventory data into one place and builds the daily workflows on top of it: scheduling, reporting, ordering, and an assistant that answers operating questions.

Modules: **Team**, **Commerce**, **Inventory**. Shared capabilities: **Integrations** (POS, labor, and supplier data) and **Sous** (assistant).

## Status

| Area | State |
| --- | --- |
| Product requirements | Draft ([PRD](docs/prd.md)) |
| Platform foundation (Phase 0) | Built: local stack, RLS, auth, Temporal workers, encryption, CI |
| Square integration (M1–M5) | Built: simulated sandbox by default, real sandbox optional |
| Commerce (Phase 2) | Built: overview, sales, menu items, freshness. Phase 2 is complete |
| Sous (read-only) | Built: local model via Ollama, 8 tools over Commerce, Team (labor, published schedule) and Inventory (below par), grounded answers, 53-case eval (`make sous-eval`) |
| Team (Phase 3) | Built: employees from Square, invitations and roles, schedule builder with forecast and projected labor %, publish with change alerts, availability and time off, employee mobile view at `/my` ([spec](docs/specs/team.md), [ADR 0004](docs/adr/0004-mobile-as-responsive-web.md)) |
| Inventory (Phase 4) | Done: catalog, counts, purchasing, receiving, recipes, plate costs, and mobile workflows ([spec](docs/specs/inventory.md)) |
| Marketing site | Served by the web app at `/` |

## Run the demo

Requires Docker Desktop, [uv](https://docs.astral.sh/uv/), and Node 24. Everything runs locally for free. For Sous, also `brew install ollama && brew services start ollama && ollama pull qwen3:8b`.

```sh
make up                      # Postgres, Temporal, RustFS, OTel, Jaeger, API, worker
make demo                    # demo account + simulated Square + 13-month backfill (~30 s)
cd apps/web && npm install && npm run dev
```

Open http://localhost:3000/login and sign in as `owner@chefos.local` / `ChefOS-demo-2026!`. The demo also seeds a manager (`gm@chefos.local`) and an employee linked to the imported Square team member Maya Okafor (`maya@chefos.local`), same password, plus a published schedule for the current week.

**Walkthrough:** Overview (live today, last 7 days vs previous, 30-day trend) → Sales (any range, compare to previous period or last year, channels, labor %) → Menu items → Integrations (sync health, backfill, mapping issues, sync runs) → Team (employees from Square, invitations) → Schedule (next week: copy last week, watch projected labor % against the forecast, publish) → log in as `maya@chefos.local` at phone width for `/my` (schedule, time off, availability, alerts) → back as the GM, approve the request in Team → Requests. New orders from the simulated POS arrive every minute during service hours (America/New_York). Inventory: stock/par review → phone-sized count and exception review → purchase order/receipt → recipe plate cost.

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
| [Spec: Team](docs/specs/team.md) | Employees, logins, schedules, forecast, requests, employee mobile view |
| [Research: ChefOS](docs/research/chefos-public-site.md) | Competitive research on the reference product |
| [Interview prep](docs/interview-prep.md) | Engineering topics mapped to this codebase, and production gaps |
