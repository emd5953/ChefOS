# ADR 0002: Production stack, and the prototype as a sandbox

**Status:** Accepted · **Date:** 2026-10-19 · **Revised:** 2026-10-19 (local-only, zero-cost)

## Context

This is a learning project: a local, demo-able restaurant platform built with production-grade practices. It has a hard constraint of **$0 spend**: no cloud accounts, paid APIs, or hosted services.

The repository also contains a runnable prototype: `server.py` (Python standard library HTTP server, SQLite) and `product.js` (a vanilla JS workspace). It was built to explore screens quickly with zero dependencies. It runs on a single process, its schema is shaped around seeded data, and it has no background workers, so it can't run connectors, backfills, or retries.

## Decision

Build the product on a production-grade stack that runs entirely on a developer machine via Docker Compose:

| Layer | Choice | Local, free implementation |
| --- | --- | --- |
| Web | Next.js, React, TypeScript | `next dev` |
| Mobile | React Native (Expo), TypeScript. **Amended by [ADR 0004](0004-mobile-as-responsive-web.md):** employee mobile (MOB-1) is a responsive web view in the web app; Expo is deferred | Expo Go / simulator |
| API / domain | Python, FastAPI, Pydantic. Modular monolith by module: commerce, inventory, team, integrations, sous | `uvicorn` container |
| Database | PostgreSQL with row-level security | `postgres` container |
| Workflows | Temporal with Python workers | Temporal dev server container |
| Files | S3 API | RustFS container (S3-compatible; MinIO no longer publishes images) |
| Secrets | Envelope encryption behind a `KeyProvider` interface | AES-GCM key from `.env` (KMS-compatible interface, not KMS) |
| Observability | OpenTelemetry traces, structured logs, metrics | OTel Collector + Jaeger containers |
| LLM (Sous) | Provider interface | Ollama with a local open-weights model |

Code is written as if it will be deployed: multi-tenant with org and location scope on every request, job, and tool; migrations; CI; secrets never in code. **Nothing is provisioned in the cloud.** Cloud equivalents (managed Postgres, Temporal Cloud, S3, KMS, a hosted LLM) are drop-in replacements behind the same interfaces and are out of scope.

External APIs are used only where free: Square's developer sandbox. Toast's partner API requires approval, so the Toast connector is built against recorded fixtures shaped from Toast's public API documentation.

**Prototype retirement:** the prototype is not extended. It stays runnable as a UX reference until the Next.js app serves the marketing site and covers the equivalent workspace screens (end of roadmap Phase 2). Then `server.py` and `product.js` are deleted. The marketing site moves into the Next.js app.

Done 2026-10-20; see tag `prototype-v0`.

## Consequences

- `docker compose up` brings up the whole platform. There's no cloud bill and no account setup beyond a free Square developer account.
- Interfaces (`KeyProvider`, object storage, `LLMProvider`) are mandatory, not optional abstractions: they're the seam where local and cloud implementations swap.
- Temporal adds weight compared with a simple job queue. We accept it for durable retries, long backfills, and visible workflow state, and because learning it is part of the point.
- A local LLM is weaker than hosted models. Sous evaluations will show this, and that's acceptable.
- One codebase in this repository. No separately deployed services.
