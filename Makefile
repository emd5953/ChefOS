COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml
API := cd apps/api && uv run

.PHONY: up down reset migrate seed demo test lint fmt web smoke logs env sous-eval sous-cleanup

env:
	@test -f .env || { cp .env.example .env; echo "created .env"; }
	@if grep -q '^CHEFOS_MASTER_KEY=$$' .env; then \
	  key=$$(openssl rand -base64 32); \
	  sed -i.bak "s|^CHEFOS_MASTER_KEY=$$|CHEFOS_MASTER_KEY=$$key|" .env && rm -f .env.bak; \
	  echo "generated CHEFOS_MASTER_KEY"; \
	fi

up: env
	$(COMPOSE) up -d --build --wait
	$(MAKE) migrate
	@echo "waiting for http://localhost:8080/readyz ..."
	@for i in $$(seq 1 60); do \
	  [ "$$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/readyz)" = "200" ] && echo "ready" && exit 0; \
	  sleep 1; \
	done; echo "readyz did not return 200" >&2; exit 1

down:
	$(COMPOSE) down

reset: env
	$(COMPOSE) down -v

# Runs as chefos_owner (MIGRATIONS_DATABASE_URL).
migrate:
	$(API) alembic upgrade head

seed:
	$(API) python -m app.cli seed-demo

# Seeds the demo org, connects the simulated Square, backfills; waits for the last 30 days.
demo:
	$(API) python -m app.cli demo

test:
	$(API) pytest -q

lint:
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy
	@if [ -f apps/web/package.json ]; then cd apps/web && npm run lint && npm run typecheck; fi

fmt:
	cd apps/api && uv run ruff check --fix . && uv run ruff format .

web:
	cd apps/web && npm run dev

smoke: up
	bash infra/smoke.sh

logs:
	$(COMPOSE) logs -f --tail=100

# Sous model eval against the demo org (needs Ollama and a seeded demo). Extra flags: ARGS="--limit 5"
sous-eval:
	cd apps/api && OLLAMA_URL=$${OLLAMA_URL:-http://localhost:11434} uv run python -m evals.sous.run $(ARGS)

# Same bounded sweep as the hourly worker maintenance.
sous-cleanup:
	$(API) python -m app.cli purge-sous
