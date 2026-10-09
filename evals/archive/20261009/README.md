# Raw results of evaluation run `20261009`

The stored output of the run that every number in the README and in `docs/eval-report.md` comes from
(ADR-013, ADR-014). Synthetic data only; no credentials, account identifiers or local paths (checked
before they were committed). With these files the report and the cost model are rebuilt from a clone:

```bash
make eval-report RESULTS=evals/archive/20261009     # evals/reports/eval-20261009.md and docs/eval-report.md
uv run python -m scripts.cost_model                 # docs/cost-model.md
```

| File | What it is |
|---|---|
| `eval-20261009-<profile>-<arm>.jsonl` | One line per golden case: the agent's full result, its label and the commit it ran at. One run per arm. |
| `attacks-20261009.json` | The 45 tool-level attacks and whether each was blocked or neutralized. They do not depend on a model. |
| `guardrails-20261009.json` | The Guardrail probes (18 + 8 attacks, 8 output texts, the 80 golden questions as legitimate traffic, and 130 delivered answers). |

The profiles and what answered them:

| Profile | Model | Arms |
|---|---|---|
| `local-qwen` | `qwen/qwen3.6-35b-a3b` (LM Studio, on the author's machine) | full, no-verify, no-semantic |
| `omniroute-free` | `nvidia/nemotron-3-ultra-550b-a55b:free` (a single free model through OmniRoute) | full |
| `bedrock-claude` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | full |
| `bedrock-claude-sonnet` | `us.anthropic.claude-sonnet-4-6` | full |
| `bedrock-claude-opus` | `us.anthropic.claude-opus-4-6-v1` | full |

What these files cannot tell you: run-to-run noise (one run per arm), and whether the draft golden
set is right (`review: draft`: no person has validated its expected results). The reading of the
failures is in `evals/notes/20261009.md`.
