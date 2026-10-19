# Technology and Architecture Notes

This document separates public evidence about ChefOS, architectural implications of the product it describes, and the implementation in this reconstruction. Public product pages describe intended capabilities; they do not reveal the production system's internal technology choices or prove every advertised capability is live.

## ChefOS product model: public evidence

ChefOS presents itself as a restaurant operating system from Counter Labs. It is designed to sit alongside a restaurant's existing POS, payroll, accounting, supplier, scheduling, delivery, reservation, and communications systems, then bring operational data and workflows into one product. The site describes 48+ connectors, a documented REST API and webhooks, and says it does not replace the POS.

The product is broader than a set of record-entry screens. Its public pages describe connected workflows across:

- **Commerce:** importing POS transactions and item detail; sales, channel, and location reporting; period comparisons; forecasts; and food-cost, labor, prime-cost, and gross-profit context.
- **Inventory:** location and storage-area counts; item, pack, and unit conversions; pars; purchasing, receiving, transfers, invoices/AP, recipe costing, waste, and line checks.
- **Team:** employee and position records; demand-aware schedules; time, availability, time-off, shift trades, open shifts, tasks, alerts, and approvals.
- **Shared operations and AI:** operational data is described as shared across modules. Sous is presented as answering natural-language questions with numbers, charts, and recommendations, producing briefings, and helping build schedules.

These descriptions imply a domain-oriented restaurant product with cross-module workflows and a shared operational model. For example, a vendor invoice or item-cost change can affect inventory and recipe cost; sales history can inform demand context for schedules; waste and yield can inform food-cost analysis. This is an architectural interpretation of the described workflows, not a claim about the internal implementation.

## ChefOS: observed implementation details and unknowns

### Publicly observable technical evidence

- Captured public-site assets include `/_next/static/` paths, Turbopack-named chunks, and Next Flight payload scripts. This strongly indicates that the **marketing site** uses Next.js; it does not establish the authenticated product's frontend framework.
- The integrations page advertises a REST API, webhooks, API access, and a sample sales-ingest request.
- The site links to an iOS app and a status page.
- Product pages show illustrative workspace screens. Treat screenshots, example metrics, connector counts, and feature descriptions as vendor-provided representations, not runtime verification.

### Not established by public evidence

No evidence here establishes the authenticated app's frontend or backend framework, database or warehouse, cloud provider, deployment model, tenant-isolation design, connector implementation or sync guarantees, event/workflow system, analytics engine, AI provider or grounding/evaluation design, mobile implementation, or production API behavior. The site supports the stated product model and workflow scope, but phrases such as “shared data layer” do not reveal how that layer is built.

## Proposed stack for a production ChefOS-style product

This is a concrete implementation proposal for the product scope described above. It is **not** a claim about ChefOS's production stack and does not change the current local prototype.

| Layer | Proposed technology | Role |
| --- | --- | --- |
| Web product | Next.js, React, TypeScript | Restaurant manager and employee workspace. The public marketing site appears to use Next.js, but that does not prove the authenticated product does. |
| Mobile | React Native, TypeScript | Shared mobile workflows for floor and kitchen staff, if native app requirements support code sharing with web. The site's iOS link alone does not identify its implementation. |
| API and domain logic | Python, FastAPI, Pydantic | Versioned REST API, validation, auth/authorization, and restaurant domain services. Keep it as a modular monolith initially, organized around Commerce, Inventory, Team, and integrations. |
| Primary database | PostgreSQL | Transactional source of truth for organizations, locations, users, shifts, sales details, inventory movements, invoices, recipes, permissions, and workflow state. Enforce tenant and location scope in service access patterns and database constraints. |
| Background workflows | Temporal with Python workers | Durable POS/payroll/accounting syncs, invoice capture and matching, sales imports, exports, retries, and schedule-generation jobs. Use idempotency keys and visible run status. |
| File storage | S3-compatible object storage | Original invoices, exports, and other source documents, with database records for ownership, retention, processing state, and provenance. |
| Analytics | PostgreSQL read models initially; dbt plus a warehouse only when justified | Keep operational calculations close to domain logic at first. Add dbt when there is a distinct analytical warehouse and repeated SQL transformations; dbt does not replace the API, domain rules, or workflow workers. |
| AI boundary | Python service layer calling model providers through constrained tools | Sous can query authorized metric/report services and propose supported actions. Keep arithmetic and business rules deterministic where possible; return evidence; isolate writes behind draft, approval, and command handling. See [Sous production architecture](sous-architecture.md). |
| Infrastructure | AWS, Docker, Terraform | Run API and worker containers, managed PostgreSQL, object storage, secrets, and networking. Use repeatable infrastructure; do not assume a separate customer AWS account is required without a security or contractual need. |
| Observability | OpenTelemetry-compatible traces, structured logs, metrics, error reporting | Track API health, integration freshness, workflow failures, queue age, and data reconciliation issues without logging sensitive payloads. |

The default deployment should be a **multi-tenant SaaS with logical tenant isolation** in a shared service, unless customer, regulatory, or contractual requirements call for dedicated environments. Every request, background job, report, and AI tool must carry and enforce organization/location scope. A separately deployed service is justified only by a real scaling, isolation, or ownership boundary.

This stack deliberately keeps the initial system operationally simple: one domain-oriented API, one PostgreSQL source of truth, and durable workers for external and long-running work. Revisit the warehouse, caches, service splits, and per-customer deployment model against measured scale and customer requirements rather than adopting them by default.

## Architectural implications for a reconstruction

If implementing the product scope described publicly, a useful conceptual model is a restaurant-specific operational platform, not a generic document-ingestion system. Likely shared concepts include organization/group, location, business date, employee, position, shift, sales transaction and item, inventory item, vendor, purchase pack, count, recipe, waste, and channel. Cross-module calculations and workflows should use consistent identities, units, location context, and time ranges.

The integration boundary would need to accommodate inbound data from POS and other restaurant systems, outbound actions such as payroll exports, and an API/webhook surface. Operational records need to remain traceable to their source where useful (for example, POS checks or invoices). Long-running imports, invoice capture, and sync retries may call for background jobs, but the public site does not confirm any particular queue or workflow technology.

Sous would need permission-aware access to the same operational facts used by reporting, plus clear handling for calculations and actions. A production-oriented proposal is documented in [Sous production architecture](sous-architecture.md): authenticated server-derived scope; intent and parameter validation; allowlisted typed tools; domain-service queries with repeated authorization; evidence-bearing results; schema/evidence validation; traceable responses; and a separate draft/approval/command path for writes. This is a design recommendation, not observed ChefOS behavior. Public marketing does not establish whether the real product uses SQL, retrieval, tools, an LLM, deterministic calculations, or a combination. Do not infer its production architecture from the local deterministic Sous prototype.

Record any implementation choice made without product access as an explicit local design decision. Keep inferred architecture separate from observed ChefOS behavior, and do not treat a plausible schema or service diagram as reverse-engineered fact.

## This repository: current implementation

This repo reconstructs the public marketing site and contains an experimental local workspace prototype. It is not a production ChefOS backend and is not feature parity with the product described above.

- `index.html`, `app.js`, and `styles.css` implement the public-facing site reconstruction.
- `product.js` implements a local workspace interface.
- `server.py` is a Python standard-library HTTP server with a same-origin JSON API and SQLite persistence.
- The prototype stores all workspace organizations in a local SQLite database file outside the web root by default. API queries are logically scoped by organization; this is not evidence of ChefOS's tenant-isolation model.
- The prototype implements basic restaurant records and CRUD-style workflows, plus a restricted sales-ingestion API key. It has no live third-party connectors, document/invoice ingestion pipeline, distributed workers, cloud provisioning, or production deployment architecture.
- Its Sous endpoint uses deterministic local queries over prototype records. It does not establish or reproduce ChefOS's advertised AI assistant.
- The prototype does not implement the proposed production orchestration, model/tool boundary, evidence schema, telemetry/evaluation pipeline, or approval-backed write path described in [Sous production architecture](sous-architecture.md).
- The server uses Python's standard-library `http.server` and `ThreadingHTTPServer`, suitable for local development only.

The current stack was selected to make a runnable prototype, not derived from analysis of ChefOS's private architecture. See [README](../README.md), the [product spec](spec.md), and the [build plan](build_plan.md) for implementation scope and gaps.
