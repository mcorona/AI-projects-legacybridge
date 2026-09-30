# 90-second demo — storyboard

Record `make demo` in a terminal (font ≥ 16 pt, 100 columns). Model waits take 10–30 s per scene:
speed them up 3–4× in editing so the video lands at ~90 s. Every scene is a live agent call.

| Time | Scene | On screen | Narration |
|---|---|---|---|
| 0:00–0:08 | Hook | Title line of `make demo`; `docs/LEGACY_DEFECTS.md` table | "Legacy ERPs have cryptic names, no foreign keys, dates as text and NULL flags. Generic text-to-SQL gets them confidently wrong." |
| 0:08–0:22 | 1 · D4 NULL flags | Question → answer **7** → evidence SQL with `COALESCE(cliact,'N') <> 'S'` | "Inactive customers: a naive query counts 'N' and misses NULLs. The agent read the business rule — and shows the exact SQL and rows it used." |
| 0:22–0:38 | 2 · D7 currencies | Two rows, MXN and USD, never summed | "Valid orders only, text dates parsed, and pesos never added to dollars. That's institutional knowledge, not prompt luck." |
| 0:38–0:46 | 3 · Direct injection | `outcome=refusal · 0 LLM calls` | "Ask for passwords and it's refused before the model is even called." |
| 0:46–0:58 | 4 · Indirect injection | Answer says the note was withheld; evidence row shows the poisoned note | "This order note tries to hijack the agent. It's stripped before the model reads it; the audit copy keeps it." |
| 0:58–1:08 | 5 · PII | `[RFC_FISICA_1]` | "A person's tax ID is personal data — masked on output. Company IDs are business data and stay visible." |
| 1:08–1:22 | 6 · Human-in-the-loop | `→ proposal: DELETE … ~215 rows` then "nothing was executed" | "Asked to delete data, it drafts the statement and estimates impact — then waits for a human. Nothing is ever executed." |
| 1:22–1:30 | Close | README results table (95.4% · 3.8% SWAR · $0) | "95% execution accuracy on a held-out test set, measured on result sets, at zero model cost. Code and reports on GitHub." |

Tips: record once with `make demo`, then re-record a single scene with `make demo ARGS="--scene N"` if
needed. Figures in the narration come from `evals/reports/2026-09-28-2307-test-local.md`.
