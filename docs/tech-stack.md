# Engineering Design and Architecture Decisions

**Status:** No application source, dependency manifest, or deployment configuration exists in this repository as of October 17, 2026. No production stack or architecture can be inferred. This document sets design criteria and records decisions only when evidence supports them.

## Design stance

Design for the first validated workflow and its failure modes. Prefer the smallest architecture that meets its data, security, and operating requirements. A modular monolith with asynchronous workers is a reasonable starting hypothesis for a small team, not a committed choice; revisit it against workload, deployment, and team constraints before adoption.

## Logical boundaries

Regardless of specific framework, keep these responsibilities explicit:

- **Client:** presentation and local interaction; never the authority for authorization or financial calculations.
- **Application boundary:** authentication, authorization, workflow orchestration, and stable client-facing contracts.
- **Domain services:** business rules, state transitions, metric definitions, and validation.
- **Integration adapters:** provider-specific authorization, mapping, pagination, rate limits, event verification, and error translation.
- **Ingestion/reconciliation:** durable work tracking, idempotency, replay, source provenance, and correction handling.
- **Persistence:** tenant-scoped records, constraints, migrations, backups, and retention/deletion controls.
- **Analytics:** reproducible calculations separated from provider payload formats and user-interface logic.
- **AI boundary (if needed):** constrained tool interface to authorized application services; no direct unrestricted database access.
- **Operations:** logs, metrics, traces, audit records, alerts, support, and recovery procedures.

These are responsibility boundaries, not a requirement to deploy separate services.

## Data and integration invariants

1. Every business record has a clear organization and, where applicable, location scope.
2. Every externally sourced value can be traced to a provider/object and observation or ingestion time.
3. Provider IDs are not internal primary identities; mapping and merge behavior are explicit.
4. Reprocessing an event or file cannot silently create duplicate business effects.
5. A sync has durable status, bounded retries, a checkpoint, visible errors, and a safe replay path.
6. Conflicting records have domain-specific source-of-truth and resolution rules.
7. Time zone, business day, currency, quantity units, and rounding semantics are explicit.
8. Raw payload retention is limited to what is needed for support/reprocessing and follows privacy policy.

For each connector, assess customer demand, provider API access, data scope, read/write direction, sync cadence, backfill, webhooks, rate limits, availability expectations, and ongoing maintenance cost before committing to it.

## Security and operations baseline

- Enforce tenant and role checks at server-side service boundaries, including worker and AI execution contexts.
- Keep secrets out of source control and logs; use least-privilege credentials and rotation.
- Define data classification, retention, deletion, backup, restore, and incident procedures.
- Use structured logs with correlation IDs; monitor user-visible latency, sync lag, failures, queue age, and reconciliation drift.
- Alert on actionable SLO violations, not every transient event. Document owner, runbook, and escalation for production workflows.
- Version and review database changes; test restore and integration replay procedures.
- Provide health/status information that support and operators can use without exposing sensitive data.

## Stack selection record (open)

Choose only after target workflow, team expertise, hosting constraints, integration needs, expected traffic, and budget are understood. Evaluate options against:

- Developer speed and maintenance ownership.
- Transactional correctness and tenant isolation.
- Background processing and recovery semantics.
- Analytics workload and data freshness needs.
- Mobile/web requirements and offline behavior.
- Operational cost, security posture, data residency, and portability.

Record selected options, alternatives, evidence, consequences, and owner in a dated decision record. The previous mention of Next.js, NestJS, PostgreSQL, Prisma, BullMQ, Redis, and S3 was an unvalidated proposal and should not be treated as a requirement.

## Architecture decision record template

For a material choice, create `docs/decisions/NNNN-short-title.md` with:

```markdown
# NNNN: Decision title

- Status: proposed | accepted | superseded
- Date:
- Owners:

## Context and constraints
## Decision
## Alternatives considered
## Trade-offs and consequences
## Evidence and revisit trigger
```

Use ADRs for decisions costly to reverse (data ownership, tenant model, integration write authority, deployment boundaries, retention). Do not create ceremony for routine implementation choices.

## Evolution triggers

- Split a component into a separately deployed service only for independent scaling, isolation, release ownership, or fault containment that the current boundary cannot meet.
- Add a dedicated analytics store when measured reporting load or freshness requirements materially affect transactional operations.
- Introduce AI when it improves a validated task against a simpler baseline and can meet quality, privacy, latency, and cost targets.
- Add integration breadth based on customer evidence and support capacity, not competitor connector counts.
