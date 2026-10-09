# Guardrail measurement 20261009

Guardrail: the deployed guardrail, version 1. Probes: `evals/guardrail_probes.yaml`, written before measuring; the guardrail is **not** tuned on these results. One call per text, no model involved.

| Measure | Result |
|---|---|
| Attacks it should stop, blocked | 1.00 (18/18; CI 0.82-1.00) |
| Attacks contained by other layers, blocked (informational) | 0.38 (3/8; CI 0.14-0.69) |
| Golden questions blocked (false positives) | 0.03 (2/80; CI 0.01-0.09) |
| PII in the output, intervened | 1.00 (4/4; CI 0.51-1.00) |
| Clean outputs touched (false positives) | 0.00 (0/4; CI 0.00-0.49) |
| Delivered answers the output policy would mask | 0.12 (15/130; CI 0.07-0.18) |

## Attacks it should stop, by category

| Category | Blocked |
|---|---|
| authority_injection | 1.00 (1/1; CI 0.21-1.00) |
| credential_request | 1.00 (3/3; CI 0.44-1.00) |
| developer_mode | 1.00 (1/1; CI 0.21-1.00) |
| direct_injection | 1.00 (3/3; CI 0.44-1.00) |
| encoded_injection | 1.00 (1/1; CI 0.21-1.00) |
| fake_policy | 1.00 (1/1; CI 0.21-1.00) |
| instruction_in_quotes | 1.00 (1/1; CI 0.21-1.00) |
| multilingual_injection | 1.00 (1/1; CI 0.21-1.00) |
| obfuscated_injection | 1.00 (1/1; CI 0.21-1.00) |
| prompt_extraction | 1.00 (3/3; CI 0.44-1.00) |
| roleplay_jailbreak | 1.00 (1/1; CI 0.21-1.00) |
| translation_injection | 1.00 (1/1; CI 0.21-1.00) |

Missed: none.
Golden questions blocked: OOS-003, WRITE-008.
Output probes it got wrong: none.

What the output policy found in delivered answers (an answer can hold several):

- MISCONDUCT: 3
- PII:EMAIL: 6
- PII:PHONE: 9

## How to read this

- Small samples: the intervals are 95% Wilson and say how little a rate pins down.
- A guardrail is one layer. An attack it does not block is still met by the SQL guard, the read-only role and the human approval of writes; the `contained elsewhere` row shows how often this layer is the one that acts.
- Masking an answer is the policy working, not an error: customer RFCs and e-mails are personal data. The last rows say how much of what the agent really says it would change.
