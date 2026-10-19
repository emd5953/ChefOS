# ChefOS Reconstruction

This repository reconstructs the public ChefOS site and contains an experimental, locally runnable product prototype. ChefOS is presented as a restaurant operating system organized around **Team, Commerce, and Inventory**, with connected workflows for managers, employees, and operators. The local workspace is not a faithful or feature-complete recreation of the authenticated product: our REA work has captured public marketing pages, not private app behavior.

## Run locally

Requires Python 3.11 or newer. No third-party packages are needed.

```sh
python3 server.py
```

Open:

- Marketing site: `http://127.0.0.1:8000/`
- Product workspace: `http://127.0.0.1:8000/app`
- Health endpoint: `http://127.0.0.1:8000/api/v1/health`

The first run creates a sample restaurant and prints its local demo sign-in in the server console:

- Email: `owner@chefos.local`
- Password: `ChefOS-demo-2026!`

The sample data is fictional. Workspace records persist in `~/.chefos/chefos.sqlite3`, outside the web root. Set `CHEFOS_DB_PATH` to choose another database file. `PORT` and `CHEFOS_HOST` configure the development server bind address.

## What the local prototype contains

The prototype has a SQLite-backed API and screens for inventory adjustments, vendor purchase orders and receiving, basic recipe costing, waste entries, employee records, weekly shifts, shift requests, time clock, tasks, sales summaries, channels, and a restricted sales-ingestion key. Sous is a deterministic local query helper, not ChefOS's advertised AI capability.

These are prototype implementation choices. They do not establish ChefOS's actual data model, calculations, permissions, integrations, or workflow behavior. In particular, advertised capabilities such as storage-area count sessions, pack/unit conversion, invoice/AP reconciliation, transfers, demand-informed scheduling, availability and time-off, detailed PMix/variance analysis, native mobile workflows, and live connectors are not yet reproduced.

The browser workspace uses the `/api/v1` API. Endpoints include `/auth/register`, `/auth/login`, `/auth/me`, `/auth/accept-invitation`, `/inventory`, `/purchase-orders`, `/recipes`, `/waste`, `/employees`, `/invitations`, `/shifts`, `/time-entries`, `/tasks`, `/sales`, `/reports/summary`, `/channels`, `/api-keys`, and `/sous`.

POS API keys are restricted to sales writes. Create and revoke them in **Workspace → Integrations**. Send the secret as a Bearer token to `POST /api/v1/sales/ingest`; include a stable `external_id` so retries update the same daily record. Secrets are only returned once and are stored as hashes.

## Security and deployment boundary

Passwords are PBKDF2-hashed; browser sessions use HttpOnly, SameSite cookies and CSRF tokens; API reads and writes are scoped to the signed-in organization; user inputs are validated at the API boundary; workspace data files are not served as static files. This is a single-process development app, not a production deployment: before exposing it to the internet, put it behind a production WSGI/ASGI server and TLS reverse proxy, add rate limiting, backups, operational monitoring, email delivery for invitations, and deployment secrets management.

## Product target and research boundary

The public pages describe Team, Commerce, Inventory, Sous, integrations, and mobile workflows. Their copy is vendor claims; page mockups are illustrative. We have not inspected an authenticated ChefOS account, called its API, or examined the native app. Do not present the local prototype as feature parity.

- [Product map](docs/product-overview.md)
- [Reconstruction specification](docs/spec.md)
- [Build plan](docs/build_plan.md)
- [Architecture evidence](docs/tech-stack.md)
- [Proposed Sous production architecture](docs/sous-architecture.md)
- [REA public-site capture](docs/evidence/2026-10-18-public-site.md)
