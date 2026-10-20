# Team (Phase 3)

**Status:** In progress (T1–T2 built) · **Last updated:** 2026-10-20
**Related:** [PRD](../prd.md) (TEAM-1 to TEAM-4, MOB-1), [Roadmap](../roadmap.md), [Architecture](../architecture.md), [Integration layer](integration-layer.md), [ADR 0002](../adr/0002-production-stack.md)

## Problem

Square team members and timecards already flow into `staff_members`, `timecards`, and `daily_labor`, so Commerce can show actual labor %. Nobody can plan labor yet. A GM can't build next week's schedule, see what it will cost against expected sales, or tell staff when it changes. Employees can't log in at all: there are no invitations, and the `employee` role has no screens and no restrictions (today an `employee` with location access could read Commerce). Time-off and availability live in group texts.

## Goals

- Every imported Square team member is an employee with positions, locations, and a pay rate, without duplicate records and without manual re-entry.
- A GM builds a weekly schedule per location and sees projected hours, cost, forecast sales, and projected labor % per day and per week as they edit.
- Publishing makes the schedule visible to employees. Republishing after edits alerts only the people whose shifts changed.
- Employees submit availability and time off from their phone. Managers approve or deny. Conflicts surface in the builder.
- Owners and managers invite employees to log in. Employees see only their own data and the published team schedule.
- Every schedule, approval, and employee change writes an audit event.

## Non-goals

- TEAM-5 shift trades and open shifts, TEAM-6 actual vs scheduled hours and late clock-in alerts (including filling `daily_labor.scheduled_hours`, which stays 0), TEAM-7 tasks and checklists.
- MOB-2 manager mobile and MOB-3 inventory mobile. Manager screens are responsive but not phone-optimized.
- Paid or external delivery: email, SMS, native push. Invitation links are copied and shared by hand.
- Overtime, labor-law rules, salaried pay, tip pooling, labor targets, auto-scheduling, forecast overrides.
- Writing employees back to Square. Unpublishing a schedule. Configurable week start (weeks run Monday to Sunday).
- A Sous `get_schedule` tool (follows this phase, per [Sous spec](sous.md)).
- Users in more than one org (sessions are single-org; see Risks).

## Approach

Team builds on the canonical records the integration layer already writes. The existing `staff_members` table **is** the Employee entity. Team takes ownership of it (models move from `app/commerce` to `app/team`; table name unchanged so pipeline SQL and `external_refs.entity_type = 'staff_member'` stay as they are) and adds team-owned columns and child tables. The API and UI call it "employee." POS-owned fields (name, POS rate, active) stay synced and read-only in the UI; team-owned fields (positions, locations, rate overrides, phone, login) are never touched by sync.

Schedules use a **draft working copy plus a published snapshot**. Managers edit `shifts` freely. Publish copies the week into `published_shifts`, diffs it against the previous snapshot, and notifies only affected employees. Employees read only the snapshot, so in-progress edits are never visible to them.

**Alternative considered: a separate `employees` table linked 1:1 to `staff_members`.** Rejected. Two person records per human means two mapping steps, timecards pointing at one while shifts point at the other, and a sync job to keep names in step. One table with field ownership is simpler and matches the architecture's single Employee entity.

**Alternative considered: live edits after publish** (every change to a published week is immediately visible and alerts the employee). Rejected. It spams employees while a GM shuffles shifts and gives managers no way to stage changes. Draft-and-republish is the norm in the category and makes the diff explicit.

## Design

### Data model

Migrations `0005_team_employees`, `0006_team_invitations`, `0007_team_schedules`, `0008_team_requests`, one per milestone (T4 adds no tables beyond 0007). Every new table carries `org_id`, gets the standard `org_isolation` RLS policy (`org_id = app_org_id()`), and is picked up by the existing RLS introspection test. Employee-vs-manager restrictions inside an org are enforced in the service layer, as for location scope today.

```
-- 0005
staff_members  + email citext null, phone null, user_id uuid null → users,
                 rate_override bigint null, source text ('square'|'manual'),
                 merged_into_id uuid null, updated_at
                 unique(org_id, user_id)
positions(id, org_id, name, color, created_at, archived_at)    unique(org_id, lower(name))
employee_positions(org_id, employee_id, position_id, is_primary) pk(employee_id, position_id)
                                                                unique(employee_id) where is_primary
stations(id, org_id, location_id, name, archived_at)            -- optional label on a shift
employee_locations(org_id, employee_id, location_id)            pk(employee_id, location_id)

-- 0006
invitations(id, org_id, email citext, role ('manager'|'employee'), employee_id null,
  location_ids uuid[], token_hash unique, created_by, created_at, expires_at,
  accepted_at, accepted_user_id, revoked_at)

-- 0007
schedules(id, org_id, location_id, week_start date, version int, published_version int null,
  published_at, published_by, updated_at)                       unique(location_id, week_start)
shifts(id, org_id, location_id, schedule_id, employee_id null, position_id, station_id null,
  business_date, starts_at, ends_at, break_minutes, notes, created_by, updated_at)
published_shifts(same columns as shifts + published_version)    -- replaced per schedule on publish
notifications(id, org_id, user_id, kind, title, body, link, entity_type, entity_id,
  created_at, read_at)                                          index(user_id, read_at)

-- 0008
availability_sets(id, org_id, employee_id, status ('pending'|'approved'|'rejected'|'superseded'),
  effective_from date, note, submitted_by, submitted_at, decided_by, decided_at, decision_note)
availability_blocks(org_id, set_id, weekday smallint, start_time null, end_time null)
  -- unavailable windows, location-local; null times = all day
time_off_requests(id, org_id, employee_id, starts_on date, ends_on date, start_time null,
  end_time null, reason, status ('pending'|'approved'|'denied'|'cancelled'),
  created_at, decided_by, decided_at, decision_note)
```

Invitation acceptance happens before any org scope exists. Like `integration_connection_for_account`, a narrow `SECURITY DEFINER` function `invitation_for_token(p_hash) → (invitation_id, org_id)` is the only way around RLS (`app.db.lookup_invitation`). It returns nothing for expired, revoked, or accepted invitations. Everything after the lookup runs in one transaction scoped to that org (`users` and `sessions` have no RLS), so `unscoped_session` stays confined to the auth code; the throttle check goes through two small `app.identity.service` helpers.

### Employee linking

- **POS → employee.** Unchanged path: `process_team_members` creates or updates a `staff_members` row and its `external_refs` entry. Two additions: the Square normalizer also returns `email_address` and `assigned_locations`, and the pipeline then calls `team.service.on_staff_imported(db, org_id, ids)`. That hook (a) sets `email` if empty, (b) inserts `employee_locations` for assigned locations that are linked on the connection (insert-only, never removes), and (c) creates a `positions` row per distinct job title and assigns it as primary if the employee has no positions at all (so an employee whose positions a manager cleared gets the POS title back on the next sync; one with any position is left alone). The pipeline's upsert writes only name, job title, POS rate, and active, and only when they changed. An active-to-inactive transition writes one `employee.deactivated_by_pos` event (the only per-row sync event). Unlinked timecard-only members still go through the existing `unmapped_team_member` mapping queue.
- **Manual employees** (`source = 'manual'`) cover staff who aren't in the POS. If the same person later appears from Square, the duplicate is resolved with **merge** (`from → into`): repoints `external_refs`, `timecards`, `shifts`, requests, and `user_id` to `into`, unions positions and locations, fills `into`'s empty email/phone/rate override from `from`, then sets `from.active = false, merged_into_id = into`. If `from` is POS-linked, `into` becomes `source = 'square'` and takes `from`'s POS-owned fields, since sync now writes to it. One transaction, one audit event. Merge is refused if both records have a `user_id` or either was already merged.
- **Employee → user.** `staff_members.user_id` is 0..1 per org (architecture: `Employee 0..1─1 User`). It's set only by accepting an invitation created from that employee record, which pre-fills the email from Square (the inviter may override it). An invitation without `employee_id` invites a manager who isn't on the schedule (owner/admin only); `email` is then required. A new invitation for the same employee revokes the pending one. The accept link is `{WEB_ORIGIN}/invite/{token}`. Acceptance is throttled by sharing the per-IP failed-login counter: each invalid token counts as a failure. Accepting creates the `users` row (or 409 if the email already has an account, see Risks), a `memberships` row with the invited role, `location_access` rows, and links the employee. Tokens are 256-bit, stored as SHA-256, valid 7 days, single use.
- **Effective rate** for projections = `rate_override` ?? POS `hourly_rate` ?? null. Null is shown as "no rate" and excluded from cost (flagged, not guessed).

### Forecast and projected labor

**Forecast sales** for business date D at a location = the **median of net sales on the last 4 complete same-weekday business dates** before today (from `daily_sales`, summed across channels). With fewer than 2 such dates, the forecast is null with reason `not_enough_history`.

Why this method: restaurant demand is dominated by day-of-week (the simulated seller's demand table is literally per weekday), so same-weekday history captures the main signal. Four weeks tracks seasonal drift without needing the 13-month backfill. Median instead of mean keeps one-offs from distorting a day: a $4,800 buyout or a snowstorm Saturday in the window moves the forecast by at most the gap between the two middle values. It is deterministic, computed on read from 28 days of an indexed read model, needs no new dependency, and fits in one sentence in the UI ("Median of the last 4 Fridays"). Rejected: year-over-year with growth adjustment (calendar alignment, holidays, needs full backfill); exponential smoothing or Prophet-style models (opaque, a new dependency, and no better on 4–8 weeks of history). The forecast carries the `orders` stream's `DataFreshness`.

**Projection** per shift: `paid_hours = (ends_at − starts_at) − break_minutes` (timestamp difference, so DST days are correct); `cost = paid_hours × effective_rate`. Per day: scheduled hours, projected cost, forecast, projected labor % = cost / forecast (null if the forecast is null or 0). Per week: the same totals. The builder also shows last 4 weeks' actual labor % from the existing Commerce summary as a reference.

### Service and HTTP API

Module `app/team/` (`models.py`, `schemas.py`, `service.py`, `forecast.py`, `notify.py`, `api.py`). Location scope uses `ctx.allowed_location_ids()` as Commerce does. **Manage** = owner, admin, or manager with access to the location. A manager sees employees assigned to at least one of their locations; editing an employee's locations only adds or removes locations the manager can access. Managers may invite only the `employee` role and can't merge employees. Positions are org-wide and can be archived (hidden from pickers, kept on existing records); stations belong to a location. A new `require_staff_role` dependency (`StaffDep`, owner/admin/manager) is added to Commerce, Sous, Integrations, and every `/v1/team/*` route so `employee` sessions get 403 (location writes were already owner/admin only). Employees keep `/v1/auth/*`, `GET /v1/org`, `GET /v1/locations`, `/v1/me/*`, and `/v1/notifications`.

```
GET    /v1/team/employees?location_id&include_inactive        → { data: Employee[] }        manage
POST   /v1/team/employees      ← { display_name, email?, phone?, location_ids, positions: [{ position_id, is_primary? }], rate_override? }
PATCH  /v1/team/employees/{id} ← team-owned fields only; POS-owned field → 422 pos_owned_field
POST   /v1/team/employees/{id}/merge ← { into_id }                                       owner/admin
GET|POST|PATCH /v1/team/positions[/{id}]      GET|POST|PATCH /v1/team/stations[/{id}]?location_id

POST   /v1/team/invitations    ← { employee_id?, email?, role, location_ids } → 201 { invitation, accept_url }  (URL shown once)
GET    /v1/team/invitations?status=pending    POST /v1/team/invitations/{id}/revoke
GET    /v1/invitations/{token}                → { org_name, email, role, employee_name, expires_at }   (no auth)
POST   /v1/invitations/{token}/accept ← { display_name, password } → 201 + session cookie (no auth, throttled)

GET    /v1/team/schedule?location_id&week_start
       → { schedule: { version, published_version, has_unpublished_changes }, shifts: Shift[],
           days: [{ business_date, scheduled_hours, projected_cost, unrated_shifts,
                    forecast_sales, forecast_basis: date[], projected_labor_pct }],
           week: {...same totals}, conflicts: Conflict[], freshness: DataFreshness }
POST   /v1/team/schedule/shifts?location_id&week_start  ← ShiftIn → 201 Shift
PATCH  /v1/team/shifts/{id}    ← partial ShiftIn + expected_updated_at        DELETE /v1/team/shifts/{id}
POST   /v1/team/schedule/copy?location_id&week_start ← { from_week_start }   (target week must be empty)
POST   /v1/team/schedule/publish?location_id&week_start ← { expected_version, acknowledge_conflicts }
       → { published_version, changed_employees, notified_users, employees_without_login }

GET    /v1/team/requests?location_id&status=pending        → { time_off: [...], availability: [...] }
POST   /v1/team/time-off/{id}/decide      ← { decision: approve|deny, note? }
POST   /v1/team/availability/{id}/decide  ← { decision: approve|reject, note? }

GET    /v1/me/week?week_start   → { my_shifts, team_shifts (name, position, times only), published_at }
GET    /v1/me/availability      PUT /v1/me/availability ← { effective_from, blocks[], note? } → pending set
GET    /v1/me/time-off          POST /v1/me/time-off ← { starts_on, ends_on, start_time?, end_time?, reason? }
POST   /v1/me/time-off/{id}/cancel
GET    /v1/notifications?unread_only    POST /v1/notifications/read ← { ids? , all? }
```

`ShiftIn = { employee_id?, position_id, station_id?, starts_at, ends_at, break_minutes, notes? }`, times in ISO with offset. The API derives `business_date` from the location's timezone and `business_day_start`. `/v1/me/*` resolves the employee from `ctx.user_id`. It never takes an employee ID. Rates and costs never appear in `/v1/me/*` responses.

**Conflicts** (computed on read, listed per shift): `overlap` (same employee, any location; also rejected 409 on write), `time_off_approved`, `time_off_pending`, `unavailable`, `inactive_employee`, `not_assigned_to_location`, `unassigned`. Only `overlap` blocks a write. Publish with any other conflict requires `acknowledge_conflicts: true`.

**Publish** in one transaction: lock the schedule row, check `expected_version`, replace `published_shifts`, diff old vs new per employee (added, removed, time/position/station changed), write one `notifications` row per affected user with a summary ("Your week of Oct 26 changed: Fri 4–11pm added, Sat moved to 5pm"), bump `published_version`, audit. Shifts that ended before now are ignored for alerts. Every shift write bumps `schedules.version`, which drives `has_unpublished_changes`.

**Notifications** are in-app only. Clients poll `GET /v1/notifications?unread_only=true` every 60 s while visible and on focus. Kinds: `schedule_published`, `schedule_changed`, `time_off_decided`, `availability_decided` (to employees), `time_off_requested`, `availability_submitted` (to managers of the employee's locations).

**Audit events** (`audit.record` in the same transaction; `details` holds before/after for updates): `employee.created|updated|merged|deactivated_by_pos`, `position.*`, `station.*`, `invitation.created|revoked|accepted`, `shift.created|updated|deleted`, `schedule.copied|published` (version, shift count, changed employee IDs), `time_off.requested|approved|denied|cancelled`, `availability.submitted|approved|rejected`. POS sync writes no per-row events (it already has `sync_runs`).

### Web (manager, `apps/web/src/app/app/team`)

| Route | Screen |
| --- | --- |
| `/app/team` | Employees table: name, positions, POS badge ("from Square", POS-owned fields locked), rate, login status (none / invited / active). Drawer: positions, locations, rate override, phone, Invite (shows copyable link once), Merge. Positions tab |
| `/app/team/schedule?week=` | Week grid: rows = employees grouped by primary position, columns = days, plus an Unassigned row. Shift dialog. Conflict chips. Day footer: hours, cost, forecast (tooltip names the 4 dates), labor %. Week totals tiles with the reference actual labor %. Sticky publish bar: "N unpublished changes · Publish". Copy last week. Freshness badge |
| `/app/team/requests` | Pending time off and availability with Approve / Deny, overlapping published shifts shown inline. History tab |

The sidebar Team item loses `soon`. A notification bell joins the shell header. Hooks go in `lib/queries.ts` with new `keys.*`. Types go in `lib/types.ts`. Every screen defines loading, empty ("No employees yet. Connect Square or add one"), and error states with the existing `ui/states` components.

### Mobile (MOB-1): recommendation

**Recommended: a mobile-first employee view in `apps/web` under `/my`**, not an Expo app, for this phase. `/team` is taken by the marketing site. Route group `(employee)` with its own minimal shell (bottom tabs: Schedule, Requests, Alerts), sized for a 390 px viewport, with a web app manifest so it can be added to the home screen. Login routes `employee` sessions to `/my`, and `/app/*` redirects them there. Managers who have an employee record also get a "My schedule" link.

- **Schedule:** this week and next, my shifts as cards, "Working with you" per day, published-at time.
- **Requests:** time off (create, cancel, status) and weekly availability (edit, submit, pending/approved state).
- **Alerts:** notification list. Opening one marks it read and deep-links to the week.
- **Offline:** the last successful `/v1/me/week` response is kept in `localStorage` (own data only, cleared on logout). Offline, the schedule renders from it with "Offline, showing schedule as of 3:12 PM", and request forms are disabled.

Why: it reuses cookie sessions, CSRF, `api.ts`, the query layer, types, and the design system as they are. Expo would need new mobile token auth (access + refresh tokens, which don't exist yet), a second UI kit, duplicated types, and a CI job, which roughly doubles MOB-1 for the same demo. `/v1/me/*` is client-agnostic, so an Expo app can be added later without API changes beyond token auth. Accepting this means a small ADR 0004 amending the mobile row in ADR 0002 and the architecture.

### Seed

`make demo` adds a manager (`gm@chefos.local`, every location, no employee record) and an employee login linked to imported "Maya Okafor" (`maya@chefos.local`), both through the real invite-and-accept path and with the demo owner's password, plus a published current week built from the simulator's shift pattern, so the walkthrough needs no manual setup (`app/team/demo.py`, idempotent).

## Behavior

### Happy path

1. After Square sync, `/app/team` lists the 18 Juniper House employees with positions (Server, Line Cook, …) created from job titles.
2. The owner invites Maya and copies the link. Maya opens it on her phone, sets a password, and lands on `/my` ("No published schedule yet").
3. The GM opens next week, copies last week, adjusts Friday, and watches Friday's projected labor % move against a forecast of the median of the last 4 Fridays. They publish. Maya gets a `schedule_published` alert and sees her shifts.
4. Maya requests Saturday off. The GM gets a `time_off_requested` alert, sees her published Saturday shift inline, and approves. Maya is notified. In the builder, her Saturday shift now shows `time_off_approved`. The GM reassigns it and republishes. Only Maya and the new assignee get `schedule_changed`.

### Edge and error cases

| Case | Behavior |
| --- | --- |
| Square sync after a manager edited positions/phone/override | Untouched. Only name, POS rate, and active are updated |
| PATCH a POS-owned field on a Square employee | 422 `pos_owned_field` |
| Team member deactivated in Square | Employee `active = false`. Draft shifts flagged `inactive_employee`. Published shifts stay until republish. Login is not revoked automatically; managers are notified |
| Same person exists as manual and Square employee | Merge. Refused (409) if both have logins |
| Overlapping shift for the same employee (any location) | 409 `shift_overlap` naming the other shift |
| Shift ends ≤ start, < 15 min, > 16 h, or break ≥ duration | 422 with field errors. Crossing midnight is allowed |
| Shift starting 01:00 local | Belongs to the previous business date (same rule as timecards) |
| Two managers edit; one publishes a stale view | `expected_version` mismatch → 409 `schedule_changed`, client refetches. Shift PATCH with stale `expected_updated_at` → 409 |
| Republish with no changes | No-op: version unchanged, no notifications |
| Employee without a login | Included in publish; response lists them under `employees_without_login` |
| No POS connection or < 2 weeks of history | Forecast and labor % null, with "Connect Square" / "Not enough history" in place of the number |
| Sales data `stale`/`failing` | Forecast shown with the freshness badge, never as current |
| Shift with no effective rate | Hours counted, cost excluded, day shows "2 shifts have no rate" |
| Employee calls `/v1/team/*`, Commerce, or Sous | 403 `forbidden` |
| Manager acts on a location without access, or an ID from another org | 404 (RLS/scope hides it) |
| Employee reads another employee's request by ID | 404 |
| Invitation expired, revoked, or already used | 410 `invitation_invalid` |
| Invited email already has a ChefOS account | 409 `email_has_account` (v1 limit) |
| Time off in the past, or `ends_on < starts_on` | 422 |
| Approving time off that overlaps published shifts | Allowed. Shifts are flagged in the draft, not deleted |
| New availability submitted while one is pending | Previous pending set → `superseded` |
| DST week | Hours from timestamp differences: a 22:00–06:00 shift on fall-back is 9 h |

## Verification

1. **RLS:** new tables appear in the introspection test. Cross-org reads and writes fail for every table. `invitation_for_token` returns only `(id, org_id)` and nothing for invalid tokens.
2. **Role matrix test** enumerating routes: employee 403 on manage, Commerce, Sous, and Integrations routes. Manager limited to `location_access` locations and cannot merge or invite managers. `/v1/me/*` never returns another employee's private data or any rate.
3. **Forecast unit tests:** median of 4, 3, 2, and fewer than 2 points, closed (zero) days, the simulator's snowstorm Saturday and buyout Tuesday don't move the forecast more than the middle-pair gap, and dates ≥ today are excluded.
4. **Projection tests:** breaks, cross-midnight, DST both directions, missing rate, rate override precedence.
5. **Publish diff tests:** a property test that republishing an unchanged week yields no notifications, and that the notified set equals exactly the employees whose published shifts differ. Plus a stale-version 409.
6. **Pipeline tests:** re-running team member sync after manager edits preserves team-owned fields. Positions are created once per title. `employee_locations` follows linked assigned locations. Merge moves timecards and refs.
7. **Invitation tests:** single use, expiry, revoke, hashed token at rest, accept throttled like login, accept creates membership, `location_access`, and the employee link atomically.
8. **Audit test:** each mutating route writes the expected `entity_type`/`action` in the same transaction (rollback leaves none).
9. **Web:** `lint`, `typecheck`, `build` in CI. Manual walkthrough of the happy path at 390 px width (devtools device mode) and desktop, including the offline banner.

## Risks / open questions

- **MOB-1 is web, not Expo. This is the decision to override if you disagree.** Trade-off: no native app, no native push, and offline limited to the last-viewed schedule. "Mobile app" in the demo is a home-screen web app. In exchange, MOB-1 costs about half as much and the codebase gets no second client, auth scheme, or CI job. The API is ready for Expo later, but token auth would still need building.
- **Phone access in demos.** Compose binds to 127.0.0.1 (deliberately, commit 708bec9). A real phone needs the Next dev server reachable via LAN or a tunnel. That's the same for Expo. Devtools device mode is the default demo.
- **Notifications are poll-based.** A minute of latency is fine for schedules. Web Push (free, VAPID) is a later upgrade, but needs HTTPS outside localhost.
- **Single-org users.** Inviting an email that already has an account returns 409 until an org switcher exists.
- **Forecast is naive.** Holidays and events misforecast (Christmas Eve looks like a normal weekday). Manual overrides are the obvious P1. The UI always names the four basis dates, so the number is explainable.
- **Wage visibility.** Managers see rates. Whether managers (vs owner/admin) should see or override wages is a product policy question. Default: they can.
- **Square `assigned_locations`** may say "all locations." That maps to every linked location on the connection.

## Milestones

Each one leaves `main` green and the app working. Docs (`api.md`, roadmap status) are updated in the same change.

1. **T1: Employees and positions.** Move `StaffMember`/`Timecard` models to `app/team`. Migration 0005. Normalizer email and assigned locations, `on_staff_imported`, employees/positions/stations API, merge, `/app/team` screen, audit. Exit: the 18 imported employees show positions from job titles, and a resync preserves manager edits.
2. **T2: Logins and roles.** Migration 0006 (invitations, `invitation_for_token`), invite/accept API and pages, `require_staff_role` on Commerce/Sous/Integrations, employee redirect to an empty `/my` shell, seed logins. Exit: Maya accepts an invite at phone width and gets 403 from every non-employee API.
3. **T3: Schedule builder and forecast.** Migration 0007 (all schedule and notification tables; T3 uses schedules and shifts), projections, forecast, conflicts (overlap, inactive, location, unassigned), copy week, builder UI. Drafts only. Exit: a GM builds a week and sees daily and weekly projected labor % against forecast.
4. **T4: Publish, alerts, employee schedule.** `published_shifts`, publish and diff, notifications table/API, bell, `/my` Schedule and Alerts tabs with offline cache, ADR 0004. Exit: an employee sees the published week on a phone-width view, and a republish alerts only changed employees.
5. **T5: Availability and time off.** Migration 0008, `/v1/me` request routes, manager requests inbox, availability and time-off conflicts in the builder, decision notifications. Exit: the Phase 3 exit criterion holds end to end on seeded data.
