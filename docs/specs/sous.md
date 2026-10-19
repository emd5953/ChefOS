# Sous: Operations Assistant

**Status:** Proposed · **Owner:** TBD · **Last updated:** 2026-10-19
**Related:** [ADR 0003: Read-only first](../adr/0003-sous-read-only-first.md), [Integration layer](integration-layer.md), [Architecture](../architecture.md)

## Problem

Managers have operating questions that cut across modules: "Why was labor high Tuesday?", "What's below par for Friday?", "How did brunch do vs last month?" Answering them today means opening several reports and doing arithmetic by hand. A natural-language assistant can help, but only if its answers are correct, scoped to what the user may see, and honest about missing or stale data.

## Goals

- Answer operations questions from authorized, current, traceable data.
- Return evidence with every answer: values, units, time window, source references, and data freshness.
- Keep authorization, calculations, and writes in deterministic service code. The model interprets intent and explains results. It doesn't decide scope, compute metrics, or touch the database.
- Later, propose changes as drafts that a person approves ([ADR 0003](../adr/0003-sous-read-only-first.md)).

## Non-goals (v1)

- Writes of any kind (draft-and-approve comes in a later milestone).
- General-purpose SQL, code execution, or open web access for the model.
- Vector search or multi-agent orchestration without a measured need.
- Payroll, payments, or HR actions.

## Approach

A tool-calling assistant over a small, allowlisted registry of typed read tools. Each tool wraps an existing domain service. The orchestrator classifies intent, validates parameters against the user's scope, calls tools, and asks the model to explain the returned facts. A validator checks that every number in the answer traces to a tool result.

**Alternative considered: text-to-SQL over a read replica.** Rejected. It moves authorization and metric definitions into model output. Errors are silent, and tenant isolation then depends on generated SQL.

## Design

### Request flow

```mermaid
sequenceDiagram
    actor Manager
    participant Client
    participant API as API / auth
    participant Sous as Orchestrator
    participant Policy as Policy + schema validation
    participant Tools as Typed tools
    participant Domain as Domain services
    Manager->>Client: Question
    Client->>API: Question + optional view context
    API->>API: Authenticate; derive org, locations, role; request ID
    API->>Sous: Scoped request
    Sous->>Policy: Classify intent, validate params
    Policy-->>Sous: Allowed tools + validated args
    Sous->>Tools: Invoke
    Tools->>Tools: Recheck authorization and scope
    Tools->>Domain: Scoped service call
    Domain-->>Tools: Records / metrics + freshness
    Tools-->>Sous: Typed facts
    Sous->>Policy: Validate grounded response
    Policy-->>API: Answer + evidence + limitations
    API-->>Client: Response
```

### Tool registry

Each tool has a versioned input/output schema, a required permission, scope rules, a timeout, a result cap, and an error contract. Initial set:

```
get_sales_summary(location_ids, from, to, group_by?: day|channel)  → SalesFacts
get_labor_summary(location_ids, from, to)                          → LaborFacts
compare_periods(metric, location_ids, period_a, period_b)           → ComparisonFacts
find_items_below_par(location_ids)                                  → ItemFacts
get_waste_summary(location_ids, from, to, group_by?)                → WasteFacts
get_schedule(location_ids, from, to)                                → ScheduleFacts
```

Every `*Facts` result includes `period`, `timezone`, `unit`/`currency`, `source_refs`, and `freshness: DataFreshness[]` (from the [integration layer](integration-layer.md)).

### Response contract

```
SousAnswer {
  answer: str
  period: { from: date, to: date, timezone: str }
  evidence: [{ label, value, unit, source_refs: [str], as_of: datetime }]
  limitations: [str]          # stale data, partial results, unmapped items
  request_id: str
}
```

### Write path (later milestone)

```
POST /v1/sous/drafts               → Draft { id, intent, targets, diff, impact, expires_at }
POST /v1/sous/drafts/{id}/approve  ← Idempotency-Key → CommandResult
```

On approval, the command endpoint reauthenticates the approver, checks draft expiry and that target records haven't changed since the draft, validates business rules, commits atomically, and writes an audit event (actor, approver, draft ID, changed fields, result). Generating or displaying a draft never commits anything.

## Behavior

| Case | Behavior |
| --- | --- |
| Clear question, data fresh | Answer with evidence and as-of times |
| Ambiguous ("how did we do?") | Ask one clarifying question (period, location) |
| Outside user's location scope | Refuse, and say which locations the user can ask about |
| Unsupported intent | Say what Sous can answer; don't improvise |
| Data `stale` or `failing` | Answer with an explicit limitation naming the stream and as-of time |
| Partial tool failure | Return what succeeded, list what failed; never combine mismatched periods |
| Number in model output not found in tool results | Validator rejects; retry once, then return the evidence table without prose |
| Instruction-like text inside imported data | Treated as data; tools return typed fields, never free text that is fed back as instructions |

## Verification

- Versioned evaluation set of restaurant questions with expected intent, tools, values, evidence, and limitations. Runs in CI on every prompt, model, or tool change.
- Metrics: answer correctness, evidence coverage, authorization violations (target 0), correct clarify/refuse rate, p95 latency, cost per answer.
- Tenant isolation and prompt-injection test cases using synthetic data.
- For writes: draft correctness, approval enforcement, idempotency, audit completeness.

## Operations

- Per-request and per-tool timeouts. Retries only for idempotent reads. Per-user rate limits.
- Traces carry request ID, tool name and version, model and prompt version, latency, tokens, and status. Full prompts and tool results are not logged by default.
- Prompts, model config, tool schemas, and metric definitions are versioned and rolled out behind flags with rollback.

## Risks / open questions

- Metric definitions must be canonical before Sous ships. Sous inherits any ambiguity in "net sales" or "labor %".
- The model runs locally via Ollama behind an `LLMProvider` interface ($0, and employee data never leaves the machine). Local models are weaker at tool calling. The evaluation set measures this, and a hosted provider is a config swap if the constraint ever lifts.
- Answer quality on sparse data (new locations, partial backfill) needs specific evaluation cases.

## Milestones

1. Canonical metrics, response schema, scope rules, and three read tools (sales, labor, compare).
2. Scoped orchestration with evidence-bearing responses and the validator.
3. Tracing, rate limits, timeouts, and the evaluation set in CI.
4. Read-only demo run against seeded data. Measure unsupported questions, freshness limitations, evidence quality, and latency.
5. Draft-and-approve for single shift add/adjust.
6. Expand tools based on observed questions. Daily briefing.
