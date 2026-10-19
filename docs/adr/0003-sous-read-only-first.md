# ADR 0003: Sous starts read-only; writes go through draft and approval

**Status:** Accepted · **Date:** 2026-10-19

## Context

Sous is the platform's operations assistant. Managers ask questions in natural language ("What was labor % last week?") and, eventually, ask for changes ("Add a line cook to Friday dinner"). A model that can change schedules, orders, or inventory directly can cause real operational and payroll harm through misread intent, prompt injection from imported data, or stale context.

## Decision

1. Sous ships **read-only first**. It answers from typed, permission-checked domain tools and returns evidence, time window, and data freshness with every answer.
2. Writes are a **separate command path**, never a side effect of answering. Sous produces a draft (target records, before/after diff, impact summary). An authorized person explicitly approves it. The command endpoint then rechecks permissions and current state, commits idempotently, and writes an audit event.
3. The first write workflow is single shift add/adjust: bounded, easy to show as a diff, and easy to reverse. Full schedule generation follows.
4. Payroll, payments, terminations, and permission changes are out of scope for the general approval mechanism and need their own product policy.

## Consequences

- Early Sous value depends on data quality and freshness, which ties it to the [integration layer](../specs/integration-layer.md).
- Every write capability needs a draft renderer, a command handler, and audit coverage. That costs more than direct tool writes and is intentional.
- Identity and scope always come from the authenticated session, never from model output.

Full design: [Sous spec](../specs/sous.md).
