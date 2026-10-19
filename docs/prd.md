# Product Requirements

**Status:** Draft · **Last updated:** 2026-10-19
**Related:** [Architecture](architecture.md), [Roadmap](roadmap.md), [Competitive research: ChefOS](research/chefos-public-site.md)

## Project scope

ChefOS is a learning project: a local, demo-able restaurant operations platform built with production-grade engineering practices. It runs entirely on a developer machine at zero cost ([ADR 0002](adr/0002-production-stack.md)). The requirements below describe the product as if it were real. That's the point of the exercise. Deployment, real customers, and paid services are out of scope.

## Problem

Independent and small multi-location restaurants run on a POS plus a patchwork of scheduling apps, payroll, supplier portals, spreadsheets, and group texts. Managers can't easily answer basic operating questions: Are we overstaffed tonight? What did we actually spend on food last week? Which items are about to run out? The data exists in other systems, but nothing connects it to the daily work of scheduling, ordering, and running service.

## Product

A restaurant operations platform that sits alongside the existing POS. It brings sales, labor, and inventory data into one place and builds daily workflows on top of it. It has three modules, sold individually or together:

- **Team:** scheduling, time and attendance, shift changes, tasks.
- **Commerce:** sales and performance reporting.
- **Inventory:** counts, purchasing, receiving, recipes, waste.

Two shared capabilities run across all three:

- **Integrations:** POS, labor, and supplier data brought in automatically.
- **Sous:** an assistant that answers operating questions from that data.

The reference product in this category is ChefOS by Counter Labs. See [competitive research](research/chefos-public-site.md).

## Users

| Persona | Context | Primary jobs |
| --- | --- | --- |
| **Owner / operator** | 1–10 locations, often not on site | Understand performance, control labor and food cost, compare locations |
| **General manager** | On site, runs the building | Build schedules, approve changes, order stock, review daily numbers |
| **Kitchen manager / chef** | Back of house | Counts, ordering, recipes and costing, waste, prep |
| **Employee** | Hourly, mobile-only | See schedule, clock in/out, request trades and time off, complete tasks |

**Target segment:** single-location full-service restaurants. The data model stays multi-location; the UX and demo data assume one location.

## Goals

- Accurate daily sales and labor numbers without manual entry, within minutes of the POS.
- Cut weekly scheduling time for a GM by half.
- Make food cost visible weekly, not monthly.
- Employees do all self-service from their phone.

## Non-goals

- Replacing the POS or processing payments.
- Payroll processing. We export to payroll providers.
- Accounting or general ledger.
- Labor-law compliance rules.
- Our own time clock in v1. Punches come from POS timecards.
- Cloud deployment, real customer accounts, or any paid service.
- Register and handheld hardware.

## Requirements

Priority: **P0** is required for first production release, **P1** follows soon after, **P2** comes later.

### Integrations

| ID | Requirement | Priority |
| --- | --- | --- |
| INT-1 | Connect a Square sandbox account via OAuth and link Square locations to platform locations | P0 |
| INT-2 | Import catalog, orders (with refunds/returns), team members, and timecards; keep them current within 5 minutes | P0 |
| INT-3 | Backfill 13 months of history on connect | P0 |
| INT-4 | Show per-connection sync health and data freshness; never present stale data as current | P0 |
| INT-5 | Mapping queue for unlinked locations, items, and team members | P0 |
| INT-6 | Push API for sales from systems without a connector | P1 |
| INT-7 | Toast connector (against recorded fixtures; partner API access requires approval) | P1 |
| INT-8 | Payroll export, supplier/invoice, and accounting connectors | P2 |

Design: [Integration layer spec](specs/integration-layer.md).

### Commerce

| ID | Requirement | Priority |
| --- | --- | --- |
| COM-1 | Daily and date-range sales by location and channel, with defined metrics (net sales, orders, average check) | P0 |
| COM-2 | Period comparisons (week over week, year over year) | P0 |
| COM-3 | Labor % (actual labor cost / net sales) by day and location | P0 |
| COM-4 | Product mix by item and category | P1 |
| COM-5 | Daily flash report delivered by email/push | P1 |
| COM-6 | Theoretical vs actual food cost variance | P2 |
| COM-7 | Weather and event context on the timeline | P2 |

### Team

| ID | Requirement | Priority |
| --- | --- | --- |
| TEAM-1 | Employees, positions, stations, and locations; linked to POS team members | P0 |
| TEAM-2 | Weekly schedule builder with projected labor cost against forecast sales | P0 |
| TEAM-3 | Publish schedules; employees see them on mobile and get change alerts | P0 |
| TEAM-4 | Availability and time-off requests with manager approval | P0 |
| TEAM-5 | Shift trades and open shifts with manager approval | P1 |
| TEAM-6 | Actual hours from imported POS timecards; late/missed clock-in alerts against the schedule | P1 |
| TEAM-7 | Shift tasks and checklists | P1 |

### Inventory

| ID | Requirement | Priority |
| --- | --- | --- |
| INV-1 | Items with purchase packs and unit conversions; pars per location | P0 |
| INV-2 | Count sessions by storage area, with exceptions for large swings | P0 |
| INV-3 | Purchase orders to suppliers; receiving against the PO | P1 |
| INV-4 | Recipes linked to items and to POS menu items; plate cost | P1 |
| INV-5 | Waste and yield logging by station, shift, and item | P1 |
| INV-6 | Transfers between locations | P2 |
| INV-7 | Invoice capture and AP reconciliation | P2 |
| INV-8 | 86 list broadcast to staff | P2 |

### Sous

| ID | Requirement | Priority |
| --- | --- | --- |
| SOUS-1 | Answer operating questions with evidence, time window, and freshness | P1 |
| SOUS-2 | Daily briefing per location | P1 |
| SOUS-3 | Draft-and-approve shift changes | P2 |
| SOUS-4 | Anomaly detection (sales, labor, waste) | P2 |

Design: [Sous spec](specs/sous.md), [ADR 0003](adr/0003-sous-read-only-first.md).

### Mobile

| ID | Requirement | Priority |
| --- | --- | --- |
| MOB-1 | Employee: schedule, availability, time off, trades, tasks | P0 |
| MOB-2 | Manager: approvals, today's staffing, late clock-ins, daily numbers | P1 |
| MOB-3 | Inventory: counts and receiving on a phone | P1 |

## Cross-cutting requirements

- **Multi-location:** every record belongs to an organization and location. Users see only the locations they're assigned to.
- **Roles:** owner, admin, manager, employee at minimum. Permissions are enforced server-side.
- **Source and freshness:** imported data keeps its source and as-of time. Any number derived from imported data shows its freshness.
- **Audit:** changes to schedules, approvals, inventory, and settings are recorded with actor and time.
- **Accessibility and states:** every workflow defines its loading, empty, error, and offline (mobile) states.

## Success criteria (demo)

- From a clean checkout, `docker compose up` plus a seed script yields a working demo in under 15 minutes.
- Connecting a Square sandbox seller shows accurate daily sales within 15 minutes.
- Daily net sales matches Square's own report exactly for every seeded test day.
- The core walkthrough (connect POS → review sales and labor → build and publish a schedule → employee sees it on mobile → ask Sous a question) runs end to end without manual database edits.

## Decisions log

| Decision | Choice |
| --- | --- |
| Name | ChefOS |
| Segment | Single-location full-service |
| Time clock | POS timecards in v1 |
| Labor compliance | Out of scope |
| Second POS | Toast (fixtures) |
| Net sales | Gross item sales − discounts/comps − returns; excludes tax, tips, service charges, gift cards; returns on processing date |
