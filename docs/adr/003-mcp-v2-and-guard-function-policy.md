# ADR-003: MCP SDK 2.x, política de funciones del guard y ejecución *fail-closed*

- **Estado:** Aceptado
- **Fecha:** 2026-09-27
- **Relacionado:** ADR-002, `src/legacybridge/guard/sql_guard.py`,
  `src/legacybridge/mcp_servers/`, `src/legacybridge/dictionary.py`

## Contexto
Al iniciar la Fase 1 encontramos tres problemas:

1. **Los MCP servers no arrancaban.** `requirements.txt` pedía `mcp>=1.2`; pip resolvió
   `mcp 2.2.0`, donde `mcp.server.fastmcp.FastMCP` ya no existe (ahora es
   `mcp.server.mcpserver.MCPServer`). Claude Code solo reportaba "Connection closed".
2. **El guard dependía de una denylist de funciones.** Varios ataques la pasaban y solo los
   detenían los grants de la BD:

   | SQL | Riesgo |
   |---|---|
   | `SELECT query_to_xml('select * from usupwd', …)` | SQL dentro de un string: el AST no la ve |
   | `SELECT * FROM otro.cliemae` | Esquema sin validar |
   | `SELECT setval(…)`, `nextval(…)`, `pg_terminate_backend(…)`, `dblink_exec(…)` | Efectos secundarios / administración |
   | `SELECT … FOR UPDATE` | Bloqueos sobre el ERP productivo |

3. **`run_query` confiaba en el DSN.** Si `LB_DSN` apuntara por error a un rol con
   privilegios, el servidor ejecutaría con ellos; los errores de BD no se devolvían de forma
   estructurada para la autocorrección del agente.

## Decisión

### MCP SDK
- Fijar `mcp>=2.2,<3` (misma versión mayor que inventory-copilot) y usar `MCPServer`.
- Cada server expone `build_server()` (probado en memoria con `mcp.Client`) y arranca con
  `python -m legacybridge.mcp_servers.<name>`; `.mcp.json` es la única fuente de configuración
  de arranque (Claude Code, `make mcp-dev`, `make mcp-check` y `tests/test_mcp_stdio.py`).
- Todas las tools se anotan `read_only_hint=True`, `destructive_hint=False`,
  `idempotent_hint=True`, `open_world_hint=False`.

### Política de funciones del guard
- Funciones que sqlglot **tipa** (`SUM`, `COALESCE`, `TO_DATE`…): permitidas salvo denylist.
- Funciones **no tipadas** (`exp.Anonymous`: UDFs, extensiones, funciones raras de Postgres):
  **solo si están en `ALLOWED_ANON_FUNCS`**. Lo desconocido se rechaza por defecto
  (`function_not_allowed`).
- Prefijos `pg_`, `lo_`, `dblink` siempre prohibidos; denylist explícita para `*_to_xml`,
  secuencias, `set_config`/`current_setting` e introspección de privilegios.
- Solo esquema `public` (o sin calificar) y sin calificador de base de datos.
- `FOR UPDATE/SHARE` → `locking_clause`; funciones de tabla en `FROM` → motivo explícito.
- `FETCH FIRST n` se normaliza a `LIMIT` con el mismo tope.

### Ejecución *fail-closed* en `run_query`
1. Guard → 2. verificación previa `current_user = lb_ro` y `transaction_read_only = on`
(si falla, **no se ejecuta nada**) → 3. `statement_timeout` local a la transacción → 4. `ROLLBACK`.
Las fallas devuelven `stage` (`guard | connection | preflight | execution`), `error_type`,
SQLSTATE y mensaje.

### Diccionario de negocio validado
`legacybridge.dictionary` carga el YAML y **rechaza arrancar** si `ctrlhis`/`usupwd` aparecen
en la allowlist o si llaves, joins, catálogos, reglas o defectos no resuelven. Una prueba de
integración contrasta las columnas documentadas con `information_schema`.

## Alternativas consideradas
- **Fijar `mcp<2`:** arreglo de una línea, pero deja el proyecto en una API congelada y
  divergente de inventory-copilot. Rechazada.
- **Allowlist total de funciones (también las tipadas):** máxima seguridad, pero frágil ante
  actualizaciones de sqlglot y con muchos falsos rechazos en SQL analítica legítima. La
  combinación elegida bloquea por defecto lo desconocido, que es donde está el riesgo.
- **Confiar solo en los grants de la BD:** es la segunda barrera, no la primera; además el
  guard da al agente un motivo accionable antes de tocar la BD.
- **Ejecutar con autocommit y rol read-only:** más simple, pero sin verificación previa ni
  timeout local; un DSN mal configurado pasaría desapercibido.

## Consecuencias
- (+) Los ataques de la tabla anterior quedan cubiertos por el guard y registrados como
  preguntas adversariales (`a004`–`a010`) con `attack_sql`/`guard_reason` verificados en CI.
- (+) Un DSN privilegiado se detecta y se rechaza (probado contra el superusuario).
- (+) El agente de la Fase 2 recibe errores estructurados para su bucle de autocorrección.
- (−) Una función legítima no tipada fuera de la allowlist se rechaza hasta agregarla a
  `ALLOWED_ANON_FUNCS` (con prueba). Es un costo deliberado.
- (−) Las pruebas de ejecución no incluyen DML contra la BD (se evitó ejecutar escrituras
  incluso en transacción); la protección de escritura se verifica por la sesión read-only
  exigida en la verificación previa y por los grants del rol.
