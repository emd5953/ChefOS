# ADR 0001: Build the integration layer first

**Status:** Accepted · **Date:** 2026-10-19

## Context

The platform runs alongside a restaurant's existing POS, payroll, accounting, and supplier systems. It does not replace them. Most numbers the product shows come from those systems:

- **Commerce** reporting (sales, channel, product mix, daily flash) comes from POS orders.
- **Labor %** and demand-based scheduling need POS sales plus time-clock or payroll hours.
- **Food cost and variance** need POS item sales mapped to recipes, plus supplier invoice costs.
- **Sous** answers are only trustworthy if the underlying data is current and its freshness is known.

The earlier plan treated integrations as the fourth workstream, after Team, Inventory, and Commerce. The prototype seeds its own data and accepts only manually posted daily totals. That order lets module screens get built on data shapes that real POS data won't match: item mapping, business-date cutoffs, refunds, late edits, and multi-location identity.

## Decision

Build the integration layer before rebuilding module workflows on the production stack.

1. Ingestion foundation and canonical data model first ([spec](../specs/integration-layer.md)).
2. One POS connector, Square, end to end: catalog, orders, team members, and timecards.
3. Commerce read models built on imported data, not seeded data.
4. Labor data next, which unblocks demand-based scheduling.
5. Supplier/invoice data after that, which unblocks food cost and variance.

Module features that need only first-party data, such as task lists and manual inventory counts, can proceed in parallel. They must use the same canonical entities.

## Consequences

- The first user-visible feature is "connect your POS and see accurate daily sales," not a full module.
- The canonical model is shaped by real POS payloads from day one, so we avoid a later migration from prototype-shaped tables.
- Freshness and source traceability become platform primitives that every module and Sous can rely on.
- Team and Inventory workflow rebuilds start later than in the previous plan.
- Square's data shapes may leak into the canonical model. Mitigation: review the connector interface against a second POS (Toast) before the first connector milestone closes.
