# CLAUDE.md — LegacyBridge

Contexto permanente para Claude Code. Léelo completo antes de cualquier cambio.

## Qué es
Agente de IA que consulta **sistemas empresariales legacy** (esquemas con nombres crípticos,
sin llaves foráneas, fechas como texto, banderas `CHAR(1)`) mediante **MCP**, con respuestas
verificables, evaluación reproducible y controles de seguridad.
Proyecto insignia de portafolio (LinkedIn/Upwork) de Manuel Corona. Continúa y generaliza
`AI-projects-inventory-copilot`.

## Principios no negociables
1. **Solo lectura.** Ninguna herramienta ejecuta DML/DDL. Toda SQL pasa por `guard/sql_guard.py`
   y se ejecuta con el rol `lb_ro`.
2. **Agnóstico de proveedor.** El código del agente solo conoce `llm.router.chat()`.
   Nunca importar SDKs de proveedor fuera de `src/legacybridge/llm/`.
3. **Costo mínimo.** Orden por defecto: `local` (LM Studio, Qwen3.6) → `omniroute` (free tier)
   → `bedrock` (solo evaluación final y escalamiento). Ver `config/models.yaml`.
4. **Datos sintéticos** siempre que se use OmniRoute o proveedores externos gratuitos.
5. **Todo resultado es evaluable.** Cada feature nueva agrega preguntas al golden set
   (`evals/questions/`) y debe mantener o subir las métricas.
6. **Evidencia en cada respuesta:** SQL ejecutada, filas fuente, tablas usadas y confianza.

## Stack
- Python 3.12, `uv` o `venv`; `sqlglot` (validación AST), `mcp` (FastMCP), `openai` (cliente
  compatible para LM Studio y OmniRoute), `boto3` Bedrock Converse.
- PostgreSQL 16 + pgvector en Docker (puerto **5433** para no chocar con inventory-copilot).
- LM Studio: `http://localhost:1234/v1`, modelo `qwen/qwen3.6-35b-a3b`, embeddings `bge-m3` (1024).
- OmniRoute: `http://localhost:20128/v1`.
- Bedrock: perfil AWS `aif`, región `us-east-1`, `us.anthropic.claude-haiku-4-5-20251001-v1:0`.

## Particularidades conocidas
- Qwen3.x emite bloques `<think>…</think>`: `llm.router` los elimina antes de devolver texto.
- Las respuestas de modelos locales pueden envolver SQL en ```sql```; usar `extract_sql()`.
- El esquema legacy (`db/legacy/01_schema.sql`) tiene defectos **intencionales** documentados
  en `docs/LEGACY_DEFECTS.md`. No los "arregles": son el caso de prueba.

## Comandos
```bash
make setup      # venv + dependencias
make db         # levanta Postgres legacy (5433) y carga esquema + datos
make test       # pytest
make eval       # corre golden set y escribe evals/reports/<fecha>.md
make mcp-dev    # inspecciona los MCP servers con MCP Inspector
```

## Estructura
```
src/legacybridge/
  llm/router.py            # capa multiproveedor + cascada + medición costo/latencia
  guard/sql_guard.py       # validador AST (SELECT-only, allowlist, LIMIT, sin catálogos)
  mcp_servers/schema_explorer.py   # metadatos + diccionario de negocio
  mcp_servers/sql_readonly.py      # ejecuta SQL validada
  rag/                     # indexado de DDL, diccionario y manuales (pgvector)
  agent/                   # orquestador (tool use) + human-in-the-loop
evals/                     # golden set + harness + reportes
docs/PLAN.md               # plan por fases con criterios de aceptación
```

## Flujo de trabajo esperado
- Trabaja por la fase activa de `docs/PLAN.md`; no adelantes fases.
- Antes de codificar: plan corto. Después: `make test` y, si toca el agente, `make eval`.
- Commits pequeños, mensajes en inglés (Conventional Commits). README y docs públicos en inglés;
  comentarios internos pueden ir en español.
- Registra decisiones de arquitectura como ADR en `docs/adr/`.
