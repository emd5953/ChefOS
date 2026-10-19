# Product Reconstruction Specification

**Purpose:** Describe the ChefOS product we are reconstructing from public evidence. This is a target behavior brief, not a spec for the current prototype and not a verified description of authenticated ChefOS.

**Evidence boundary:** Public marketing pages and their illustrative mockups were captured with REA on 2026-10-18. No account, live workflow, API, or native app behavior has been observed. See the [evidence record](evidence/2026-10-18-public-site.md).

## Product outcome

Help restaurant teams plan and run service with connected views of people, sales, and stock. Team, Commerce, and Inventory are the primary product modules. Work in one area should make relevant information available in the others: sales and demand context can inform labor and prep; recipes connect menu items to ingredients and cost; receiving, counts, transfers, and waste affect inventory and performance reporting.

The exact shared data model is unknown. The relationships above are product-level expectations inferred from the public feature descriptions and must be verified against product access before being treated as exact behavior.

## Actors and operating context

The public site implies employees, managers, restaurant operators, and multi-location groups. It describes work organized around locations and references positions, stations, shifts, storage areas, business dates, suppliers, and sales channels. Exact roles, permissions, and organization setup are unknown.

## Target workflows

### 1. Plan and manage a team

**Advertised:** schedule building with demand context; staffing by employee, position, station, and location; open shifts; shift trades and manager approval; availability and time off; tasks; attendance and compliance.

**Expected reconstruction behavior:** managers can create and adjust coverage, employees can view schedules and request changes, managers can review requests, and the team can work from assigned shift tasks. Mobile copy also describes schedule-change alerts, manager attention to late clock-ins, and approval queues.

**Do not guess:** forecast formula, labor-law rule set, schedule publishing behavior, trade state machine, notification channels, clock-in source, payroll export mapping, or audit semantics.

### 2. Understand restaurant performance

**Advertised:** sales, labor, margin, product mix, channel, daily flash, and variance reports. The Commerce page capture exposes a date-range timeline with individual daily date buttons and weather context.

**Expected reconstruction behavior:** users can inspect performance by date range and day, and see the advertised sales and cost dimensions in restaurant context.

**Do not guess:** formulas, comparison baselines, metric rounding, business-day cutoff, data freshness, POS reconciliation, weather source, or variance alerts. Public sample data is illustrative.

### 3. Control inventory and purchasing

**Advertised:** stock counts by storage area and unit, count exceptions, pars, orders and receiving, pack conversion, invoices/AP, transfers, recipes, waste, stock alerts, forecast-derived prep sheets, 86 broadcasting, and theoretical-versus-actual variance.

**Expected reconstruction behavior:** stock quantities can be counted and reviewed; purchasing can replenish stock; receiving and transfers represent stock movement; ingredients connect to recipes; waste and yield events can inform cost and availability.

**Do not guess:** count-session lifecycle, blind counts, approval thresholds, unit-conversion rules, invoice OCR fields or corrections, supplier catalog semantics, transfer reconciliation, inventory valuation, theoretical usage, or variance calculation.

### 4. Record waste and yield

**Advertised:** track yield and shrink by station, shift, and SKU; surface root-cause signals.

**Expected reconstruction behavior:** waste/yield observations need enough context to support the advertised breakdowns and analysis.

**Do not guess:** reason taxonomy, capture device, quantity basis, recipe yield model, shrink formula, or correction/audit flow.

### 5. Use Sous with operational data

**Advertised:** natural-language questions, generated reports and briefings, anomaly detection, suggested actions, and schedule generation.

**Expected reconstruction behavior:** Sous is a cross-module assistant grounded in restaurant operations and can produce useful operational outputs. A production design proposal is detailed in [Sous production architecture](sous-architecture.md). In brief, it should authenticate and derive user/org/location scope server-side; classify and validate requests; call only allowlisted, typed, permission-checked domain tools; return answers with source evidence, units, time range, and freshness; and surface missing or stale data rather than guessing. Calculations should use canonical domain/reporting logic where possible.

For writes such as schedule generation, the assistant should produce a draft and impact summary. An authorized manager reviews and explicitly approves it; the command path then rechecks permissions/current state, validates, commits idempotently, and records an audit event. Answering a question must not itself mutate operational data.

**Do not guess:** actual model/provider, retrieval design, internal APIs, source citations, permission checks, action tools, confirmation rules, retention, or whether public examples reflect released functionality. The proposed controls above are recommended reconstruction/product requirements, not observed ChefOS behavior. A deterministic keyword response is not evidence of Sous parity.

**Proposed acceptance criteria for a production-style implementation:** every tool call is tenant/location scoped and authorized; model output cannot choose identity or access scope; answers expose evidence and freshness; unsupported/ambiguous requests clarify or refuse; tool failures and partial data are disclosed; read-only questions cannot mutate records; writes require an approved draft and audited idempotent command. These criteria do not assert that the current prototype meets them.

### 6. Connect existing systems

**Advertised:** connectors across POS, payroll/HR, accounting, scheduling/time, suppliers/inventory, delivery, reservations, and communications/identity; REST API and webhooks are also mentioned. A public API example shows a sales-ingest request.

**Expected reconstruction behavior:** connected systems exchange the operational data needed by the product areas and expose setup/status to users.

**Do not guess:** supported connector list, authorization flow, fields, direction, sync cadence, retry behavior, webhook signatures, idempotency, or error resolution. The public example is a documentation sample only; it was not called.

### 7. Support work on mobile

**Advertised:** employees access schedules and availability; managers review staffing, tasks, late clock-ins, and approvals; inventory users perform counts, invoice scans, 86 checks, and follow-ups.

**Expected reconstruction behavior:** mobile supports role-relevant work in the field, not only a desktop screen that happens to resize.

**Do not guess:** native versus web implementation details, offline support, push behavior, app permissions, or exact screen navigation until the app is inspected.

## Reconstruction acceptance principles

- Build around the advertised restaurant jobs and their connections, not generic CRUD pages.
- Keep Team, Commerce, and Inventory as coherent product areas; don’t elevate incidental mockup labels into confirmed features.
- Use restaurant concepts and workflows only when supported by observed evidence or clearly marked product decisions.
- Preserve source, date, and correction context for operational data when the observed design supports it.
- Distinguish implemented reconstruction behavior from vendor claims in both interface copy and project documentation.
- Treat responsive behavior, accessibility, loading/empty/error states, and role context as part of each workflow.

## Evidence labels

- **Observed:** directly captured or reproduced in an interactive product state.
- **Vendor claim:** stated on a public ChefOS page; not independently verified.
- **Inferred:** a relationship or behavior suggested by multiple public claims.
- **Unknown:** requires authenticated product access, official documentation, or further observation.

## Discovery required before feature parity claims

1. Obtain authorized access to the web workspace and map onboarding, navigation, roles, locations, and representative workflows.
2. Inspect the linked native app and its version metadata; capture employee, manager, and inventory flows.
3. Exercise schedule publishing, shift trade, approval, and attendance flows.
4. Exercise count, order, receiving, invoice, transfer, recipe, waste, and 86 workflows.
5. Inspect Commerce filters, definitions, daily detail, and reconciliation.
6. Verify Sous outputs, sources, permissions, and action-confirmation behavior.
7. Verify which integrations are available and observe at least one permitted setup and sync flow.

Until these are done, the repo is a public-surface reconstruction plus an experimental local prototype, not a feature-complete ChefOS clone.
