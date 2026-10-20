# Sous: Operations Assistant

**Status:** In progress (milestones 1–4) · **Owner:** TBD · **Last updated:** 2026-10-20
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

## v1 build contract (milestones 1–4)

Team and Inventory don't exist yet, so v1 tools cover Commerce and data freshness only. `find_items_below_par`, `get_waste_summary` and `get_schedule` wait for their modules. Labor % comes from the sales summary, so there's no separate labor tool.

### HTTP

```
POST /v1/sous/ask     (session + X-CSRF-Token)
  in:  { question: str (1..500 chars), location_id: uuid,
         history?: [{ role: "user" | "assistant", content: str }]  (max 10 turns) }
  out: SousAnswer
GET  /v1/sous/status  (session)
  out: { provider: "ollama" | "fake", model: str, available: bool, model_pulled: bool, prompt_version: str }
       # available = provider reachable and the configured model pulled

SousAnswer {
  kind: "answer" | "clarify" | "refuse" | "unsupported" | "evidence_only"
  answer: str                                   # markdown-free plain text
  period: { from: date, to: date, timezone: str } | null
  evidence: [{ label: str, value: number, unit: "usd_cents" | "count" | "percent" | "percent_pts" | "hours",
               source: str (tool name), as_of: datetime | null }]
  limitations: [str]
  tools_used: [{ name: str, args: object, ms: int, ok: bool }]
  model: str
  prompt_version: str
  request_id: str
}
```

Errors are problem+json like the rest of the API: 404 `not_found` if the location isn't in the user's scope (the same behavior as `/v1/commerce/*`, which doesn't reveal that a location exists), 422 validation, 429 rate limit (`code: "rate_limited"`, 20 questions/minute/user, in-memory), 503 `code: "sous_unavailable"` when the provider is unreachable.

### Tools (v1)

The API resolves and authorizes the location before Sous runs. Tools receive the `Location` and never take a location argument from the model. Each wraps an existing `app.commerce.service` function.

| Tool | Args (model-supplied) | Wraps |
| --- | --- | --- |
| `get_sales_summary` | `from`, `to`, `compare?: previous_period \| previous_year` | `service.summary` |
| `get_daily_sales` | `from`, `to` (max 92 days) | `service.daily` |
| `get_top_items` | `from`, `to`, `limit?: 1..20` | `service.items` |
| `get_today` | none | `service.today` |
| `get_data_freshness` | none | freshness for the location |

Dates are ISO `YYYY-MM-DD` business dates. Validation rejects `to < from`, ranges over 400 days (92 for `get_daily_sales`), and dates after today's business date. Validation errors and tool exceptions go back to the model as an error result (and appear as `ok: false` in `tools_used`); they are never raised. The system prompt gives the model today's business date, weekday, location timezone and the 04:00 business-day rule, so it resolves "last week" itself. Each tool returns a JSON-safe dict for the model plus deterministic `evidence` rows. The model never builds evidence.

### Orchestration

1. Build the system prompt (`PROMPT_VERSION`, metric definitions from the PRD, tool list, today's date).
2. Call the model with tools. Execute tool calls (max 4 rounds, 6 calls total), feed results back.
3. The final model text is the answer. The model signals clarify, refuse or unsupported by starting its reply with `CLARIFY:`, `REFUSE:` or `UNSUPPORTED:`. The orchestrator strips the prefix and sets `kind`. Only `answer` and `evidence_only` carry `period`, `evidence` and `limitations`. If the model is still calling tools in round 4, or answers twice without a grounded reply, the result is `evidence_only`.
4. **Validator:** every number in the answer must match a value in the tool results after formatting: money as dollars at 0 or 2 decimals, percents at 0 or 1 decimal, counts exact. A `$` figure must match a money field and a `%` figure a percent field. Dates, clock times, years, ordinals, list markers and digits inside returned item names are exempt. Shorthand such as `$52.9k` is never accepted; the prompt asks for exact figures. Tool data gives money in dollars (fields ending `_usd`) and percents in percent units, so the model doesn't convert. On failure, retry once with the offending numbers named. On a second failure, return `kind: "evidence_only"` with a deterministic one-line answer and the evidence table.
5. `limitations` is deterministic: one line for each `stale`, `failing` or `not_connected` stream touched, naming the stream and as-of time, plus any failed tool.
6. Tool results are typed fields. Free text from POS data, such as item names, is passed as data, and the system prompt says so.

### Provider

`LLMProvider` protocol: `async chat(messages, tools) -> LLMReply(content: str, tool_calls: [ToolCall(name, arguments: dict)])`.
- `OllamaProvider` calls `POST {OLLAMA_URL}/api/chat` with `stream: false`, `think: false` and native `tools`.
- `FakeProvider` replays scripted replies; tests use it.

Config:
- `SOUS_PROVIDER=ollama|fake` (default `ollama`).
- `OLLAMA_URL` (default `http://host.docker.internal:11434`).
- `SOUS_MODEL` (default `qwen3:8b`).
- `SOUS_TIMEOUT_SECONDS` (default 90).

### Evaluation

- `apps/api/evals/sous/cases.yaml` holds the questions, each with the expected `kind`, the expected tools, and expected values as references to tool-result fields. Expected values aren't hard-coded numbers, so cases stay valid as the simulated data moves with the clock.
- `make sous-eval` runs the cases against the demo org with the configured provider and prints these metrics:
  - correctness
  - evidence coverage
  - kind accuracy
  - authorization violations
  - p50/p95 latency
- CI runs the deterministic tests (tools, validator, scope, prompt injection) with `FakeProvider`. The model eval runs locally, because the model doesn't run on CI.
