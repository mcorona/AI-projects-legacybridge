# LegacyBridge

Reliable AI agents over legacy enterprise databases: secure, observable and measured.

> Work in progress (Phase 1). The full README is written in Phase 8; the plan lives in
> [`docs/PLAN.md`](docs/PLAN.md).

```bash
make db-up      # Postgres 16 on 127.0.0.1:5434 (creates .env from .env.example)
make seed       # deterministic synthetic data (seed 42)
make test
make lint
```
