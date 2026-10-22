# Interview prep

**Last updated:** 2026-10-22
**Related:** [Sous spec](specs/sous.md), [Architecture](architecture.md)

Engineering topics this codebase demonstrates, each mapped to what we actually built, plus the gaps a production Sous would still need to close. Use it to answer "how did you handle X, and what was the trade-off?" with a concrete story instead of a definition.

## Topics

### AI and LLM system design

| Topic | What we did |
|---|---|
| Tool calling (function calling) | The model picks one of eight typed read tools. Code does all data access and math. |
| Grounding and hallucination control | The model selects fact IDs; code renders each metric, period, entity, sign and unit. Strict validation, one retry, then the evidence table only. |
| Model vs. system of record | We rejected text-to-SQL: tenant isolation would depend on the model writing the right query. |
| Deterministic guardrails around a non-deterministic component | Authorization, metrics, date ranges (precomputed in the prompt) and evidence are all code. |
| Prompt engineering and versioning | `PROMPT_VERSION` (`sous-prompt-8`) is recorded on every answer. |
| Evals | 53-case golden set with live-computed expectations; correctness, grounding, kind accuracy, authorization violations, latency (`make sous-eval`). Found the Manhattan/Brooklyn and date-range bugs. |
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

- ~~Number-to-label grounding~~ (2026-10-22): models select current-tool fact IDs; code renders metrics, periods, signs, entities and units. No model-authored prose is displayed. Clarification/refusal/unsupported text is controlled too.
- ~~Server-stored conversations~~ (2026-10-22): private conversation/turn tables with org+user RLS, location binding, committed history, idempotent retries and fenced in-flight attempts. The browser sends IDs and the new question; nonempty client history is rejected.
- **Statistically meaningful evals.** 39/40 is one run, one model, temperature 0. Build: 200+ cases with several phrasings per intent, repeated runs with variance, results per model and prompt version.

### 2. Reliability under load

- ~~Hold a DB connection only per tool call~~ (v1.1).
- ~~Streaming progress over SSE~~ (v1.1).
- ~~Circuit breaker and deterministic fallback~~ (v1.1).
- **Model capacity.** Model calls now have a per-process admission cap (4), output/context budgets and wall-clock deadlines. Benchmark the chosen hardware and tune these limits; admission control does not establish an acceptable latency or throughput SLO.
- **Shared rate limit and breaker.** Both are per-process memory. Move to Redis or Postgres before running a second API process.

### 3. Observability and learning from use

- **Answer logging with feedback.** Store question, tool calls, outcome kind, validation result, latency, model and prompt version, with retention and owner-only access. Add thumbs up/down; failed and down-voted questions become eval cases.
- **Dashboards and alerts** for validator failure rate, evidence-only rate, p95 latency and unsupported rate (e.g. evidence-only above 10% in an hour means a regression).

### 4. Release process

- **Eval gate before merge** for prompt, model or tool changes: block on a pass-rate drop or any authorization failure. CI has no model, so run a small one (qwen3 1.7B) or a local pre-merge hook.
- **Feature-flagged prompt versions** for rollback without a deploy.
- **Pin the model by digest** (`qwen3:8b` is a moving tag) and record the digest on every answer.

### 5. Product completeness

- **Coverage:** Commerce, Team (labor, published schedule) and Inventory (items below par). Waste and food-cost questions are still unsupported.
- **Write actions (milestone 5):** draft → manager reviews diff and cost → approve → audited commit. Hard parts: expiring drafts, detecting the schedule changed since the draft, idempotent approval, applying the approver's permissions rather than the model's.
- **Multi-location questions:** tools that take a list of authorized locations, and validation that only allowed ones are named.
- **Daily briefing:** built as a deterministic, per-request view that never calls the model. Production next steps: a scheduled Temporal workflow and email delivery.

### 6. Security hardening

- **Red-team suite:** injection, exfiltration and jailbreak attempts, including through POS data (item and staff names). See [injection hardening](specs/sous-injection-hardening.md).
- **Output handling:** answers render as plain text today. Rendering model markdown or links would need sanitizing (XSS, phishing).
- **Per-role tool access:** once pay data exists, a line cook mustn't be able to ask about others' wages.
- **Privacy review:** with Team data, employee names and hours enter prompts, logs and stored conversations, which need retention and access rules.

### Suggested order

1. ~~Server-stored conversations~~ (built).
2. ~~Number-to-label grounding~~ (built).
3. Answer logging with feedback, plus a larger, repeated eval set.
4. Eval gate on prompt changes and a pinned model digest.
5. Write actions, once the Team module exists.

## Production hardening audit, 2026-10-22

Scope: Sous orchestration, provider parsing, tool registry and permissions; session/RLS
boundaries; receipt/count transactions; Square ingestion, webhooks and push ingestion;
retry/error handling and tracing. This is a code and regression-test audit, not a penetration
test, live Square certification or production load test.

### Fixed, by impact

| Priority | Failure | Fix and regression evidence |
| --- | --- | --- |
| P1 | A location-bound push key could overwrite another location's sales: synthetic IDs used only the date/record ID on an org-wide connection. | Namespace IDs by location and restrict stale-ticket deletion to the authorized location. Both daily-sales and orders regressions failed before the fix; tests also cover corrections, clearing a day, and replacement of legacy IDs without double-counting. |
| P1 | Concurrent webhook/backfill/deferred writers could read absent or outdated canonical orders before the upsert, then replace children or rebuild dates using stale state. | Take one transaction-scoped advisory lock per connection before landing/processing or replaying pending records. A real two-transaction test holds the first commit, observes the second blocked in PostgreSQL, and verifies the newest lines and both affected days. Network fetches stay outside the lock. |
| P1 | A receipt that closed its purchase order could not be retried; changed quantities/costs under an existing key were reported as successful. | Authorize, resolve the existing receipt, and compare stored lines and approval audit records before checking closed status. Tests cover simultaneous full receipts, changed payloads, one stock posting and rollback after a later line fails. No migration is needed. |
| P1 | Long assistant requests retained their initial role/session assumptions after access was revoked. | Re-authenticate and re-authorize at the HTTP result boundary before another model round or delivery. Tests revoke sessions, demote staff and remove location access, for JSON and SSE. |
| P1 | Unsupported model claims without digits passed the number validator without any successful tool. | Require a successful tool for ordinary answers, repair once and otherwise withhold prose. Failed-only tools cannot support an answer; incomplete reasoning blocks are removed. Follow-up: strict fact selections now replace free-form prose, including controlled clarification/refusal text. |
| P1 | Client-supplied assistant history could forge prior answers. | Migration 0014 adds private org+user RLS conversations, per-location binding, server-loaded completed turns, idempotent replay, overlap rejection, cancellation cleanup and fenced expired workers. |
| P2 | Malformed provider output could cause a 500 or silently convert bad arguments to a valid no-argument tool call. | Validate content/call/argument shapes and sizes; reject malformed JSON, non-object arguments and truncated generations. |
| P2 | Trickling responses, unbounded outputs and overlapping model calls could consume resources indefinitely or queue excessively. | Bound overall request/tool/model duration, input/output size, generation tokens and in-flight calls. Tests exercise trickling bytes, oversized context, timeout cleanup, cancellation and admission release. |
| P2 | A stale completion could reset the breaker or release a recovery slot belonging to a later generation. | Generation-scoped completion handling, tested with overlapping provider calls. |

### Remaining release blockers

1. **Complete live-model intent evaluation (P1).** Prompt 8's live `qwen3:8b` smoke passed
   5/5 cases; the repeated full run reached 16/106 with all completed cases passing before
   the user paused local inference to move to a 64 GB desktop. The gate remains incomplete.
   [Results and desktop command](../apps/api/evals/sous/results/2026-10-22.md) preserve runtime
   metadata and every completed case. The gate now checks complete semantic facts and fails
   on errors, unresolved expectations, fallback responses and incomplete runs. Complete both
   repeats across all 53 cases before treating model quality as a release pass.
   Retention is implemented in migration 0015: 30 days from creation after an answer, 24 hours
   for unanswered conversations, with bounded hourly cleanup that protects active requests.

2. **Push data repair and source semantics (P1).** Existing cross-location collisions may
   already have lost canonical records. Reconcile affected locations with the source and
   re-push with new idempotency keys; automatic inference of overwritten totals is unsafe.
   Daily totals and individual-order pushes can also overlap and double-count the same sales;
   a location needs an enforced ingestion mode or explicit reconciliation rules.
3. **Durable integration dispatch (P1).** Webhook receipt insertion and Temporal dispatch
   cross a database/external-system boundary. Use a transactional outbox and reconciliation
   for crashes/ambiguous dispatch outcomes. The new ingestion lock protects canonical writes,
   not workflow dispatch, disconnect races, token rotation or stale cached location links.
4. **Operational deployment (P1).** Current configuration/Compose is local/demo oriented
   (local/test environments, development credentials, insecure-cookie default and Temporal
   development server). Supply production secrets, HTTPS/secure cookies, durable services,
   backup/restore drills and tested rollout/rollback procedures before public exposure.
5. **Shared capacity and backend deadlines (P2).** Rate limits, breaker and model admission
   are process-local; idle rate-limit entries have no eviction. Add shared limits before
   scaling workers, and measure database lock waits, connector deadlines and deadlock retries.
   Connection-wide ingestion serialization favors correctness over throughput; benchmark
   concurrent webhook/backfill workloads before narrowing locks.
6. **Observability and release evidence (P2).** Existing spans record tool/question timings
   and outcome shape, but there is no demonstrated alerting, token-cost accounting, model
   digest pinning, or a completed full live-model quality gate. Track timeouts, saturation, tool failures,
   evidence-only answers, semantic eval results and p95 latency without recording sensitive
   prompt/tool contents by default. Verify sandbox Square and deployed SSE disconnect behavior.

### Validation

Regression coverage is in `tests/sous/test_hardening.py`, `tests/sous/test_claims.py`,
`tests/sous/test_conversations.py`, `tests/sous/test_retention.py`, `tests/evals/test_scoring.py`,
`tests/test_receipt_failures.py`,
`tests/integrations/test_pipeline.py` and `tests/integrations/test_push.py`. Tests use a real,
isolated PostgreSQL 17.6 database and the restricted `chefos_app` role, with scripted LLM and
Square providers. Python 3.14 dependencies come from the frozen lockfile. Ruff, formatting and
strict mypy checks apply to the entire API. Real Ollama/Square behavior and production load
remain separate release gates.

Prior audit validation: **544 tests passed** (239.93 seconds), Ruff check and format check
passed, strict mypy passed for 92 source files, and `git diff --check` passed. The database
was an isolated temporary PostgreSQL instance on `127.0.0.1:55432`; no application data was
used. The test command was `TEST_DB_HOST=127.0.0.1:55432 .venv/bin/pytest -q` from `apps/api`.
An earlier mixed-directory selection encountered pytest fixture discovery errors; the
pipeline-only run passed all 12 tests, and the final full-suite run passed all collected tests.

Retention/evaluation follow-up validation (2026-10-22): **616 backend tests passed** in
234.23 seconds after stopping local inference; the final focused evaluation suite passed all
**27 tests**, including the subsequently added freshness-metadata regression. Ruff check,
format check, strict mypy (97 source files), and `git diff --check` passed. Migration 0015
was also checked against pre-existing completed/unanswered rows in a separate database,
including backfill, purge, downgrade and re-upgrade. The initial concurrent model/test run
hit the simulator's 60-second performance budget (83.98 seconds); that run was interrupted
and the full suite passed once the competing model was stopped. The live full-model gate is
still pending on the desktop, as documented in the linked report above.
