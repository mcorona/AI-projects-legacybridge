.PHONY: db-up db-reset seed test lint

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
