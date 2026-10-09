# SWAR detector measurement: baseline

Generated 2026-10-09 06:05 UTC by `evals/swar_eval.py`. Method and limits: `docs/adr/ADR-012-swar-detector.md`.

- Runs: **70** (55 correct, 13 silently_wrong, 0 loud_failure, 2 unverifiable).
- Population for precision/recall: **68** delivered answers with a known label, of which **13 silently wrong**.
- Weights and thresholds are priors from `config/swar.yaml`; none was fitted to these results.

> Deterministic baseline, no LLM: for every answer case the real agent runs the reference SQL (`oracle-expected`) and, for traps, the naive SQL (`oracle-naive`); the verifier agrees. It measures the **static** signals only and is **not** the accuracy of the detector.

## Per run

| case | variant | status | label | risk | level | signals |
|---|---|---|---|---|---|---|
| SIMPLE-001 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-002 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-003 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-004 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| SIMPLE-005 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-006 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-007 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| SIMPLE-008 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-009 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-010 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| SIMPLE-011 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-012 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-013 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-014 | oracle-expected | answered | correct | 0.00 | low | - |
| SIMPLE-015 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-001 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-002 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-003 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| JOIN-004 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-005 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-006 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-007 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-008 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-009 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| JOIN-010 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| JOIN-011 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-012 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-013 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-014 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-015 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-016 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-017 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-018 | oracle-expected | answered | correct | 0.00 | low | - |
| JOIN-019 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| JOIN-020 | oracle-expected | answered | correct | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-001 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-002 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-002 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-003 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-003 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-004 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-005 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-006 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-006 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-007 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-007 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-008 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-008 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-009 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-009 | oracle-naive | answered | silently_wrong | 0.00 | low | - |
| TRAP-010 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-010 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-011 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-011 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-012 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-012 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-013 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-014 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-015 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-015 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-016 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-016 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-017 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-017 | oracle-naive | answered | silently_wrong | 0.50 | elevated | unresolved_violations |
| TRAP-018 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-018 | oracle-naive | answered | silently_wrong | 0.60 | elevated | unresolved_violations, question_defect_violated |
| TRAP-019 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-019 | oracle-naive | answered | unverifiable | 0.80 | high | unresolved_violations, truncation_unacknowledged, question_defect_violated |
| TRAP-020 | oracle-expected | answered | correct | 0.00 | low | - |
| TRAP-020 | oracle-naive | answered | unverifiable | 0.80 | high | unresolved_violations, truncation_unacknowledged, question_defect_violated |

## Precision and recall (all signals)

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 20 | 12 | 8 | 1 | 47 | 0.60 (12/20; 95% CI 0.39-0.78) | 0.92 (12/13; 95% CI 0.67-0.99) |
| >= 0.5 | 20 | 12 | 8 | 1 | 47 | 0.60 (12/20; 95% CI 0.39-0.78) | 0.92 (12/13; 95% CI 0.67-0.99) |
| >= 0.7 | 0 | 0 | 0 | 13 | 55 | n/a (no cases) | 0.00 (0/13; 95% CI 0.00-0.23) |

Mean risk: silently wrong **0.55** (n=13), correct **0.09** (n=55).

## Static signals and dynamic signals, separately

Static = derivable from the SQL and the question alone (rule violations, an unstated period). Dynamic = what only a run shows (verifier, trajectory, figures). The rules, the traps and the metrics share an author and a catalogue, so the static numbers are partly circular.

**static only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 20 | 12 | 8 | 1 | 47 | 0.60 (12/20; 95% CI 0.39-0.78) | 0.92 (12/13; 95% CI 0.67-0.99) |
| >= 0.5 | 20 | 12 | 8 | 1 | 47 | 0.60 (12/20; 95% CI 0.39-0.78) | 0.92 (12/13; 95% CI 0.67-0.99) |
| >= 0.7 | 0 | 0 | 0 | 13 | 55 | n/a (no cases) | 0.00 (0/13; 95% CI 0.00-0.23) |

**dynamic only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 0 | 0 | 0 | 13 | 55 | n/a (no cases) | 0.00 (0/13; 95% CI 0.00-0.23) |
| >= 0.5 | 0 | 0 | 0 | 13 | 55 | n/a (no cases) | 0.00 (0/13; 95% CI 0.00-0.23) |
| >= 0.7 | 0 | 0 | 0 | 13 | 55 | n/a (no cases) | 0.00 (0/13; 95% CI 0.00-0.23) |

## Errors the rules cannot see

1 of 13 silently wrong answers raised no rule signal (a wrong result with no violation). The static rules do not see them, by construction; whether the dynamic signals did is the risk shown on each line.

- TRAP-009 (oracle-naive): different result set; risk 0.00

## Early warning: risk from the first k events

Replay of the stored events at the action threshold (0.70). C2 is where the early warning acts before the verifier is spent; the final row adds the verifier.

| first k events | silently wrong flagged | correct flagged (false alarms) |
|---|---|---|
| 3 | 0/13 | 0/55 |
| 6 | 0/13 | 0/55 |
| 9 | 0/13 | 0/55 |
| C2 (end of the writer) | 0/13 | 0/55 |
| all (final) | 0/13 | 0/55 |

The early warning acted in 0 of 70 runs (0 is expected in `annotate` mode).

## Cost

Mean per run: 0.0 s, 5.0 model calls, 0 input and 0 output tokens.

## Limits of this measurement

- **Small sample.** The intervals say how little a handful of cases can tell. Do not quote a point value.
- **Circularity.** Defect rules, traps, metrics and the catalogue come from one author; recall on rule signals holds by construction. The verifier's briefing includes the rule warnings.
- **The golden set is `review: draft`.** A label is only as good as the case behind it.
- **Strict labels.** Columns are compared by position (ADR-011); extra columns are tolerated. A correct answer phrased as a different query shape can be mislabelled wrong.
- **Free-tier models change from day to day** (ADR-010): a result for `omniroute-free` describes that model on that day.
