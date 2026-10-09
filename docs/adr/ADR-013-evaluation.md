# ADR-013: How the agent is evaluated, defined before the runs

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** Phase 6 of `docs/PLAN.md`, ADR-010, ADR-011, ADR-012, `evals/labels.py`,
  `evals/metrics.py`, `evals/gate.py`

## Context

Phase 5 measured the SWAR detector on 40 runs and 2, 3 and 5 positives, and corrected five
definition errors after seeing results. That was right for building the detector and wrong as a
way to produce a number to defend. Phase 6 produces the numbers, so the definitions are fixed here,
**before** the first run, and a run records the commit it ran at. Anything changed after seeing
results is reported twice, "as it ran" and "corrected" (`--relabel` / `--rescore`), as in Phase 5.

## What is evaluated

The 80 golden cases (`evals/golden_set.yaml`, still `review: draft`) and the 45 attacks
(`evals/attacks.yaml`), in three **arms**:

| Arm | What changes |
|---|---|
| `full` | the system as it is |
| `no-verify` | `AgentConfig(verify=False)`: no independent verifier |
| `no-semantic` | an empty ontology: no business summary in the prompts, no `explain_business_term`, no rule warnings from `run_query`, no defect notes in `describe_table`, and no semantic signals in the detector |

The ablations remove a component; the golden set is not touched and **no prompt is tuned**. In the
`no-semantic` arm the passages of the prompts that refer to what was removed (the ontology tool, the
rule warnings, the business summary) are dropped **mechanically**: they are marked in the prompt
files and `agent/prompt_loader.py` removes them, adding only the words that close a sentence.
Leaving them in would measure the model's confusion about a tool that does not exist. The `full`
arm uses the prompts byte for byte as in earlier phases (`tests/fixtures/prompts_phase5/` pins them).
The general caution that the database has known defects stays in the `no-semantic` prompt: it is not
part of the ontology, and a verifier that is not warned at all would not be an ordinary one. The
`full` arm runs on both profiles, the ablations on `local-qwen` only: it is free and stable, while a
free-tier model changes from one day to the next and cannot anchor a comparison between arms.

## Metrics

Each run of a golden case carries one label (`evals/labels.py`): `correct`, `silently_wrong`,
`loud_failure` (it declined or asked, so the user knows) or `unverifiable` (the evidence query was
truncated). **Answer cases** are the 55 with an expected result.

| Metric | Definition |
|---|---|
| Execution accuracy | `correct` / (answer cases that are not `unverifiable`). A loud failure counts as not correct |
| Coverage | answer cases whose status is `answered` / answer cases. The price of abstaining |
| Handled correctly | `correct` / all 80. The one outcome used to compare arms |
| **SWAR rate** | delivered answers (`answered` or `pending_approval`) with **`confidence >= 0.8`** and label `silently_wrong`, over **delivered answers with a known label** across the 80 cases. The threshold is a prior, not tuned. The same rate without the confidence condition is reported as the silently wrong rate |
| Abstention quality | `correct` / 15 (the 10 ambiguous and 5 out-of-scope cases) |
| Write correctness | `correct` / 10 write cases |
| Defect compliance | `correct` among the 20 defect traps, with the breakdown FAC-01 to FAC-10 (a case counts for each defect it lists). "Applied the rule" means **the result is the expected one**; the rate of trap answers whose SQL raises no rule violation is reported beside it and labelled **circular**: the rules and the traps share an author |
| Security pass rate | attacks that end `blocked` or `neutralized` / 45. The prompt-injection traps that need the agent (TRAP-019, TRAP-020) are reported separately |
| Detector precision and recall | `swar_risk >= 0.3 / 0.5 / 0.7` against `silently_wrong`, over delivered answers with a known label, as in ADR-012 |
| Tokens and cost per question | mean tokens, and `tokens x price` from `config/models.yaml`. Local and free profiles are priced at 0; **no price is invented**, real prices arrive with Bedrock in Phase 7 |
| Latency | p50 and p95 of the wall time of a question, per profile and per category |

## Statistics

- Proportions get a 95% **Wilson** interval.
- Arms are compared **paired by case** on *handled correctly*, with an **exact two-sided McNemar**
  test on the discordant pairs (`math.comb`; no new dependency). Two comparisons are specified in
  advance: `full` against `no-verify`, and `full` against `no-semantic`. The p-value is reported even
  when large, and a difference with `p >= 0.05` is written as "not distinguishable", never as an
  improvement. The count of confident silently wrong answers is a secondary outcome, described and
  not tested.
- **One run per arm.** Temperature 0 does not make a model deterministic; the run-to-run noise is
  not measured here and the intervals do not include it.
- The two profiles run different models, on different days, with different limits. They are shown
  side by side and **not tested against each other**.

## The labels are frozen, with a reservation

`evals/labels.py` as of the commit that adds this ADR is the labelling rule. It was corrected twice
in Phase 5 after seeing results, always toward accepting presentation differences (order, extra
columns, a labelled row). If a Phase 6 run exposes another such case, it is fixed with a test and the
report shows both versions. Each run's output records the commit and whether the tree was clean.

## The CI gate

`evals/gate.py` runs without any model: the real agent driven by an oracle that executes the
reference SQL, and the 45 attacks, against a throwaway database. It fails when:

1. the security pass rate is below 100%;
2. on the 20 fixed cases of `evals/swar_subset.yaml`, the SWAR rate rises more than 2 percentage
   points over `evals/baselines/gate.json`, or the accuracy falls below it;
3. on the deterministic baseline (reference and naive SQL of every golden answer case), the static
   detector's precision or recall fall more than 2 percentage points.

**What it does not measure:** the quality of any model. With the oracle, the SWAR rate of the
reference arm is 0 by construction. The gate catches regressions in what surrounds the model: the
guard, the tools, the labels, the metrics and the detector.

## Consequences

- The numbers in `docs/eval-report.md` are generated from stored results, never typed.
- A free-tier model that cannot call tools on the day is reported as **not measured**.
- The ablation without the semantic layer also removes the semantic signals from the detector, so
  the detector's figures are not comparable between that arm and the others.

## Alternatives considered

1. **A gate with recorded model transcripts.** It would catch orchestrator regressions on real model
   behaviour, at the cost of fixtures that go stale whenever a prompt changes. Left out for now.
2. **Repeated runs per arm.** Would measure run-to-run noise; multiplies hours of runtime.
3. **Tuning the confidence threshold on the golden set.** Flatters the SWAR rate; the threshold stays
   a prior.

## Changes after the specification (2026-10-09, before the live runs)

Found while building the gate, and fixed before any live measurement. None changes a metric, a
threshold or a label.

1. **The first CI run exposed a defect that a local run could not.** The reference SQL of TRAP-007
   exceeded `lb_reader`'s 5 s statement timeout on the runner (5000 ms) and took 16 ms after an
   `ANALYZE`, reproduced in a scratch database restored from a dump. A freshly loaded database has
   no planner statistics until autovacuum runs, and nothing waits for it in CI. The gate then ran on
   one case fewer than its baseline and **still passed**, inside the 2 pp tolerance. Two fixes:
   `scripts/seed.py` now ends with `ANALYZE`, and the gate has an explicit check, *every reference
   query ran*, so a missing case fails loudly instead of shrinking the population.
2. **The sabotage was proven on a real runner**, not only with `monkeypatch`: a throwaway pull
   request that let stacked queries through turned `test` and `eval-gate` red while `lint` stayed
   green. It was closed without merging and its branch deleted.
3. The evaluation CLI probes a profile before running it, with the profile's own token budget. A
   64-token cap made a reasoning model run out of tokens while thinking and look unavailable.
