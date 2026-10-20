# ADR 0004: Employee mobile is a responsive web view, not an Expo app (for now)

**Status:** Accepted · **Date:** 2026-10-20 · **Amends:** [ADR 0002](0002-production-stack.md) (Mobile row)

## Context

ADR 0002 lists React Native (Expo) as the mobile client. Phase 3 needs MOB-1: employees see their published schedule, submit availability and time off, and get change alerts on their phone. The web app already has cookie sessions with CSRF, a typed API client, a query layer, shared types, and a design system. The API has no token auth: the architecture's "short-lived access tokens plus refresh tokens" for mobile was never built.

An Expo app for MOB-1 would need that token auth, a second UI kit, duplicated API types, and its own CI job. That is roughly double the cost of MOB-1 for the same demo, and the project's constraint is $0 and one maintainer.

## Decision

1. MOB-1 ships as a **mobile-first view inside `apps/web`** at `/my` (route group `(employee)`), with its own minimal shell: a 480 px column sized for a 390 px phone, bottom tabs (Schedule, Requests, Alerts), and a web app manifest (`start_url: /my`) so it can be added to the home screen.
2. Login sends `employee` sessions to `/my`, and `/app/*` redirects them there. Managers with an employee record get a "My schedule" link.
3. The API stays client-agnostic. `/v1/me/*` resolves the employee from the session, never from a parameter, so an Expo client can use the same endpoints later. It would need token auth added first.
4. Alerts are in-app and poll-based (`GET /v1/notifications` every 60 s while visible, and on focus). No native push and no paid delivery.
5. Offline is limited to the last successfully loaded week: the `/v1/me/week` response (the employee's own data, no rates) is kept in `localStorage` and cleared on logout. Request forms are disabled offline.

Expo stays the plan for MOB-2 (manager mobile) or later if native capabilities (push, background refresh) become worth their cost.

## Consequences

- One client, one auth scheme, one CI job. MOB-1 reuses everything the web app already has.
- No native app and no native push: "mobile app" in the demo is a home-screen web app. Alert latency is up to a minute.
- Offline shows only the last-viewed schedule, not a synced store.
- A real phone needs the Next dev server reachable over the LAN or a tunnel (Compose binds to 127.0.0.1). Devtools device mode is the default demo, and the same limitation would apply to Expo Go.
- Web Push (free, VAPID) is the natural upgrade, but requires HTTPS outside localhost.
