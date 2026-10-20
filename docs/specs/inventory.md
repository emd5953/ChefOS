# Inventory Core (Phase 4)

**Status:** In progress · **Owner:** TBD · **Last updated:** 2026-10-20
**Related:** [PRD](../prd.md), [Roadmap](../roadmap.md), [Architecture](../architecture.md), [Platform foundation](platform-foundation.md), [Integration layer](integration-layer.md)

## Problem

Restaurants need a reliable view of what is on hand, what to order, and what each menu item costs to make. The prototype has inventory, purchase-order, and recipe screens, but its seeded quantities and costs are UX examples only. Production inventory needs normalized units, location-scoped stock, auditable count and receiving events, and links to the canonical POS menu items already owned by Commerce.

Phase 4 establishes that operational source of truth. Counts and receiving must work on a phone in the kitchen, while managers can maintain items, pars, suppliers, orders, and recipes from the web app. Costing is useful only when units and mappings are explicit.

## Goals

- Items have a base unit, purchase packs, safe conversions, a supplier, and location-specific par levels.
- Count sessions are organized by storage area. A submitted count becomes the on-hand truth snapshot and records material exceptions for review.
- Purchase orders can be placed and received in full or in part, with each receipt updating stock and the item's last-received unit cost.
- Recipes use inventory items and explicit quantities; recipes link to canonical POS menu items through the existing `menu_items.recipe_id`. Mapped menu items show plate cost.
- Count and receive workflows are usable on a phone as mobile-first responsive web routes, including loading, empty, and error states.
- Every mutation is tenant- and location-scoped and audited in the same transaction.

## Non-goals

- INV-5 waste and yield logging, INV-6 inter-location transfers, INV-7 invoice capture and AP reconciliation, and INV-8 staff 86 broadcasts; these remain Phase 6 work.
- Theoretical usage from POS sales, depletion projections, or actual-vs-theoretical food cost (COM-6); recipes and plate cost are the foundation, but usage accounting is later work.
- Supplier integrations, automatic ordering, invoice matching, multi-currency purchasing, and POS writeback.
- Lot tracking, expiry, nutritional data, recipe sub-recipes, and production/prep batches.
- Inventory adjustments outside counts and receiving in the first release.

## Approach

Inventory is a module in the existing FastAPI modular monolith. Its tables are authoritative for stock and purchasing; Commerce retains ownership of `menu_items` and `menu_item_variants`. All quantities are stored as decimal quantities in an item's base unit: grams (`g`), milliliters (`ml`), or each (`ea`). A conversion may only relate units in the same dimension, using an exact positive factor. Purchase packs describe how many base units a purchased pack contains; they do not create new dimensions or implicit conversions.

On-hand is a current balance updated transactionally by receipts and submitted count snapshots. A count is truth at its submission point: it replaces the prior balance for every item counted in that area. Each count and receipt also creates an immutable stock movement with a location-derived `business_date`, so the balance can be explained. This is not a perpetual theoretical depletion ledger; sales-based usage is deferred to COM-6.

**Alternative considered: store stock in the supplier's purchase unit.** Rejected. A kitchen counts and recipes use different measures (for example, count pounds, receive a case, recipe in ounces). Storing one arbitrary unit makes comparisons and costing ambiguous. A dimensioned base unit plus explicit pack size provides one quantity for stock, count, and recipe math.

## Design

### Data model

The migration number is the next number after the current head (currently `0004`; Phase 3 is expected to take `0005`). All listed tables carry `org_id`; location-scoped records also carry `location_id`. UUIDv7 IDs, `timestamptz`, `created_at`/`updated_at`, explicit org filters, and RLS follow platform conventions. Every org-scoped reference uses a composite foreign key `(org_id, referenced_id)` to a matching unique key, including location, supplier, item, storage area, pack, parent, and Commerce menu references. Location-scoped service operations also verify the caller has access to that location.

| Table | Key columns |
| --- | --- |
| `inventory_items` | `id, org_id, name, category, base_unit, dimension, active, default_supplier_id, currency` |
| `item_unit_conversions` | `id, org_id, item_id, unit, base_units_per_unit` (positive numeric factor; unique item/unit) |
| `item_packs` | `id, org_id, item_id, supplier_id, name, purchase_unit, base_quantity, last_price_minor, active` |
| `storage_areas` | `id, org_id, location_id, name, sort_order, active` |
| `item_storage_assignments` | `org_id, location_id, storage_area_id, item_id` (unique location/item) |
| `inventory_pars` | `org_id, location_id, item_id, par_base_quantity` (unique location/item) |
| `inventory_levels` | `org_id, location_id, item_id, on_hand_base_quantity, version, updated_at` (unique location/item; increment version on every balance change) |
| `count_sessions` | `id, org_id, location_id, storage_area_id, status, started_by, submitted_by, started_at, submitted_at, note` |
| `count_lines` | `id, org_id, location_id, session_id, item_id, expected_base_quantity, expected_level_version, counted_base_quantity, entered_quantity, entered_unit, exception_state, note` |
| `suppliers` | `id, org_id, name, contact fields, active` |
| `purchase_orders` | `id, org_id, location_id, supplier_id, status, ordered_at, expected_at, created_by, currency` |
| `purchase_order_lines` | `id, org_id, location_id, purchase_order_id, item_id, pack_id, ordered_packs, ordered_pack_name, ordered_base_quantity_per_pack, ordered_unit_price_minor, ordered_quantity, received_quantity, closed_quantity` |
| `receipts` / `receipt_lines` | receipt has `id, org_id, location_id, purchase_order_id, received_by, received_at, business_date`; line has item, `received_base_quantity`, `total_cost_minor` |
| `recipes` / `recipe_ingredients` | `id, org_id, name, yield_quantity, yield_unit, active`; ingredient item and base quantity per recipe yield |

Use the existing nullable `menu_items.recipe_id` as the sole recipe link. Commerce owns the column and canonical menu item; Inventory owns recipe records and validates that the referenced recipe belongs to the same org. Do not add a link table or variant-specific link in Phase 4. A menu item has at most one recipe; multiple menu items may reference one recipe. Menu price remains Commerce-owned.

`base_unit` is constrained by dimension: mass uses `g`, volume uses `ml`, and count uses `ea`. Item-level conversions include stable units such as kg→1000 g and L→1000 ml; supplier cases belong in `item_packs.base_quantity`, not general conversions. Factors must be positive and finite. API and UI reject cross-dimension conversions. Count and recipe units require explicit configuration. Count and recipe quantities may be fractional where meaningful. Quantities use `numeric`, never floating point.

Money is integer minor units plus ISO currency. Each org has one supported currency, matching its Commerce configuration; item, pack, PO, and receipt writes with another currency are rejected with 422. A receipt line stores authoritative `total_cost_minor` for `received_base_quantity`. The derived last-received cost is `total_cost_minor / received_base_quantity` as `numeric(18,6)` minor currency units per base unit; round half-up to an integer minor unit only for display. `inventory_items.last_received_cost_minor` is removed; derive it from the latest posted receipt line. Opening counts set quantity but do not invent cost. Plate cost is the sum of each ingredient's recipe base quantity multiplied by that item's current last-received cost per base unit, normalized to the recipe yield; it is displayed in minor units with a cost-as-of timestamp and an incomplete-cost state where any ingredient has no received cost. It is not divided by menu price to imply margin when the POS price is missing.

### Tenancy and authorization

The service passes `org_id` and allowed `location_id` explicitly to every query. PostgreSQL RLS uses `org_id = app_org_id()` for every inventory table, including org-level suppliers/items; service authorization checks location access on every location-scoped operation. Add every new org table to the structural RLS inventory and policy checks. Every mutation calls `audit.record` in the same transaction. Audited actions are item create/update/archive; conversion and pack create/update/archive; storage-area and supplier create/update/archive; item-area assignment and par changes; count start, line save, variance override, submit; PO create/update/close/reopen; receipt post; recipe and ingredient create/update/archive; and menu recipe link changes. Submitted sessions, posted receipt lines, and their snapshots are immutable; corrections use a new count or receipt.

### API

Routes use `/v1/inventory`; request schemas omit tenant and actor IDs, which come from auth context. Role rules: employees may save count lines and receive against an open PO only at a location they can access. Every other route, including starting/submitting counts, variance overrides, PO creation/edits/close/reopen, and all catalog, supplier, area, par, conversion, pack, and recipe changes, requires manager, admin, or owner plus access to the target location when location-scoped. No assignment model is introduced.

```text
GET    /v1/inventory/items?location_id=UUID → ItemList
POST   /v1/inventory/storage-areas(body: StorageAreaCreate) → StorageAreaOut
GET/PATCH /v1/inventory/storage-areas[/{area_id}] → StorageAreaList | StorageAreaOut
POST   /v1/inventory/suppliers(body: SupplierCreate) → SupplierOut
GET/PATCH /v1/inventory/suppliers[/{supplier_id}] → SupplierList | SupplierOut
POST   /v1/inventory/items(body: ItemCreate) → ItemOut
PATCH  /v1/inventory/items/{item_id}(body: ItemUpdate) → ItemOut
PUT    /v1/inventory/items/{item_id}/par(location_id: UUID, body: ParUpdate) → ParOut
GET    /v1/inventory/count-sessions?location_id=UUID&status=... → CountSessionList
POST   /v1/inventory/count-sessions(body: CountSessionCreate) → CountSessionOut
PUT    /v1/inventory/count-sessions/{session_id}/lines/{item_id}(body: CountEntry) → CountLineOut
POST   /v1/inventory/count-sessions/{session_id}/submit(body: CountSubmit) → CountSessionOut
GET    /v1/inventory/purchase-orders?location_id=UUID → PurchaseOrderList
POST   /v1/inventory/purchase-orders(body: PurchaseOrderCreate) → PurchaseOrderOut
PATCH  /v1/inventory/purchase-orders/{po_id}(body: PurchaseOrderUpdate) → PurchaseOrderOut
POST   /v1/inventory/purchase-orders/{po_id}/close(body: {reason}) → PurchaseOrderOut
POST   /v1/inventory/purchase-orders/{po_id}/reopen(body: {reason}) → PurchaseOrderOut
POST   /v1/inventory/purchase-orders/{po_id}/receive(body: ReceiveRequest) → ReceiptOut
GET/POST/PATCH /v1/inventory/recipes[/{recipe_id}](body: RecipeCreate | RecipeUpdate) → RecipeOut
GET    /v1/inventory/recipes/menu-links?location_id=UUID → MenuRecipeCostList
PUT    /v1/commerce/menu-items/{menu_item_id}/recipe(body: {recipe_id}) → MenuRecipeCostList
```

Signatures are illustrative; responses include IDs, base quantities, status, and relevant timestamps. Count submit includes the level versions captured when the session began; in one transaction it locks affected levels, compares all versions, and rejects the whole submission with 409 if any changed. A successful submit increments each affected level version. Inventory movement and receipt events carry `business_date`, derived from the location timezone and configured business-day start using the Commerce business-date rule. A stale count must be refreshed and recounted; it is never force-applied. Validation errors use problem+json; inaccessible org/location resources return 404. The web and phone clients call the same endpoints.

### Web and mobile

Web screens under `/app/inventory`: stock list with on-hand, par, below-par state, and cost; item/pack/conversion editor; storage-area setup; count-session history and exception review; supplier and PO list/detail; recipe editor with ingredient quantities, menu links, plate cost, and missing-cost/mapping states. Follow the current app shell, location scope, loading skeleton, empty state, and retryable error conventions. Pages read `?loc=` and pass its value as API `location_id`.

The mobile-first count flow opens a location and storage area, shows searchable items, pre-fills expected quantity as context, accepts quantity and unit, and saves each line. Receiving opens an outstanding PO, accepts received pack/quantity and line cost, supports partial delivery, and posts after review. Use large touch targets, a numeric keypad, clear saved state, and retry after reconnect. Offline queueing is deferred. MOB-3 is delivered as mobile-first responsive web routes in `apps/web`; lines save online and server stock changes only on successful submission/posting.

## Behavior

### Happy path: count, order, receive, and cost

1. A manager creates an item with a base unit, conversion(s), supplier pack, and location par. The system validates that all units share a dimension.
2. The manager starts a count session for a storage area. Its lines capture the current on-hand snapshot as expected quantities. An employee or manager with location access enters quantities in a supported unit; the API converts to base quantity and saves drafts against the captured level version.
3. Submission rejects with 409 if any affected level version is stale. Otherwise it flags differences beyond the exception threshold, replaces on-hand for counted items, increments level versions, records movements and audit events atomically, and closes the session.
4. The manager creates a PO in supplier packs. Receiving records what arrived and actual line cost. Posting creates a receipt, increases on-hand by received base quantity, derives last-received unit cost, and updates per-line ordered, received, and closed quantities.
5. The chef builds a recipe from item quantities, chooses yield, links it through `menu_items.recipe_id`, and sees plate cost.

### Error and edge cases

| Case | Result |
| --- | --- |
| Conversion unit belongs to another dimension, is missing, non-positive, or would produce an invalid base quantity | 422; no line, stock, or cost mutation |
| Count entry uses a unit not configured for that item | 422 with the accepted units; existing draft remains unchanged |
| Menu item has no recipe link | Cost view says “No recipe linked”; unmapped POS items are listed as uncosted and are not silently matched by name |
| Recipe would contain itself, a duplicate ingredient, or a cycle through future recipe references | 422; Phase 4 only supports direct inventory ingredients, and server validation still rejects a cycle if sub-recipes are introduced |
| Receipt quantity exceeds remaining ordered quantity | 409 by default; manager can explicitly mark an overage before posting, which is audited |
| PO is received in multiple deliveries | Each line tracks ordered, cumulative received, and closed quantity in base units. Remaining receivable is ordered minus received minus closed. Overage returns 409 unless a manager explicitly accepts it with a reason. Short close moves the remainder into closed quantity with a reason; manager reopen reverses that amount and is audited before further receiving. PO is `partially_received` while receivable quantity remains and `closed` when every line is fully received or closed. |
| Duplicate network retry submits the same receipt | Idempotency key returns the original receipt; stock is not incremented twice |
| Count variance exceeds threshold | Line is marked `review_required` and session cannot submit until a manager confirms or edits it with a note; threshold is configurable per org, with default greater of 20% or 1 base unit |
| Network drops during phone entry | Only server-saved lines are retained; show a retryable error and keep submission disabled until the line is saved. Offline queueing is deferred. |
| Item is archived | Items are archive-only and never hard-deleted. Archived items cannot be added to new counts, POs, or recipes, but remain readable in recipes, posted history, assignments, and receipts. Existing drafts and open PO lines may finish using their saved item/pack snapshots. Posted count and receipt records preserve item name, unit, and quantity snapshots; renames affect new drafts only. |

## Verification

1. **Unit and costing:** conversion round trips and precision; dimension mismatch rejection; pack-to-base receiving; last receipt changes cost; rounding and missing-cost behavior; plate cost with multiple ingredients and yield.
2. **Inventory behavior:** count snapshot replaces only items in the submitted storage area; receipts increment once; partial/short-closed PO transitions; concurrent submissions cannot double-apply; idempotency retries return the same receipt.
3. **RLS tests**, connecting as `chefos_app`: every new inventory table is RLS-protected; no scope sees no rows; org A cannot read or mutate org B; location access is checked by service even inside the same org. Structural RLS inventory includes the new tables.
4. **Authorization and audit:** role matrix for item/recipe/PO administration and count/receive; every mutation writes an audit event in the same transaction; posted records reject in-place edits.
5. **API and UI:** problem+json validation, inaccessible IDs return 404, unmapped menu items have explicit empty state, and count/receive pages cover loading, empty, error, and online retry states.
6. **Manual:** on a phone-sized viewport, complete a storage-area count, resolve an exception, receive a partial PO, and view plate cost for a mapped POS item.

## Risks / open questions

- Restaurants may count by weight while recipes use volume. Cross-dimension conversions require item-specific density and are deliberately excluded; clarify this limitation in the item editor.
- Last-received cost is simple and explainable but can move sharply with one unusual delivery. Weighted average or FIFO costing would require a separate accounting decision.
- The exception threshold default (greater of 20% or one base unit) is a starting point; kitchens may need item-specific tolerances and an org setting.

## Milestones

Each milestone leaves `main` usable and green in CI.

1. **INV-1: Catalog and stock foundation.** Next migration after current head, RLS, items, packs, conversions, suppliers, storage areas, pars, and stock view. Exit: manager can configure an item and par; invalid or mismatched references fail safely.
2. **INV-2: Counts.** Versioned levels, draft sessions, employee line entry, manager submit/review, exceptions, movements, and audit. Exit: stale submit returns 409; a fresh area count updates on-hand once.
3. **INV-3: Purchasing and receiving.** Snapshotted PO lines, ordered/received/closed quantities, partial receipts, explicit overage and reopen transitions, cost snapshots, and supplier/PO screens. Exit: manager can place, partially receive, close/reopen, and complete an order without double-counting.
4. **INV-4: Recipes and plate cost.** Recipe editor, ingredient normalization, existing `menu_items.recipe_id` mapping, cost state, and plate-cost display. Exit: mapped menu item shows reproducible plate cost; unmapped or cost-incomplete items are explicit.
5. **MOB-3: Mobile-first web workflows.** Responsive count and receiving routes in `apps/web`, with location scope mapping, touch-friendly entry, loading/empty/error states, and online retry. Exit: complete a count and receive flow at phone viewport; CI remains green.

### Implementation notes

- INV-1 is in progress. Revision `0009` adds the initial catalog, units, packs, suppliers, storage areas, pars, levels, and org RLS policies. The initial API supports item creation/editing, supplier and storage-area creation/listing, pack creation, item-area assignment, par updates, and a stock/par view. Item and pack archive/edit flows and storage-area/supplier updates remain before INV-1 exits. The web stock list is present; catalog editor screens remain outstanding.
- Subsequent inventory revisions continue from `0009` in this checkout. At merge, the migration owner must re-chain `0009` after the final Phase 3 head as described above.
