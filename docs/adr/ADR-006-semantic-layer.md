# ADR-006: A versioned semantic layer that also checks the SQL

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** Phase 3 of `docs/PLAN.md`, ADR-001, ADR-004, `src/legacybridge/semantic/`,
  `docs/legacy-defects.md`

## Context

The legacy schemas hide their meaning: text-typed amounts and dates, logical deletes, duplicated
folios, columns whose meaning changes with the year. A model that only sees the DDL answers with
confidence and is silently wrong (SWAR). The knowledge needed to avoid that must not live in a
prompt, where it cannot be reviewed, versioned or tested.

Phase 2 already warns about defects, but passively: every query that touches a column with a known
defect gets the same note, whether it handles the defect well or not. That is noise, and it cannot
tell the verifier (Phase 4) or the SWAR detector (Phase 5) whether a query is risky.

## Decision

`src/legacybridge/semantic/ontology.yaml` is the single, versioned home of business knowledge:

| Section | Holds |
|---|---|
| `entities` | Customer, invoice, product, article, stock, movement, supplier, warehouse: tables, keys, the role of each column, synonyms in Spanish and English |
| `terms` | Glossary and synonyms ("ventas" = invoices that are not cancelled) |
| `joins` | The graph of allowed relationships, each with its exact `ON` |
| `metrics` | Canonical SQL with validated parameters (`facturacion_total`, `existencia_actual`...) |
| `defects[].rule` | For every catalogued defect, a machine-checkable rule |

### Rules check the SQL; they never block it

`defect_rules.check(sql, schema, ontology)` analyses the query's AST (`sqlglot`, following each
column through CTEs and subqueries) and returns `violations[]`: which defect rules apply and were
not met. `run_query` returns them, and `warnings` carries the actionable ones.

- They **warn, they do not block.** Static analysis of SQL is heuristic: it cannot prove a query
  right or wrong. Blocking would turn every false positive into a lost answer. Only the guard
  (ADR-004) blocks.
- Severity is `warning` (probably a wrong answer) or `info` (worth knowing).
- Defects that live in the *data*, not in the query (instructions in free text, mojibake), are
  checked on the result cells (`kind: data`).
- Each rule carries its own `bad_sql` (must violate) and `good_sql` (must not violate). These are
  the tests, and they sit next to the rule they document.

### The ontology is executed, not just read

Every join is run against the database, every metric is run and must produce **no violations**
(the ontology follows its own rules), and metrics are cross-checked against golden-set results.
A definition that stops working fails a test instead of drifting.

### Measured, not asserted

Because the rules are heuristic, the golden set measures them: recall (the naive SQL of each trap
triggers a rule of its defect), specificity (the reference SQL triggers none of its own) and noise
(neutral cases flagged). The numbers are reported; they are not claimed to be perfect.

## Consequences

- Adding a defect means adding one entry with a rule and two examples; most rules are a parameter
  set over one of about fifteen check types, so only a new *kind* of check needs Python.
- The verifier and the SWAR detector get structured inputs (`violations`, `defects_touched`).
- A human can review the whole ontology in `docs/ontology.md`, generated from the YAML.
- In Phase 7 the Bedrock Knowledge Base indexes the glossary and defect text for free-text
  questions; the YAML stays the source for anything that is checked.

## Alternatives considered

1. **Put the rules in the prompt.** Unreviewable, untestable and different for every model.
2. **Block queries that violate a rule.** False positives become lost answers.
3. **A separate rules file next to the defect catalogue.** Two lists that drift apart.
4. **Only the LLM verifier decides.** It is non-deterministic and costs a call per query; the
   static rules are free and give it a head start.
