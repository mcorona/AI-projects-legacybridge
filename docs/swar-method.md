# SWAR: predicting when an agent is about to be wrong, silently

> **Draft.** The method and the measurement sections are written from the code and the reports. The
> thesis section is a short statement of the idea; the author may expand it.

## The problem

An agent over a legacy database fails in two very different ways.

- **Loud**: it errors, abstains or asks a question. The user knows nothing was answered and can act.
- **Silent**: it answers, confidently, and the answer is wrong. This is *Silently Wrong At Runtime*
  (SWAR), and in a legacy schema it is the common failure: a text amount summed without cleaning it,
  a logically deleted row counted, an invoice captured twice and counted twice. The query runs, the
  number looks plausible, and nothing in the output says otherwise.

A model's own confidence does not separate them (in the evaluation, 13% to 30% of the delivered
answers were wrong *and* confident). The question is whether the *run itself* leaves evidence that the
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

Three measurements, in `evals/reports/` and summarised in ADR-012:

- **Baseline with no LLM** (static signals, 68 delivered answers, 13 silently wrong): precision 0.60
  and recall 0.92 at 0.3 or 0.5, and nothing flagged at the action threshold. The rules recognise most
  of what they were built to recognise and are wrong about two in five of what they flag.
- **Live, local Qwen and `omniroute-free`, 20 fixed cases each**: 27 delivered answers, 5 silently
  wrong. The static rules saw none of the five. At 0.5 the detector flagged 3 of 5 (recall 3/5,
  precision 3/6); at the action threshold 2 of 5. What caught them was the verifier.
- **Two wrong answers nothing caught**: the writer and the verifier shared the same wrong reading of
  a question. That is the SWAR this method does not see, and the one a second, independent reading
  would address.

**The early warning did not act on any of 40 real runs.** C2 flagged none of the wrong answers
before the verifier ran, and C1 was reached in 5 runs and never crossed the threshold. The mechanism
is verified by scripted tests, not yet by a real case, and this sample does not support the claim that
the first spans already tell you the answer will be wrong.

Limits, stated plainly:

- **Circularity.** The rules, the traps, the metrics and the defect catalogue come from the same
  author. Recall on the rule signals holds *by construction*; it is not evidence that the detector
  finds errors it was not built to find. The verifier's briefing also includes the rule warnings.
- **Tiny samples.** Two, three and five positives. The intervals span most of the range; quote none.
- **Corrected after the fact.** Five definition errors (four in the detector and its rules, one in the
  labels) were fixed after seeing results. None changed a weight or a threshold. The relabelled cases
  all moved toward "correct"; the reports keep the as-run figures next to the corrected ones.
- **Draft labels.** The golden set is still `review: draft`.
- **Priors, not fits.** Weights and thresholds were written down before measuring.
- **Models change.** A free-tier result describes that model on that day (ADR-010).

## Connection to the author's thesis

The author's doctoral thesis, *Software Behavior Prediction Using Ontologies* (2015), is about
predicting the behavior of software from its execution traces, with an ontology giving the traces
their meaning. SWAR applies the same idea to a different kind of software, an agent: the trace of a
run is read against an ontology of the domain (here, the defect rules and term definitions of
`docs/ontology.md`) to predict, before anyone checks the answer, whether the run is likely to end in a
silent error.

This states the idea, not an equivalence. This project does not reuse the thesis's method or results,
and how far the two approaches coincide is left for the author to write.
