# Demo video: 3 minutes, with the agent live

A script for recording, not a transcript. The README GIF (`make demo-gif`) needs no model; this video
shows the **agent** answering, which does. Use `local-qwen` (LM Studio running) or `bedrock-claude`
(after `aws login`). Record at 1080p with the terminal font at 16 or more.

**Before recording:** `make db-up && make seed && make obs-up`, `LB_TRACING=1` exported, Phoenix
open at http://localhost:6006, `uv run python -m evals.demo --act 1` once to warm everything up.

**Honesty rules for the narration:** say "synthetic data", say that the expected answers were validated by their author and are not an independent benchmark,
and do not say the agent is always right: the point of the project is that it knows when it may not be.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Title card, then the README GIF | "Legacy databases fail text-to-SQL *silently*: the query runs, the number looks right, and it is wrong. This is an agent built for that." |
| 0:20 | `uv run python -m evals.demo --act 1` | "Same question, two queries. The naive one says 833. The rule engine flags it, an invoice captured twice, and the risk score goes up. The right answer is 829." |
| 0:50 | `uv run legacybridge ask "¿Cuántas facturas activas hubo en 2004?" --profile local-qwen` | "Now the real agent, with a real model. Watch the answer, the SQL that ran, the rows, the defects it touched, and the verifier agreeing." Point at `confidence`, `verified`, `defects_touched`. |
| 1:25 | `uv run python -m evals.demo --act 2` | "Attacks go to the tools the way an agent would send them: stacked statements, a union against the password table, a delete. Every one is stopped at the guard, and the database would refuse them on its own." |
| 1:50 | `uv run legacybridge ask "Da de baja al cliente 0000000006" --profile local-qwen`, then `uv run legacybridge approvals list` | "A write is a proposal. Nothing changed. A person has to approve it, and cannot be the one who proposed it." |
| 2:10 | `uv run legacybridge approvals reject <id> --reviewer demo --note "demo"` | "Rejected. The business data is identical." |
| 2:20 | Phoenix: open the last trace and expand it | "Every question is one trace: the planner, each model call with its tokens, each tool call, and the SWAR risk on the root span." |
| 2:40 | The results table of the README | "Five models, 80 questions. Bigger models answer more, not necessarily better, and the target I set for silent errors, 5 percent, was not met. The expected answers were validated by me, their author, after the run, so this is a reviewed reference and not an independent benchmark. That is the honest state." |
| 2:55 | The repository URL | "Everything is in the repository, including the tests I wrote before running them." |

**Things that can go wrong:** the local model is slow (cut the wait, never speed it up); the first
question after starting LM Studio can be empty (ask again); `approvals reject` needs the id the
agent printed. Afterwards reject the request or run
`make db-reset && make seed` to start clean.
