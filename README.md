# ChefOS

A learning project: a local, demo-able restaurant operations platform built with production-grade practices, at zero cost. It runs alongside the POS. It brings sales, labor, and inventory data into one place and builds the daily workflows on top of it: scheduling, reporting, ordering, and an assistant that answers operating questions.

Modules: **Team**, **Commerce**, **Inventory**. Shared capabilities: **Integrations** (POS, labor, and supplier data) and **Sous** (assistant).

## Status

| Area | State |
| --- | --- |
| Product requirements | Draft ([PRD](docs/prd.md)) |
| Platform foundation (Phase 0) | Built: local stack, RLS, auth, Temporal workers, encryption, CI |
| Square integration (M1–M5) | Built: simulated sandbox by default, real sandbox optional |
| Commerce (Phase 2 / COM-4) | Built: overview, sales, menu item and category mix, unmapped sales, freshness. Phase 2 is complete |
| Sous | Built: local model via Ollama, 8 grounded read tools plus manager-reviewed schedule addition, adjustment, and removal proposals; changes require approval and publish, 53-case eval (`make sous-eval`) |
| Daily briefing (SOUS-2) | Built: deterministic briefing card on `/app/sous` (yesterday's sales and labor, today's published staffing, items below par) with per-stream freshness; works with the model offline |
| Team (Phase 3) | Built: employees from Square, invitations and roles, schedule builder with forecast and projected labor %, publish with change alerts, availability and time off, employee mobile view at `/my` ([spec](docs/specs/team.md), [ADR 0004](docs/adr/0004-mobile-as-responsive-web.md)) |
| Inventory (Phase 4 + INV-5) | Done: catalog, counts, purchasing, receiving, recipes, plate costs, mobile workflows, audited waste entries ([spec](docs/specs/inventory.md)) |
| External sales push (INT-6) | Built: scoped, expiring API keys (hashed, shown once), idempotent totals ingestion; push freshness lasts 26 hours and reports unsupported streams as not connected |
| Clock-in alerts (Phase 6, TEAM-6) | Built: late/missed clock-in alerts to managers, attendance view (scheduled vs actual per shift and day, open punches as running time), `daily_labor.scheduled_hours` from published shifts |
| Team shift coverage (TEAM-5) | Built: employees can claim open shifts or propose trades; coworker and manager approvals update the draft, with publish required before changes take effect ([spec](docs/specs/team-shift-coverage.md)) |
| Backend module boundaries | Enforced: modules expose a declared `PUBLIC` surface, routes stay thin over service layers, and a test blocks new module cycles ([spec](docs/specs/backend-module-boundaries.md)) |
| Food cost variance (COM-6) | Built: count-anchored actual vs recipe-theoretical usage with historical recipe mappings, receipt weighted-average valuation, explicit coverage, and manager report ([spec](docs/specs/food-cost-variance.md)) |

## Run the demo

Requires Docker Desktop, [uv](https://docs.astral.sh/uv/), and Node 24. Everything runs locally for free. For Sous, also `brew install ollama && brew services start ollama && ollama pull qwen3:8b`.

```sh
make up                      # Postgres, Temporal, RustFS, OTel, Jaeger, API, worker
make demo                    # demo account + simulated Square + 13-month backfill (~30 s)
cd apps/web && npm install && npm run dev
```

Open http://localhost:3000/login and sign in as `owner@chefos.local` / `ChefOS-demo-2026!`. The demo also seeds a manager (`gm@chefos.local`) and an employee linked to the imported Square team member Maya Okafor (`maya@chefos.local`), same password, plus a published schedule for the current week.

**Walkthrough:** Overview (live today, last 7 days vs previous, 30-day trend) → Sales (any range, compare to previous period or last year, channels, labor %) → Menu items → Integrations (sync health, backfill, mapping issues, sync runs) → Team (employees from Square, invitations) → Schedule (next week: copy last week, watch projected labor % against the forecast, publish) → log in as `maya@chefos.local` at phone width for `/my` (schedule, time off, availability, alerts, shift claims and trades) → back as the GM, approve coverage and trade requests in Team → Requests. New orders from the simulated POS arrive every minute during service hours (America/New_York). Inventory: stock/par review → phone-sized count and exception review → purchase order/receipt → recipe plate cost → count-anchored food cost variance. Phase 6: Sous → daily briefing (yesterday's sales and labor, today's staffing, items below par; works with the model off) → manager-reviewed schedule proposal and approval → Menu items category mix → Inventory waste log → Team → Attendance (the GM's late and missed clock-in alerts for today) → Integrations API keys, then push a day's totals with `curl` (see [push API](docs/api.md)).

| URL | What |
| --- | --- |
| http://localhost:3000 | Web app and marketing site |
| http://localhost:8080/docs | API (OpenAPI) |
| http://localhost:8233 | Temporal UI (workflows, schedules) |
| http://localhost:16686 | Jaeger (traces) |

Other targets: `make test`, `make lint`, `make smoke`, `make down` (keep data), `make reset` (wipe data). To use a real Square sandbox, set `SQUARE_MODE=sandbox` and the `SQUARE_*` credentials in `.env`.

If PostgreSQL port 5432 is already occupied, set `POSTGRES_PORT` in `.env` and update both
host database URLs to that port. Tests use `TEST_DB_HOST=localhost:<port>`. Container-to-container
connections retain port 5432. Windows desktop evaluation setup and the completed **106/106**
live-model quality gate are documented in the [evaluation report](apps/api/evals/sous/results/2026-10-22.md).

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
| [Spec: Inventory](docs/specs/inventory.md) | Catalog, counts, purchasing, receiving, recipes, plate cost |
| [Spec: Breadth](docs/specs/breadth.md) | Phase 6: Sous breadth, daily briefing, product mix, waste, push API, clock-in alerts, follow-ups |
| [Spec: Team shift coverage](docs/specs/team-shift-coverage.md) | Open-shift claims and two-party shift trades |
| [Spec: Food cost variance](docs/specs/food-cost-variance.md) | COM-6 metric contract, data limits, and implementation plan |
| [Research: ChefOS](docs/research/chefos-public-site.md) | Competitive research on the reference product |
| [Interview prep](docs/interview-prep.md) | Engineering topics mapped to this codebase, and production gaps |
