# ADR 0002: Production stack, and the prototype as a sandbox

**Status:** Accepted · **Date:** 2026-10-19

## Context

The repository contains a runnable prototype: `server.py` (Python standard library HTTP server, SQLite) and `product.js` (a vanilla JS workspace). It was built to explore screens and workflows quickly with zero dependencies. It runs on a single process, its schema is shaped around seeded data, and it has no background workers, so it can't run connectors, backfills, or retries.

We need a production architecture that supports durable integrations, multi-tenant isolation, and an AI assistant with typed tools.

## Decision

Build production services on this stack:

| Layer | Choice |
| --- | --- |
| Web | Next.js, React, TypeScript |
| Mobile | React Native, TypeScript |
| API / domain | Python, FastAPI, Pydantic. Modular monolith organized by module: commerce, inventory, team, integrations, sous |
| Database | PostgreSQL with row-level security as defense in depth |
| Workflows | Temporal with Python workers (syncs, backfills, imports, exports, schedule generation) |
| Files | S3-compatible object storage |
| Analytics | PostgreSQL read models; add a warehouse and dbt only when justified by measured need |
| Infra | AWS, Docker, Terraform |
| Observability | OpenTelemetry traces, structured logs, metrics, error reporting |

Deployment is multi-tenant SaaS with logical isolation. Every request, job, report, and assistant tool carries and enforces organization and location scope.

The stdlib prototype stays as a **UI and workflow sandbox**. It is not ported, extended with integrations, or deployed. New production capabilities, starting with the [integration layer](../specs/integration-layer.md), are built only on the production stack.

## Consequences

- One consistent production architecture; no throwaway connector code in the prototype.
- The prototype and production diverge. Prototype screens are references for UX, not for data model or API shape.
- Temporal adds operational weight compared with a simple job queue. We accept it for durable retries, long backfills, and visible workflow state, which integrations need from day one.
- A separately deployed service is justified only by a real scaling, isolation, or ownership boundary, not by module lines.
