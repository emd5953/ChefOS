# Roadmap

**Last updated:** 2026-10-19
**Related:** [PRD](prd.md), [ADR 0001: Integration-first](adr/0001-integration-first.md)

The sequence is integration-first. Module features are built on real imported data, not seeded records. Each phase has an exit criterion. Phases overlap where noted.

## Current state

- Static reproduction of the ChefOS public site (design reference).
- Prototype workspace (`server.py` + `product.js`): SQLite, auth, and basic screens for inventory, purchase orders, recipes, waste, employees, shifts, time clock, tasks, sales summaries, and a daily-sales ingest key. A sandbox, not the production codebase.
- Production services: not started.

## Phase 0: Platform foundation

FastAPI service skeleton, PostgreSQL with migrations and RLS, identity module (orgs, locations, users, roles, sessions), Temporal cluster and worker skeleton, Terraform for dev and staging, CI, OpenTelemetry wiring.

**Exit:** a user can sign up, create an org and location, and sign in on staging. A no-op Temporal workflow runs scoped to that org.

## Phase 1: Square integration (P0)

[Integration layer spec](specs/integration-layer.md), milestones M1–M5:

1. Connect Square, link locations.
2. Catalog and orders pipeline, mapping queue.
3. `daily_sales` read model, freshness, reconciliation against Square.
4. Webhooks and 13-month backfill.
5. Team members and timecards → `daily_labor`.

**Exit:** a sandbox seller connects. Daily net sales and labor match Square exactly for test days and update within 5 minutes.

## Phase 2: Commerce on real data (P0)

Web app shell. Commerce daily and date-range views, channel breakdown, period comparisons, labor %, freshness indicators throughout. COM-1 to COM-3.

**Exit:** an owner can answer "how did last week compare to the week before, by location and channel, and what was labor %?" without leaving the app. Every number shows its as-of time.

## Phase 3: Team (P0), can start during Phase 2

Employees linked to POS team members, schedule builder with projected labor cost against forecast sales, publish, availability and time off with approvals, mobile employee app. TEAM-1 to TEAM-4, MOB-1.

**Exit:** a GM builds and publishes a week's schedule against forecast. Employees see it on mobile, and time-off requests flow through approval.

## Phase 4: Inventory core (P0/P1)

Items, packs and unit conversions, pars, storage-area count sessions with exceptions, purchase orders and receiving, recipes linked to POS menu items. INV-1 to INV-4.

**Exit:** a kitchen manager runs a full count on a phone and places and receives an order. Plate cost is visible for mapped menu items.

## Phase 5: Sous read-only (P1)

Canonical metric definitions, typed read tools over commerce/team/inventory services, evidence and freshness in every answer, evaluation set in CI. [Sous spec](specs/sous.md) steps 1–4.

**Exit:** internal pilot meets evaluation thresholds for metric correctness, evidence coverage, and authorization.

## Phase 6: Breadth (P1/P2)

Push API (M6), second POS connector, payroll export, shift trades and open shifts, time clock, product mix, daily flash, waste and yield, Sous draft-and-approve shift changes, supplier/invoice connectors, food cost variance.

Order within Phase 6 is set by pilot customer demand.

## Competitive research (ongoing, not blocking)

Continue mapping the reference product: remaining public pages, the iOS App Store listing, and authenticated workflows if legitimate demo access becomes available. Findings go in [research/](research/chefos-public-site.md) and inform requirements. They are not a parity target.
