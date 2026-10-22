# API Reference (v1)

**Status:** Contract for the demo build · **Last updated:** 2026-10-20
**Related:** [Platform foundation](specs/platform-foundation.md), [Integration layer](specs/integration-layer.md), [Team](specs/team.md), [Inventory](specs/inventory.md)

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

  `code` values: `validation_error`, `unauthenticated`, `session_expired`, `csrf_failed`, `forbidden`, `not_found`, `conflict`, `unavailable`, `internal_error`, `rate_limited`, `sous_unavailable`, `pos_owned_field`, `invitation_invalid` (410), `email_has_account`, `shift_overlap`, `schedule_changed`, `conflicts_unacknowledged`.
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

**Roles.** `owner` and `admin` see everything. `manager` sees the locations in its `location_access`. `employee` sessions are limited to `/v1/auth/*`, `GET /v1/org`, `GET /v1/locations`, `/v1/me/*`, and `/v1/notifications`: every Commerce, Sous, Integrations, and `/v1/team/*` route returns `403 forbidden` for them. The web app sends employees to `/my`.

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

### Push sales

Owners and admins manage API keys; create and revoke are audited. The secret (`chef_` + 256 random bits) is returned once and stored only as a SHA-256 hash. A key has explicit scopes, an optional location binding (unbound keys may push for any active location in the org) and an optional `expires_at`. Push requests send `Authorization: Bearer <key>` and an `Idempotency-Key` (1-128 printable ASCII). The org always comes from the key, never the payload. Revoked, expired or unknown keys get `401`; a missing scope `403`; a location outside the key's binding or org `404`.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/api-keys` | `{ scopes: ("orders:write"\|"daily_sales:write")[], location_id?, expires_at? }` | `201 ApiKey & { secret }` (secret shown once) |
| GET | `/v1/api-keys` | none | `200 { data: ApiKey[] }` (never includes secret or hash) |
| DELETE | `/v1/api-keys/{id}` | none | `200 ApiKey` (revokes) |
| POST | `/v1/ingest/orders` | `{ location_id, records: [{ id, closed_at, net_sales, orders?, currency? }] }` | `200 { accepted, applied, location_id, freshness }` |
| POST | `/v1/ingest/daily-sales` | `{ location_id, totals: [{ business_date, net_sales, orders?, currency? }] }` | `200 { accepted, applied, location_id, freshness }` |

Totals land through the shared pipeline as unitemized tickets (they appear as unmapped sales in item mix) and refresh the location's `orders` freshness. Push orders remain fresh for 26 hours after a successful push; push locations report `timecards`, `catalog`, and `team_members` as `not_connected`. Square freshness continues to use its stream sync intervals. `net_sales` is integer minor units (0 to 10^11). `closed_at` is an ISO timestamp with offset and not in the future; `business_date` is a date in the location's business calendar, not after today. `id` matches `[A-Za-z0-9._-]+`; ids and dates are unique per batch, and a batch uses one ISO currency matching the location's existing sales. Batches cap at 500 order records or 400 daily totals and 5,000 expanded orders. Re-pushing an order id or date replaces it. Reusing an `Idempotency-Key` with the same endpoint, location and body returns the original response; anything else is `409`. Push is refused (`409`) for a location fed by a live connector, and a Square location cannot be linked to a push-fed location.

Order IDs and dates are scoped to the requested location: another location may use the same
record IDs/dates without affecting these totals. Corrections remove obsolete tickets only
within that location. Re-pushing replaces legacy synthetic IDs atomically; previously lost
totals from a cross-location collision require reconciliation and re-push from the source.

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

Optional `category` filters the returned item rows to that current menu category. Categories
and overall totals remain unfiltered. Categories are sorted by net sales descending, then name;
items use the same stable tie-break. Category assignment reflects the current menu mapping.

```ts
{ currency: "USD"; total_net_sales: number;
  categories: Array<{ category: string; net_sales: number; share_pct: number }>;
  items: Array<{ name: string; category: string; quantity: number; net_sales: number; share_pct: number }>;
  unmapped_net_sales: number }
```

`total_net_sales` is the sum of order-line net values for the date range. Mapped category totals
plus `unmapped_net_sales` reconcile to it, including negative line adjustments. Unmapped lines
remain visible in the separate total and are not assigned to a category.

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

### Invitations

Logins are created only by accepting an invitation. Links are shared by hand (no email delivery). Tokens are 256-bit, stored as SHA-256, valid 7 days, single use.

```ts
type Invitation = { id: string; email: string; role: "manager" | "employee";
                    employee_id: string | null; employee_name: string | null; location_ids: string[];
                    status: "pending" | "accepted" | "revoked" | "expired";
                    created_at: string; expires_at: string; accepted_at: string | null }
```

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/team/invitations` | `{ employee_id?, email?, role: "employee" \| "manager", location_ids }` | `201 { invitation, accept_url }`. `accept_url` (`{WEB_ORIGIN}/invite/{token}`) is shown once |
| GET | `/v1/team/invitations?status=pending\|accepted\|revoked\|expired` | none | `200 { data: Invitation[] }` (newest first) |
| POST | `/v1/team/invitations/{id}/revoke` | none | `200 Invitation`. `409` if already accepted |
| GET | `/v1/invitations/{token}` | none (no auth) | `200 { org_name, email, role, employee_name, expires_at }` or `410 invitation_invalid` |
| POST | `/v1/invitations/{token}/accept` | `{ display_name, password }` (no auth, no CSRF) | `201 { user, org, role, csrf_token }` + session cookie |

- `email` defaults to the employee's email; without `employee_id` it is required (a manager who isn't on the schedule). A new invitation for the same employee (or email) revokes the pending one.
- Managers may invite only `role: "employee"` and only to their locations; `employee_id` must be active and without a login (`409`).
- Accepting creates the user, the membership with the invited role, `location_access` for the invited locations, and the employee link, in one transaction with a session. `410 invitation_invalid` for expired, revoked, or used tokens; `409 email_has_account` if the email already has a ChefOS login (users belong to one org in v1); `422` for a weak password. Invalid tokens count against the same per-IP limit as failed logins (`429`).

### Schedule

Weeks run Monday to Sunday (`week_start` must be a Monday, else `422`). Shift times are ISO 8601 with an offset; the API derives `business_date` from the location's timezone and business-day start (a 01:00 start belongs to the previous date). Managers edit a draft; employees see only what is published.

```ts
type Shift = { id: string; location_id: string; employee_id: string | null; position_id: string;
               station_id: string | null; business_date: string; starts_at: string; ends_at: string;
               break_minutes: number; notes: string;
               paid_hours: number;          // (ends_at − starts_at) − break, so DST days are right
               cost: number | null;         // cents; null without an effective rate or employee
               updated_at: string }
type ShiftIn = { employee_id?: string | null; position_id: string; station_id?: string | null;
                 starts_at: string; ends_at: string; break_minutes?: number; notes?: string }
type Conflict = { shift_id: string; detail: string;
                  kind: "overlap" | "time_off_approved" | "time_off_pending" | "unavailable" |
                        "inactive_employee" | "not_assigned_to_location" | "unassigned" }
type DayTotals = { business_date: string; scheduled_hours: number; projected_cost: number;
                   unrated_shifts: number; forecast_sales: number | null;
                   forecast_basis: string[];   // the dates the median used
                   forecast_reason: "not_connected" | "not_enough_history" | null;
                   projected_labor_pct: number | null }
```

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/schedule?location_id&week_start` | none | `200 Schedule` (below) |
| POST | `/v1/team/schedule/shifts?location_id&week_start` | `ShiftIn` | `201 Shift` |
| PATCH | `/v1/team/shifts/{id}` | partial `ShiftIn` + `expected_updated_at` (required) | `200 Shift`. `409` if the shift changed since `expected_updated_at` |
| DELETE | `/v1/team/shifts/{id}` | none | `204` |
| POST | `/v1/team/schedule/copy?location_id&week_start` | `{ from_week_start }` | `200 { copied, unassigned }`. `409` if the target week has shifts |
| POST | `/v1/team/schedule/publish?location_id&week_start` | `{ expected_version, acknowledge_conflicts? }` | `200 { published_version, changed, changed_employees: string[], notified_users, employees_without_login: [{ id, display_name }] }` |

```ts
type Schedule = {
  location_id: string; timezone: string; currency: "USD";
  schedule: { id: string | null; week_start: string; version: number; published_version: number | null;
              published_at: string | null; has_unpublished_changes: boolean;
              unpublished_changes: number /* shifts added, removed, or changed since the last publish */ };
  shifts: Shift[];
  employees: Array<{ id: string; display_name: string; active: boolean; primary_position_id: string | null;
                     position_ids: string[]; has_rate: boolean; assigned: boolean /* works at this location */ }>;
  days: DayTotals[];                                       // 7, Monday first
  week: Omit<DayTotals, "business_date" | "forecast_basis" | "forecast_reason"> &
        { reference_labor_pct: number | null;              // actual labor % over the last 28 days
          reference_period: { from: string; to: string } };
  conflicts: Conflict[];
  freshness: Freshness[] }                                 // the orders stream
```

- **Validation (422):** end after start, at least 15 minutes, at most 16 hours, break shorter than the shift, business date inside the week, an active employee, a non-archived position, a station of this location. Crossing midnight is allowed.
- **Overlap:** the same employee on two overlapping shifts at any location is `409 shift_overlap`, naming the other shift. Other conflicts never block a write; they are listed per shift on read.
- **Forecast:** median net sales (all channels) of the last 4 same-weekday business dates before min(date, today). Days after the location's first sales day with no sales count as 0 (closed). Fewer than 2 dates with history: `null`, `not_enough_history`; no POS: `null`, `not_connected`. Labor % = projected cost ÷ forecast. The week has a forecast only if every day has one.
- **Publish** runs in one transaction: lock the schedule, check `expected_version` (`409 schedule_changed` if stale; refetch), copy the draft into the published snapshot, and alert only employees whose published shifts were added, removed, moved, or reassigned (shifts that already ended are ignored). The first publish of a week sends `schedule_published`; later ones send `schedule_changed` with a summary ("Fri moved to 5pm-11pm"). Overlaps block publishing (`409 shift_overlap`); any other conflict needs `acknowledge_conflicts: true` (`409 conflicts_unacknowledged`). Republishing an unchanged week is a no-op (`changed: false`, no alerts). Employees without a login are published but listed in `employees_without_login`.
- **Copy** keeps local wall-clock times (DST-safe). Inactive employees, and employees who already work elsewhere at the copied time, are copied as unassigned.

### Requests inbox (managers)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/requests?location_id&status=pending\|history` | none | `200 { time_off: TimeOff[], availability: Availability[] }` for employees the caller manages (at `location_id` if given). Pending oldest first; history (decided) newest first |
| POST | `/v1/team/time-off/{id}/decide` | `{ decision: "approve" \| "deny", note? }` | `200 TimeOff`. `409` if not pending |
| POST | `/v1/team/availability/{id}/decide` | `{ decision: "approve" \| "reject", note? }` | `200 Availability`. `409` if not pending |

Each pending time-off item lists the published shifts it overlaps (`overlapping_shifts`). Approving never deletes shifts: the builder flags them (`time_off_approved`; `time_off_pending` while undecided; `unavailable` against the employee's approved availability). Decisions notify the employee.

### Attendance (managers)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/attendance?location_id&from&to` | none | `200 Attendance`. `from`/`to` are business dates, default today; at most 31 days. `404` for a location the caller doesn't manage |

```ts
type AttendanceStatus = "upcoming" | "awaiting_data" | "on_time" | "late" | "missed" | "in_progress";
type Attendance = { location_id: string; timezone: string; period: { from: string; to: string };
                    grace_minutes: 10; as_of: string | null; open_shifts_excluded: number;
                    freshness: DataFreshness[] /* timecards */;
                    days: { business_date: string; scheduled_hours: number; actual_hours: number;
                            counts: Record<AttendanceStatus, number>;
                            shifts: { shift_id: string; employee_id: string; employee_name: string | null;
                                      position_id: string; position_name: string; position_color: string;
                                      starts_at: string; ends_at: string; scheduled_hours: number;
                                      clock_in: string | null; clock_out: string | null; actual_hours: number;
                                      status: AttendanceStatus; minutes_late: number | null }[];
                            unscheduled: { timecard_id: string; employee_id: string | null;
                                           employee_name: string | null; clock_in: string;
                                           clock_out: string | null; actual_hours: number }[] }[] };
```

- Compares **published** shifts with imported timecards. A timecard belongs to the same employee's shift whose window `[start − 2h, end)` holds its clock-in (nearest start wins); the rest are `unscheduled`. Open shifts count toward `scheduled_hours` but are not judged.
- **Late:** no clock-in 10 minutes after the start (or a clock-in after that). **Missed:** none by the end. Overnight shifts belong to the business date they start on.
- Statuses are judged as of `as_of`, the instant timecards are imported through (the location's `timecards` stream, capped at now). A shift due after it is `awaiting_data`, never late; a location without a timecard source has `as_of: null` and is never judged.
- Closed timecards use imported paid hours. An open timecard is `in_progress`; its hours are running time from clock-in through the timecard stream's `as_of` (capped at now), since breaks are unknown until clock-out. Day actual hours include both closed paid hours and open running time.
- **Alerts:** after every scheduled sync of a connection, late and missed shifts that started in the last 36 hours notify the location's managers (owners, admins, managers with access) as `shift_late` / `shift_missed`, linking to `/app/team/attendance`. At most one per recipient, shift, and kind.
- `daily_labor.scheduled_hours` is the paid hours of published shifts for that day, set on each labor rebuild and refreshed on publish. Days without timecards have no `daily_labor` row.

### Positions and stations

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/team/positions?include_archived` | none | `200 { data: Position[] }` |
| POST | `/v1/team/positions` | `{ name, color? }` | `201 Position`. `409` if the name exists (ignoring case) |
| PATCH | `/v1/team/positions/{id}` | `{ name?, color?, archived? }` | `200 Position` |
| GET | `/v1/team/stations?location_id&include_archived` | none | `200 { data: Station[] }` |
| POST | `/v1/team/stations` | `{ location_id, name }` | `201 Station` |
| PATCH | `/v1/team/stations/{id}` | `{ name?, archived? }` | `200 Station` |

## Employee self-service and notifications

Any signed-in role. `/v1/me/*` resolves the employee from the session's user (never a parameter); `404` if the login has no employee record. Responses never include rates or costs.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/me/week?week_start` | none | `200 MyWeek`. `week_start` defaults to the current week at the employee's location |
| GET | `/v1/notifications?unread_only` | none | `200 { data: Notification[] /* newest 50 */, unread_count }` |
| POST | `/v1/notifications/read` | `{ ids?: string[], all?: boolean }` | `200 { unread_count }`. Only the caller's own notifications |
| GET | `/v1/me/availability` | none | `200 { current: Availability \| null, pending: Availability \| null, upcoming: Availability[] }` |
| PUT | `/v1/me/availability` | `{ effective_from, blocks: Block[], note? }` | `200 Availability` (`pending`; replaces a pending one, which becomes `superseded`). `effective_from` today or later |
| GET | `/v1/me/time-off` | none | `200 { data: TimeOff[] }` (own requests, newest first) |
| POST | `/v1/me/time-off` | `{ starts_on, ends_on, start_time?, end_time?, reason? }` | `201 TimeOff`. `422` if it starts in the past or `ends_on < starts_on` |
| POST | `/v1/me/time-off/{id}/cancel` | none | `200 TimeOff`. Pending or approved and not over; another employee's id is `404` |

```ts
type MyWeek = { week_start: string; employee: { id: string; display_name: string };
                published_at: string | null;              // null: nothing published for this week yet
                locations: Array<{ id: string; name: string; timezone: string }>;
                my_shifts: Array<{ id: string; location_id: string; business_date: string; starts_at: string;
                                   ends_at: string; break_minutes: number; position_name: string;
                                   position_color: string; station_name: string | null; notes: string }>;
                team_shifts: Array<{ location_id: string; business_date: string; starts_at: string; ends_at: string;
                                     employee_name: string; position_name: string; position_color: string }> }
type Notification = { id: string; kind: "schedule_published" | "schedule_changed" | "time_off_decided" |
                        "availability_decided" | "time_off_requested" | "availability_submitted" |
                        "employee_deactivated" | "shift_late" | "shift_missed";
                      title: string; body: string; link: string | null /* in-app path */;
                      entity_type: string | null; entity_id: string | null;
                      created_at: string; read_at: string | null }
```

```ts
type Block = { weekday: number /* 0 = Monday */; start_time: string | null; end_time: string | null }  // unavailable; no times = all day
type Availability = { id: string; employee_id: string; employee_name: string | null;
                      status: "pending" | "approved" | "rejected" | "superseded"; effective_from: string;
                      note: string; blocks: Block[]; submitted_at: string; decided_at: string | null;
                      decision_note: string | null }
type TimeOff = { id: string; employee_id: string; employee_name: string | null; starts_on: string; ends_on: string;
                 start_time: string | null /* on the first day */; end_time: string | null /* on the last day */;
                 reason: string; status: "pending" | "approved" | "denied" | "cancelled"; created_at: string;
                 decided_at: string | null; decision_note: string | null;
                 overlapping_shifts: Array<{ id; location_id; business_date; starts_at; ends_at; position_name }> }
```

Times on requests are location-local wall clock (`HH:MM`). The approved availability with the latest `effective_from` on or before a date applies to that date. Submitting availability or time off notifies the employee's managers (owners/admins and managers of their locations).

`MyWeek` reads only the published snapshot: draft edits are invisible until the next publish. Notifications are in-app only; clients poll every 60 s while visible and on focus.

## Inventory (catalog foundation)

Inventory routes require an authenticated session. Catalog mutations require owner, admin, or manager; location-scoped requests also check location access. Quantities use the item's base unit.

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/v1/inventory/items?location_id` | location UUID | `{ data: Item[] }`, including on-hand, par, and below-par state |
| POST | `/v1/inventory/items` | name, category, base unit, dimension, supplier, currency, conversions | `201 Item` |
| PATCH | `/v1/inventory/items/{item_id}` | name, category, active, supplier | `Item` |
| PUT | `/v1/inventory/items/{item_id}/par` | location ID and base quantity | `Par` |
| GET/POST | `/v1/inventory/suppliers` | supplier fields on create | `{ data: Supplier[] }` / `201 Supplier` |
| GET/POST | `/v1/inventory/storage-areas` | location query / location ID, name, sort order | `{ data: StorageArea[] }` / `201 StorageArea` |

Item base units must be `g` for mass, `ml` for volume, or `ea` for count. INV-1 remaining catalog endpoints and later count, purchasing, recipe, and mobile workflows are documented in the [inventory spec](specs/inventory.md).

### Waste (INV-5)

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| POST | `/v1/inventory/waste` | `{ location_id, item_id, quantity, unit?, reason, idempotency_key }` | `201 WasteEntry` (replay returns the original entry) |
| GET | `/v1/inventory/waste?location_id&from&to` | inclusive business-date range, at most 367 days | `200 { data: WasteEntry[] }`, newest first |

`quantity` is positive; `unit` defaults to the item's base unit and otherwise must be a configured conversion. Waste snapshots the latest known per-base-unit receipt cost, decrements stock, adds a `waste` stock movement, and writes an audit event atomically. Entries that would make stock negative are rejected. `WasteEntry` includes the quantity in base units, reason, business date, creator, and creation time.

## Sous

Read-only operations assistant over a location's sales, labor, published schedule and inventory pars. The model picks from eight typed tools (`get_sales_summary`, `get_daily_sales`, `get_top_items`, `get_today`, `get_data_freshness`, `get_labor_summary`, `get_schedule`, `find_items_below_par`) and selects facts from their results. The server renders each selected fact with its metric, period, entity, signed value and unit. Model-authored prose never becomes an answer; evidence and limitations are also computed by the API. Design: [Sous spec](specs/sous.md).

```ts
type SousKind = "answer" | "clarify" | "refuse" | "unsupported" | "evidence_only"
type Evidence = { label: string; value: number;
                  unit: "usd_cents" | "count" | "percent" | "percent_pts" | "hours"
                        | "g" | "ml" | "ea" /* stock in an item's base unit */;
                  source: string /* tool name */; as_of: string | null }
type SousAnswer = {
  conversation_id: string; turn_id: string; conversation_version: number;
  kind: SousKind;
  answer: string;                       // plain text, no markdown
  period: { from: string; to: string; timezone: string } | null;
  evidence: Evidence[];
  limitations: string[];                // stale / failing / not connected streams, data gaps
                                        // (no timecards, unpublished dates, no par), failed tools
  tools_used: Array<{ name: string; args: Record<string, unknown>; ms: number; ok: boolean }>;
  model: string; prompt_version: string; request_id: string }
type SousStatus = { provider: "ollama" | "fake"; model: string;
                    available: boolean /* reachable and model pulled */;
                    model_pulled: boolean; prompt_version: string }
```

### `POST /v1/sous/ask`

Body: `{ question: string /* 1-500 chars */; location_id: string; conversation_id?: string; turn_id?: string }`. IDs are UUIDs. Response `200 SousAnswer`.

Omit `conversation_id` to start a private conversation; send the returned ID for follow-ups.
The server owns prior turns, scoped by organization, user and location (including same-user
RLS). Only the last five completed question/answer pairs enter model context; prior answers
are not evidence for the current question. Nonempty client `history` is rejected with 422;
`history: []` is accepted for compatibility. Unknown request fields are rejected.

Use a fresh `turn_id` per question and reuse it for retries. A completed retry replays the
saved answer without model/tool work, including during a provider outage. The original
answer's `request_id` is retained on replay. Without a turn ID the server generates one, so
clients should provide it to recover when a response is lost. One question may run per
conversation. Failed/cancelled attempts are excluded from history; expired workers are fenced
from committing over a replacement. Conversations allow 200 completed turns; then start a new
one. Conversations expire 30 days after creation once an answer has been saved; unanswered
conversations expire after 24 hours. Replays and follow-ups do not extend that deadline.
Saved retries expire with their conversation. Expired conversations cannot supply history or replay an answer. Cleanup runs at worker startup
and hourly, deletes up to 1,000 expired conversations per sweep, and cascades through their
turns. Active leases and locked rows are skipped until a later sweep. Manual cleanup:
`make sous-cleanup`. Physical deletion requires the worker or manual cleanup to run; database
backups follow their own retention policy.

`POST /v1/sous/ask/stream` accepts the same body. Its first event is
`conversation` with `{ conversation_id, turn_id }`, followed by status/tool progress and a
terminal answer or error. The answer is committed before delivery.

`DELETE /v1/sous/conversations/{id}` clears the caller's conversation and all its turns,
returning 204. Session, staff role, CSRF, ownership and current location access are required.
Clearing fences any still-running answer; unknown or inaccessible IDs return 404.

- `kind` is `clarify` when the question needs a period or other detail, `refuse` when asked to leave its role, `unsupported` for anything outside the tools (editing schedules, placing orders, waste, payroll), and `evidence_only` when a fact selection is invalid after one repair, or the request exceeds the context budget (`answer` is deterministic and `evidence` carries any collected figures). Only `answer` and `evidence_only` include `period`, `evidence` and `limitations`.
- Evidence money is integer cents (`usd_cents`); percents are in percent units.
- A `location_id` outside the user's scope is `404 not_found`, as for Commerce.
- `409 sous_turn_running`: another attempt is still active; retry after it finishes.
- `409 sous_turn_conflict`: the turn ID was reused for a different request.
- `409 sous_turn_stale`: a failed turn was superseded, or a conversation was cleared.
- `409 sous_conversation_full`: start a new conversation.
- `410 sous_conversation_expired`: clear the expired conversation and start a new one.
  After physical cleanup, the same conversation ID returns `404`.
- `429 rate_limited`: 20 questions per minute per user.
- `503 sous_unavailable`: the language model is unreachable or timed out.
- `503 sous_busy`: model concurrency capacity is full; Retry-After is 5 seconds.
- `504 sous_timeout`: the question exceeded the 120-second orchestration deadline.
- Session, staff role and location access are rechecked before tool-result handoff and final delivery. After SSE headers are sent, failures are terminal `error` events carrying the status/code/detail rather than a new HTTP status.

### `GET /v1/sous/status`

`200 SousStatus`. Pings the provider (2 s timeout). The web app uses it to show whether Sous is available.

### `GET /v1/sous/briefing?location_id&date`

Daily briefing (SOUS-2). Deterministic: built per request from the Commerce read models, the published schedule and inventory levels; it never calls the model, so it works when Sous is unavailable. Nothing is stored.

```ts
type SectionStatus = "ok" | "no_data" | "unavailable"   // unavailable: the source failed or isn't connected
type Section = { status: SectionStatus; source: string /* "commerce.daily" | "team.published_schedule" | "inventory.levels" */;
                 business_date: string | null; as_of: string | null; note: string | null }
type Briefing = {
  location_id: string; location_name: string; timezone: string;
  business_date: string;            // the briefing's day: staffing is for this date
  results_date: string;             // business_date − 1: sales and labor
  generated_at: string; currency: "USD";
  highlights: string[];             // plain sentences, one per available section
  sales: Section & { net_sales; orders; avg_check; compare_date /* same weekday a week earlier */;
                     compare_net_sales; net_sales_change_pct };                     // null unless ok
  labor: Section & { actual_hours; actual_cost; labor_pct };                        // null unless ok
  staffing: Section & { published_at; shifts; scheduled_hours; unassigned_shifts; first_start; last_end;
                        by_position: Record<string, number>;
                        shift_list: Array<{ employee_name: string | null; position_name; starts_at; ends_at; paid_hours }> /* max 50 */ };
  stock: Section & { below_par_count; items_without_par;
                     items: Array<{ item_id; name; category; base_unit; on_hand; par; shortfall; as_of }> /* max 20, worst first */ };
  evidence: Evidence[];             // source is the service name above
  freshness: Array<{ stream; data_as_of; status }>;   // orders, timecards, catalog, team_members
  limitations: string[];            // stale/failing streams, missing or unavailable sections
  unavailable: string[] }           // sources that failed or aren't connected
```

- `date` defaults to today's business date at the location (its timezone and day start), so results cover the last completed business day. `date` may be up to 14 days ahead or 400 back (`422` otherwise).
- Numbers come straight from `GET /v1/commerce/daily`, the published schedule (drafts never count), and the same below-par rule as `GET /v1/inventory/items`. Stock is current state, not as of `date`.
- No timecards is `labor.status: "no_data"` with null figures, never zero labor. A section whose source fails is `unavailable` and named in `unavailable`; the other sections still return.
- Staff only (`403` for employees); a `location_id` outside the caller's scope is `404`.

## Dev-only (`ENV=local`)

| Method | Path | Response |
| --- | --- | --- |
| POST | `/v1/dev/ping-workflow` | `202 { workflow_id }` |
| GET | `/v1/dev/ping-workflow/{id}` | `200 { status, result }` |
