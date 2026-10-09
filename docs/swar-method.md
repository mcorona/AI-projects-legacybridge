# SWAR: predicting when an agent is about to be wrong, silently

> **Draft.** The method and the measurement sections are written from the code and the reports. The
> section marked **👤** is yours: it connects this to your thesis, and I do not know its details well
> enough to write it without inventing them.

## The problem

An agent over a legacy database fails in two very different ways.

- **Loud**: it errors, abstains or asks a question. The user knows nothing was answered and can act.
- **Silent**: it answers, confidently, and the answer is wrong. This is *Silently Wrong At Runtime*
  (SWAR), and in a legacy schema it is the common failure: a text amount summed without cleaning it,
  a logically deleted row counted, an invoice captured twice and counted twice. The query runs, the
  number looks plausible, and nothing in the output says otherwise.

A model's own confidence does not separate them (in an earlier version of this project the reported
confidence was almost always 1.0). The question is whether the *run itself* leaves evidence that the
answer is risky.

## The method

Every question is a run: planner, writer, verifier, with model calls and tool calls in between.
Each run is traced (OpenTelemetry, GenAI conventions; `docs/adr/ADR-007-observability.md`), and a
detector scores it from raw signals, never from the agent's confidence
(`docs/adr/ADR-012-swar-detector.md`, weights in `config/swar.yaml`).

| Family | Looks at | Example signals |
|---|---|---|
| Result | what the run produced | the verifier disagreed; rule violations left unresolved; a confident answer over zero rows; truncation not acknowledged; figures not in the evidence |
| Trajectory | how it got there | the same tool call repeated; a cascade of failures; rejected SQL; a writer retry |
| Semantics | the question against the ontology | a term whose known defect the SQL violates; a period the question needed and nobody stated |

The score is `1 - prod(1 - w_i)` over the signals that fired. The signals that come from the static
defect rules are capped below the action threshold, because the rules are heuristic and noisy: a lone
heuristic can raise the level but never make the agent act.

### The early warning

The score is also computed *while the run is going*:

- **C1**, after a few tool results, on trajectory signals alone: it can stop a writer that is going in
  circles.
- **C2**, when the writer has finished and before the verifier is spent: result and semantic signals
  join in.

At or above the action threshold the agent picks one action from what raised the risk: ask a
clarification question (an unstated period), rerun on a second model (a risky trajectory), or abstain.
The final score is recomputed at the end, with the verifier's verdict, and returned with every answer
as `swar_risk`, its `swar_signals` and `defects_touched`.

## How it is measured

- **Label.** A delivered answer is silently wrong when it differs from the golden case: a different
  result set (re-executed, hashed as in ADR-011), a figure the answer does not state, no query behind
  it, or, for ambiguous and out-of-scope questions, answering instead of asking or abstaining.
  Refusing is a loud failure and is not counted. `evals/labels.py`.
- **A fixed subset**, 20 golden cases, chosen and committed *before* any measurement
  (`evals/swar_subset.yaml`).
- **Two measurements**, kept apart on purpose:
  1. a **deterministic baseline** with no LLM, in which the real agent runs the reference SQL and the
     naive SQL of every golden answer case. It measures the static signals only;
  2. **live runs** with real models on the subset.
- **Reports** in `evals/reports/swar-*.md`: precision and recall at three thresholds with Wilson
  intervals, static and dynamic signals separately, the errors the rules cannot see, and the risk
  recomputed from the first *k* events of each stored run.

```bash
uv run python -m evals.swar_eval baseline                      # no LLM
uv run python -m evals.swar_eval live --profile local-qwen     # the 20 cases, live
```

## What the numbers do and do not say

The baseline (static signals, 68 delivered answers, 13 silently wrong) gives precision 0.60 and
recall 0.92 at threshold 0.3 or 0.5, and flags nothing at the action threshold. Read that as: the
rules recognise most of the wrong answers they were designed to recognise, are wrong about one in
three of what they flag, and by construction cannot make the agent act on their own.

Limits, stated plainly:

- **Circularity.** The rules, the traps, the metrics and the defect catalogue come from the same
  author. Recall on the rule signals holds *by construction*; it is not evidence that the detector
  finds errors it was not built to find. The verifier's briefing also includes the rule warnings.
- **Blind spots.** One wrong answer in the baseline (TRAP-009) raises no rule signal at all. Only the
  dynamic signals can catch errors the rules do not know, and the report lists those separately.
- **Small samples.** Twenty live cases yield few positives; the intervals are wide and a point value
  should not be quoted.
- **Draft labels.** The golden set is still `review: draft`.
- **Priors, not fits.** The weights were written down before measuring and are not fitted to the
  results. Three definition errors were fixed after the first baseline; none was a weight change
  (see the change log in ADR-012).

## 👤 Connection to your thesis

*(Yours to write.)* The thesis, *Software Behavior Prediction Using Ontologies* (2015), is about
predicting behaviour from execution traces. The idea here is the same move, from traces of software
to traces of agents. What belongs in this section, and only you can say it accurately:

- which parts of the thesis's approach map to the signal families above, and which do not;
- how the ontology plays the same role in both (here: defect rules and term definitions);
- what the thesis found about prediction from partial traces, which is what the early warning's
  first-*k*-events replay tries to test;
- where this work deliberately differs from it.
