# Roadmap

**Last updated:** 2026-10-21
**Related:** [PRD](prd.md), [ADR 0001: Integration-first](adr/0001-integration-first.md)

The sequence is integration-first. Module features are built on real imported data, not seeded records. Each phase has an exit criterion. Phases overlap where noted.

## Current state

As of 2026-10-20:

- **Phase 0:** done.
- **Phase 1:** M1–M5 done in simulated mode. Real-sandbox mode is untested.
- **Phase 2:** done. Commerce views built, marketing site moved into `apps/web`, prototype deleted.
- **Phase 5:** done. Sous read-only over Commerce, Team (labor, published schedule) and Inventory (below par). The 53-case eval passes 50/53 on qwen3:8b and meets all thresholds (0 authorization violations, 100% grounded, 95.5% value correctness, 97.4% evidence coverage).
- **Phase 3:** done ([spec](specs/team.md), T1–T5). Employees from Square, logins by invitation, schedule builder with forecast and projected labor %, publish with change alerts, availability and time off with approvals, and an employee mobile view at `/my` ([ADR 0004](adr/0004-mobile-as-responsive-web.md)). Seeded by `make demo`.
- **Phase 4:** done ([spec](specs/inventory.md)). INV-1 to INV-4 and MOB-3: catalog, counts with exceptions, purchasing and receiving, recipes with plate cost, phone-sized count and receive.
- **Phase 6:** selected scope done ([spec](specs/breadth.md), B1–B6): Sous Team/Inventory tools, daily briefing (SOUS-2), product mix (COM-4), waste (INV-5), push API with API keys (INT-6), clock-in alerts and attendance (TEAM-6). Follow-ups: F1 gives pushed sales a 26-hour freshness window; F3 counts open punches as running time. Still deferred: Toast connector, payroll export, shift trades and open shifts, Sous draft-and-approve, supplier/invoice connectors, food cost variance.
- **Prototype workspace:** deleted from `main`. UX reference at tag `prototype-v0`; run it with `git worktree add ../chefos-prototype prototype-v0 && cd ../chefos-prototype && python3 server.py`.

## Phase 0: Platform foundation

[Spec](specs/platform-foundation.md). Monorepo layout (`apps/api`, `apps/web`, `infra/`), Docker Compose (Postgres, Temporal dev server, RustFS, OTel Collector, Jaeger), FastAPI skeleton, migrations and RLS, identity module (orgs, locations, users, roles, sessions), worker skeleton, `KeyProvider`, GitHub Actions CI (free tier), OpenTelemetry wiring.

**Exit:** from a clean checkout, `docker compose up` starts everything. A user can sign up, create an org and location, and sign in. A no-op Temporal workflow runs scoped to that org and shows up in Jaeger. CI runs tests, lint, and migrations.

## Phase 1: Square integration (P0)

[Integration layer spec](specs/integration-layer.md), milestones M1–M5:

1. Connect Square, link locations.
2. Catalog and orders pipeline, mapping queue.
3. `daily_sales` read model, freshness, reconciliation against Square.
4. Webhooks and 13-month backfill.
5. Team members and timecards → `daily_labor`.

**Exit:** a sandbox seller connects. Daily net sales and labor match Square exactly for test days and update within 5 minutes.

## Phase 2: Commerce on real data (P0)

Web app shell with the marketing site moved in. Commerce daily and date-range views, channel breakdown, period comparisons, labor %, freshness indicators throughout. COM-1 to COM-3. Delete the prototype (`server.py`, `product.js`) once its screens are covered.

**Exit:** an owner can answer "how did last week compare to the week before, by location and channel, and what was labor %?" without leaving the app. Every number shows its as-of time.

## Phase 3: Team (P0), can start during Phase 2

[Spec](specs/team.md). Employees linked to POS team members, schedule builder with projected labor cost against forecast sales, publish, availability and time off with approvals, mobile employee view (a responsive web view, [ADR 0004](adr/0004-mobile-as-responsive-web.md)). TEAM-1 to TEAM-4, MOB-1.

**Exit:** a GM builds and publishes a week's schedule against forecast. Employees see it on mobile, and time-off requests flow through approval. Met 2026-10-20 (`tests/team/test_exit_criterion.py` runs it on seeded data).

## Phase 4: Inventory core (P0/P1) — done

Items, packs and unit conversions, pars, storage-area count sessions with exceptions, purchase orders and receiving, recipes linked to POS menu items. INV-1 to INV-4.

**Exit:** a kitchen manager runs a full count on a phone and places and receives an order. Plate cost is visible for mapped menu items.

## Phase 5: Sous read-only (P1)

Canonical metric definitions, typed read tools over commerce/team/inventory services, evidence and freshness in every answer, evaluation set in CI. [Sous spec](specs/sous.md) steps 1–4.

**Exit:** the evaluation set meets thresholds for metric correctness, evidence coverage, and authorization.

## Phase 6: Breadth (P1/P2)

Push API (M6), Toast connector (fixtures), payroll export, shift trades and open shifts, product mix, daily flash, waste and yield, Sous draft-and-approve shift changes, supplier/invoice connectors, food cost variance.

Order within Phase 6 is set by what makes the demo walkthrough strongest.

## Competitive research (ongoing, not blocking)

Continue mapping the reference product: remaining public pages, the iOS App Store listing, and authenticated workflows if legitimate demo access becomes available. Findings go in [research/](research/chefos-public-site.md) and inform requirements. They are not a parity target.
