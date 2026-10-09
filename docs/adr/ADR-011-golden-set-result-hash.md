# ADR-011: Canonical result-set hash for the golden set

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 1 and Phase 6 of `docs/PLAN.md`, `evals/hashing.py`, `scripts/golden_hash.py`

## Context

The eval harness (Phase 6) measures *execution accuracy* and the *SWAR rate* by comparing result
sets, never SQL text: two different queries can be equally right. The golden set therefore stores
a hash of the expected result set (`expected_result_hash`) instead of the rows themselves.

The legacy schema makes naive comparison unreliable:

- `CHAR(n)` columns come back padded with spaces, so `'C000000042 '` and `'C000000042'` are the
  same business value (FAC-01).
- Amounts and dates are text in several tables, so the same figure can be rendered in more than
  one way depending on the cast the query uses.
- Row order is undefined unless the query has an `ORDER BY`.
- Column aliases differ between a reference query and a model-written one.

## Decision

`expected_result_hash` is `sha256:<hex>` of a canonical JSON form of the result set:

1. **Columns by position.** Names and aliases are ignored.
2. **Strings** are stripped of leading and trailing whitespace; inner whitespace is preserved.
3. **Numbers** (`int`, `float`, `Decimal`) are rounded to 4 decimal places and rendered without
   trailing zeros, so `3`, `3.0` and `3.0000` are equal.
4. **Dates and timestamps** are rendered as ISO-8601; `NULL` is JSON `null`.
5. **Row order.** Rows are sorted by their canonical JSON unless the query has a top-level
   `ORDER BY` (detected with `sqlglot`, Postgres dialect), in which case the order is kept.
   A reference query that uses `LIMIT` must also have a total `ORDER BY`; a test enforces it.
6. The hash is computed over compact JSON (`separators=(",", ":")`, `ensure_ascii=False`).
   Any change to these rules is a new ADR and forces re-hashing and re-approval of the golden set.

Cases without a result set (`clarify`, `abstain`, `propose_write`) have
`expected_result_hash: null`.

### Review flow

- Every case carries `review: draft | approved`. Cases drafted by Claude start as `draft`.
- `expected_result_hash: auto` is a placeholder. `scripts/golden_hash.py --write` runs each
  `expected_sql` as `lb_reader`, replaces `auto` and **only touches `draft` cases**; hashes of
  `approved` cases are never rewritten by tooling.
- `scripts/golden_hash.py --check` fails if any stored hash differs from the live result.
- Defect traps also carry `naive_sql`, the query a defect-unaware text-to-SQL would write. A test
  asserts its hash differs from the expected one, so a trap is only valid if it actually bites.
- The hashes depend on the deterministic seed (`seed=42`). Changing the seed data or the
  generator after approval invalidates them and requires the owner's sign-off.

## Consequences

- The harness must execute the agent's SQL as `lb_reader` and hash it with the same function, so
  `evals/hashing.py` is shared by the golden set tooling and the Phase 6 harness.
- Stripping strings makes the comparison lenient about padding; a padding-only difference is not
  counted as an error. Wrong rows and wrong values still are.
- Rounding to 4 decimals can mask a tiny numeric difference. This is acceptable for business
  amounts and keeps text-to-numeric casts from causing false negatives.
- Free-text answers are not covered by this hash; Phase 6 checks them separately.

## Alternatives considered

1. **Compare SQL text or AST.** Brittle: correct paraphrases of the same query fail.
2. **Store the full expected rows.** Larger files and harder reviews, with no benefit over a hash
   for pass/fail scoring (the reference SQL is kept for diagnosing a miss).
3. **LLM-as-judge.** Costly, non-deterministic and not reproducible across profiles.
