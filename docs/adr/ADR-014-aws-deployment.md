# ADR-014: The AWS deployment and the Bedrock comparison, defined before the paid runs

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** Phase 7 of `docs/PLAN.md`, ADR-003 (model access), ADR-007 (tracing), ADR-010
  (synthetic data), ADR-013 (evaluation), `infra/`, `src/legacybridge/aws/`,
  `src/legacybridge/llm/bedrock*.py`

## Context

Phases 1 to 6 run on a laptop with two models that cost nothing. Phase 7 adds the production route
on AWS and closes the comparison with a paid model. Two things can go wrong there that did not
before: money (a stack left running, a loop of paid calls) and a result chosen after the fact. So
this ADR fixes, **before the first deploy and the first paid run**, what is built, what is
measured and how, as ADR-013 did for Phase 6. Anything changed afterwards goes in the change log
at the end, and the report says "as it ran" next to "corrected".

## The deployment

```
client --HTTPS--> API Gateway (HTTP API, 3 routes, throttled)
                     |
                  front Lambda  (no VPC, no database, no model: a bearer token, a job in DynamoDB)
                     |  asynchronous invoke, no retries
                  worker Lambda (VPC) --> RDS PostgreSQL 16 (private, db.t4g.micro)
                                      --> Bedrock Converse (model), ApplyGuardrail, Retrieve (KB)
                                      --> CloudWatch (one EMF record per question), X-Ray (optional)
```

**Two functions, not one.** `POST /ask` answers `202` with a job id and `GET /ask/{id}` is polled,
because API Gateway ends a request at 30 seconds and an agent run on a hosted model can take
longer. The function that takes the request does no model or database work, so it needs no VPC and
cannot reach either: whoever breaks into it can read jobs and start questions, nothing else. The
worker is the only function with the model and the read and propose database roles; the database
initializer is a third function and the only one that holds the admin secret. All three are one
container image (arm64, Python 3.12) with a different `CMD`. The front is an ASGI app behind
Mangum, so it is tested with real API Gateway events and ran through the Lambda runtime emulator.

**Cost is the design constraint.**

| Decision | Chosen | Not chosen, and why |
|---|---|---|
| Network | isolated subnets, **no NAT gateway**; three interface endpoints (Bedrock runtime, Bedrock agent runtime, Secrets Manager, in one subnet) and a free DynamoDB gateway endpoint | NAT gateway: billed per hour and per GB, and a route to the internet the worker does not need |
| Database | RDS PostgreSQL 16 `db.t4g.micro`, single AZ, no backups (the seed rebuilds it), `RemovalPolicy.DESTROY` | Postgres on Fargate (state and operations to carry for a saving of cents); Aurora Serverless v2 (higher floor per hour) |
| Knowledge base vectors | **S3 Vectors** (pay per use) | OpenSearch Serverless: roughly 175 USD a month for as long as it exists, the wrong trade for three documents |
| API | HTTP API, three explicit routes, no catch-all, 2 requests/s and a burst of 5 | Lambda Function URL (simpler, but not API Gateway); synchronous API Gateway (the 30 s limit would fail the slow questions) |
| Dashboard data | **EMF** records written to the logs: no agent, collector or API call | a custom-metrics API call per question |
| Traces | optional (`xray_traces`): OTLP straight to the X-Ray endpoint, signed with SigV4 by botocore, X-Ray trace ids; off by default | the ADOT collector as a Lambda extension in a container image (fiddly to build), see the spike below |

Hourly prices of RDS and of the interface endpoints are read from the AWS Price List when the
stack is first deployed and written here; until then the figure of about **0.07 USD an hour while
deployed, and 0 when destroyed** is an estimate. `make destroy` is followed by `make aws-leftovers`
(the tagging API plus NAT gateways, endpoints and the stack itself): a destroy that is not checked
is not trusted. The CDK bootstrap stack is shared and is listed apart, not removed.

**Permissions.** No wildcard in an action or a resource, except the two X-Ray write actions when
traces are turned on, which X-Ray only allows on `*`. The model is allowed on the inference profile
and on the foundation model in each region the `us.` profile may route to (a profile id needs
both). The assertions on the synthesized template (`infra/tests/`) check these, and that every
resource that keeps data is deleted by `destroy`.

**Guardrail.** Prompt-attack filter on input at HIGH, abusive-content filters both ways, one denied
topic (credential and prompt-extraction requests), and on output the e-mail and phone entities and
a regex for an individual's RFC (13 characters; a company's RFC has 12 and is business data) are
anonymized. Writes are **not** a denied topic: a request to change a client is the legitimate,
human-approved flow of ADR-005, and PII is not touched on input because such a request carries an
e-mail. Customer names are not masked: they are the data the agent exists to report. The agent
treats the guardrail as a layer: a blocked question never reaches a model, a masked answer is kept
but its evidence rows are withheld, a blocked answer is replaced, and a guard that fails **closes**
the answer.

**Knowledge base.** The ontology stays the authority for what a term means and for the canonical
SQL of a metric. `explain_business_term` asks the knowledge base only for a term the ontology does
not know, marks the passages as documentation and not as a definition, and keeps failing as
`unknown_term` (so the writer cannot answer from them as if they were definitions). `PLAN.md` says
the tool "consults the KB and falls back to the ontology"; this reverses that order so that a text
passage can never displace a canonical definition. If the KB is absent or down the tool is what it
always was.

## What is measured, fixed in advance

### 1. The comparison with Bedrock

- **Profile and model:** `bedrock-claude`, Claude Haiku 4.5 through the `us.` inference profile
  (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), temperature 0, 4096 tokens. The same model as the
  v1 project, so the price was already verified. The price list names newer Claude models; they are
  not used here and could be a later comparison.
- **Arm and cases:** `full`, the 80 golden cases and the 45 attacks, with the labels, metrics and
  tolerances of ADR-013 **unchanged**. The guardrail and the knowledge base are **off**: this run
  measures the model, not the layers. One run, added to run id `20261009` so that the report shows
  the three profiles side by side; it is not repeated and nothing is relabeled after seeing it.
- **Cost:** tokens as the model reported them times the per-million prices in `config/models.yaml`
  (Haiku 4.5 Regional, 1.10 and 5.50 USD, Price List published 2026-10-08, the `us.` profile is
  geographic). Latency from the laptop includes the network, so it is not comparable with the
  local model.
- **Statistics:** two paired comparisons on *handled correctly*, exact McNemar on the discordant
  pairs: `bedrock-claude` vs `local-qwen` and `bedrock-claude` vs `omniroute-free`. Two comparisons,
  so each is read at **0.025** (0.05 split between them), stricter than the 0.05 of the ablation.
  The models differ and so do the days; the test says whether the two sets of correct cases differ,
  not why. Accuracy figures keep inheriting the draft status of the golden set and the finding of
  Phase 6 that both models read "invoice" as "valid invoice" where the draft reference does not.
- **Not measured:** ablations on Bedrock (paid, and the question of what each component is worth
  was answered on the free model); any claim about Bedrock Model Evaluation, which is an LLM judge
  and not an execution check, so the harness of ADR-013 is used instead.
- **Before the full run:** one question, then the five live cases of `tests/test_live.py`
  (validating the Phase 4 definition of done on this profile). Estimated cost of the full run, from
  the tokens per question measured on the local model: about 2.7 USD, to be replaced by the real one.

### 2. The guardrail

`evals/guardrail_probes.yaml`, written before measuring: 18 attacks the guardrail should stop
(direct and authority injection, prompt extraction, credential requests, role-play, encoded,
obfuscated, translated and multilingual injection), 8 attacks that the SQL guard and the database
roles contain (reported apart, since a miss is expected and is defense in depth), 8 output texts
with and without PII, and the **80 golden questions as legitimate traffic**: every block of one is
a false positive. Stored answers may be added to see how much of what the agent really says the
output policy would mask (that is the policy working, so it is counted by finding, not as an error).
Rates are reported with Wilson intervals. **The guardrail is not tuned on these results.**
`ApplyGuardrail` judges a text without calling a model, so the whole set costs cents.

### 3. The knowledge base

`evals/kb_probes.yaml`: 20 queries, each with the documents that would be a right answer; a query
hits when one is among the top 3 passages. Twenty queries over three overlapping documents (the
ontology document repeats the defect catalogue) is a small sample, and a hit says the right text
was found, not that this layer is needed.

## The traces spike

The X-Ray OTLP endpoint needs every request signed (SigV4), X-Ray trace ids (the first four bytes
are the start time) and Transaction Search turned on in the account, which stores spans in
CloudWatch Logs. The code for that exists and is tested offline (signature, ids, switch), and the
stack adds one endpoint and the two write actions when `xray_traces` is on. Whether it works from
a Lambda in the VPC is only known at the first deploy. **If it does not, the dashboard stays on EMF
(latency, tokens, cost, SWAR risk, guardrail interventions) and the traces stay in Phoenix, and the
Phase 7 status says so.** Turning on Transaction Search is an account-level change and is done only
with explicit confirmation.

## Consequences

- Nothing here spends money or creates a resource by itself: every step that does (`cdk bootstrap`,
  `cdk deploy`, the paid runs) waits for an explicit confirmation, and `aws login` is the user's.
- The public API spends Bedrock money, so it demands a bearer token (fail closed: the front refuses
  to start without one), is throttled, and the worker has a concurrency cap and no retries (a retry
  would pay for the same question twice).
- Approvals stay off in the cloud: the approval API needs `LB_API_TOKEN`, which is not set, so it
  answers 503, and a write request is only queued.
- The stack and the image are verified without AWS (assertions, a local image build, the Lambda
  emulator, a scratch Postgres for the database initializer). What only the first deploy proves:
  that the endpoints suffice, S3 Vectors with the knowledge base, the guardrail tier, X-Ray.

## Alternatives considered

NAT gateway, Fargate Postgres, Aurora Serverless v2, OpenSearch Serverless, Lambda Function URL,
synchronous API Gateway, Lambda Web Adapter (replaced by Mangum, which can be tested with real
events offline), the ADOT collector extension, and Bedrock Model Evaluation, each above or in the
sections where they were rejected.

## Changes after the specification

None yet.
