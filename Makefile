# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
.PHONY: install run serve tunnel test unit lint fmt cov replay docker-build docker-run \
	compose-up compose-proxy clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

run:
	$(VENV)/bin/uvicorn app.main:app --reload --port $${PORT:-8000}

unit:
	$(VENV)/bin/pytest tests/unit -v

test:
	$(VENV)/bin/pytest -m "not integration" --cov=app --cov-report=term-missing --cov-fail-under=80

# Serve without --reload: the reloader's child process would hold a second
# event store, and webhook deliveries would land in whichever one GitHub reached.
serve:
	$(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port $${PORT:-8000}

# Public URL for GitHub to deliver webhooks to. Leave running in its own terminal.
tunnel:
	cloudflared tunnel --url http://localhost:$${PORT:-8000}

test-integration:
	$(VENV)/bin/pytest -m integration -v

# Proof of idempotency: send the same signed delivery repeatedly, expect one row.
replay:
	$(VENV)/bin/python scripts/replay_webhook.py --url http://127.0.0.1:$${PORT:-8000}

# Webhook end-to-end: needs `make serve`, `make tunnel`, and a configured webhook.
test-webhook:
	SERVICE_BASE_URL=http://127.0.0.1:$${PORT:-8000} \
		$(VENV)/bin/pytest tests/integration/test_webhook_delivery.py -v

lint:
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/mypy app

fmt:
	$(VENV)/bin/ruff format app tests
	$(VENV)/bin/ruff check --fix app tests

docker-build:
	docker build -t issues-api-testbed .

# Mount a named volume at /data so the webhook event store survives restarts.
docker-run:
	docker run --rm -p 8000:8000 --env-file .env \
		-e DATABASE_URL=sqlite:////data/events.db \
		-v issues-api-events:/data \
		--name issues-api issues-api-testbed

compose-up:
	docker compose up --build

compose-proxy:
	docker compose --profile proxy up --build

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
