# Breadth: Phase 5 completion and Phase 6

**Status:** Proposed · **Last updated:** 2026-10-20  
**Related:** [Roadmap](../roadmap.md), [PRD](../prd.md), [Sous](sous.md), [Team](team.md), [Inventory](inventory.md), [Integration layer](integration-layer.md), [Architecture](../architecture.md)

## Problem

The demo already connects real sales to labor, schedules, inventory and Sous, but those modules do not yet tell a coherent daily operations story. Phase 5 still lacks Sous tools over Team and Inventory and the promised daily briefing. Phase 6 should add visible value on top of existing data without expanding into connector or payroll projects before the walkthrough is stronger.

## Goals / Non-goals

**Goals:** finish scoped Sous reads and their evals; provide a location's in-app daily briefing; show item-level sales mix, log waste against stock, and accept external sales pushes using identity-owned API keys. Evaluate deferred Team requirements and ship high-value, small additions.

**Non-goals:** email delivery; LLM-authored or automatic schedule edits; Toast or supplier connectors, payroll export, invoices/AP, food-cost variance, transfers, 86 broadcast, native push, and broad labor compliance. These add external dependencies, substantial workflow or reconciliation scope, or weaker demo value per effort than the selected features. Do not include TEAM-5 trades/open shifts (approval, coverage and employee notification flows are a larger vertical slice), or TEAM-7 tasks/checklists (new assignment, recurrence and completion workflows). Revisit after the selected demo path.

## Approach

Build on domain services and canonical records; no new source of truth for sales or labor. Sous tools remain typed, read-only, scoped, evidence-bearing and eval-gated. Product mix aggregates order lines. Waste is an audited inventory movement. Push reuses the integration pipeline and freshness model, with key issuance/revocation owned by identity. Daily briefing is a deterministic in-app view built from the same services and Sous tools; it must work when the local model is unavailable.

**Demo order:** ask Sous what needs attention → open the daily briefing → inspect yesterday's item mix and record a waste entry → push a day's sales from an external client. This makes cross-module insight visible before showing data entry and integration setup.

## Design

All routes require authenticated, location-scoped access except push ingestion, which authenticates a scoped API key. Tables carry `org_id`, use existing RLS conventions, and writes are audited.

| Feature | Tables / migration | Routes | Screens |
| --- | --- | --- | --- |
| Sous Team/Inventory reads + evals | None; reuse schedule, timecard, inventory and freshness models | Tool signatures: `get_labor_summary(location_ids, from, to) → LaborFacts`; `get_schedule(location_ids, from, to) → ScheduleFacts`; `find_items_below_par(location_ids) → ItemFacts`; retain existing sales tools | Sous remains the question surface |
| SOUS-2 daily briefing | No tables; compute per request | `GET /v1/sous/briefing?location_id=&date=` → `BriefingOut` | `/app/sous` briefing card, date/location context, evidence and freshness |
| COM-4 product mix | None; `sales_order_lines` and `menu_items` | `GET /v1/commerce/items` (extend with category and stable sort/filter as needed) | Commerce item/category mix with mapped and unmapped sales stated |
| INV-5 waste | `0011_inventory_waste`: `waste_entries(id, org_id, location_id, item_id, quantity_base, unit_cost_minor, reason, station_id?, shift_id?, business_date, created_by, created_at)`; append matching `stock_movements` type `waste` | `POST /v1/inventory/waste`; `GET /v1/inventory/waste?location_id=&from=&to=` | Inventory waste log and entry form; station/shift optional until supported by catalog |
| INT-6 push API | `0012_identity_api_keys`: hashed key, prefix, scopes, optional location binding, creator, created/revoked timestamps; no plaintext stored | Admin key lifecycle `POST/GET/DELETE /v1/api-keys`; `POST /v1/ingest/orders`, `POST /v1/ingest/daily-sales` with `Idempotency-Key` | Integrations API key management and copy-once secret |

`BriefingOut` contains location/business date/timezone, highlights, evidence references, freshness and limitations. Initial sections: yesterday sales/labor, today's published staffing, and items below par. No persisted briefing or email queue. `LaborFacts` reports actual hours/cost and sales-derived labor percent; schedule tool reports published shifts for the requested dates and clearly labels draft access only for authorized managers if later added. Inventory facts include quantity, base unit, par, location and as-of. Tool outputs cap ranges/results and reject unauthorized locations before querying.

## Behavior

- Resolve relative dates in the location timezone and business-day boundary. A missing date defaults to the last completed business day for results and today's published schedule for staffing.
- Briefing and Sous answers show freshness per contributing stream, disclose unmapped/partial data, and never imply missing `scheduled_hours` are zero actual labor. If a component fails, show available sections and identify the unavailable source.
- Product mix sums `sales_order_lines.quantity` and `net` by canonical variant/category; unmapped lines remain in an explicit total. Respect date bounds and return processed refunds per canonical sales definitions.
- Waste requires positive quantity, item, reason and location; convert to base units, snapshot unit cost, decrement stock once, and audit. Duplicate idempotency key returns original result. Reject negative stock unless the existing inventory policy explicitly allows it.
- Push validates key scope and location binding, payload limits, business date and currency; identical idempotency retries return the original response. One location has one sales source; reject conflicts with an active connector. Never accept org or location scope from payload without key-bound authorization.
- Sous clarifies ambiguous dates/locations, refuses out-of-scope requests, grounds every numeric claim, and returns evidence-only fallback if grounding fails.

## Verification

- Extend Sous evals with expected tool, values, evidence and limitation: labor summary including no timecards; schedule draft vs published and overnight shifts; below-par units, missing par and inaccessible location; stale/failing streams; partial tool failure; ambiguous date; prompt injection in item names. Thresholds: 0 authorization violations, 100% numeric grounding, metric correctness ≥95%, evidence coverage ≥95%.
- Briefing contract cases: local midnight/day-start, missing each data stream, stale data, no published schedule, model unavailable; compare each number to its underlying service result.
- Product mix reconciles mapped category totals plus unmapped sales to order-line totals, including returns, empty periods and category changes.
- Waste tests unit conversion, audit, stock movement, duplicate submission and cross-tenant denial.
- Push tests per-scope authorization, revoked/expired keys, key hashing, idempotent replay, source conflict, invalid payload and RLS isolation.
- Manual demo walkthrough on seeded data; no tests added beyond these acceptance cases unless requested.

## Risks

Briefings can look authoritative when source streams are stale; freshness must be prominent. Product names and category links can be incomplete, so unmapped totals must stay visible. Waste valuation depends on the latest known item cost and is an estimate, not accounting. API keys are bearer credentials: display once, hash at rest, scope narrowly, and audit lifecycle. Late-clock alerts remain misleading until schedule hours feed `daily_labor` consistently.

## Milestones

Ordered by demo value; milestones have disjoint module ownership and can be built in parallel (migration numbers are assigned in merge order after 0010).

1. **B1 Sous breadth (remaining Phase 5):** `apps/api/app/sous/{tools.py,schemas.py}`, `apps/api/tests/sous/`, `docs/specs/sous.md`. Add three read tools and eval cases. Exit: authorized Team/Inventory questions are grounded and thresholds pass.
2. **B2 Daily briefing (SOUS-2):** `apps/api/app/sous/{api.py,service.py,schemas.py}`, `apps/web/app/app/sous/`, `apps/web/src/`. Deterministic in-app briefing, no email. Exit: usable with model offline and freshness visible.
3. **B3 Product mix (COM-4):** `apps/api/app/commerce/{service.py,schemas.py,api.py}`, `apps/web/app/app/commerce/`. Exit: category/item mix reconciles and exposes unmapped sales.
4. **B4 Waste (INV-5):** `apps/api/migrations/versions/0011_inventory_waste.py`, `apps/api/app/inventory/{models.py,schemas.py,api.py}`, `apps/web/app/app/inventory/`. Exit: audited entry changes stock once and appears in date-filtered log.
5. **B5 Push API (INT-6):** `apps/api/migrations/versions/0012_identity_api_keys.py`, `apps/api/app/identity/{models.py,api.py,service.py}`, `apps/api/app/integrations/{api.py,push.py}`, `apps/web/app/app/integrations/`. Exit: external totals-only client ingests through shared pipeline with idempotency and freshness.
6. **B6 Clock-in alerts (TEAM-6):** `apps/api/migrations/versions/0013_team_attendance.py`, `apps/api/app/team/`, `apps/api/app/integrations/readmodels.py` (scheduled hours only), `apps/web/src/app/app/team/`. Fill `daily_labor.scheduled_hours` from published shifts. Compare imported timecards with published shifts: a shift with no clock-in 10 minutes after start is *late*, and one with none by its end is *missed*. Late and missed shifts raise in-app notifications to the location's managers (the existing notifications table, deduplicated per shift and kind) and show on an attendance view (scheduled vs actual per shift and day). Exit: on seeded data a GM sees today's late or missed clock-ins and scheduled vs actual hours. Added by the coordinator. The earlier objection, that scheduled hours were zero, is resolved by filling them from published shifts.
