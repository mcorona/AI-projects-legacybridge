# Architecture decision records

Every decision that shaped LegacyBridge, in the order it was taken. Each says what was decided,
why, what it costs, and what was not chosen. Accepted decisions are changed only by a later ADR or by
a dated entry at the end of the same one (the evaluation ADRs, 013 and 014, do this on purpose: they
were written before the runs and record what changed after).

| ADR | Decision | Phase | Instead of |
|---|---|---|---|
| [001](ADR-001-mcp-server.md) | Expose the legacy database through our own **MCP server** | 2 | function calling inside the agent |
| [002](ADR-002-agent-orchestration.md) | Our own **model-agnostic agent loop** with an independent verifier | 4 | an agent SDK or a managed agent |
| [003](ADR-003-model-access.md) | Models through **profiles** and one OpenAI-compatible adapter (Bedrock Converse added in Phase 7) | 4, 7 | one provider's SDK |
| [004](ADR-004-sql-security.md) | **SQL security in three layers**: AST guard, database roles, rule engine | 2 | trusting the prompt |
| [005](ADR-005-writes-and-hitl.md) | **Writes are proposals**; a human approves them | 4 | forbidding writes, or letting the agent write |
| [006](ADR-006-semantic-layer.md) | A **versioned semantic layer** (ontology and defect rules) that also checks the SQL | 3 | a prompt with the DDL |
| [007](ADR-007-observability.md) | **OpenTelemetry** traces with GenAI conventions, viewed in Phoenix | 5 | a proprietary tracing SDK |
| [008](ADR-008-deployment.md) | **Lambda containers** behind an asynchronous API Gateway | 7 | ECS Fargate, a function URL |
| [009](ADR-009-infrastructure-as-code.md) | **AWS CDK in Python**, tested without AWS | 7 | Terraform |
| [010](ADR-010-free-tiers-via-omniroute.md) | **Free tiers via OmniRoute**, synthetic data only | 4 | local model only |
| [011](ADR-011-golden-set-result-hash.md) | A canonical **result-set hash** for the golden set | 1 | comparing SQL text |
| [012](ADR-012-swar-detector.md) | A **SWAR risk score** from raw signals, with an early warning that acts | 5 | trusting the model's confidence |
| [013](ADR-013-evaluation.md) | How the agent is **evaluated**, defined before the runs | 6 | tuning the metrics after seeing results |
| [014](ADR-014-aws-deployment.md) | The **AWS deployment** and the Bedrock comparison, defined before the paid runs | 7 | an always-on stack, OpenSearch Serverless |

ADR-008 and ADR-009 were written in Phase 8 for decisions taken in Phase 7; ADR-014 holds the detail.
