# 90-second demo — storyboard

**Generated video** (≈ 88 s, 1280×720, burned-in English captions):

```bash
DEMO_CAST=/tmp/cast.json make demo            # real run, every printed line recorded with its time
python scripts/make_video.py /tmp/cast.json docs/demo/legacybridge-demo.mp4   # needs Pillow (separate venv)
make demo-replay CAST=/tmp/cast.json          # same replay in the terminal
```

The video draws the terminal from the recorded real output; only the model waits are compressed, and the
opening card says so. (A live terminal recording with VHS froze mid-run, so frames are rendered instead.)

**Recording it yourself:** run `make demo` in a terminal (font ≥ 16 pt, 100 columns) and speed up the
10–30 s model waits 3–4× in editing.

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
