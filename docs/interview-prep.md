# Interview prep

**Last updated:** 2026-10-20
**Related:** [Sous spec](specs/sous.md), [Architecture](architecture.md)

Engineering topics this codebase demonstrates, each mapped to what we actually built, plus the gaps a production Sous would still need to close. Use it to answer "how did you handle X, and what was the trade-off?" with a concrete story instead of a definition.

## Topics

### AI and LLM system design

| Topic | What we did |
|---|---|
| Tool calling (function calling) | The model picks one of five typed read tools. Code does all data access and math. |
| Grounding and hallucination control | The validator pulls every dollar amount, percent and count from the answer and checks each against the tool results, with units. One retry, then the evidence table only. |
| Model vs. system of record | We rejected text-to-SQL: tenant isolation would depend on the model writing the right query. |
| Deterministic guardrails around a non-deterministic component | Authorization, metrics, date ranges (precomputed in the prompt) and evidence are all code. |
| Prompt engineering and versioning | `PROMPT_VERSION` (`sous-prompt-4`) is recorded on every answer. |
| Evals | 40-case golden set with live-computed expectations; correctness, grounding, kind accuracy, authorization violations, latency (`make sous-eval`). Found the Manhattan/Brooklyn and date-range bugs. |
| Prompt injection | User text and POS text are data inside tool results; injection cases are in the eval and tests. Proposed next layer: [injection hardening](specs/sous-injection-hardening.md). |
| Model abstraction (provider pattern) | `LLMProvider` interface: Ollama locally, `FakeProvider` in tests, a hosted model is a config swap. |

### Distributed systems and resilience

| Topic | What we did |
|---|---|
| Circuit breaker (closed / open / half-open) | Opens after 3 consecutive model failures, fails fast for 30 s, then lets one trial call through. |
| Graceful degradation and fallbacks | When the model is down, simple period questions get a fixed-template answer from the same tool, labeled "Quick answer · assistant offline". Anything else gets a clean 503. |
| Timeouts | 3 s connect timeout separate from the 90 s read timeout; caps of 4 model rounds and 6 tool calls. |
| Fail fast vs. retry | Retry only safe-to-repeat reads; an open breaker returns 503 immediately. |
| Bulkheads and resource isolation | One slow feature (Sous) must not starve the rest of the app; see connection pooling below. |
| Cancellation propagation | The Stop button / client disconnect cancels the model call on the server. |

### Databases

| Topic | What we did |
|---|---|
| Connection pooling and pool exhaustion | Pool is 5 + 10 overflow with a 30 s wait. Holding a connection for a whole Sous question (up to 90 s) would let 15 users freeze every page. |
| Transaction scope | Never hold a connection, lock or transaction across a slow external call. Each tool call now opens a short session (~5–20 ms). A test asserts zero idle-in-transaction connections while the model "thinks". |
| Row-level security and multi-tenancy | `SET LOCAL app.org_id`; the `chefos_app` role has no BYPASSRLS. |
| Savepoints | A failed tool doesn't poison the outer transaction. |

### Real-time and networking

| Topic | What we did |
|---|---|
| SSE vs. WebSockets vs. long polling | One-way progress events fit SSE (`POST /v1/sous/ask/stream`). |
| Streaming through proxies | Bug: the Next.js proxy gzipped the stream and buffered it until the end (curl looked fine because it doesn't ask for gzip). Fix: `Cache-Control: no-cache, no-transform`. Also `X-Accel-Buffering` for nginx. |
| Heartbeats / keepalives | `: ping` every 10 s so proxies don't drop quiet connections. |
| Backpressure and partial responses | Steps stream; the answer text doesn't, because it must pass validation first. |

### Rate limiting and abuse prevention

| Topic | What we did |
|---|---|
| Algorithms | Fixed window, sliding window, token bucket. Sous uses 20 questions/min per user. |
| Distributed rate limiting | Ours is an in-memory dict: resets on restart, counts separately per process (4 workers → 80/min), and grows forever. Fix: Redis (`INCR` + `EXPIRE` or a sliding-window script) or a Postgres table. |
| Login throttling / credential stuffing | Postgres-backed login throttle (commit 708bec9). |

### API design

| Topic | What we did |
|---|---|
| Error contracts | problem+json (RFC 9457) with stable codes: 503 `sous_unavailable`, 429 rate limited. |
| Errors before vs. during a stream | Before the stream starts, return an HTTP error; once it starts, send an `error` event. |
| Backward-compatible changes | The new `mode` field defaults to `"model"`. |

### Observability

| Topic | What we did |
|---|---|
| Distributed tracing | OpenTelemetry spans per question and per tool call. Gotcha: an async generator can't safely use `start_as_current_span`, so we start the span and pass its context explicitly. |
| Privacy in logs | Prompts and tool results aren't logged by default. |
| SLO-style metrics | p50/p95 latency, error rate, breaker state on `/status`. |

### Testing

| Topic | What we did |
|---|---|
| Test doubles | Fake Square client, fake LLM provider, injectable clocks for the breaker. |
| Testing invariants, not outputs | "No transaction held while the model thinks." |
| Deterministic tests for non-deterministic systems | Scripted model in CI; real model in evals. |

### Security

| Topic | What we did |
|---|---|
| Authorization in code, not in the model | Location access is checked before Sous runs; tools take no location argument. |
| CSRF and session cookies | Every Sous request needs both. |
| Least privilege | App DB role can't bypass RLS; v1 has no write tools at all. |

## What production would still need

Ordered by what breaks first under real use. Struck-through items are done.

### 1. Correctness

- **Number-to-label grounding.** The validator checks a number exists in the results, not that it's attached to the right label. Sous once said "decreased by 0.7%" when the data meant 0.7 *points*, and it could swap this week's and last week's totals. Build: require numbers near a matching label, or have the model emit structured claims (`{metric, period, value}`) that code checks and renders.
- **Server-stored conversations.** The browser sends prior turns, so a client can fake "the assistant said revenue was $1M". Build: `sous_conversations` and `sous_messages` tables with RLS; the client sends only a conversation id and the new question.
- **Statistically meaningful evals.** 39/40 is one run, one model, temperature 0. Build: 200+ cases with several phrasings per intent, repeated runs with variance, results per model and prompt version.

### 2. Reliability under load

- ~~Hold a DB connection only per tool call~~ (v1.1).
- ~~Streaming progress over SSE~~ (v1.1).
- ~~Circuit breaker and deterministic fallback~~ (v1.1).
- **Model concurrency.** Ollama serves about one or two requests at once; ten users means 30–60 s queues. Build: a visible request queue or background job, and in production a hosted model or vLLM behind `LLMProvider`.
- **Shared rate limit and breaker.** Both are per-process memory. Move to Redis or Postgres before running a second API process.

### 3. Observability and learning from use

- **Answer logging with feedback.** Store question, tool calls, outcome kind, validation result, latency, model and prompt version, with retention and owner-only access. Add thumbs up/down; failed and down-voted questions become eval cases.
- **Dashboards and alerts** for validator failure rate, evidence-only rate, p95 latency and unsupported rate (e.g. evidence-only above 10% in an hour means a regression).

### 4. Release process

- **Eval gate before merge** for prompt, model or tool changes: block on a pass-rate drop or any authorization failure. CI has no model, so run a small one (qwen3 1.7B) or a local pre-merge hook.
- **Feature-flagged prompt versions** for rollback without a deploy.
- **Pin the model by digest** (`qwen3:8b` is a moving tag) and record the digest on every answer.

### 5. Product completeness

- **Coverage:** Commerce only. Labor-cause questions wait on Team; waste and food cost wait on Inventory.
- **Write actions (milestone 5):** draft → manager reviews diff and cost → approve → audited commit. Hard parts: expiring drafts, detecting the schedule changed since the draft, idempotent approval, applying the approver's permissions rather than the model's.
- **Multi-location questions:** tools that take a list of authorized locations, and validation that only allowed ones are named.
- **Daily briefing:** generated by a scheduled Temporal workflow.

### 6. Security hardening

- **Red-team suite:** injection, exfiltration and jailbreak attempts, including through POS data (item and staff names). See [injection hardening](specs/sous-injection-hardening.md).
- **Output handling:** answers render as plain text today. Rendering model markdown or links would need sanitizing (XSS, phishing).
- **Per-role tool access:** once pay data exists, a line cook mustn't be able to ask about others' wages.
- **Privacy review:** with Team data, employee names and hours enter prompts, logs and stored conversations, which need retention and access rules.

### Suggested order

1. Server-stored conversations.
2. Number-to-label grounding.
3. Answer logging with feedback, plus a larger, repeated eval set.
4. Eval gate on prompt changes and a pinned model digest.
5. Write actions, once the Team module exists.
