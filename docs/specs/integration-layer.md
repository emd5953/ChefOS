# Integration Layer

**Status:** Proposed · **Owner:** TBD · **Last updated:** 2026-10-19
**Related:** [ADR 0001: Integration-first](../adr/0001-integration-first.md), [Architecture](../architecture.md), [PRD](../prd.md)

## Problem

The platform sits alongside a restaurant's existing POS. It does not replace it. Sales, menu items, staff, and hours already live in other systems, and Commerce reporting, demand-based scheduling, labor %, and food-cost variance are only as good as the data brought in from those systems. Without connectors the product has no source of truth. The current prototype hides this: it uses seeded rows and a single daily-totals ingest key.

We need a layer that brings external data in reliably, maps it onto our domain model, and tells every consumer (UI, reports, Sous) how fresh and complete that data is.

## Goals

- Connect a Square sandbox seller account via OAuth and map Square locations to platform locations.
- Import catalog items, orders (including refunds and returns), team members, and timecards. Keep them current within minutes.
- Backfill historical data on connect (default 13 months, so year-over-year comparisons work).
- Make every import idempotent. Replays, duplicate webhooks, and overlapping pulls must converge on the same state.
- Keep the raw source payload for every record so mappings can be re-run and disputes traced.
- Expose per-connection, per-stream freshness and sync health to the UI and to Sous.
- Provide a push API for systems we don't have a connector for. It reuses the same pipeline.
- Define connector interfaces so the second POS is mostly mapping code, not pipeline code.

## Non-goals (v1)

- Writing back to the POS (menu changes, 86 sync, price updates). Read-only scopes only.
- Payroll, accounting, supplier/invoice, delivery, reservation, and communications connectors. The interfaces must accommodate them, but none are built.
- Production Square accounts, Square app review, or any cloud deployment ([ADR 0002](../adr/0002-production-stack.md)).
- Automatic POS item → recipe mapping. v1 provides a manual mapping queue.
- Real-time (sub-minute) dashboards.
- Multi-currency organizations.
- Porting or extending the stdlib prototype (`server.py`). See [ADR 0002](../adr/0002-production-stack.md).
- A connector marketplace or self-serve connector SDK.

## Approach

**Pull is the source of truth. Webhooks only trigger pulls.** Each connection runs a scheduled incremental pull per stream from a stored watermark. Square webhooks don't carry authoritative state. They enqueue a targeted fetch so changes land within about a minute. If webhooks are lost or arrive out of order, the next scheduled pull still converges.

Every record flows through one pipeline:

```
fetch → land raw → normalize → upsert canonical → rebuild read models → emit domain events
```

Connectors own only `fetch` and `normalize`. Landing, idempotency, identity mapping, business-date assignment, read models, and freshness are shared. The push API enters the same pipeline at `land raw`.

**Alternative considered: webhook-driven ingestion** (treat webhook payloads as the change feed). Rejected. Square webhooks are at-least-once and unordered, and not every relevant change emits one. A webhook-first design needs a reconciliation pull anyway, so the pull becomes primary and webhooks become a latency optimization.

**Alternative considered: a third-party unified POS API** (an aggregator that normalizes many POS systems). Deferred. It speeds up connector count but adds per-location cost, hides source payloads, and caps us at the aggregator's lowest-common-denominator schema. Revisit when connector breadth matters more than data depth.

## Design

### Runtime

| Concern | Choice |
| --- | --- |
| API | FastAPI service, `integrations` module in the modular monolith |
| Workflows | Temporal (Python SDK) workers on the local dev server: one workflow type per job kind |
| Storage | PostgreSQL. Raw payloads in `jsonb`; payloads over 256 KB go to object storage (RustFS locally) with a pointer |
| Secrets | OAuth tokens envelope-encrypted through `KeyProvider` (local AES-GCM key from `.env`); DB stores ciphertext only |
| Square | Developer sandbox only (free). Official `squareup` Python SDK, `Square-Version` pinned in config |

**Square modes.** `SQUARE_MODE=fake` (default) swaps the HTTP client for `FakeSquareClient`. It's a deterministic simulated seller ("Juniper House", Brooklyn, full-service) that returns payloads in Square's v2 shapes: about 13 months of history, new orders appearing as wall-clock time passes, late refunds, an unmapped "open food" item, and an unlinked team member. Everything downstream of the client (landing, normalization, read models, freshness) is the real pipeline. The demo works with zero credentials, and the same normalizer tests cover both modes. `SQUARE_MODE=sandbox` uses `HttpSquareClient` against `connect.squareupsandbox.com` with the developer's free sandbox app credentials.

Everything runs locally per [ADR 0002](../adr/0002-production-stack.md). OAuth redirect and webhook delivery need a reachable URL. The redirect works on `localhost`. For webhooks, use a free tunnel (e.g. `cloudflared` quick tunnel) during demos. Scheduled pulls still converge without it.

### Data model (PostgreSQL)

All tables carry `org_id` and are covered by row-level security in addition to service-layer scoping. Money is `bigint` minor units plus a `currency` column.

**Connection state**

```
integration_connections(id, org_id, provider, status, external_account_id,
  scopes text[], credential_ciphertext, credential_expires_at, api_version,
  created_by, created_at, disconnected_at)
  status ∈ {pending, active, needs_reauth, error, disconnected}

integration_location_links(id, connection_id, external_location_id, location_id,
  timezone, business_day_start time default '04:00', active)
  unique(connection_id, external_location_id)

integration_streams(connection_id, stream, watermark timestamptz, page_cursor text,
  last_attempt_at, last_success_at, last_error_class, last_error_detail,
  backfill_completed_through date)
  pk(connection_id, stream)
  stream ∈ {locations, catalog, orders, team_members, timecards}

sync_runs(id, connection_id, stream, kind, status, window_from, window_to,
  started_at, finished_at, fetched_count, changed_count, error_class, error_detail)
  kind ∈ {incremental, backfill, webhook, push, renormalize}
```

**Landing and identity**

```
raw_records(id, org_id, connection_id, stream, external_id, source_updated_at,
  source_version, payload_sha256, payload jsonb, payload_uri, fetched_at,
  sync_run_id, normalize_status, normalizer_version, normalize_error)
  unique(connection_id, stream, external_id, payload_sha256)
  normalize_status ∈ {pending, ok, quarantined}

webhook_events(provider, event_id, connection_id, event_type, received_at, processed_at)
  pk(provider, event_id)

external_refs(org_id, connection_id, entity_type, external_id, entity_id)
  unique(connection_id, entity_type, external_id)

mapping_issues(id, org_id, connection_id, kind, external_id, label, first_seen_at,
  last_seen_at, occurrences, resolved_at, resolved_by)
  kind ∈ {unmapped_location, unmapped_item, unmapped_team_member, unknown_channel}
```

**Canonical entities** (owned by domain modules, written by the integration layer)

```
menu_items(id, org_id, name, category, active, recipe_id null)
menu_item_variants(id, menu_item_id, name, price, currency)
sales_orders(id, org_id, location_id, business_date, opened_at, closed_at, state,
  channel, gross_sales, discounts, returns, net_sales, tax, tips, service_charges,
  currency, source, source_version)
sales_order_lines(id, order_id, menu_item_variant_id null, external_item_ref, name,
  quantity numeric, gross, discounts, net)
timecards(id, org_id, location_id, employee_id, business_date, clock_in, clock_out,
  break_minutes, hourly_rate, currency, source, source_version)
```

`employees` already exists in the domain model. Square team members link to it through `external_refs`.

**Read models** (rebuilt per affected location × business date)

```
daily_sales(org_id, location_id, business_date, channel, orders, net_sales,
  gross_sales, discounts, returns, tax, tips, currency, computed_at, data_as_of)
daily_labor(org_id, location_id, business_date, scheduled_hours, actual_hours,
  actual_cost, currency, computed_at, data_as_of)
```

### Metric definitions (v1)

- **Net sales** = item gross − item discounts − returns. Tax, tips, and service charges are excluded and reported separately. Gift card sales are excluded.
- **Business date** = local date of `closed_at` (orders) or `clock_in` (timecards), shifted by the location's `business_day_start`. With the default 04:00, an order closed at 01:30 local belongs to the previous date.
- **Returns** reduce net sales on the business date the return was *processed*, not the date of the original order. This matches how operators reconcile against the drawer.
- **Channel** is derived from Square order source and fulfillment type (`dine_in`, `pickup`, `delivery`, `online`, `other`). Unrecognized combinations become `other` and raise an `unknown_channel` mapping issue.

### Connector interface

```python
class Connector(Protocol):
    provider: str
    streams: tuple[str, ...]

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> Credentials: ...
    def refresh(self, creds: Credentials) -> Credentials: ...
    def revoke(self, creds: Credentials) -> None: ...

    def fetch_page(self, creds: Credentials, stream: str, window: Window,
                   cursor: str | None, location_ids: list[str]) -> Page: ...
    def fetch_by_ids(self, creds: Credentials, stream: str, ids: list[str]) -> list[RawItem]: ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes, url: str) -> bool: ...
    def parse_webhook(self, body: bytes) -> WebhookEvent: ...

class Normalizer(Protocol):
    provider: str
    version: str   # bump to trigger re-normalization of landed raw records

    def normalize(self, stream: str, raw: RawItem, ctx: MappingContext) -> list[CanonicalRecord]: ...

@dataclass
class Page:
    items: list[RawItem]
    next_cursor: str | None
    max_updated_at: datetime | None

@dataclass
class RawItem:
    external_id: str
    updated_at: datetime | None
    version: str | None
    payload: dict
```

Normalizers are pure functions of `(raw, ctx)`. They do no I/O, so they can be re-run over stored raw records when mappings or metric definitions change.

### Square stream mapping

| Stream | Square API | Incremental strategy | Scope |
| --- | --- | --- | --- |
| locations | Locations `ListLocations` | Full refresh hourly (small) | `MERCHANT_PROFILE_READ` |
| catalog | Catalog `SearchCatalogObjects` with `begin_time`, `include_deleted_objects` | Watermark on catalog `updated_at` | `ITEMS_READ` |
| orders | Orders `SearchOrders`, filter `updated_at`, sort `UPDATED_AT ASC`, ≤10 locations per call | Watermark with 10-min overlap | `ORDERS_READ` |
| team_members | Team `SearchTeamMembers` | Full refresh every 6h + webhooks | `EMPLOYEES_READ` |
| timecards | Labor API timecard search by workday range | Re-pull trailing 3 business days every run + webhooks | `TIMECARDS_READ` |

Webhook subscriptions: order created/updated, catalog version updated, team member created/updated, labor timecard created/updated/deleted. The Labor API renamed shifts to timecards in 2025. Pin `Square-Version` and confirm event names against the pinned version during M1.

### HTTP API

All routes are under `/v1`, authenticated by session, and limited to owner/admin for connection management.

```
POST   /v1/integrations/square/authorize            → { authorize_url }
GET    /v1/integrations/square/callback?code&state   → 302 to app; creates connection
GET    /v1/integrations/connections                  → [Connection + stream health]
GET    /v1/integrations/connections/{id}             → Connection + streams + recent runs
PATCH  /v1/integrations/connections/{id}/locations   ← [{ external_location_id, location_id, business_day_start }]
POST   /v1/integrations/connections/{id}/sync        ← { streams? }               → 202 { run_ids }
POST   /v1/integrations/connections/{id}/backfill    ← { from, to, streams? }     → 202 { workflow_id }
DELETE /v1/integrations/connections/{id}             → revokes token; status=disconnected; data retained
GET    /v1/integrations/mapping-issues?status=open
POST   /v1/integrations/mapping-issues/{id}/resolve  ← { entity_id | ignore: true }

POST   /v1/webhooks/square                           (unauthenticated; HMAC-verified)

POST   /v1/ingest/orders                             (API key with ingest:orders; Idempotency-Key header)
POST   /v1/ingest/daily-sales                        (API key with ingest:daily_sales; for totals-only systems)
```

Freshness is part of every read response that depends on imported data:

```
DataFreshness { stream, location_id, data_as_of: datetime, status: fresh|stale|failing|not_connected }
```

### Temporal workflows

| Workflow | Trigger | Behavior |
| --- | --- | --- |
| `ConnectionSync(connection_id)` | Schedule, every 5 min per connection | For each due stream: page through `fetch_page` from watermark, land, normalize, upsert, advance watermark only after commit |
| `Backfill(connection_id, from, to, streams)` | On connect; manual | Day-sized child activities, newest first, rate-limited; updates `backfill_completed_through` |
| `WebhookFetch(connection_id, stream, ids)` | Webhook receipt | `fetch_by_ids` → same land/normalize path |
| `TokenRefresh(connection_id)` | Daily schedule | Refresh when expiry < 7 days; on failure set `needs_reauth` |
| `Renormalize(connection_id, stream, normalizer_version)` | Deploy of new normalizer version | Re-run normalize over landed raw records; rebuild affected read models |

One `ConnectionSync` runs per connection at a time (workflow ID = connection ID). Activities are idempotent, so Temporal retries are safe.

### File layout (production repo)

```
app/integrations/
  api.py              routes above
  pipeline.py         land → normalize → upsert → rebuild; idempotency
  freshness.py        DataFreshness computation
  identity.py         external_refs + mapping_issues
  business_date.py
  secrets.py          envelope encrypt/decrypt via KeyProvider
  push.py             /v1/ingest/* → pipeline
  providers/square/
    connector.py
    normalizer.py
    webhooks.py
    fixtures/         recorded sandbox payloads for tests
app/workflows/integrations.py   Temporal workflows + activities
migrations/                     schema above
```

### Dependencies

`squareup` (official SDK), `temporalio`, `boto3` (S3 API against RustFS), `cryptography` (AES-GCM). All free. No unified-API vendor.

## Behavior

### Happy path: connect Square

1. Owner clicks **Connect Square**. API creates a `pending` connection, signs `state` (connection ID + CSRF nonce, 10-min expiry), and returns the authorize URL.
2. Square redirects to the callback. API verifies `state`, exchanges the code, encrypts tokens, records merchant ID and granted scopes, and fetches locations.
3. Square locations matching an existing platform location by name and address are pre-linked. Others are shown for the owner to link, create, or ignore.
4. On save, the connection becomes `active`, webhooks begin processing, `ConnectionSync` is scheduled, and `Backfill` starts for 13 months (newest first, so recent days are usable within minutes).
5. The Integrations page shows each stream's status, `data_as_of`, backfill progress, and open mapping issues.

### Happy path: order lifecycle

1. A check closes in Square. An `order.updated` webhook arrives. We verify the HMAC, insert into `webhook_events` (duplicates are no-ops), return 200, and start `WebhookFetch`.
2. The order is fetched, landed (new hash → new raw row), and normalized into `sales_orders` and `sales_order_lines`. Business date comes from the location link.
3. `daily_sales` for that location and business date is rebuilt. A `sales.order_recorded` domain event is emitted for downstream consumers (forecasting, Sous caches).
4. A refund later issues a new order version. Same path. The return lands on the processing business date.

### Edge cases and errors

| Case | Behavior |
| --- | --- |
| Same webhook delivered twice | `webhook_events` PK conflict → ignored |
| Same record fetched by webhook and scheduled pull | Identical `payload_sha256` → no new raw row, no downstream work |
| Out-of-order versions | Upsert only if `source_version`/`source_updated_at` is newer than stored; older payloads are landed but not applied |
| Watermark crash mid-page | Watermark advances only after the page commits; overlap window plus idempotent landing make re-reads harmless |
| Square 429 / 5xx / timeout | Temporal activity retry, exponential backoff with jitter, max 10 min; stream marked `failing` after 3 consecutive failed runs |
| Square 401 / token revoked | Connection → `needs_reauth`; scheduled syncs pause; owner notified; data retained |
| Merchant revokes app in Square | Same as 401 when next detected; OAuth revocation webhook handled where available |
| Order for unlinked Square location | Raw record landed; normalization deferred; `unmapped_location` issue raised; processed when linked |
| Line item for unknown catalog item | Line stored with `external_item_ref`, `menu_item_variant_id` null; `unmapped_item` issue; still counts toward sales totals |
| Timecard for unlinked team member | Stored against a placeholder; excluded from per-employee views, included in location labor totals; `unmapped_team_member` issue |
| Normalizer throws on a payload | Raw record `quarantined` with error; other records continue; alert if quarantine rate > 0.1% of a run |
| Location timezone or day-start changed | Affected business dates recomputed via `Renormalize` for that location |
| Backfill hits rate limits | Backfill yields to incremental sync (separate task queue, lower concurrency) |
| Disconnect | Token revoked at Square, status `disconnected`, schedules removed. Historical data stays and is labeled with its source |
| Push API retry with same `Idempotency-Key` | Returns the original response; no duplicate |
| Push and connector both send the same location's sales | Rejected at configuration: a location's sales stream has exactly one source |

### Freshness rules

- `fresh`: last successful run within 2× the stream's schedule interval.
- `stale`: older than that, with no failing runs (for example, a paused worker).
- `failing`: 3+ consecutive failed runs, or connection in `needs_reauth`/`error`.
- Reports and Sous must show `data_as_of` and must not present `stale` or `failing` data as current.

## Verification

1. **Normalizer unit tests** over recorded Square sandbox fixtures: dine-in, takeout, delivery, split tender, discount, partial refund, full return, voided order, cross-midnight close, deleted catalog item.
2. **Idempotency property tests:** replaying any shuffled sequence of fixture pages and webhooks, with duplicates, yields identical canonical tables and read models.
3. **Business-date tests:** timezone and day-start boundaries, DST transitions, returns on a later date.
4. **Contract tests** against the Square sandbox in CI (nightly, not per-PR): OAuth round trip, each stream's fetch, webhook signature verification with a real signature key.
5. **End-to-end sandbox scenario:** create orders, a refund, and a timecard in a sandbox seller account. Confirm `daily_sales`/`daily_labor` match Square's own reporting totals exactly for the test day.
6. **Failure injection:** 429 bursts, 500s, revoked token, worker killed mid-page. Each converges without duplicates and shows correct freshness status.
7. **Tenant isolation tests:** connection, webhook, and push requests can never write to another org's rows (RLS plus service checks).

## Risks / open questions

- **Square definitions vs ours.** Square's dashboard "net sales" may differ from ours (gift cards, service charges). M3 includes a reconciliation report against Square's totals; differences must be explained, not hidden.
- **Timecard search** has no `updated_at` filter in the versions reviewed, hence the trailing 3-day re-pull. If edits older than 3 days are common, extend the window or add a weekly 30-day sweep.
- **Raw payload retention:** payloads contain employee names and wages. Decided: 13 months (matching backfill depth), then purge payloads and keep hashes. A daily purge job ships in M7.
- **Sandbox data volume:** the Square sandbox starts empty. A seed script (part of M2) creates a realistic catalog, orders across channels, refunds, and timecards via the sandbox API so demos have data.
- **Second POS is Toast**, built against fixtures because partner API access needs approval. Validate the connector interface against Toast's API shape before M2 closes to avoid Square-shaped abstractions.

## Milestones

Each milestone ships behind a feature flag and leaves the system working.

1. **M1: Foundation.** Schema, `KeyProvider` secrets, connection model, Square OAuth connect/disconnect, location linking UI. Exit: an owner can connect a sandbox seller and link locations.
2. **M2: Catalog + orders pipeline.** Shared pipeline, raw landing, Square catalog and orders incremental sync, identity mapping, mapping-issue queue. Exit: sandbox orders appear as canonical `sales_orders` within 5 min, and replays are no-ops.
3. **M3: Sales read model + freshness.** Business-date logic, `daily_sales`, `DataFreshness` on Commerce endpoints, reconciliation report vs Square. Exit: Commerce daily view matches Square totals for sandbox test days.
4. **M4: Webhooks + backfill.** HMAC-verified webhook endpoint, `WebhookFetch`, `Backfill`. Exit: changes land in under 1 min; 13-month backfill completes without starving incremental sync.
5. **M5: Labor.** Team members → employees linking, timecards, `daily_labor`. Exit: labor % for a sandbox day matches hand calculation.
6. **M6: Push API.** `/v1/ingest/orders` and `/v1/ingest/daily-sales` on the same pipeline, scoped API keys, idempotency keys. Exit: a non-Square location can report sales with identical freshness semantics.
7. **M7: Operate.** Alerts (failing streams, quarantine rate, needs_reauth), runbook, sync-run history UI, `Renormalize` on normalizer version bump. Exit: on-call can diagnose a failing connection from the UI and logs alone.
