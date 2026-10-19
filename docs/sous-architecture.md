# Sous Production Architecture (Proposal)

**Status:** proposed engineering design for a ChefOS-style production assistant. This is not reverse-engineered ChefOS behavior. Public pages advertise Sous capabilities, but the private product architecture and action-confirmation rules have not been observed. The local `/api/v1/sous` implementation is deterministic and does not implement this design.

## Goals and boundaries

- Answer restaurant operations questions from authorized, current, traceable data.
- Keep authorization, calculations, and writes in deterministic service code.
- Use the model for intent interpretation and explanation, not as an authority over tenant scope, business rules, or database access.
- Start with read-only tools. Add carefully bounded write actions only through a review and approval flow.
- Preserve a clear audit trail without retaining sensitive prompt or result content by default.

## Request and response flow

```mermaid
sequenceDiagram
    actor Manager
    participant Client as ChefOS client
    participant API as API/auth boundary
    participant Sous as Sous orchestrator
    participant Policy as Policy and schema validation
    participant Tools as Typed domain tools
    participant Domain as Domain services/data
    participant Trace as Audit/observability
    Manager->>Client: Ask operations question
    Client->>API: Question + optional view context
    API->>API: Authenticate; derive org, location, role; assign request ID
    API->>Sous: Scoped request context
    Sous->>Policy: Classify intent and validate parameters
    Policy-->>Sous: Allowed tool set and validated arguments
    Sous->>Tools: Invoke approved tools
    Tools->>Tools: Recheck authorization and scope
    Tools->>Domain: Parameterized domain query
    Domain-->>Tools: Authoritative records/metrics
    Tools-->>Sous: Facts + units, window, source IDs, freshness
    Sous->>Policy: Compose and validate grounded response
    Policy-->>API: Typed answer + evidence + limitations
    API-->>Client: Response
    Client-->>Manager: Answer with evidence links
    API->>Trace: Correlated request metadata
    Sous->>Trace: Model/tool/version/latency metadata
    Tools->>Trace: Tool outcome metadata
```

The caller supplies a question and optional non-authoritative context such as the visible date range. The API derives identity and access scope from the authenticated session. Do not accept user, organization, location, or role claims from the model. The orchestrator classifies intent, validates extracted parameters, and selects from an allowlisted tool registry. If the question is ambiguous, unsupported, outside the user's scope, or cannot be grounded, return a clear clarification, refusal, or limitation.

Tools call domain services rather than exposing a database connection to the model. Each tool validates its input, checks organization/location permissions again, uses parameterized queries, enforces result limits, and returns typed facts. Facts should carry units, currency where relevant, business date/time zone, query window, source record references, and freshness/as-of time. Arithmetic and canonical business definitions belong in reporting/domain code. The model explains the returned facts and must not manufacture missing values.

The external response should have a stable schema, for example:

```json
{
  "answer": "Labor was 2.4 percentage points above the target for the selected week.",
  "period": { "from": "2026-10-12", "to": "2026-10-18", "timezone": "America/New_York" },
  "evidence": [
    { "label": "Actual labor cost", "value": 18240, "unit": "USD", "source_refs": ["report:..."], "as_of": "2026-10-19T14:00:00Z" },
    { "label": "Net sales", "value": 76000, "unit": "USD", "source_refs": ["report:..."], "as_of": "2026-10-19T14:00:00Z" }
  ],
  "limitations": []
}
```

This is an illustrative contract, not a current API response or validated ChefOS metric definition. Define metric formulas and units centrally before shipping.

## Tool design and authorization

Create a small registry of narrow operations (for example `get_sales_summary`, `get_labor_summary`, `find_items_below_par`, `get_waste_summary`). Each has a versioned input/output schema, explicit permission requirement, scope rules, timeout, result cap, and error contract. Avoid general-purpose SQL, arbitrary code execution, unrestricted HTTP tools, or write tools exposed to the model by default.

Resolve organization and location from server-side auth context. Enforce scope in both the tool/service layer and database access pattern; consider database row-level security as defense in depth. Background jobs and cached results must also preserve tenant/location scope. Do not rely solely on prompt instructions to enforce access.

## Write and approval path

Writes use a distinct domain command API, not a side effect of answering a question:

1. A supported intent produces a validated draft with target records, before/after diff, assumptions, and impact summary.
2. The client presents the draft to an authorized person. No write is committed while merely generating or displaying the draft.
3. On explicit approval, the command endpoint reauthenticates/rechecks the approver's scope, checks draft expiry and source version/current state, validates business rules, and commits atomically where possible.
4. Use an idempotency key so retries cannot duplicate an action. Return the committed record and outcome.
5. Record actor, approver, request/draft/command IDs, changed fields, timestamps, and result in an append-only audit event. Keep the audit payload appropriately minimized.

Schedule creation is a useful first candidate for a draft workflow. Payroll, payments, or other consequential actions need their own product policy and controls; do not infer them from a general Sous approval mechanism.

## Reliability, security, and operations

- Set per-request and per-tool timeouts, bounded retries only for safe/idempotent operations, concurrency limits, and rate limits.
- Define behavior for unavailable tools and partial data. Label partial results and avoid silently combining incompatible periods or stale sources.
- Use scoped service identities, secrets management, TLS, least privilege, and redaction. Do not put credentials or unnecessary employee/customer details into model context.
- Emit a correlated trace across API, orchestrator, tools, and domain services. Capture request ID, user/org IDs in appropriately protected form, tool name/version, model/prompt version, latency, token/cost metadata, status, and error class. Do not log full prompts or sensitive records by default.
- Monitor availability, p50/p95 latency, tool errors, stale integration data, refusal/clarification rate, cost, and approved-action failures. Alert on authorization denials or cross-tenant invariant violations.
- Version prompts, model configuration, tool schemas, and metric definitions. Roll out behind feature flags/canaries and retain a rollback path.

## Quality and release gates

Maintain a versioned evaluation set of representative restaurant questions with expected intent, allowed tools, calculation results, evidence references, and expected limitations. Evaluate:

- Metric correctness and date/time-zone handling.
- Evidence coverage and faithful explanation of returned facts.
- Permission boundaries, tenant isolation, and prompt-injection resistance.
- Appropriate clarification/refusal for ambiguous, unsupported, stale, or unauthorized requests.
- Tool timeout/partial-failure behavior, latency, and model/tool cost.
- Draft correctness, approval enforcement, idempotency, and audit completeness for writes.

Use synthetic or appropriately de-identified test data. Run checks in CI and compare candidate prompt/model/tool versions before rollout. A model's fluent answer is not a correctness signal.

## Suggested implementation sequence

1. Define canonical metrics, response schemas, scope rules, and a few read-only tools.
2. Add auth-scoped orchestration and evidence-bearing responses; keep calculations in domain services.
3. Add traces, redacted audit metadata, rate limits, timeouts, and evaluations before broad exposure.
4. Pilot read-only Q&A with internal users; measure unsupported questions, data freshness, evidence quality, and cost.
5. Add one draft-and-approve workflow, such as schedule proposals, only after read-only behavior is reliable and command/audit semantics are defined.
6. Expand tool coverage based on observed user needs. Add retrieval, a warehouse, or multi-agent orchestration only when measured use cases require them.

## Mapping to this repository

The current prototype has a single deterministic handler at `/api/v1/sous`, a UI under **Workspace → Ask Sous**, and local SQLite records. It is useful as a product boundary for experimentation but has no model provider, tool registry, evidence contract, production auth infrastructure, observability pipeline, or approval-backed command path. The local helper must not be represented as production Sous or feature parity. See [technology notes](tech-stack.md), [product overview](product-overview.md), and [reconstruction spec](spec.md).
