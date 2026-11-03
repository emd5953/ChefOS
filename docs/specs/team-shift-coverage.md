# Team shift coverage (TEAM-5)

**Status:** Built · **Last updated:** 2026-11-02

## Product contract

Employees can request an open published shift or propose a swap of two published shifts. Requests never change the published schedule. A trade requires the affected coworker to accept, then a manager to approve; an open-shift claim requires manager approval. Approval changes the manager's draft schedule only. The manager reviews the resulting diff and publishes it through the existing schedule publish flow, which remains the only way changes become visible to staff.

Employees can discover and request open shifts, or propose swapping two published shifts. A trade requires the coworker to accept and a manager to approve. Managers can approve or decline open shift claims. Approval changes the draft schedule only; the manager publishes the reviewed draft to make the changes visible to staff.

## Invariants

- Resolve the acting employee from the signed-in user; never accept a requester ID from the client.
- A request references only future published shifts in the same location and weekly schedule. For a trade, both employees must be linked, active, assigned to the location, and eligible for the shift they would receive.
- An employee can have at most one pending claim for a shift. Multiple employees may request the same open shift; manager approval assigns one employee and closes the other pending claims.
- A trade proposal must reserve both shifts so neither can be claimed or included in another pending trade.
- Acceptance/decision operations lock the request and schedule rows and re-read the published shift snapshot before changing state. Stale or already-resolved requests return `409`.
- A manager decision verifies the schedule still matches the snapshot the request was based on. It applies assignments to draft shifts in one transaction, increments the schedule version, and records audit events. It does not publish.
- Cancellation is allowed to the request's employee only while pending. All decisions are terminal and auditable.
- In-app notifications are transactional. Notification failure must roll back the state change.

## Request lifecycle

`open_shift_claim`: employee requests an unassigned published shift → manager approves/denies → approval assigns the employee in the draft schedule → manager publishes.

`shift_trade`: employee requests a swap between one of their assigned shifts and a coworker's assigned shift → coworker accepts/declines → manager approves/denies → approval swaps the draft assignments → manager publishes.

Requests for past shifts, unpublished shifts, changed snapshots, inactive or ineligible employees, overlapping trade assignments, and shifts outside the employee's location access are rejected. Trade overlap and eligibility are rechecked at manager approval. Open-shift claims can be submitted while an employee has a conflict; approval rejects a schedule overlap, while existing time-off and availability conflicts remain visible to the manager through the normal publish conflict review.

## API/UI

- Employee self-service endpoints list published open shifts, create/cancel claims and trades, and accept/decline trades addressed to them. Trade selection is currently limited to the employee's current published week and a coworker at the same location.
- Manager inbox is location-scoped and supports approve/deny with an optional note.
- Employee pages show status and the schedule publication state. Manager requests view includes shift details, both employees' acceptance state, and the required follow-up publish action.
- Responses omit pay rates and projected cost from employee routes.

## Audit and verification

Audit creation, acceptance, cancellation, manager decision, and draft assignment changes. Cover authorization and tenant/location isolation, duplicate and concurrent requests, stale snapshots, conflicts, cancellation, notifications, and the fact that approval never mutates `published_shifts`.

## Implementation status

- **Delivered:** open-shift list for employee locations; claims and cancellation; coworker trade proposals, acceptance/decline, and requester cancellation; location-scoped manager inboxes and decisions; snapshot revalidation; employee location, position, activity, and overlap checks; transactional audit and in-app notifications. Multiple employees may request an open shift; approving one claim declines the remaining claims. Trades reserve both shifts. Schedule publish updates existing published rows in place so requests survive normal republishing; removing a referenced shift invalidates pending requests before removing the published row. All approvals change the draft and bump its version; employees see changes only after publish.
- **Not delivered:** manager decision history UI and multi-location or cross-week trades.
- **Verification:** `pytest tests/team/test_shift_trades.py tests/team/test_shift_coverage.py tests/team/test_publish.py tests/team/test_schedule.py tests/team/test_requests.py tests/team/test_rls_team.py -q`; `ruff check`, `mypy app/team`; web `npm run typecheck` and `npm run lint`.
