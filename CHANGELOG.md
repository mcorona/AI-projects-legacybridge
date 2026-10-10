# Changelog

All notable changes, by phase of `docs/PLAN.md`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). `1.0.0-rc1` was a pre-release until the
golden set was validated; it became `1.0.0` on 2026-10-09 without a change to the agent.

## [Unreleased]

### Documentation

- `docs/measurement-environment.md`: the machine (MacBook Pro, Apple M5 Max, 36 GB), the local model's
  build, the AWS deployment and the CI runner behind every timing, and what each timing includes.
  The README results table, the evaluation report, the cost model and the archive README point to it.
- The review status of the golden set now reads "reviewed by a person, its author".

## [1.0.0] - 2026-10-09

### Changed

- **The golden set is validated.** The author reviewed all 80 expected answers after evaluation run
  `20261009` and accepted them unchanged: `review: approved` on every case. The runs were not repeated
  and no label or figure changed.
- The generated README results block, the evaluation report and the cost model now state the review
  status from the golden file instead of a fixed sentence (`evals/golden.py: all_approved`).
- The historical reports in `evals/reports/swar-*.md` keep their text: they were written when the set
  was a draft.

### Known limitations

- The golden set has one reviewer, its author, who also wrote the cases and the reference SQL: it is a
  reviewed reference, not an independent benchmark. `docs/legacy-defects.md`, the catalogue behind it,
  is still marked as a draft.
- One run per model; run-to-run noise is not measured. The models ran at different times.
- The plan's target for the SWAR rate (5% or less) was not reached: the best profile has 13%.
- The detector does not flag a wrong answer that follows the rules; the verifier shares the agent's
  reading of them.

## [1.0.0-rc1] - 2026-10-09

### Added

**Phase 8: packaging**
- README with a generated results table, a model-free demo (`make demo`, `docs/demo/demo.gif`) and a
  quickstart that CI runs verbatim.
- `docs/architecture/` (context, containers, AWS deployment as Mermaid), `docs/threat-model.md`
  (STRIDE by trust boundary and OWASP LLM Top 10, 2025, with the 45 attacks one by one),
  `docs/cost-model.md` (generated), ADR-008 and ADR-009, and an index of the 14 ADRs.
- The raw results of the evaluation run in `evals/archive/20261009/`, so every figure can be rebuilt
  from a clone (`make eval-report RESULTS=evals/archive/20261009`).

**Phase 7: AWS**
- `bedrock-claude`, `bedrock-claude-sonnet` and `bedrock-claude-opus` profiles on the Converse API
  with tool use, priced from the AWS Price List.
- Bedrock Guardrails around the question and the answer, and a Bedrock Knowledge Base behind
  `explain_business_term` for terms the ontology lacks; both optional and off by default.
- An asynchronous API (`POST /ask`, `GET /ask/{id}`) as two Lambda functions behind API Gateway, a
  worker in a VPC with RDS PostgreSQL, one EMF metrics record per question, and X-Ray traces through
  Transaction Search. A CDK stack in `infra/` with 37 assertions that run without AWS.
- Deployed, measured and destroyed (ADR-014); `make aws-leftovers` checks the account directly.

**Phase 6: evaluation**
- Pre-registered methodology (ADR-013): Wilson intervals, exact paired tests, frozen labels, a
  reproducible runner with an `arm` for each ablation, a report with charts, and a CI **eval gate**
  that needs no model and fails when the guard, the tools, the labels or the detector regress.
- GitHub Actions: lint, tests, eval gate and the stack's assertions, actions pinned by commit.

**Phase 5: traces and SWAR**
- OpenTelemetry spans with GenAI conventions, viewed in Phoenix; a SWAR detector that scores a run
  from raw signals and an early warning that can ask, escalate or abstain (ADR-012).

**Phase 4: the agent**
- planner, sql_writer and an independent verifier over MCP; writes are proposals that a person
  approves; CLI and HTTP API; `local-qwen` and `omniroute-free` profiles (ADR-010).

**Phase 3: semantic layer**
- A versioned ontology and 28 defect rules that warn, with the fix, when a query reads a defect
  wrongly; seven canonical metrics.

**Phase 2: MCP server**
- Six tools, a SQL guard that parses to an AST, three database roles, and 45 attacks that must all
  end blocked or neutralized.

**Phase 1: data**
- A year-2000-style invoicing schema and a synthetic inventory schema with 28 documented defects,
  deterministic seed 42, and an 80-case golden set verified by a result-set hash (ADR-011).

### Found by measuring, and fixed

- A reference query timed out in CI on a freshly seeded database with no planner statistics, and the
  gate passed on a smaller population: the seed now runs `ANALYZE` and the gate fails if a reference
  query does not run.
- The cost model showed that the dashboard metrics were repeated for every status, 55 series where 15
  were enough.
- The first deploy found that a cross-region guardrail needs permission on its profile in every
  destination region (the agent failed closed), and that RDS does not let the master user set the
  synthetic-data marker.
- Phase 5 corrected five definition errors in the detector and the labels after the first runs, all
  reported as "as it ran" next to "corrected".

### Known limitations

- The expected answers of the golden set are a **draft**: no person has validated them.
- One run per model; run-to-run noise is not measured.
- The plan's target for the SWAR rate (5% or less) was not reached: the best profile has 13%.
- The detector does not flag a wrong answer that follows the rules; the verifier shares the agent's
  reading of them.
- The retrieval probe of the knowledge base cannot tell a good retriever from a trivial one.
- The deployed database cannot carry the synthetic-data marker.
- Synthetic data only; one shared API token.
