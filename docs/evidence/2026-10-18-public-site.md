# REA Capture: Public ChefOS Site

**Captured:** 2026-10-18  
**Target:** `https://chefos.us/` public routes  
**Method:** REA `capture_browser_scenario`, Chromium launch mode, fresh isolated profile per route, 800–1,500 ms settle, end capture of accessibility tree and URL (homepage also captured DOM). No login, form submission, network response-body capture, or control interaction was performed. The provider-owned browser process was terminated after each scenario.

## Evidence records

| Route | REA Evidence ID | Directly observed |
|---|---|---|
| `/` | `ev_64a2f1489087134d3707d9ca922046d1c926af6bb32d307fd90f9b464280a955` | Homepage structure and accessibility tree; marketing copy, sections, nav/footer links, and page mockups. DOM captured (189,686 bytes); no screenshot. |
| `/inventory` | `ev_52a3d1a117a62331ebefa004d754896b821191400da104b3f288368d78ff9092` | Inventory page content describes count sessions, unit conversions, order/receiving, invoices/AP, transfers, recipes, and waste. Mockup labels itself illustrative. |
| `/commerce` | `ev_95d5e4f409bdeb24fea6cd131be8c21687b7e1cd53e4883847a3d9cef29a10b6` | Accessibility tree exposes a sales activity and Wilmington weather timeline from Mar 1 to Oct 18, 2026, with a button for each daily date. Controls were not operated. |
| `/team` | `ev_6641e538524ee942c4899255917008f3f95bb1a04c7c1ab73c24f00309537acb` | Team page content describes demand context, scheduling by employee/position/station/location, open shifts, trade requests and manager approval, time off, tasks, employee records, and compliance. Mockup labels itself illustrative. |
| `/integrations` | `ev_63dd0464bffbcb163ebc21245f2441143ae1c2705ba2d368bae1aa52effeaa9a` | Eight connector categories and named vendors; public page says 48+ connectors and gives an API example. No connector was configured or called. |
| `/pricing` | `ev_27e13e1c20b8e8bf637e123577df2cacc2b9410a59a010a299845c5ce053fb7a` | Three module checkboxes, Team selected by default, single-location price presentation, and multi-location sales copy. Checkboxes were not changed. |
| `/mobile-app` | `ev_4168e9bd00db7c5e7fb1caed38909a663b622df11153fc842ad05618bcd4bcfe` | Mobile page copy describes employee schedule/self-service, manager operations, and inventory tasks. The App Store link is exposed; native app not opened. |

All scenarios completed with captured end-state accessibility and URL. REA reports `controlled-replay` / `observed` for these sessions, but the evidence is of the public website rendering, not of an authenticated ChefOS account or backend behavior. The browser used Chromium 155.0.8059.82, en-US, UTC, light color scheme, reduced motion, and blocked service workers. Artifact identity was unavailable; browser host platform and architecture were not observed. Network events were not selected, so these captures say nothing about API calls or resource request behavior.

## Additional homepage implementation observation

The captured homepage DOM contains `/_next/static/` assets, Turbopack-named chunks, and server-rendered Next Flight payload scripts. This is strong evidence that the public marketing site is built with Next.js (with a Turbopack build/runtime marker). It does not establish the architecture of the authenticated ChefOS product, backend services, database, hosting, or mobile app.

The raw homepage HTML digest is `a69d11eeb68680527a250f2ad83e05cc6240c5d03442c3bfd18e631f297bd17d`; its captured size was 189,686 bytes. The accessibility snapshot digest is `c62dc9b7fc9bad07e0d2f9bdaa4a41da9234be08d7f75e733aa42bf2fd164cfe`; size 10,990 bytes.

## Reproduction notes

The original REA request used a provider-owned Chromium process with:

```json
{
  "browser": {
    "mode": "launch",
    "executable_path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  },
  "start_url": { "url": "https://chefos.us/" },
  "actions": [
    { "step_id": "settle", "action": "wait_for_timeout", "duration_ms": 1500 }
  ],
  "capture": { "at_end": ["dom", "accessibility", "url"] }
}
```

Route captures used the same shape with the relevant route, an 800 ms settle, and `at_end: ["accessibility", "url"]`.
