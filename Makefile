.PHONY: setup db test mcp-dev smoke eval

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	test -f .env || cp .env.example .env

db:
	docker compose up -d --wait

test:
	. .venv/bin/activate && python -m pytest -q

smoke:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m scripts.smoke_llm

mcp-dev:
	npx @modelcontextprotocol/inspector .venv/bin/python src/legacybridge/mcp_servers/sql_readonly.py

eval:
	@echo "Fase 3: implementar evals/run.py"
