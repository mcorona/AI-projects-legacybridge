.PHONY: setup db test test-unit mcp-dev mcp-check smoke eval

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	test -f .env || cp .env.example .env

db:
	docker compose up -d --wait

test:
	. .venv/bin/activate && python -m pytest -q

test-unit:      # sin Postgres
	. .venv/bin/activate && python -m pytest -q -m "not integration"

smoke:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m scripts.smoke_llm $(ARGS)

# MCP Inspector (UI en el navegador). SERVER=schema | sql. Usa .mcp.json como fuente única.
SERVER ?= sql
INSPECTOR = npx -y @modelcontextprotocol/inspector

mcp-dev:
	$(INSPECTOR) --config .mcp.json --server legacybridge-$(SERVER)

# Verificación sin UI: lista las tools de ambos servers con el CLI del Inspector.
mcp-check:
	@for s in schema sql; do echo "== legacybridge-$$s"; \
	  $(INSPECTOR) --cli --config .mcp.json --server legacybridge-$$s --method tools/list \
	  | python3 -c "import sys,json; print([t['name'] for t in json.load(sys.stdin)['tools']])" || exit 1; \
	done

eval:
	@echo "Fase 3: implementar evals/run.py"
