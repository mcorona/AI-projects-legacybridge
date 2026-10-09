.PHONY: db-up db-reset seed test lint obs-up obs-down

# Starts Postgres (creates .env from .env.example on first run) and waits until it is healthy.
db-up:
	@test -f .env || cp .env.example .env
	docker compose up -d --wait

# Drops the volume and recreates schema and roles from db/init/.
db-reset:
	@test -f .env || cp .env.example .env
	docker compose down -v
	docker compose up -d --wait

# Deterministic synthetic data (seed 42). ARGS="--dry-run" | "--seed 7"
seed: db-up
	uv run python -m scripts.seed --seed 42 $(ARGS)

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .

# Phoenix (UI on http://localhost:6006) and the OTel Collector (OTLP on 127.0.0.1:4317/4318).
# Then trace a question with: LB_TRACING=1 uv run legacybridge ask "..." (see .env.example).
obs-up:
	@test -f .env || cp .env.example .env
	docker compose --profile obs up -d phoenix otel-collector
	@for i in $$(seq 1 40); do curl -fs http://localhost:6006/healthz >/dev/null && echo "phoenix is up: http://localhost:6006" && exit 0; sleep 2; done; echo "phoenix did not become healthy" >&2; exit 1

obs-down:
	docker compose --profile obs stop phoenix otel-collector
