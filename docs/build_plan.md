# Discovery and Delivery Plan

**Status:** Proposed process, not a committed feature roadmap. The repository contains no application code or validated customer requirements. Sequence work by reducing the largest product and delivery risks first.

## Stage 1 — Select the problem

Talk to representative users and observe the current workflow. Capture user, trigger, frequency, tools, handoffs, failure cost, and workaround. Compare candidate problems by user impact, frequency, feasibility, data access, and strategic fit. Select one initial segment and one workflow; record what is excluded.

**Exit evidence:** a named user and job, current baseline, validated pain, measurable target, and an accountable product owner.

## Stage 2 — Prove data and workflow feasibility

Map the workflow end to end. Identify authoritative sources, data permissions, correction path, timing/freshness needs, integration access, edge cases, and manual fallback. Prototype the riskiest interaction or integration before building broad infrastructure.

**Exit evidence:** sample data or provider access, data mapping, permission model, failure/recovery design, and a demonstrated path through the critical workflow.

## Stage 3 — Design and ship the thin vertical slice

Build the smallest complete user journey across client, application/domain logic, persistence, and any necessary connector. Include tenant authorization, observability, audit where needed, recovery, and success instrumentation from the start. Keep unrelated modules and generalized abstractions out of scope.

**Exit evidence:** target users complete the workflow with real or representative data; acceptance criteria pass; support can diagnose and recover failures; no unresolved critical security or integrity issue.

## Stage 4 — Validate outcomes and harden

Run with a limited user cohort. Compare the agreed outcome to its baseline, inspect failures and manual workarounds, reconcile data, and measure reliability and support burden. Fix the highest-impact issues before widening access.

**Exit evidence:** users adopt the flow, outcome moves toward target, reliability objectives are met, and cost/support load is understood.

## Stage 5 — Expand deliberately

Only then decide whether to deepen the workflow, add the next user segment, integrate another system, or introduce automation/AI. Revisit architecture decisions when measured constraints or product needs trigger them. Do not expand because a competitor advertises the capability.

## Definition of ready

- User problem and outcome are supported by evidence.
- Scope, exclusions, and owner are clear.
- Data sources, authority, units/time semantics, and permissions are specified.
- Failure, retry, correction, and manual recovery behaviors are defined.
- Acceptance scenarios and measurements are testable.
- Privacy, security, operational ownership, and rollout plan are reviewed.

## Definition of done

- End-to-end acceptance criteria are met for authorized and failure cases.
- Data quality and source reconciliation are verified for the intended use.
- Logs, metrics, alerts, dashboards/runbooks, and support ownership are in place.
- Rollback or safe disablement is possible for risky changes.
- User documentation and known limitations are current.
- Post-release measurement is assigned and scheduled.

## Current unresolved product decisions

1. Initial target user and restaurant segment.
2. Highest-value first workflow and baseline metric.
3. Existing systems and data sources required for that workflow.
4. Required mobile/offline behavior and deployment constraints.
5. Data access, retention, and correction expectations.
6. Whether AI provides measurable value in the selected workflow.

These decisions should be resolved through discovery. The ChefOS research in [product-overview.md](product-overview.md) provides category context only and does not determine these answers.
