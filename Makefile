.PHONY: help up up-observability down down-observability logs logs-observability ps ps-observability bootstrap api-dev api-reseed worker-dev web-dev web-test-unit web-test-e2e api-test worker-test evaluator-test test-all evaluator-run check test lint format prompts-validate

COMPOSE_FILE := infra/docker/docker-compose.dev.yml
OBS_COMPOSE_FILE := infra/docker/docker-compose.observability.yml
WEB_WORKSPACE := @eduground/web
DOCKER_COMPOSE ?= docker-compose

help:
	@echo "Targets: bootstrap, up, up-observability, down, down-observability, logs, logs-observability, ps, ps-observability, api-dev, api-reseed, worker-dev, web-dev, web-test-unit, web-test-e2e, api-test, worker-test, evaluator-test, test-all, evaluator-run, check, test, lint, format, prompts-validate"

bootstrap:
	@powershell -NoProfile -Command "if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }"
	@echo ".env is ready or already present."
	@echo "Install workspace dependencies, then run 'make up' and the app targets below."
	@echo "API: $$env:PYTHONPATH='apps/api'; python -m uvicorn app.main:app --reload --reload-dir apps/api --reload-exclude .venv --reload-exclude node_modules --host localhost --port 8000"
	@echo "Reseed: $$env:PYTHONPATH='apps/api'; python -m app.scripts.reseed"
	@echo "Worker: $$env:PYTHONPATH='apps/worker'; python -m dramatiq app.jobs.ingestion app.jobs.maintenance"
	@echo "Web: npm.cmd run --workspace $(WEB_WORKSPACE) dev"
	@echo "Evaluator: $$env:PYTHONPATH='apps/evaluator'; python -m app.main"

up:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d

up-observability:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) -f $(OBS_COMPOSE_FILE) up -d

down:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down

down-observability:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) -f $(OBS_COMPOSE_FILE) down

logs:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f

logs-observability:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) -f $(OBS_COMPOSE_FILE) logs -f

ps:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) ps

ps-observability:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) -f $(OBS_COMPOSE_FILE) ps

api-dev:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/api'; python -m uvicorn app.main:app --reload --reload-dir apps/api --reload-exclude .venv --reload-exclude node_modules --host localhost --port 8000"

api-reseed:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/api'; python -m app.scripts.reseed"

worker-dev:
	@powershell -NoProfile -Command "$$processes = if ($$env:CURRICULUM_TUTOR_WORKER_PROCESSES) { $$env:CURRICULUM_TUTOR_WORKER_PROCESSES } else { '2' }; $$threads = if ($$env:CURRICULUM_TUTOR_WORKER_THREADS) { $$env:CURRICULUM_TUTOR_WORKER_THREADS } else { '4' }; $$env:PYTHONPATH='apps/worker'; python -m dramatiq --processes $$processes --threads $$threads app.jobs.ingestion app.jobs.maintenance"

web-dev:
	npm.cmd run --workspace $(WEB_WORKSPACE) dev

web-test-unit:
	npm.cmd run --workspace $(WEB_WORKSPACE) test:unit

web-test-e2e:
	npm.cmd run --workspace $(WEB_WORKSPACE) test:e2e

api-test:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/api'; python -m pytest apps/api/tests -q"

worker-test:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/worker'; python -m pytest apps/worker/tests -q"

evaluator-test:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/evaluator'; python -m pytest apps/evaluator/tests -q"

test-all: api-test worker-test evaluator-test web-test-unit web-test-e2e

evaluator-run:
	@powershell -NoProfile -Command "$$env:PYTHONPATH='apps/evaluator'; python -m app.main"

check:
	npm.cmd run check

test:
	npm.cmd run test

lint:
	npm.cmd run lint

format:
	npm.cmd run format

prompts-validate:
	npm.cmd run prompts:validate
