# SWAR detector measurement: omniroute-free

Generated 2026-10-09 06:05 UTC by `evals/swar_eval.py`. Method and limits: `docs/adr/ADR-012-swar-detector.md`.

- Runs: **20** (15 correct, 3 silently_wrong, 2 loud_failure, 0 unverifiable).
- Population for precision/recall: **14** delivered answers with a known label, of which **3 silently wrong**.
- Weights and thresholds are priors from `config/swar.yaml`; none was fitted to these results.

## Per run

| case | variant | status | label | risk | level | signals |
|---|---|---|---|---|---|---|
| SIMPLE-003 | live | answered | correct | 0.00 | low | - |
| SIMPLE-008 | live | answered | correct | 0.00 | low | - |
| SIMPLE-013 | live | answered | correct | 0.00 | low | - |
| JOIN-005 | live | answered | silently_wrong | 0.00 | low | - |
| JOIN-009 | live | answered | silently_wrong | 0.00 | low | - |
| JOIN-014 | live | answered | correct | 0.00 | low | - |
| TRAP-001 | live | needs_clarification | loud_failure | 0.00 | low | - |
| TRAP-005 | live | answered | correct | 0.00 | low | - |
| TRAP-006 | live | answered | correct | 0.00 | low | - |
| TRAP-008 | live | answered | correct | 0.00 | low | - |
| TRAP-011 | live | needs_clarification | loud_failure | 0.00 | low | - |
| TRAP-013 | live | answered | correct | 0.00 | low | - |
| TRAP-019 | live | answered | correct | 0.40 | elevated | repeated_tool_call |
| AMB-001 | live | needs_clarification | correct | 0.00 | low | - |
| AMB-005 | live | needs_clarification | correct | 0.00 | low | - |
| AMB-008 | live | needs_clarification | correct | 0.00 | low | - |
| OOS-002 | live | abstained | correct | 0.00 | low | - |
| WRITE-002 | live | pending_approval | correct | 0.00 | low | - |
| WRITE-008 | live | pending_approval | silently_wrong | 0.90 | high | verifier_disagreed_final |
| WRITE-009 | live | pending_approval | correct | 0.00 | low | - |

## Precision and recall (all signals)

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 2 | 1 | 1 | 2 | 10 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.33 (1/3; 95% CI 0.06-0.79) |
| >= 0.5 | 1 | 1 | 0 | 2 | 11 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.33 (1/3; 95% CI 0.06-0.79) |
| >= 0.7 | 1 | 1 | 0 | 2 | 11 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.33 (1/3; 95% CI 0.06-0.79) |

Mean risk: silently wrong **0.30** (n=3), correct **0.04** (n=11).

## As it ran, before the detector was fixed

These runs were scored and labelled while they ran, with the detector and the labelling rules as they were then. Both were corrected afterwards (ADR-012, change log), and the tables above are the same stored runs **rescored and relabelled**. Because the defects were seen in these very runs, the corrected numbers are not an independent test.

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 2 | 1 | 1 | 4 | 8 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.20 (1/5; 95% CI 0.04-0.62) |
| >= 0.5 | 1 | 1 | 0 | 4 | 9 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.20 (1/5; 95% CI 0.04-0.62) |
| >= 0.7 | 1 | 1 | 0 | 4 | 9 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.20 (1/5; 95% CI 0.04-0.62) |

Cases whose risk or label changed:

- JOIN-014: risk 0.00 -> 0.00, label silently_wrong -> correct (same rows, different order)
- TRAP-013: risk 0.00 -> 0.00, label silently_wrong -> correct (the figure is one labelled row of 2)

## Static signals and dynamic signals, separately

Static = derivable from the SQL and the question alone (rule violations, an unstated period). Dynamic = what only a run shows (verifier, trajectory, figures). The rules, the traps and the metrics share an author and a catalogue, so the static numbers are partly circular.

**static only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 0 | 0 | 0 | 3 | 11 | n/a (no cases) | 0.00 (0/3; 95% CI 0.00-0.56) |
| >= 0.5 | 0 | 0 | 0 | 3 | 11 | n/a (no cases) | 0.00 (0/3; 95% CI 0.00-0.56) |
| >= 0.7 | 0 | 0 | 0 | 3 | 11 | n/a (no cases) | 0.00 (0/3; 95% CI 0.00-0.56) |

**dynamic only**

| threshold | flagged | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| >= 0.3 | 2 | 1 | 1 | 2 | 10 | 0.50 (1/2; 95% CI 0.09-0.91) | 0.33 (1/3; 95% CI 0.06-0.79) |
| >= 0.5 | 1 | 1 | 0 | 2 | 11 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.33 (1/3; 95% CI 0.06-0.79) |
| >= 0.7 | 1 | 1 | 0 | 2 | 11 | 1.00 (1/1; 95% CI 0.21-1.00) | 0.33 (1/3; 95% CI 0.06-0.79) |

## Errors the rules cannot see

3 of 3 silently wrong answers raised no rule signal (a wrong result with no violation). The static rules do not see them, by construction; whether the dynamic signals did is the risk shown on each line.

- JOIN-005 (live): different result set; risk 0.00
- JOIN-009 (live): different result set; risk 0.00
- WRITE-008 (live): the request would change different rows than expected; risk 0.90

## Early warning: risk from the first k events

Replay of the stored events at the action threshold (0.70). C2 is where the early warning acts before the verifier is spent; the final row adds the verifier.

| first k events | silently wrong flagged | correct flagged (false alarms) |
|---|---|---|
| 3 | 0/3 | 0/11 |
| 6 | 0/3 | 0/11 |
| 9 | 0/3 | 0/11 |
| C2 (end of the writer) | 0/3 | 0/11 |
| all (final) | 1/3 | 0/11 |

The early warning acted in 0 of 20 runs (0 is expected in `annotate` mode).

## Cost

Mean per run: 68.8 s, 6.4 model calls, 15329 input and 1528 output tokens.

## Limits of this measurement

- **Small sample.** The intervals say how little a handful of cases can tell. Do not quote a point value.
- **Circularity.** Defect rules, traps, metrics and the catalogue come from one author; recall on rule signals holds by construction. The verifier's briefing includes the rule warnings.
- **The golden set is `review: draft`.** A label is only as good as the case behind it.
- **Strict labels.** Columns are compared by position (ADR-011); extra columns are tolerated. A correct answer phrased as a different query shape can be mislabelled wrong.
- **Free-tier models change from day to day** (ADR-010): a result for `omniroute-free` describes that model on that day.
