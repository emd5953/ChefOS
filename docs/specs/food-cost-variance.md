# Food cost variance (COM-6)

**Status:** COM-6A through COM-6D implemented, including complete-period golden evaluation · **Last updated:** 2026-11-02
**Related:** [PRD](../prd.md), [Roadmap](../roadmap.md), [Inventory](inventory.md), [Integration layer](integration-layer.md)

## Purpose

Show managers how much inventory depletion differs from the recipe-based usage implied by POS sales, with enough coverage information to tell whether a period total is comparable. This is an operational estimate for kitchen management, not accounting COGS or a tax valuation.

## What the current data supports

- `count_sessions` and `count_lines` preserve submitted stock snapshots by location, storage area, item, and count time. Each active item assigned to an area is captured when a count starts; submitted counts replace the stock balance.
- `receipt_lines` preserve received base quantities and actual line cost, and `receipts` preserve location and business date. Receipt movements update the current stock balance.
- `waste_entries` preserve a quantity and a unit-cost snapshot, but waste is already included in inventory depletion. It is a reason breakdown, not an extra quantity to add to actual usage.
- `sales_order_lines` preserve POS quantity and menu-item-variant mapping. Variants resolve to canonical `menu_items`, which can link to a recipe. Sales orders provide close time, business date, state, and an aggregate returns amount.
- `stock_movements` preserve receipt, waste, and count events, but do not carry cost. Counts do not capture an item valuation. The current plate-cost endpoint values recipes using the latest received unit cost across the organization; this is useful for current plate-cost display, not historical variance.

Current records do **not** contain historical recipe ingredient versions, effective-dated menu-to-recipe links, per-item POS return quantities, or an opening inventory valuation. Recipe ingredients and menu links can change, and the line item only points to the current mapping. A report over old sales cannot safely apply today's recipe and claim it represents the historical recipe. Existing stock data also cannot support FIFO or a perpetual-inventory COGS ledger.

## V1 measurement contract

### Period and inventory anchors

The user selects a location and submitted opening and closing count sessions for every active storage area at that location. All opening sessions must have the same location business date; all closing sessions must have the same later business date. Counts are treated as after-close snapshots: the measured business-date range is the day after the opening count date through the closing count date, inclusive. The UI instructs managers to complete each count after service close and reports the exact submission times and this boundary assumption.

Per item, actual depletion quantity is:

```text
opening counted quantity
+ received quantity on measured business dates
- closing counted quantity
```

This includes recorded waste and all other physical depletion (sold/prepared use, spoilage not logged as waste, and unexplained shrinkage). Do not add waste a second time. Report recorded waste as a visible subset of actual depletion. An intermediate count can be shown as an operational checkpoint but does not change the opening-to-closing equation. Receipts and POS sales use their location business-date buckets so the quantity equation and theoretical usage cover the same stated range.

The endpoint rejects reversed or mismatched count pairs (wrong org/location, wrong storage area, inconsistent opening/closing business dates, draft sessions, or a count that does not cover an active item assigned to its area). Negative depletion is a data-quality exception; never clamp it to zero. If counts do not cover all active assigned items, return item-level partial results and mark the location total incomplete. Count timestamps within the same business-date bucket can differ; the response exposes this limitation because the report cannot reconstruct intraday item usage from POS lines, which lack line-level close timestamps.

### Theoretical usage

For a mapped, completed POS line, theoretical ingredient usage is:

```text
sold menu quantity × recipe ingredient base quantity ÷ recipe yield quantity
```

V1 treats one sold menu unit as one recipe yield unit. The recipe's yield unit must represent the menu portion. Show this assumption in the report; recipes whose yield semantics are not confirmed are excluded from the complete total. This makes direct plate recipes comparable while avoiding an invented portion size for batch recipes.

Only completed sales are included. `sales_order_lines.quantity` is the sold quantity. The current return model is monetary and does not identify returned items or units; if the period contains an order return, mark theoretical usage as return-adjusted-incomplete and show the gross sold-unit basis. Do not guess item-level refund quantities.

Historical theoretical usage is available only from the first date at which the system has effective-dated recipe and menu-recipe snapshots. Add immutable recipe versions and effective-dated menu-recipe assignments before enabling the report. For historical backfill before that baseline, return `not_available` rather than applying the current recipe retroactively. When a line has no menu mapping, the menu item has no recipe, or its recipe is incomplete, report it in coverage exclusions and do not treat it as zero usage.

### Cost valuation and variance

Use a period- and location-specific **weighted-average received unit cost** per ingredient:

```text
sum(receipt line total cost in minor units) ÷ sum(received base quantity)
```

Use the same unit cost for actual depletion and theoretical usage, so the variance isolates quantity usage differences rather than mixing cost-method differences. Include only posted receipts whose location business date is in the measured range. Use exact decimals through aggregation and round half-up to the currency's minor unit only for display. Do not fall back to the current organization-wide last-received cost when the period has no receipts for an item; show quantity variance for that item and mark its dollar value unavailable.

```text
actual depletion cost = actual depletion quantity × period weighted-average unit cost
theoretical food cost = theoretical ingredient usage × same unit cost
variance = actual depletion cost - theoretical food cost
variance % = variance ÷ theoretical food cost, only when theoretical food cost > 0
```

Positive variance means the restaurant depleted more inventory value than recipes imply; negative means less. The report must label the variance as an estimate and show its period valuation basis. Missing or zero cost must remain `null`, not zero.

### Coverage and report state

Return coverage separately for inventory counts, receipt valuation, POS line mapping, recipe completeness, and order freshness. At minimum show item counts and sales-line/quantity coverage, plus explicit exclusion reasons:

- missing, stale, or incomplete opening/closing counts;
- no location receipt cost for an ingredient;
- unmapped sales lines, missing recipe links, or recipes with missing/archived ingredients;
- sales returns that cannot be allocated to menu items;
- sales/order stream not connected, stale, failing, or not fully synced through the end of the period;
- recipe or menu mapping history unavailable before the effective-dated baseline;
- inventory item assignment changes that prevent a matching count pair.

The response state is `complete`, `partial`, or `unavailable`. A complete location total requires every active assigned ingredient to have a matching count pair and valuation, all completed POS sales lines to map to a valid versioned recipe, no unresolved item-level return adjustments, and complete/fresh source data through the selected range. A partial response may show the covered subtotal and coverage percentages, but must not present it as the location's total or calculate an overall variance percentage. Never fill gaps with zero.

## Data changes required

1. Add immutable `recipe_versions` and `recipe_version_ingredients`, written transactionally for recipe creation and every recipe/ingredient edit. Each version records `effective_at`, yield, units, and ingredient quantities.
2. Add effective-dated `menu_recipe_assignments` (including explicit unlinked intervals) so sales resolve to the recipe assignment in effect at order close time. Backfill the initial assignment only from the feature activation date; do not infer older assignment history from the current value.
3. Record a feature `effective_from` baseline per org. Before this date historical theoretical cost is unavailable.
4. Keep all new tables org-scoped and under RLS. Add audit events for recipe versions and menu mapping changes; versions are immutable and corrections append a new version.

### Implemented backend foundation

Migration `0019` establishes the baseline at rollout and snapshots current recipes and menu links. Recipe creation and edits append immutable recipe versions with ingredient snapshots; menu link changes append effective-dated assignments, including explicit unlinks. Records before the baseline remain unavailable. `POST /v1/inventory/food-cost-variance` validates submitted count anchors and calculates item-level actual/theoretical usage, period weighted-average received cost, exclusions, returns, freshness, and completeness. It leaves location totals null when the input is partial. Recipe and menu mapping writes continue to use their existing audit events. The manager UI is at `/app/inventory/food-cost`. The complete-period golden fixture exercises receiving, recipe mapping, completed sales, and count anchors through the API and asserts the full variance response.

Opening inventory quantities and receipts are valued only for the selected operational period using the period weighted-average policy above. No cost is invented for opening stock and no claim is made that this is accounting-grade COGS. If accounting COGS becomes a requirement, make a separate decision about inventory valuation (FIFO or moving average), count valuation, transfers, and shrinkage treatment.

## API and UI

- `POST /v1/inventory/food-cost-variance` accepts `{ location_id, opening_count_session_ids, closing_count_session_ids }` and returns a location-scoped report with derived period, currency, actual/theoretical/variance totals (nullable), per-item quantity and cost components, coverage, freshness, valuation policy, recipe baseline, and limitations.
- Manager, owner, and admin roles may read the report at locations they manage. Employee access is denied. Tenant and location scope come from the session, never request payload fields.
- The `/app/inventory/food-cost` screen selects submitted opening and closing counts for each active storage area; the period is derived from those anchors. It shows coverage, freshness, a per-ingredient breakdown, and exclusions. Incomplete data is prominent; the screen never renders a partial amount as a complete total.
- Sous may read this report only after a typed, scoped tool exists and has an eval set. It must repeat the report's limitations and never estimate missing ingredients or costs.

## Verification and evals

- Formula tests: receipt weighted average, quantity conversion, fractional recipe yields, half-up display rounding, no receipts, zero theoretical cost, and negative depletion.
- Count coverage tests: complete matching snapshots, missing storage area/item, wrong location/org, open count, stale count, moved item, inconsistent business-date anchors, and reversed periods.
- Recipe/sales tests: recipe edits and menu remapping across effective dates, backfilled lines before baseline, unmapped menu lines, inactive/missing ingredients, multiple variants for one menu item, and sales returns without item allocation.
- Freshness tests: complete and partial sync, stale/failing orders, and stream cutoff before period end. Missing inputs never become zero.
- Authorization/RLS tests: owner/admin/manager location scope, inaccessible location, employee denial, and cross-org rows.
- Golden fixture/eval: a complete-period API fixture covers receipts, completed sales, recipe mapping, and opening/closing count anchors with hand-calculated actual/theoretical/variance. Pure formula and partial-period tests cover additional edge cases.
- Property invariant: with no missing events, `opening + receipts - closing` equals summed actual depletion across items; waste is a subset and is not double-counted.

## Milestones

1. **COM-6A: Semantics and versioned inputs.** Implemented: immutable recipe versions, effective-dated menu links, and org baseline. No report for data before the baseline.
2. **COM-6B: Read model/service.** Implemented: deterministic calculations, count coverage, receipt weighted-average cost, theoretical usage, freshness, and exclusions.
3. **COM-6C: API and tests.** Backend endpoint, pure formula tests, partial- and complete-period API tests, and authorization/RLS checks implemented.
4. **COM-6D: UI and Sous integration.** Implemented manager report at `/app/inventory/food-cost`. Sous read access remains a separately gated follow-up until its authorization, grounding, and limitation evals pass.
