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

### 2026-10-09, before any of the runs below: attacks are not rerun, and two larger models are added

The Haiku 4.5 run was started with `--set golden`. The 45 attacks exercise the tools and the SQL
guard, not a model, so their result does not depend on the profile and run id `20261009` already
holds it (45 of 45); running them again would only overwrite an identical file. Section 1 said
"the 80 golden cases and the 45 attacks": the attacks are the ones already measured.

The user asked for larger models to be used as well, accepting the cost. **Extension, preregistered
here before either model has answered one case:**

- **Models:** Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`, profile `bedrock-claude-sonnet`) and
  Claude Opus 4.6 (`us.anthropic.claude-opus-4-6-v1`, profile `bedrock-claude-opus`): the most recent
  model of each family that the account can use. Everything newer in the price list (4.7, 4.8, 5, 5.5,
  Fable, Haiku 5.5) has an agreement that is `NOT_AVAILABLE` for this account (read with
  `get-foundation-model-availability` on 2026-10-09); none of them was invoked. A smoke test of a
  tool-use round trip with each of the two chosen models passed before this entry.
- **Run:** `full` arm, the 80 golden cases, same labels and tolerances, one run each, **after** the
  Haiku run finishes and never at the same time (two runs would share the write-request queue and the
  data fingerprint of the local database). Same run id, so the report shows five profiles.
- **Prices** from the AWS Price List published 2026-10-08T23:56:37Z, Regional (the `us.` profile is
  geographic): Sonnet 4.6 3.30 and 16.50, Opus 4.6 5.50 and 27.50 USD per million tokens. The
  estimate is about 8 and 13 USD for 80 cases; the real figure replaces it.
- **Statistics:** a second family of two paired comparisons on *handled correctly*, each read at
  **0.025**: `bedrock-claude-sonnet` vs `bedrock-claude` and `bedrock-claude-opus` vs `bedrock-claude`.
  It is a separate family from the first two comparisons, so it does not change their significance.
  These models are *larger*: a difference says that spending more changes which cases are right, and
  a non-difference says that this golden set, as it is, cannot tell them apart, which is what the
  Phase 6 finding about the draft reference and the validity rule would predict. It does not say the
  models are equal.
- **Not done:** no prompt, label or tolerance changes for these models. The monthly budget of 40 USD
  may send its alert with these runs; it is an alert, not a cap, and the credits cover the cost.

### 2026-10-09, after the first deploy: what it taught, and what was measured

The stack deployed at the first attempt (627 s) and was destroyed in the same session. Three things
that the offline checks could not show:

1. **The Guardrail's cross-region profile needs its own permissions.** The first question was
   refused with `AccessDenied` from `ApplyGuardrail`, and the agent **closed** (no model call, an
   explicit `guardrail_error`), as designed. The IAM simulator showed the guardrail ARN allowed and the
   profile ARN denied. Allowing the profile only in the caller's region was still not enough; AWS
   requires the action on the guardrail, on the profile in the source region and on the profile in
   each destination region (`us-east-2`, `us-west-2` for `us.guardrail.v1:0`). Four ARNs, no wildcard.
2. **RDS refuses the synthetic marker.** `ALTER DATABASE ... SET lb.synthetic` and the fallback
   `ALTER ROLE lb_reader ...` are both denied to the RDS master user (not a superuser), so the
   deployed database carries **no** `lb.synthetic` marker (`synthetic_marker: none`). The worker does
   not need it (`bedrock-claude` is not `synthetic_data_only`), and the check fails safe: a profile
   that demands synthetic data would refuse to start against this database. It is synthetic by
   construction (seed 42), but the database cannot say so.
3. **A guardrail error is not an intervention.** The first dashboard image counted two failed calls
   as interventions. `GuardrailErrors` is now its own metric and series.

Measured on the deployed stack, as preregistered:

- **API:** 401 without or with a wrong token, 422 on an invalid body, 404 on an unknown route, 200 on
  `/health`; a question returns 202 and is polled to `done` in 5 to 22 s. A cold container in the VPC
  adds about 13 s. A prompt-injection question is blocked in the guardrail (`PROMPT_ATTACK`,
  `MISCONDUCT`, `TOPIC:credenciales`) without calling a model; a question whose answer holds an
  individual's RFC comes back with `{RFC_FISICA}` and **no evidence rows**, and the real RFC appears
  nowhere in the response.
- **Guardrail probes** (`evals/reports/guardrails-20261009.md`, not tuned on): the 18 attacks it
  should stop were all blocked (18/18, CI 0.82-1.00; one to three per category, so no category is
  separately defensible); the 8 attacks that other layers contain were blocked 3/8, as expected for
  a layer that is not the main defense; output PII was masked correctly on 4/4 and 0/4 clean outputs
  were touched. Of the 80 golden questions, **2 were blocked**: OOS-003 asks for the system
  passwords, an out-of-scope case the agent must refuse anyway, so blocking it is right even though
  the rule written beforehand counts it as a false positive; **WRITE-008** (deactivate all inactive
  clients of one state) is a legitimate write request, a real false positive. 15 of 130 delivered
  answers would be masked (e-mail 6, phone 9) and 3 answers (TRAP-019, in all three profiles) were
  blocked on output because they repeat the instruction text found in the data. The probes were
  written by the same person who wrote the guardrail's policy: a clean result on them is weaker
  evidence than the same result on probes from someone else.
- **Knowledge base** (`evals/reports/kb-20261009.md`): hit@3 20/20, but **every one of the 20 queries
  accepts `ontology.md`**, the largest document, so a retriever that ignored the query would also
  score 20/20. The probe shows that the knowledge base is reachable, indexed (3 documents, none
  failed) and returns relevant text; it cannot show that it adds anything the ontology lacks, and the
  report prints that baseline next to the hit rate. A probe that discriminates would need queries
  whose only right answer is a passage the ontology does not contain.
- **Dashboard:** the per-question EMF record reaches CloudWatch (latency, tokens, cost, `swar_risk`,
  guardrail interventions, blocks and errors, by status); images of the widgets with real data are in
  `docs/aws/`.
- **Not done:** traces to X-Ray. The account's trace destination is `XRay`, not CloudWatch Logs
  (Transaction Search is off), and turning it on is an account-level change that was not made. The
  code (SigV4 signing, X-Ray trace ids, the switch and its endpoint) is tested offline and
  `xray_traces` stays off; whether it works from the VPC is **not known**. The traces of the
  deployment are in the logs and the metrics, not in X-Ray.
- **Cost of the stack**, from the Price List published 2026-10-06: RDS `db.t4g.micro` Single-AZ
  PostgreSQL 0.016 USD an hour, gp3 storage 0.115 USD per GB-month (20 GB), 0.01 USD an hour per
  interface endpoint (three here), plus secrets and logs: about **0.05 USD an hour deployed**, less
  than the 0.07 estimated above (which counted a fourth endpoint for X-Ray that is off). It existed
  for under an hour.
- **A process note.** The Sonnet 4.6 run shares the local database with the test suite, and a full
  test run overlapped its write cases. Its write labels all read their request from the queue and match;
  the gaps in the request ids are the ones the tests consumed. Nothing anomalous was found, but a run
  and a test suite should not share a database, and the later runs were not overlapped.
- **Destroying took 27 minutes, not 5.** Deleting a Lambda that is in a VPC waits for its network
  interfaces to be released, and CloudFormation cannot delete the database, the subnets or the
  security groups until then; the database kept running (0.016 USD an hour) meanwhile. Nothing failed
  and nothing had to be forced. `aws_leftovers` then reported four VPC endpoints that no longer
  existed: the tagging API is eventually consistent and kept listing them. The script now asks EC2
  directly about the network resources the index lists and drops the ones EC2 says are gone, and keeps
  anything it cannot check (a stale listing must not hide a real leftover). After that, and with direct
  counts of RDS instances, endpoints, VPCs, guardrails, knowledge bases and secrets all at zero, nothing
  of the project was left except the shared CDK bootstrap stack, which was **not** removed.
- **Bedrock runs, as ADR-014 extended them** (the numbers are in `docs/eval-report.md`): Haiku 4.5
  2.09 USD, Sonnet 4.6 6.08 USD and Opus 4.6 10.39 USD for the 80 cases, 18.56 in all (the estimate
  for Haiku was 2.7). Haiku against `local-qwen` is distinguishable at 0.025 (14 cases against 3,
  p = 0.013) and against `omniroute-free` is not; Opus against Haiku is (15 against 2, p = 0.0023) and
  Sonnet against Haiku is not (13 against 4, p = 0.049, above the 0.025 fixed for that family). The
  larger models mostly answer questions that Haiku declined; they do not clearly err less.

### 2026-10-09, later: Transaction Search turned on and X-Ray traces verified

The traces spike above had no answer; it has one now. At the user's request Transaction Search was
enabled with the four documented steps, in order: a CloudWatch Logs resource policy
(`TransactionSearchXRayAccess`, scoped to the X-Ray service, the account, and the two log groups
`aws/spans` and `/aws/application-signals/data`), `update-trace-segment-destination CloudWatchLogs`
(about 13 minutes from `PENDING` to `ACTIVE`), and the indexing rule at 1%. The stack was deployed
again with `-c xray_traces=true` (a fourth interface endpoint and the two X-Ray write actions), a
question was asked, and the stack was destroyed.

- **It works from the VPC.** The worker's SigV4-signed OTLP export reached CloudWatch Logs through the
  new endpoint: one question produced **one trace of 29 spans** with an X-Ray trace id (the first four
  bytes are the start time), the agent spans (`invoke_agent` for planner, sql_writer and verifier,
  `chat <model>` with token counts, `execute_tool`), the MCP server's `tools/call` spans nested under
  their tool, and the `lb.*` attributes (status, confidence, `swar_risk`, verifier agreement,
  guardrail action) on the root. The tree is in `docs/aws/xray-trace.md`. The question and the answer
  are not in it: content capture stays off. That settles the question the spike left open.
- **What X-Ray's own API shows.** `BatchGetTraces` and `GetTraceSummaries` returned nothing for it: with
  Transaction Search X-Ray indexes only the configured percentage (1%) as trace summaries, and the
  full spans live in the `aws/spans` log group, which is what was read. Seeing every trace needs
  CloudWatch (Transaction Search, Logs Insights), not the X-Ray trace API.
- **The cost of leaving it on.** Transaction Search was **left enabled** at 1% indexing. It costs
  nothing without traffic; with traffic, ingested spans are billed at CloudWatch Logs rates and the
  1% indexing is free (AWS lists a separate free tier for new use; not relied on here). The `aws/spans`
  log group, created by the service, keeps its data 30 days. It is one
  command to put back: `aws xray update-trace-segment-destination --destination XRay`.
- **Latency.** The first question after a cold start took 34 s (against 18 s warm on the earlier
  deployment), with the extra endpoint and a cold container in the VPC.

