# ADR-012: A SWAR risk score from raw signals, with an early warning that acts

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 5 and Phase 6 of `docs/PLAN.md`, ADR-002, ADR-006, ADR-007,
  `src/legacybridge/observability/swar_detector.py`, `src/legacybridge/agent/early_warning.py`,
  `config/swar.yaml`

## Context

SWAR (*Silently Wrong At Runtime*) is an answer that is wrong and delivered with confidence. Loud
failures (an error, an abstention, a clarification question) are not SWAR. The agent already lowers
its confidence for some observable problems (`calibrate`), but confidence alone cannot tell us
whether a run was risky, and the Phase 6 SWAR rate is defined from confidence, so a detector built
on confidence would be circular.

## Decision

**A score from raw signals, never from `confidence`.** `swar_detector.assess` is a pure function of
what happened in a run. It returns a risk in [0, 1], a level and the list of signals that fired,
in three families:

| Family | Signals |
|---|---|
| Result | The verifier disagreed (even if a retry later fixed it); rule violations left unresolved; a confident answer over zero rows; truncation not acknowledged in the text; figures in the answer that are not in the evidence; no independent check |
| Trajectory | Identical tool calls repeated; a cascade of failing tool calls; repeated rejected SQL; a writer retry; model retries or empty completions; the step limit |
| Semantics | A term in the question whose known defects are *violated* in the SQL; a *parameter left unstated* (the question touches a metric that needs a year, names none, and the assumptions do not either) |

**Combination:** `risk = 1 - prod(1 - w_i)` over fired signals (noisy-OR). The signals derived from the static defect rules form a **group with a cap of 0.6**,
below the action threshold: the rules are heuristic (8 of 35 neutral golden cases draw a warning),
so a lone heuristic can raise the level but never make the agent act.

**The weights are priors, written down in `config/swar.yaml`, and are not fitted to the golden
set.** Fitting them to the cases we then report on would flatter the detector. Precision and recall
are reported at several thresholds, not at the best one.

**An early warning that acts**, controlled by `swar_action = gate | annotate | off`:

- Checkpoint **C1**, after *k* tool results (default 6), uses trajectory signals only and can stop a
  stuck writer loop early.
- Checkpoint **C2**, when the writer finishes and before the verifier runs, adds result and
  semantic signals.
- At or above the action threshold: an *unstated parameter* becomes a clarification question built
  from the metric's parameter; a trajectory or result risk **escalates** (one rerun from scratch on
  an optional second profile, costs accumulated); with no escalation profile, or if the risk
  persists, the agent **abstains** (`swar_risk`) and labels the candidate unverified.
- The final risk is recomputed at the end, including the verifier's verdict, and returned as
  `swar_risk`.

## Measuring it honestly

- **Label.** An `answered` result is silently wrong when the result set of its evidence SQL differs
  from the case's `expected_result_hash` (re-executed, because evidence rows are truncated, and
  hashed with the expected SQL's ordering), or when it answered with no evidence. For ambiguous and
  out-of-scope cases, answering instead of abstaining is silently wrong; for write cases, queuing a
  different change is.
- **Circularity is stated, not hidden.** The rules, the traps and the metrics share an author and a
  catalogue, so recall on the static signals holds by construction; the verifier's briefing includes
  the rule warnings, so its agreement correlates with them. The report therefore separates static
  from dynamic signals and lists rule-blind errors (wrong result, no violation) on their own.
- **A fixed 20-case subset**, committed before measuring, free of tie-prone `ORDER BY ... LIMIT`
  cases. The golden set is still `review: draft`, so labels inherit that uncertainty.
- **Early warning is checked by replay:** the stored events of each run give the risk computed from
  the first *k* spans.

## Consequences

- Every answer carries `swar_risk`, its signals and `defects_touched`, and the root span carries the
  same as attributes.
- With the early warning on, thresholds that are too eager would turn right answers into
  abstentions. Phase 6 measures that with an ablation (`gate` against `annotate`).
- A wrong answer that triggers no signal is invisible to this detector; the report counts those.

## Alternatives considered

1. **Fit the weights on the golden set.** Better-looking numbers, meaningless as a measurement.
2. **Use the calibrated confidence as the score.** Circular with the Phase 6 SWAR rate.
3. **An LLM judge for risk.** Non-deterministic and costs a call per question; the signals are free.
4. **Only annotate.** Safe, but the plan asks for an agent that acts on the warning.

## Changes after the first baseline (2026-10-09)

The deterministic baseline (`python -m evals.swar_eval baseline`: the real agent running the
reference and the naive SQL of every golden answer case, no LLM, verifier agreeing) exposed three
**definition errors**. None is a change of weights or thresholds; all three were wrong on their face
and were fixed with a test:

1. `unstated_parameter` fired on count questions ("how many units have been sold") that have a
   legitimate all-time reading. Count questions are now exempt; "how much" still fires.
2. A finding about the data (instructions in a cell, FAC-10) counted as `unresolved_violations`,
   whose message says "the final query still breaks FAC-10". Data-rule findings are excluded.
3. The rule engine did not recognise a key lookup through a normalised key
   (`LPAD(TRIM(numfac), 10, '0') = '...'`), so the correct fix for FAC-01 raised FAC-03.

Static signals only, population of 68 delivered answers (13 silently wrong), threshold 0.3 or 0.5:

| | flagged | precision | recall |
|---|---|---|---|
| before | 24 | 0.50 (12/24) | 0.92 (12/13) |
| after | 20 | 0.60 (12/20) | 0.92 (12/13) |

At the action threshold (0.7) three answers were flagged before (1 right, 2 wrong); **none is
after**, which is the design: rule signals alone never act. The remaining 8 false alarms are the
known neutral-case noise of the rules (`EXPECTED_NOISE` in `tests/test_semantic.py`). One silently
wrong answer (TRAP-009) raises no rule signal at all: the rules are blind to it by construction.

These fixes were motivated by golden cases, which is why the 20-case subset was fixed and committed
before the baseline ran, and why none of the cases that exposed them (JOIN-001, TRAP-007, TRAP-020)
belongs to it, except TRAP-019, whose behaviour follows from fix 2 and not from its label.

## Changes after the first live runs (2026-10-09)

Two live runs on the fixed 20-case subset (local Qwen and, with a single free model,
`omniroute-free`) exposed two more definition errors, one in the detector and one in the labels:

4. **A verdict was treated as speaking for the final answer when it judged an earlier attempt.** The
   verifier disagreed with attempt 0, the writer retried, the retry got no verdict, and the run was
   scored `verifier_disagreed_final`. A verdict now counts only for the attempt it judged; an
   unjudged final answer is `unverified`, and an earlier disagreement is `verifier_disagreed_retried`.
5. **The labels punished differences of presentation.** The same rows in another order, when the
   expected query sorts but does not `LIMIT`, and a single expected figure inside a wider result,
   were labelled silently wrong. Both are now accepted, the second only when the figure sits in
   exactly one row **that carries a label** and the answer states it. The first version of that
   second rule was too loose: it accepted TRAP-015's naive query, which returns both captures of an
   invoice in one unlabelled column. The baseline test caught it before it was published.

Because the detector and the labels are functions of what a result stores, the stored runs were
**rescored and relabelled**, and every report shows both versions ("As it ran"). That is not an
independent test: the defects were seen in those very runs, and **all three relabelled cases moved
from silently wrong to correct**, so the corrections made the agent look better, not the detector.

| | as it ran | relabelled |
|---|---|---|
| local Qwen, silently wrong | 3 of 13 delivered answers | 2 of 13 (SIMPLE-013: same rows, other order) |
| omniroute-free, silently wrong | 5 of 14 | 3 of 14 (JOIN-014 order; TRAP-013 labelled row) |

## What the first live runs say

Final risk, 27 delivered answers (13 local Qwen, 14 omniroute-free), **5 silently wrong**. The two
models are different, so the pooled line is a description, not an estimate for either.

| | local Qwen (2 wrong) | omniroute-free (3 wrong) | pooled (5 wrong) |
|---|---|---|---|
| risk >= 0.3 | recall 2/2, precision 2/6 | recall 1/3, precision 1/2 | recall 3/5, precision 3/8 |
| risk >= 0.5 | recall 2/2, precision 2/5 | recall 1/3, precision 1/1 | recall 3/5, precision 3/6 |
| risk >= 0.7 (action) | recall 1/2, precision 1/2 | recall 1/3, precision 1/1 | recall 2/5, precision 2/3 |

With 2, 3 and 5 positives these are not estimates: the 95% intervals in the reports span most of the
range. What they support:

- **The static rules saw none of the five.** All five wrong answers raised no rule signal. The rules
  are what the traps were built from, and these errors are of a different kind (see below).
- **What caught the wrong answers was the verifier, not the rules.** TRAP-008 (0.91) and WRITE-008
  (0.90, the verifier noticed that the request touched 1 of 2 rows) were flagged by
  `verifier_disagreed_*` and trajectory signals. JOIN-005 on Qwen reached 0.55 after a retry.
- **Two wrong answers were invisible to everything.** On `omniroute-free`, JOIN-005 (240 instead of
  241) and JOIN-009 (2 instead of 1) both added `faccab.status = 'V'`, a filter the question did not
  ask for. The verifier **agreed** with the same reading and no signal fired. This is the SWAR the
  detector cannot see: the writer and the verifier share a wrong interpretation.
- **False alarms** are mostly erratic-but-right trajectories (TRAP-006 on Qwen: repeated calls and a
  failure cascade, correct answer, risk 0.92 after the verifier's disagreement on a retry).

### The early warning did not act on any real run

C2 (before the verifier) flagged **0 of 5** wrong answers and 0 of 25 right ones. C1 was reached in
only 5 of 40 runs (the writer rarely makes six tool calls) and in none did the trajectory risk reach
the action threshold. So **with these priors, `gate` and `annotate` would have behaved identically on
all 40 runs**: the early warning is verified only by the scripted tests (`tests/test_swar.py`), which
exercise every action, and not by a real run. The claim that a risk computed from the first spans is
informative is **not supported** by this sample; what the data shows is that the verifier's verdict
is what separates most of the wrong answers that can be separated. Whether `gate` pays for itself,
whether lower thresholds would, and what an independent second reading of the question would add
(the case of JOIN-005 and JOIN-009) are questions for Phase 6, which has the sample size to ask them.
