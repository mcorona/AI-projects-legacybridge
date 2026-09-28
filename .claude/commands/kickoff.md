---
description: Arranca o continúa la fase activa de LegacyBridge
---
Lee `CLAUDE.md`, `docs/PLAN.md` y `docs/LEGACY_DEFECTS.md`.

1. Identifica la primera fase con casillas sin marcar en `docs/PLAN.md`.
2. Verifica el entorno: `make test`; si la fase lo requiere, `docker compose ps` y `make smoke`.
3. Si existe `../AI-projects-inventory-copilot`, compara su capa LLM y SQL guard con las de este repo
   y propón qué portar (no copies sin mostrarme el diff).
4. Propón un plan de máximo 8 pasos para la fase activa y espera mi aprobación.
5. Al terminar cada paso: pruebas en verde, marca la casilla en `docs/PLAN.md` y haz commit pequeño.

Argumento opcional: $ARGUMENTS (p. ej. "fase 2" para forzar una fase).
