# Prototype Retirement (Phase 2 close-out)

**Status:** Done · **Last updated:** 2026-10-20
**Related:** [Roadmap](../roadmap.md), [Architecture](../architecture.md), [ADR 0002: Production stack](../adr/0002-production-stack.md), [Inventory](inventory.md)

## Problem

Phase 2 says to delete the prototype (`server.py`, `product.js`) once its screens are covered, and to move the marketing site into `apps/web`. The marketing move is done: `apps/web/public/` holds copies of `index.html`, `app.js`, `styles.css` and `assets/`, and `next.config.ts` rewrites `/` and the 12 marketing routes to `/index.html`. The prototype is still at the repo root, though. That leaves two copies of the site that can drift apart, a second auth stack (stdlib, SQLite) to keep secure, and a README that documents two ways to run the product.

Read literally, "covered" can't be met until the end of Phase 4. Team has no spec yet, Inventory's spec is only proposed, and several prototype screens (chat, time clock, manual sales entry) will never have a replacement because the PRD drops them. ADR 0002 already says the prototype is a UX reference only and that its schema and API are not the production design. A git tag preserves a UX reference just as well as keeping the files on `main`.

## Goals / Non-goals

Goals
- Every prototype screen and endpoint is accounted for: covered, deferred to a named phase or spec, or dropped with a reason.
- One copy of the marketing site (in `apps/web/public/`) and one runnable product.
- The prototype is still reachable for UX reference from a tag, with a documented one-line run command.
- Docs describe only what exists.

Non-goals
- Porting the marketing site from static vanilla JS to React components.
- Building any Team or Inventory screen.
- Merging the two Geist font copies (`public/fonts/` is used by `layout.tsx`, `public/assets/fonts/` by `styles.css`).
- Rewriting ADRs or older specs. They are historical records.

## Approach

**Deletion rule.** A prototype screen counts as retired when it is (a) covered by a production screen, (b) deferred to a roadmap phase or spec that owns it, or (c) dropped with a reason recorded here. The table below shows that every row meets one of these today. So the whole prototype goes at the end of Phase 2, after it is tagged `prototype-v0`. Nothing waits for Team or Inventory: those specs work from PRD requirements, and Inventory's spec already treats the prototype's data as examples only.

The prototype frontend is not a pure duplicate. `server.py` serves the root `index.html`, which loads `app.js`, and `app.js` boots `product.js` on `/app`. The root files are the prototype's own frontend, so they leave together with `server.py` as one unit.

**Alternative considered: keep `server.py` on `main` until the Phase 4 exit.** Rejected. It keeps the duplicate site and the second auth stack alive for months to protect a reference that the tag already preserves. `git worktree add ../chefos-prototype prototype-v0 && cd ../chefos-prototype && python3 server.py` reproduces it exactly.

## Design

### Coverage

| Prototype screen (tab) | Endpoints (`/api/v1/...`) | Replacement | Status |
| --- | --- | --- | --- |
| Marketing site: `/`, `/team`, `/commerce`, `/inventory`, `/integrations`, `/pricing`, `/mobile-app`, `/company`, `/contact`, `/blog[/:slug]`, `/privacy`, `/terms` | n/a (static) | `apps/web/public/*` served through `next.config.ts` rewrites | Covered |
| Sign in / register | `auth/login`, `auth/register`, `auth/logout`, `auth/me` | `/login`, `/signup`; `/v1/auth/{login,logout,signup,me}` | Covered |
| Location switcher | `locations` GET/POST | Shell location scope, `/app/locations`; `/v1/locations` | Covered |
| Overview | `reports/summary`, `inventory`, `tasks`, `shifts` | `/app` Overview covers sales and labor. Low stock: Inventory Phase 4. Tasks and today's shifts: Team Phase 3 | Covered (sales); rest deferred |
| Reports | `reports/summary` | `/app/sales` (COM-1–3, labor %). Food and prime cost: COM-6, Phase 6. Covers: dropped (not a PRD metric; orders and average check replace it) | Covered / deferred / dropped |
| Sales entry | `sales` GET/POST | Square import (Phase 1) feeds `daily_sales` | Dropped: manual entry conflicts with integration-first ([ADR 0001](../adr/0001-integration-first.md)) |
| Integrations & API keys | `api-keys`, `sales/ingest` (Bearer `ck_live_`) | `/app/integrations` covers connections, sync health and mapping. Push API with scoped keys: INT-6 / M6, Phase 6 | Covered (connections); keys deferred |
| Inventory (stock, adjust, add item, count) | `inventory`, `inventory/count`, `vendors` | [Inventory spec](inventory.md) Phase 4 (INV-1, INV-2). Ad-hoc "adjust" is replaced by count sessions | Deferred, Phase 4 |
| Log waste | `waste` | INV-5, Phase 6 (out of scope in Inventory spec) | Deferred, Phase 6 |
| Purchasing | `purchase-orders` (+ receive), `vendors` | Inventory spec Phase 4 (INV-3) | Deferred, Phase 4 |
| Recipes & costing | `recipes` | Inventory spec Phase 4 (INV-4) | Deferred, Phase 4 |
| Team: employees | `employees` | Team spec, Phase 3 (TEAM-1; not yet written) | Deferred, Phase 3 |
| Team: workspace accounts and invites | `users`, `invitations`, `auth/accept-invitation` | Team spec, Phase 3 (employee logins for MOB-1) | Deferred, Phase 3 |
| Team: time clock | `time-entries/clock-in`, `clock-out` | Square timecards → `daily_labor` (M5) | Dropped: PRD non-goal "our own time clock in v1" |
| Schedule | `shifts` | Team spec, Phase 3 (TEAM-2, TEAM-3) | Deferred, Phase 3 |
| Shift requests | `shift-requests` | Time off: TEAM-4, Phase 3. TEAM-5 claims and trades ([status](team-shift-coverage.md)) | Time off, open-shift claims, and trades built |
| Tasks | `tasks` | TEAM-7 (P1), after Phase 3 | Deferred, unscheduled |
| Team chat | `channels`, `channels/:id/messages` | None | Dropped: no PRD requirement |
| Ask Sous | `sous` (keyword matching) | `/app/sous`; `/v1/sous/ask[/stream]` (LLM, typed tools, evidence) | Covered |
| (no screen) audit log | `audit-log` | `audit` module (append-only, no UI) | Covered (backend) |
| (no screen) health | `health` | `/healthz`, `/readyz` | Covered |

### Files

**Delete now, independent of the rule.** These are dead or untracked:

| Path | Why |
| --- | --- |
| `apps/web/public/product.js` | Dead. Next.js owns `/app` (`src/app/app/page.tsx`), so `/index.html` is never served there and `app.js` never calls `ChefOSProduct.boot()`. It is still downloaded (51 KB) on every marketing page |
| `<script src="/product.js?v=2">` in `apps/web/public/index.html` | Goes with the file |
| `if(r==="app"){window.ChefOSProduct.boot();return;}` in `apps/web/public/app.js` | Unreachable. Remove it so a future rewrite of `/app` can't throw a `ReferenceError` |
| `__pycache__/`, `dist/` (empty), `data/chefos.sqlite3` | Untracked local artifacts from the prototype. Delete from disk; nothing to commit |

**Delete as one unit, after tagging `prototype-v0`:** `server.py`, `product.js`, `index.html`, `app.js`, `styles.css`, `assets/` (28 files). Each root file matches its `apps/web/public/` copy byte for byte, except `index.html`, which differs only in leading-slash paths. Also drop `.chefos/` and `data/` from `.gitignore` (prototype-only). Keep `*.sqlite*` and `*.db`.

### Doc edits

- **README.md:** delete "Run the prototype" (including "What the prototype covers" and "Security boundary"). Delete the "Prototype workspace" status row. Change the Commerce row to note Phase 2 is complete.
- **docs/architecture.md:** replace the "Current." paragraph and table (it still says "the production services do not exist yet") with the real layout: `apps/api`, `apps/web` (Next.js plus `public/` static marketing site), `infra/`, `docs/`. Replace the prototype paragraph with one line: retired, reference at tag `prototype-v0`.
- **docs/roadmap.md:** Current state: Phase 2 done. Replace the "Prototype workspace" bullet with the tag and the worktree run command.
- **docs/adr/0002-production-stack.md:** add one dated line under "Prototype retirement": "Done 2026-10-xx; see tag `prototype-v0`." No other edits.
- **apps/web/next.config.ts:** no change. Its comment already points at `public/app.js`.

## Verification

- `git tag prototype-v0` is pushed. The worktree command above starts the prototype and http://127.0.0.1:8000/app signs in.
- `cd apps/web && npm run build` passes. With `npm run dev`, all 13 marketing routes return 200, the page source has no `product.js`, `/product.js` returns 404, the browser console on `/` and `/team` is clean, and `/app` still renders the React workspace.
- `git grep -n -e server.py -e product.js -e ':8000'` matches only ADRs and older specs (`platform-foundation.md`, `integration-layer.md`, `inventory.md`), plus the tag note.
- CI is green. No job references the prototype (checked: `ci.yml` covers only `apps/api`, `apps/web` and `make smoke`).

## Risks

- **Losing the UX reference for Team.** No Team spec exists yet, and the prototype's schedule and team screens are the only visual reference. Mitigation: the tag and the run command go in the roadmap. Optionally, screenshot the Schedule, Team and Tasks tabs into the Team spec when it is written.
- **Hidden references.** A stale link to `/app#schedule` or similar from marketing copy. The marketing "Workspace" link goes to `/app`, which is fine. The grep above catches the rest.
- **Local data.** `~/.chefos/chefos.sqlite3` and `data/` hold only seeded fake data. Deleting them by hand is safe.

## Milestones

1. **Safe cleanup** (any time): remove `apps/web/public/product.js`, its script tag, and the `/app` boot branch. Delete local `__pycache__/`, `dist/` and `data/`.
2. **Tag:** `prototype-v0` on the commit before removal.
3. **Remove:** delete the root prototype unit and tidy `.gitignore`.
4. **Docs:** README, architecture, roadmap, ADR 0002 note. Mark Phase 2 done.

Milestones 1, 3 and 4 can ship as one PR. Milestone 2 must land first.
