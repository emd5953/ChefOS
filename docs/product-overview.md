# Product Direction

**Status:** Working product direction for our product. This is not a commitment to match another vendor's feature list. User interviews, operating data, and business priorities should refine it.

## Product intent

Build an operations product that makes the specific restaurant workflows we choose materially faster and more reliable. The intended advantage is a focused, integrated experience for our users—not breadth for its own sake. Start from their highest-cost recurring decisions and workflow friction, then add capability only when it improves those outcomes.

The repository does not yet contain customer research, source code, or an approved product roadmap. Audience, first workflow, and success measures remain decisions to validate before implementation scope is fixed.

## Working principles

1. **Start with a real operator job.** Every capability should name its user, trigger, current workaround, and measurable improvement.
2. **Optimize for the chosen customer.** Avoid assuming that independent restaurants, enterprise groups, retail, and hospitality venues have the same needs.
3. **Prove workflow value before platform breadth.** Prefer a narrow end-to-end workflow over empty modules and speculative integrations.
4. **Integrate selectively.** Connect the systems required by the target workflow. Keep the source of truth explicit for each data domain.
5. **Make operational data trustworthy.** Show freshness, scope, provenance, and gaps; preserve corrections and reconciliation history.
6. **Keep consequential actions reviewable.** AI and automation can prepare work; people approve actions with financial, staffing, or customer impact.
7. **Ship an operable product.** Design permissions, recovery, monitoring, support, and data lifecycle alongside the feature.

## Initial product questions to answer

These are discovery questions, not implied features:

- Which user and restaurant segment are we optimizing for first?
- What recurring job causes the greatest avoidable time, cost, or error today?
- What is the narrowest workflow that can solve it end to end?
- Which source systems and files are actually present in those customers?
- Which system owns each critical fact, and who may correct it?
- What measurable result would justify continued investment?
- Which constraints are decisive: mobile/offline behavior, deployment model, cost, compliance, or integration access?

## Market reference: ChefOS

We researched [ChefOS](https://chefos.us/) as one market reference. Its public positioning spans scheduling, inventory/purchasing, sales analytics, waste, team communications, and AI reporting, with connectors to restaurant systems. The company says it works alongside existing POS tools. This is useful for understanding category language and possible workflows; it is not our product specification, architecture, scope, or roadmap.

Public materials are vendor claims and may mix generally available, limited, or planned capabilities. Connector lists and outcome examples are not independently verified. Use the links below for competitive context only; revisit them during market research rather than carrying their claims into requirements.

### Research links

- [ChefOS homepage](https://chefos.us/)
- [Product areas: inventory](https://chefos.us/inventory), [commerce](https://chefos.us/commerce), [team](https://chefos.us/team)
- [Integrations](https://chefos.us/integrations) and [pricing](https://chefos.us/pricing)
- [Mobile app page](https://chefos.us/mobile-app) and [App Store listing](https://apps.apple.com/us/app/chefos-mobile/id6763660343)
- [Privacy](https://chefos.us/privacy), [terms](https://chefos.us/terms), and [security](https://chefos.us/security)

## Boundary

Do not infer our customer, supported systems, feature availability, pricing, data model, or technology choices from ChefOS. Those decisions belong to our product discovery and engineering design. See [spec.md](spec.md), [tech-stack.md](tech-stack.md), and [build_plan.md](build_plan.md) for our working design process.
