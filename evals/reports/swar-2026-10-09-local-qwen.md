# SWAR detector measurement: local-qwen

Generated 2026-10-09 06:05 UTC by `evals/swar_eval.py`. Method and limits: `docs/adr/ADR-012-swar-detector.md`.

- Runs: **20** (15 correct, 2 silently_wrong, 3 loud_failure, 0 unverifiable).
- Population for precision/recall: **13** delivered answers with a known label, of which **2 silently wrong**.
- Weights and thresholds are priors from `config/swar.yaml`; none was fitted to these results.

## Per run

| case | variant | status | label | risk | level | signals |
|---|---|---|---|---|---|---|
| SIMPLE-003 | live | answered | correct | 0.00 | low | - |
| SIMPLE-008 | live | abstained | loud_failure | 0.00 | low | - |
| SIMPLE-013 | live | answered | correct | 0.55 | elevated | writer_retry, verifier_disagreed_retried |
| JOIN-005 | live | answered | silently_wrong | 0.55 | elevated | writer_retry, verifier_disagreed_retried |
| JOIN-009 | live | answered | correct | 0.68 | elevated | model_retries, unresolved_violations, question_defect_violated |
| JOIN-014 | live | abstained | loud_failure | 0.20 | low | model_retries |
| TRAP-001 | live | needs_clarification | loud_failure | 0.00 | low | - |
| TRAP-005 | live | answered | correct | 0.00 | low | - |
| TRAP-006 | live | answered | correct | 0.92 | high | repeated_tool_call, error_cascade, writer_retry, model_retries, verifier_disagreed_retried, unverified |
| TRAP-008 | live | answered | silently_wrong | 0.91 | high | repeated_tool_call, error_cascade, figures_not_in_evidence, unverified |
| TRAP-011 | live | answered | correct | 0.40 | elevated | repeated_tool_call |
| TRAP-013 | live | answered | correct | 0.00 | low | - |
| TRAP-019 | live | answered | correct | 0.00 | low | - |
| AMB-001 | live | needs_clarification | correct | 0.00 | low | - |
| AMB-005 | live | needs_clarification | correct | 0.00 | low | - |
| AMB-008 | live | needs_clarification | correct | 0.00 | low | - |
| OOS-002 | live | abstained | correct | 0.00 | low | - |
| WRITE-002 | live | pending_approval | correct | 0.00 | low | - |
| WRITE-008 | live | pending_approval | correct | 0.00 | low | - |
| WRITE-009 | live | pending_approval | correct | 0.00 | low | - |

## Precision and recall (all signals)

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 6 | 2 | 4 | 0 | 7 | 0.33 (2/6; 95% CI 0.10-0.70) | 1.00 (2/2; 95% CI 0.34-1.00) |
| >= 0.5 | 5 | 2 | 3 | 0 | 8 | 0.40 (2/5; 95% CI 0.12-0.77) | 1.00 (2/2; 95% CI 0.34-1.00) |
| >= 0.7 | 2 | 1 | 1 | 1 | 10 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.50 (1/2; 95% CI 0.09-0.91) |

Mean risk: silently wrong **0.73** (n=2), correct **0.23** (n=11).

## As it ran, before the detector was fixed

These runs were scored and labelled while they ran, with the detector and the labelling rules as they were then. Both were corrected afterwards (ADR-012, change log), and the tables above are the same stored runs **rescored and relabelled**. Because the defects were seen in these very runs, the corrected numbers are not an independent test.

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 6 | 3 | 3 | 0 | 7 | 0.50 (3/6; 95% CI 0.19-0.81) | 1.00 (3/3; 95% CI 0.44-1.00) |
| >= 0.5 | 5 | 3 | 2 | 0 | 8 | 0.60 (3/5; 95% CI 0.23-0.88) | 1.00 (3/3; 95% CI 0.44-1.00) |
| >= 0.7 | 2 | 1 | 1 | 2 | 9 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.33 (1/3; 95% CI 0.06-0.79) |

Cases whose risk or label changed:

- SIMPLE-013: risk 0.55 -> 0.55, label silently_wrong -> correct (same rows, different order)
- TRAP-006: risk 0.98 -> 0.92, label correct -> correct (exact)

## Static signals and dynamic signals, separately

Static = derivable from the SQL and the question alone (rule violations, an unstated period). Dynamic = what only a run shows (verifier, trajectory, figures). The rules, the traps and the metrics share an author and a catalogue, so the static numbers are partly circular.

**static only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 1 | 0 | 1 | 2 | 10 | 0.00 (0/1; 95% CI 0.00-0.79) | 0.00 (0/2; 95% CI 0.00-0.66) |
| >= 0.5 | 1 | 0 | 1 | 2 | 10 | 0.00 (0/1; 95% CI 0.00-0.79) | 0.00 (0/2; 95% CI 0.00-0.66) |
| >= 0.7 | 0 | 0 | 0 | 2 | 11 | n/a (no cases) | 0.00 (0/2; 95% CI 0.00-0.66) |

**dynamic only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 5 | 2 | 3 | 0 | 8 | 0.40 (2/5; 95% CI 0.12-0.77) | 1.00 (2/2; 95% CI 0.34-1.00) |
| >= 0.5 | 4 | 2 | 2 | 0 | 9 | 0.50 (2/4; 95% CI 0.15-0.85) | 1.00 (2/2; 95% CI 0.34-1.00) |
| >= 0.7 | 2 | 1 | 1 | 1 | 10 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.50 (1/2; 95% CI 0.09-0.91) |

## Errors the rules cannot see

2 of 2 silently wrong answers raised no rule signal (a wrong result with no violation). The static rules do not see them, by construction; whether the dynamic signals did is the risk shown on each line.

- JOIN-005 (live): different result set; risk 0.55
- TRAP-008 (live): different result set; risk 0.91

## Early warning: risk from the first k events

Replay of the stored events at the action threshold (0.70). C2 is where the early warning acts before the verifier is spent; the final row adds the verifier.

| first k events | silently wrong flagged | correct flagged (false alarms) |
|---|---|---|
| 3 | 0/2 | 0/11 |
| 6 | 0/2 | 0/11 |
| 9 | 0/2 | 0/11 |
| C2 (end of the writer) | 0/2 | 0/11 |
| all (final) | 1/2 | 1/11 |

The early warning acted in 0 of 20 runs (0 is expected in `annotate` mode).

## Cost

Mean per run: 52.9 s, 7.8 model calls, 20662 input and 4342 output tokens.

## Limits of this measurement

- **Small sample.** The intervals say how little a handful of cases can tell. Do not quote a point value.
- **Circularity.** Defect rules, traps, metrics and the catalogue come from one author; recall on rule signals holds by construction. The verifier's briefing includes the rule warnings.
- **The golden set is `review: draft`.** A label is only as good as the case behind it.
- **Strict labels.** Columns are compared by position (ADR-011); extra columns are tolerated. A correct answer phrased as a different query shape can be mislabelled wrong.
- **Free-tier models change from day to day** (ADR-010): a result for `omniroute-free` describes that model on that day.
