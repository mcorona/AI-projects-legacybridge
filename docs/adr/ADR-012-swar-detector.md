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

## Changes after the first live run (2026-10-09)

The first live run (local Qwen, the fixed 20-case subset) exposed a fourth definition error:

4. **A verdict was treated as speaking for the final answer when it judged an earlier attempt.** The
   verifier disagreed with attempt 0, the writer retried, the retry got no verdict, and the run was
   scored `verifier_disagreed_final`. A verdict now only counts for the attempt it judged; an
   unjudged final answer is `unverified`, with an earlier disagreement recorded as
   `verifier_disagreed_retried`.

Because the detector is a pure function of what a result stores, the stored runs were **rescored**
with the corrected detector, and the report shows both versions. This does not make the rescored
numbers an independent test: the defect was seen in those very runs. In fact the fix moved one case
(TRAP-006, 0.98 to 0.92) and **no cell of the confusion tables**; that case stays "high" because its
trajectory really was erratic (repeated calls, a cascade of failures) although its final answer was
right. That is a false alarm of a detector that scores the run and not only the answer, and it is
reported as one.

## What the first live run says about the early warning

Local Qwen, 20 cases, 13 delivered answers of which 3 were silently wrong (a different result set,
two of them after a writer retry and a verifier disagreement; one that mentions figures that are not
in its evidence). None of the three was visible to the static rules.

| | silently wrong flagged | correct flagged |
|---|---|---|
| risk >= 0.5, final | 3/3 | 2/10 |
| risk >= 0.7 (action threshold), final | 1/3 | 1/10 |
| risk >= 0.7 at **C2**, before the verifier | **0/3** | 0/10 |

So, on this sample, **the early warning would not have caught any of the three wrong answers before
the verifier ran**. The writer produced them cleanly; what exposed them was the verifier disagreeing,
or flailing. The claim that a risk computed from the first spans is informative is **not supported
here**. What the checkpoints can still do is stop a stuck loop and spare model calls (C1), which this
sample did not exercise. Whether `gate` improves the SWAR rate is the Phase 6 ablation; this phase
only shows that the detector, with the verifier's verdict, separates most wrong answers (recall 3/3 at
0.5), at a cost of two false alarms in ten correct answers.
