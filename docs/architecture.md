# Architecture

**Status:** Draft · **Last updated:** 2026-10-19
**Related:** [PRD](prd.md), [ADR 0002: Production stack](adr/0002-production-stack.md), [Integration layer](specs/integration-layer.md), [Sous](specs/sous.md)

## Overview

A multi-tenant SaaS design: one modular-monolith API, one PostgreSQL source of truth, and durable workers for integrations and long-running jobs. External systems feed a shared canonical model, and every module and Sous reads from that model.

It runs entirely locally under Docker Compose at zero cost ([ADR 0002](adr/0002-production-stack.md)). Cloud services are replaced by local equivalents behind the same interfaces.

```mermaid
flowchart LR
    subgraph External
      POS[POS: Square]
      PAY[Payroll / accounting / suppliers - later]
      PUSH[Third-party push clients]
    end

    subgraph Clients
      WEB[Web app - Next.js]
      MOB[Mobile app - React Native]
    end

    subgraph Platform
      API[API - FastAPI modular monolith]
      WK[Temporal workers]
      DB[(PostgreSQL)]
      S3[(MinIO object storage)]
      LLM[Ollama - local LLM]
    end

    POS -- OAuth pull --> WK
    POS -- webhooks --> API
    PUSH -- ingest API --> API
    PAY -.-> WK
    API --> WK
    WK --> DB
    WK --> S3
    API --> DB
    WEB --> API
    MOB --> API
    API --> LLM
```

## Modules

The API is one deployable, organized by module. Modules talk through service interfaces and domain events, not each other's tables.

| Module | Owns |
| --- | --- |
| **identity** | Organizations, locations, users, roles, sessions, API keys |
| **integrations** | Connections, sync state, raw records, identity mapping, freshness ([spec](specs/integration-layer.md)) |
| **commerce** | Sales orders and lines, menu items, daily sales read models, reports |
| **team** | Employees, positions, schedules, shifts, availability, time off, trades, timecards, tasks |
| **inventory** | Items, packs and units, pars, counts, suppliers, purchase orders, receiving, recipes, waste, transfers |
| **sous** | Assistant orchestration, tool registry, drafts ([spec](specs/sous.md)) |
| **audit** | Append-only audit events |

## Data flow

1. **Ingest.** The integrations module pulls from connected systems on a schedule, takes webhooks as triggers, and accepts pushes. Raw payloads are stored before any transformation.
2. **Normalize.** Provider-specific normalizers map raw records onto canonical entities owned by commerce and team. External IDs resolve through `external_refs`. Records that can't be resolved raise mapping issues instead of being dropped.
3. **Derive.** Read models (`daily_sales`, `daily_labor`) are rebuilt for each affected location and business date. Domain events notify interested modules.
4. **Serve.** Module APIs serve the web and mobile clients. Any response built on imported data carries a `DataFreshness` block.
5. **Assist.** Sous calls the same module services through typed tools and carries their freshness through to its answers.

## Core domain model

```
Organization 1─* Location 1─* {Employee, Shift, Timecard, SalesOrder, InventoryLevel, Count, PurchaseOrder, WasteEntry}
Organization 1─* {User, Supplier, Item, Recipe, MenuItem, IntegrationConnection}
MenuItem *─0..1 Recipe *─* Item (via RecipeIngredient, with units)
SalesOrder 1─* SalesOrderLine *─0..1 MenuItemVariant
Employee 0..1─1 User      (employees may not have logins)
IntegrationConnection 1─* LocationLink ─1 Location
ExternalRef: (connection, entity_type, external_id) → canonical entity
```

Conventions:

- IDs are UUIDv7. Money is integer minor units plus currency. Times are `timestamptz` (stored in UTC).
- **Business date** is a first-class column on operational records, computed from the location's timezone and day-start time (default 04:00).
- Records from external systems keep `source` and `source_version`.

## Tenancy, auth, and authorization

- Every table carries `org_id`. Location-scoped tables also carry `location_id`.
- Scope is derived from the authenticated session or API key on every request. It is never accepted from request bodies or model output.
- Enforcement happens twice: in the service layer (explicit scope parameters on every query) and in PostgreSQL row-level security keyed on a per-transaction `app.org_id` setting.
- Background jobs carry org scope in their workflow input and set it on every DB transaction.
- Web sessions use HttpOnly SameSite cookies plus CSRF tokens. Mobile uses short-lived access tokens plus refresh tokens. Integrations use scoped API keys (hashed at rest, shown once).

## Tech stack

See [ADR 0002](adr/0002-production-stack.md) for the decision and rationale.

| Layer | Technology | Local runtime |
| --- | --- | --- |
| Web | Next.js, React, TypeScript | `next dev` |
| Mobile | React Native (Expo), TypeScript | Expo Go / simulator |
| API | Python, FastAPI, Pydantic | `uvicorn` container |
| Database | PostgreSQL (RLS enabled) | `postgres` container |
| Workflows | Temporal, Python workers | Temporal dev server container |
| Files | S3 API | MinIO container |
| Secrets | Envelope encryption via `KeyProvider` | AES-GCM key from `.env` |
| LLM | `LLMProvider` interface | Ollama |
| Observability | OpenTelemetry traces, structured logs, metrics | OTel Collector + Jaeger containers |
| Orchestration | Docker Compose | `docker compose up` |

## Observability

- Traces span API → worker → external call, correlated by request ID or workflow ID.
- Key metrics: API latency and error rate, per-stream sync lag (`now − data_as_of`), sync failures by error class, quarantine rate, webhook verification failures, queue backlog.
- Alerts: stream `failing`, connection `needs_reauth`, quarantine rate > 0.1%, cross-tenant invariant violations.
- Logs never contain OAuth tokens, raw payloads, or employee PII.

## Repository layout

Target (created in Phase 0):

```
apps/api/        FastAPI service and Temporal workers
apps/web/        Next.js app (workspace + marketing site)
apps/mobile/     Expo app
infra/           docker-compose.yml, OTel collector config, seed scripts
docs/
```

Current. The production services do not exist yet. Today's repository contains:

| Path | What it is |
| --- | --- |
| `index.html`, `app.js`, `styles.css`, `assets/` | Static reproduction of the ChefOS public marketing site, kept as design reference |
| `server.py` | Prototype API: Python stdlib HTTP server, SQLite, session auth, CSRF, org-scoped queries |
| `product.js` | Prototype workspace UI (vanilla JS) served at `/app` |
| `docs/` | Product and engineering documentation |

The prototype is a UX reference only ([ADR 0002](adr/0002-production-stack.md)). Its schema and API are not the production design. It's deleted once `apps/web` serves the marketing site and equivalent screens (end of Phase 2). The marketing site moves into `apps/web`.
