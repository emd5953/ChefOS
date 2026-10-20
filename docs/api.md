# API Reference (v1)

**Status:** Contract for the demo build · **Last updated:** 2026-10-20
**Related:** [Platform foundation](specs/platform-foundation.md), [Integration layer](specs/integration-layer.md), [Team](specs/team.md)

The browser reaches the API only through the Next.js origin: `http://localhost:3000/api/*` is rewritten to `http://localhost:8080/*`. All paths below are relative to the API root (e.g. `/v1/auth/me`; the browser calls `/api/v1/auth/me`).

## Conventions

- JSON in and out. Timestamps are ISO 8601 with offset (UTC). Dates are `YYYY-MM-DD`.
- **Money is integer minor units (cents).** Every money-bearing response includes `currency` (`"USD"`).
- **Percentages are numbers in percent units**, e.g. `28.4` means 28.4%. Null means "not computable" (e.g. zero sales).
- IDs are UUID strings.
- **Auth:** session cookie `chefos_session` (HttpOnly). Unsafe methods (POST/PATCH/PUT/DELETE) require header `X-CSRF-Token` with the token from `/v1/auth/login`, `/v1/auth/signup`, or `/v1/auth/me`.
- **Errors:** `application/problem+json` (RFC 9457):

```json
{ "type": "about:blank", "title": "Validation failed", "status": 422, "code": "validation_error",
  "detail": "…", "request_id": "…", "errors": { "email": "Already registered" } }
```

  `code` values: `validation_error`, `unauthenticated`, `session_expired`, `csrf_failed`, `forbidden`, `not_found`, `conflict`, `unavailable`, `internal_error`, `rate_limited`, `sous_unavailable`, `pos_owned_field`.
- Every response carries `X-Request-ID`.
- Lists are `{ "data": [...] }`.

## Types

```ts
type User         = { id: string; email: string; display_name: string }
type Org          = { id: string; name: string; timezone: string }
type Role         = "owner" | "admin" | "manager" | "employee"
type Location     = { id: string; name: string; address: string; timezone: string;
                      business_day_start: string /* "04:00" */; archived_at: string | null }

type FreshnessStatus = "fresh" | "stale" | "failing" | "not_connected"
type Freshness    = { stream: "orders" | "timecards" | "catalog" | "team_members";
                      location_id: string; data_as_of: string | null; status: FreshnessStatus }

type StreamHealth = { stream: "locations" | "catalog" | "orders" | "team_members" | "timecards";
                      status: "fresh" | "stale" | "failing" | "pending";
                      data_as_of: string | null; last_success_at: string | null;
                      last_error: string | null;
                      backfill_target_from: string | null;        // date
                      backfill_completed_through: string | null } // date; backfill runs newest → oldest, so this is the OLDEST date done

type LocationLink = { external_location_id: string; external_name: string; location_id: string | null }

type Connection   = { id: string; provider: "square"; mode: "fake" | "sandbox";
                      status: "pending" | "active" | "needs_reauth" | "error" | "disconnected";
                      external_account_id: string | null; created_at: string;
                      location_links: LocationLink[]; streams: StreamHealth[] }

type SyncRun      = { id: string; stream: string; kind: "incremental" | "backfill" | "webhook";
                      status: "running" | "succeeded" | "failed";
                      started_at: string; finished_at: string | null;
                      fetched_count: number; changed_count: number; error_detail: string | null }

type MappingIssue = { id: string; kind: "unmapped_location" | "unmapped_item" | "unmapped_team_member" | "unknown_channel";
                      external_id: string; label: string; occurrences: number;
                      first_seen_at: string; last_seen_at: string; resolved_at: string | null }

type Channel      = "dine_in" | "pickup" | "delivery" | "online" | "other"

type Totals       = { net_sales: number; gross_sales: number; discounts: number; returns: number;
                      tax: number; tips: number; orders: number; avg_check: number | null;
                      labor_cost: number; labor_hours: number; labor_pct: number | null }
```

## Health

| Method | Path | Response |
| --- | --- | --- |
| GET | `/healthz` | `200 { "status": "ok" }` |
| GET | `/readyz` | `200 { "status": "ok", "checks": { "db": "ok", "temporal": "ok" } }` or `503` with failing checks |

## Auth

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/auth/signup` | `{ email, password, display_name, org_name, location_name, timezone }` | `201 { user, org, location, role, csrf_token }` + cookie |
| POST | `/v1/auth/login` | `{ email, password }` | `200 { user, org, role, csrf_token }` + cookie |
| POST | `/v1/auth/logout` | none | `204`, cookie cleared |
| GET | `/v1/auth/me` | none | `200 { user, org, role, csrf_token }` or `401` |

Password minimum is 12 characters. `timezone` is an IANA name.

## Org and locations

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/org` | none | `200 Org` |
| PATCH | `/v1/org` | `{ name?, timezone? }` | `200 Org` (owner/admin) |
| GET | `/v1/locations` | none | `200 { data: Location[] }` (excludes archived unless `?include_archived=true`) |
| POST | `/v1/locations` | `{ name, address?, timezone, business_day_start? }` | `201 Location` (owner/admin) |
| PATCH | `/v1/locations/{id}` | partial `Location` fields | `200 Location` (owner/admin) |
| POST | `/v1/locations/{id}/archive` | none | `200 Location` (owner/admin) |

## Integrations

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/integrations/square/authorize` | none | `200 { authorize_url }`. The browser navigates to it |
| GET | `/v1/integrations/square/callback?code&state` | none | `302` to `{WEB_ORIGIN}/app/integrations?connected={id}`, or `?error={code}` |
| GET | `/v1/integrations/connections` | none | `200 { data: Connection[] }` (excludes `disconnected`) |
| GET | `/v1/integrations/connections/{id}` | none | `200 Connection & { recent_runs: SyncRun[] /* newest 20 */ }` |
| PATCH | `/v1/integrations/connections/{id}/locations` | `{ links: [{ external_location_id, location_id \| null }] }` | `200 Connection` |
| POST | `/v1/integrations/connections/{id}/sync` | `{ streams?: string[] }` | `202 { workflow_id }` |
| POST | `/v1/integrations/connections/{id}/backfill` | `{ from: date, to: date }` | `202 { workflow_id }` |
| DELETE | `/v1/integrations/connections/{id}` | none | `204` |
| GET | `/v1/integrations/mapping-issues?status=open\|resolved` | none | `200 { data: MappingIssue[] }` |
| GET | `/v1/integrations/mapping-issues/{id}/candidates` | none | `200 { data: [{ id, label }] }` (menu item variants or staff members; 422 for other kinds) |
| POST | `/v1/integrations/mapping-issues/{id}/resolve` | `{ entity_id }` or `{ ignore: true }` | `200 MappingIssue` (mapping backfills existing rows; 409 if already mapped) |
| POST | `/v1/webhooks/square` | Square webhook | `200`. Unauthenticated, HMAC-verified (sandbox mode only) |

**Square mode.** `SQUARE_MODE=fake` (default) uses a simulated Square sandbox. `authorize_url` points at the API's own `/v1/integrations/square/fake-consent?state=…`, which redirects straight to the callback, and data comes from a deterministic generator in Square's payload shapes. `SQUARE_MODE=sandbox` uses the real Square sandbox with `SQUARE_APP_ID`/`SQUARE_APP_SECRET`/`SQUARE_WEBHOOK_SIGNATURE_KEY`.

**Single-location auto-link.** If the org has exactly one location and Square reports exactly one, they're linked automatically on connect, and sync and backfill start immediately.

## Commerce

All endpoints take `location_id` (required). Date ranges are inclusive business dates.

### `GET /v1/commerce/daily?location_id&from&to`

```ts
{ location_id: string; currency: "USD";
  days: Array<Totals & { business_date: string; channels: Record<Channel, number> /* net_sales cents */ }>;
  freshness: Freshness[] }
```

Days with no data appear with zeros (the series is contiguous).

### `GET /v1/commerce/summary?location_id&from&to&compare=previous_period|previous_year`

```ts
{ currency: "USD";
  period: { from: string; to: string }; compare_period: { from: string; to: string };
  current: Totals; previous: Totals;
  change: { net_sales_pct: number | null; orders_pct: number | null;
            avg_check_pct: number | null; labor_pct_pts: number | null };
  channels: Array<{ channel: Channel; net_sales: number; share_pct: number }>;
  freshness: Freshness[] }
```

`previous_period` is the same length immediately before `from`. `previous_year` shifts by 364 days, so weekdays line up.

### `GET /v1/commerce/items?location_id&from&to&limit=20`

```ts
{ currency: "USD";
  items: Array<{ name: string; category: string; quantity: number; net_sales: number; share_pct: number }>;
  unmapped_net_sales: number }
```

### `GET /v1/commerce/today?location_id`

```ts
{ currency: "USD"; business_date: string; net_sales: number; orders: number; avg_check: number | null;
  last_order_at: string | null;
  by_hour: Array<{ hour: number /* 0-23 local */; net_sales: number; orders: number }>;
  freshness: Freshness[] }
```

## Team

Design: [Team spec](specs/team.md). Every `/v1/team/*` route requires owner, admin, or manager; an `employee` session gets `403 forbidden`. Owners and admins manage every location; a manager manages the locations in their `location_access` and the employees assigned to them. Anything outside that scope, or in another org, is `404`. Every mutation writes an audit event in the same transaction.

```ts
type Position = { id: string; name: string; color: string /* #rrggbb */; archived_at: string | null }
type Station  = { id: string; location_id: string; name: string; archived_at: string | null }
type Employee = { id: string; display_name: string; email: string | null; phone: string | null;
                  source: "square" | "manual"; active: boolean; job_title: string | null;
                  pos_hourly_rate: number | null;     // cents, synced from the POS (read-only)
                  rate_override: number | null;       // cents, team-owned
                  effective_rate: number | null;      // rate_override ?? pos_hourly_rate; null = "no rate"
                  currency: "USD";
                  positions: Array<{ id: string; name: string; color: string; is_primary: boolean }>;
                  location_ids: string[]; login: "none" | "invited" | "active";
                  merged_into_id: string | null; updated_at: string }
type PositionRef = { position_id: string; is_primary?: boolean }   // first one is primary unless one is marked
```

### Employees

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/employees?location_id&include_inactive` | none | `200 { data: Employee[] }`. Without `location_id`, every employee the caller manages |
| POST | `/v1/team/employees` | `{ display_name, email?, phone?, location_ids, positions?: PositionRef[], rate_override? }` | `201 Employee` (`source: "manual"`) |
| PATCH | `/v1/team/employees/{id}` | any of `{ email, phone, rate_override, location_ids, positions }`; manual employees also `{ display_name, active }` | `200 Employee` |
| POST | `/v1/team/employees/{id}/merge` | `{ into_id }` | `200 Employee` (the survivor). Owner/admin only |

- **Field ownership.** For Square employees, `display_name`, `active`, `job_title`, and `hourly_rate` are synced and read-only: sending any of them is `422 pos_owned_field` with that field in `errors`. Sync never changes team-owned fields.
- **Locations.** A manager's `location_ids` edit only touches locations they can access; the employee's other locations are kept.
- **Merge** (`{id}` into `into_id`) repoints external refs, timecards, and later shifts and requests, moves the login, unions positions and locations, then sets `{id}` inactive with `merged_into_id`. If `{id}` came from Square, the survivor becomes POS-linked. `409` if both have logins or either was already merged.
- **POS import.** After each team member sync, empty emails are filled from Square, assigned linked locations are added (never removed; "all locations" means every linked location), and each job title becomes a position, set as primary on employees that have no positions.

### Positions and stations

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/positions?include_archived` | none | `200 { data: Position[] }` |
| POST | `/v1/team/positions` | `{ name, color? }` | `201 Position`. `409` if the name exists (ignoring case) |
| PATCH | `/v1/team/positions/{id}` | `{ name?, color?, archived? }` | `200 Position` |
| GET | `/v1/team/stations?location_id&include_archived` | none | `200 { data: Station[] }` |
| POST | `/v1/team/stations` | `{ location_id, name }` | `201 Station` |
| PATCH | `/v1/team/stations/{id}` | `{ name?, archived? }` | `200 Station` |

## Sous

Read-only operations assistant over a location's sales and labor data. The model picks from five typed tools (`get_sales_summary`, `get_daily_sales`, `get_top_items`, `get_today`, `get_data_freshness`) and explains what they return. Numbers in the answer must match tool results; evidence and limitations are computed by the API. Design: [Sous spec](specs/sous.md).

```ts
type SousKind = "answer" | "clarify" | "refuse" | "unsupported" | "evidence_only"
type Evidence = { label: string; value: number;
                  unit: "usd_cents" | "count" | "percent" | "percent_pts" | "hours";
                  source: string /* tool name */; as_of: string | null }
type SousAnswer = {
  kind: SousKind;
  answer: string;                       // plain text, no markdown
  period: { from: string; to: string; timezone: string } | null;
  evidence: Evidence[];
  limitations: string[];                // stale / failing / not connected streams, failed tools
  tools_used: Array<{ name: string; args: Record<string, unknown>; ms: number; ok: boolean }>;
  model: string; prompt_version: string; request_id: string }
type SousStatus = { provider: "ollama" | "fake"; model: string;
                    available: boolean /* reachable and model pulled */;
                    model_pulled: boolean; prompt_version: string }
```

### `POST /v1/sous/ask`

Body: `{ question: string /* 1-500 chars */; location_id: string; history?: Array<{ role: "user" | "assistant"; content: string }> /* max 10 */ }`. Response `200 SousAnswer`.

- `kind` is `clarify` when the question needs a period or other detail, `refuse` when asked to leave its role, `unsupported` for anything outside the tools (schedules, inventory, waste, payroll), and `evidence_only` when no written answer passed the number check (`answer` is a fixed sentence and `evidence` carries the figures). Only `answer` and `evidence_only` include `period`, `evidence` and `limitations`.
- Evidence money is integer cents (`usd_cents`); percents are in percent units.
- A `location_id` outside the user's scope is `404 not_found`, as for Commerce.
- `429 rate_limited`: 20 questions per minute per user.
- `503 sous_unavailable`: the language model is unreachable or timed out.

### `GET /v1/sous/status`

`200 SousStatus`. Pings the provider (2 s timeout). The web app uses it to show whether Sous is available.

## Dev-only (`ENV=local`)

| Method | Path | Response |
| --- | --- | --- |
| POST | `/v1/dev/ping-workflow` | `202 { workflow_id }` |
| GET | `/v1/dev/ping-workflow/{id}` | `200 { status, result }` |
