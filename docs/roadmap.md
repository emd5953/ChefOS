# Reconstruction Plan

**Goal:** Reconstruct the ChefOS product around its advertised restaurant workflows, while keeping public claims, observed product behavior, and our implementation choices distinct. The current local product prototype is a first pass, not a completed app.

## Current state

- Public marketing pages were captured and reconstructed in the repository.
- A local full-stack prototype was added with SQLite persistence and general restaurant records.
- The prototype is not a faithful workflow reconstruction: it omits or simplifies advertised capabilities such as storage-area count sessions, unit/pack conversion, invoice/AP flow, transfers, demand-informed schedule planning, availability/time-off, Commerce PMix and variance detail, mobile-specific workflows, and connected integrations.
- Sous currently uses deterministic local responses; it does not establish or reproduce ChefOS's advertised assistant.
- Public site mockups are illustrative, and no authenticated ChefOS workspace or native app has been inspected.

## Work sequence

### 1. Complete the evidence map

Capture the remaining public product pages and states: FAQ, pricing interactions, legal/security/status surfaces, and any linked product guides. Inspect the App Store listing and native app metadata where available. Preserve the existing REA evidence IDs and record new captures with date, route, method, and limits.

**Done when:** the public route and mobile-app surface inventory is complete and each capability points to a source.

### 2. Get product behavior evidence

Use authorized demo or customer access if available. Map onboarding, organization/location setup, navigation, roles, and the highest-value end-to-end workflows across Team, Commerce, and Inventory. Capture states before and after actions, including errors and corrections. Never fill unknowns with plausible CRUD behavior and call it parity.

**Done when:** each core workflow has reproducible observations, actors, state transitions, and evidence references—or is explicitly blocked as unknown.

### 3. Rebuild around restaurant workflows

Prioritize complete cross-module work, not module-shaped screens:

1. **Team:** demand context → coverage plan → publish/adjust → employee availability, trades and approvals → shift tasks and attendance.
2. **Inventory:** storage-area count → unit conversion and exceptions → order → receive and reconcile invoice → stock movement/transfer → recipe and 86 availability → waste/yield and variance.
3. **Commerce:** date range and daily detail → sales/channel/PMix/margin/labor views → variance and action context.
4. **Sous and integrations:** ground assistant behavior in real operational data; document connector setup and sync states only as they are evidenced. For a production-style Sous implementation, follow the staged proposal in [Sous production architecture](sous-architecture.md): begin with scoped read-only tools and evidence-bearing responses, add evaluation and observability before rollout, then consider one draft-and-approve write workflow.
5. **Mobile:** implement the employee, manager, and inventory jobs described on the public mobile page as first-class flows.

This ordering is a reconstruction priority, not a claim about ChefOS's internal navigation or release order.

### 4. Validate workflow parity

Compare each implemented journey with captured source behavior. Check data meaning, state transitions, permissions, responsive/device context, loading/empty/error states, and correction paths. Track each requirement as observed, vendor claim, inferred, implemented locally, or unknown.

**Done when:** acceptance criteria derive from evidence and no unverified local design is described as ChefOS behavior.

## Immediate next work

1. Keep docs accurate (this pass) and label the current app as a prototype.
2. Inspect the remaining public pages and native app listing.
3. Obtain authorized workspace access or an artifact with authenticated behavior; without that, the next app changes can only implement the public claims as a clearly labeled interpretation.
4. Replace the generic workflows with end-to-end Team, Inventory, and Commerce flows after gathering evidence or making explicit product decisions.
5. For Sous, define canonical metrics and source freshness, validate role/location boundaries, and evaluate evidence and failure handling before making any parity claim. The architecture proposal is a design input; authenticated product evidence is still required to establish ChefOS behavior.

## Evidence ledger convention

For each finding record: capability, source/route, timestamp, evidence ID, evidence type (**observed**, **vendor claim**, **inferred**, **unknown**), confidence, and unresolved questions. Keep direct observations separate from our implementation decisions.
