# Competitive Research: ChefOS (Counter Labs)

**Subject:** ChefOS by Counter Labs / Counter Hospitality Labs, Inc. ([chefos.us](https://chefos.us/))
**Reviewed:** 2026-10-18 · **Basis:** public website only ([capture record](evidence/2026-10-18-public-site.md))

ChefOS is the closest reference product for this project. This document records what its public site claims, to inform our [requirements](../prd.md). None of it has been verified in the product itself. No account, API, or native app was used.

## Evidence labels

- **Observed:** directly captured from a rendered page.
- **Vendor claim:** stated on a public page, not independently verified.
- **Inferred:** a relationship suggested by several claims.
- **Unknown:** needs product access or official documentation.

Unless marked otherwise, everything below is a **vendor claim**.

## Positioning

A restaurant operating system with three modules (Team, Commerce, Inventory) that runs alongside the existing POS and connected services rather than replacing them. It claims support for single and multi-location operators and describes a "shared data layer" across modules.

## Modules

### Team

Demand-informed scheduling; employees, positions, stations, locations; open shifts; shift trades with manager approval; availability and time off; tasks; attendance; compliance. Mobile copy adds schedule access, change alerts, and manager views of staffing, tasks, late clock-ins, and approvals.

**Unknown:** forecast inputs, labor-law rules, trade state machine, time-clock source, payroll export mapping.

### Commerce

Sales, labor, margin, product mix, channel, daily flash, and variance reporting. **Observed:** the Commerce page renders a date-range sales timeline (Mar 1–Oct 18, 2026) with per-day buttons and Wilmington weather context.

**Unknown:** metric formulas, business-day cutoff, POS reconciliation, weather source and use.

### Inventory

Counts by storage area and unit, count exceptions, pars, ordering and receiving, pack/unit conversion, invoices and AP, transfers, recipes, waste, stock alerts, forecast-driven prep sheets, 86-list broadcasting, theoretical vs actual variance.

**Unknown:** count-session lifecycle, approval thresholds, conversion rules, invoice OCR, valuation method, variance formula.

### Cross-cutting

- **Waste and yield:** tracked by station, shift, and SKU, with shrink analysis and root-cause signals.
- **Sous (AI):** natural-language questions, reports and briefings, anomaly detection, suggested actions, schedule generation. How it cites sources and which actions need confirmation is unknown.
- **Integrations:** 48+ connectors in eight categories (POS, payroll/HR, accounting, scheduling/time, inventory/suppliers, delivery, reservations, communications/identity), plus a REST API and webhooks. **Observed:** the page shows a sample sales-ingest API request (not called).
- **Mobile:** iOS app linked from the site. Employee self-service, manager operations, and inventory tasks (counts, invoice scans, 86 checks). The app has not been inspected.
- **Hardware:** register and handheld labeled "Coming soon."

## Technology signals

- **Observed:** the marketing site ships `/_next/static/` assets, Turbopack chunk names, and Next Flight payloads, so the marketing site is built with Next.js. This says nothing about the authenticated product.
- The site links to a status page and an iOS app.
- Nothing public establishes the product's backend, database, cloud, tenancy model, connector implementation, or AI design.

## Pricing (as presented)

Per location per month: $200 for one module, $300 for two, $450 for all three. Multi-location pricing is custom. Terms and limits are unknown.

## Takeaways for our product

1. **Integrations are the foundation.** The product's value depends on POS and labor data it doesn't own. This drove [ADR 0001](../adr/0001-integration-first.md).
2. **Modules are sold separately**, so each must work on its own while sharing one data model.
3. **Mobile is a primary surface** for employees and inventory work, not a responsive afterthought.
4. **The AI assistant is a headline feature.** Ours must win on trust: evidence, freshness, and approval-gated actions ([ADR 0003](../adr/0003-sous-read-only-first.md)).

## Further research

- Remaining public pages: FAQ, legal, security, status.
- App Store listing and screenshots.
- Integration catalog: exact vendor list per category.
- Authenticated product walkthrough, if legitimate demo access becomes available.

## Sources

- [Homepage](https://chefos.us/) · [Team](https://chefos.us/team) · [Commerce](https://chefos.us/commerce) · [Inventory](https://chefos.us/inventory) · [Integrations](https://chefos.us/integrations) · [Mobile app](https://chefos.us/mobile-app) · [Pricing](https://chefos.us/pricing)
- [Capture record with evidence IDs](evidence/2026-10-18-public-site.md)
