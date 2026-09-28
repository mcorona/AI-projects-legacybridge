.PHONY: setup db db-migrate seed index ask agent-check rescore test test-unit mcp-dev mcp-check smoke eval

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	test -f .env || cp .env.example .env

db:
	docker compose up -d --wait

# Aplica migraciones idempotentes (03_* en adelante) a un volumen ya inicializado.
# docker-entrypoint-initdb.d solo corre en un volumen NUEVO.
db-migrate: db
	@for f in db/legacy/0[3-9]_*.sql; do echo "== $$f"; \
	  docker exec -i legacybridge-db psql -q -U postgres -d legacy -v ON_ERROR_STOP=1 < $$f || exit 1; \
	done

test:
	. .venv/bin/activate && python -m pytest -q

test-unit:      # sin Postgres
	. .venv/bin/activate && python -m pytest -q -m "not integration"

# Recarga datos sintéticos deterministas (ancla + generados, seed 42). ARGS="--dry-run" | "--seed 7"
seed: db
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m scripts.gen_data $(ARGS)

# Indexa DDL, diccionario y defectos en pgvector (incremental). ARGS="--dry-run" para previsualizar.
index:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m legacybridge.rag.index $(ARGS)

# Pregunta al agente: make ask Q="¿Cuántos clientes activos hay?" [P=local|omniroute|bedrock|cascade]
ask:
	@. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m legacybridge.agent $(if $(P),--provider $(P)) "$(Q)"

# Corrida rápida de trabajo sobre dev (antes scripts/agent_check.py). ARGS extra, p. ej. ARGS="--ids e001,d003"
# Re-puntúa una corrida con las reglas vigentes sin volver a correr el agente: make rescore R=evals/reports/<...>.json
rescore:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m evals.rescore $(R)

agent-check:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m evals.run --split dev -p local --scratch $(ARGS)

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

# Evaluación reproducible (Fase 3). Oficial: split test, Qwen local, 3 repeticiones.
#   make eval ARGS="--split dev -p local --scratch"      corrida de trabajo
#   make eval ARGS="--resume evals/results/raw/<id>.jsonl"   continuar una corrida cortada
EVAL_ARGS ?= --split test -p local --repeats 3
eval:
	. .venv/bin/activate && set -a && . ./.env && set +a && PYTHONPATH=src python -m evals.run $(if $(ARGS),$(ARGS),$(EVAL_ARGS))
