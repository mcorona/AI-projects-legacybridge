# ADR-008: Deploy as Lambda containers behind an asynchronous API Gateway

- **Status:** Accepted
- **Date:** 2026-10-09 (recorded in Phase 8; the decision was taken and built in Phase 7)
- **Related:** ADR-009 (the stack is CDK), ADR-014 (the deployment, as built and measured),
  ADR-002 (the agent loop is our own), `infra/`, `docs/architecture/deployment-aws.md`

## Context

The plan named this decision before Phase 7 and `docs/PLAN.md` carried it as "Lambda (container) +
API Gateway for cost; AgentCore Runtime as a next step; alternative ECS Fargate". It was not written
down as an ADR then. The detail of what was built is in ADR-014; this records the choice and the
reasons that outlive the stack.

The workload is bursty and small: a question is a few model calls and takes from 5 to 30 seconds,
and for a portfolio demo most hours have no traffic at all.

## Decision

**Lambda container images (arm64), one image with three handlers, behind an API Gateway HTTP API.**

- The agent runs in a **worker** function, the only one that sees the database and the model.
- API Gateway ends a request at 30 seconds and an agent run can take longer, so the API is
  **asynchronous**: `POST /ask` answers `202` with a job id (kept in DynamoDB) and `GET /ask/{id}`
  is polled. A small **front** function takes the request, checks the bearer token, writes the job
  and starts the worker; it has no VPC, database or model access.
- Concurrency is reserved and capped (and asynchronous invocations are not retried: a retry would
  pay for the same question twice).

**AgentCore Runtime is the next step, not part of this decision.** The agent loop is our own
(ADR-002) because comparing models with controlled traces needed it, and hosting it in a managed
agent runtime is a deployment change that this ADR does not rule out. It was not evaluated here.

## Consequences

- Nothing runs when nothing is asked: the always-on part of the stack is the database and the
  network endpoints, not compute (`docs/cost-model.md`).
- A cold container in a VPC adds about 13 seconds to the first question after idle (measured in
  ADR-014); a warm one does not.
- The client must poll. A streaming response would remove that and was left out.
- Lambda in a VPC takes about 25 minutes to release its network interfaces when deleted, so
  `destroy` is slow, though not costly.

## Alternatives considered

- **ECS Fargate** (a container service behind a load balancer): simple to reason about and no
  15-minute or 30-second limits. It bills per hour whether or not anyone asks, and needs a load
  balancer to be reachable; for a demo that is the wrong side of the trade. It becomes reasonable at
  sustained volume.
- **Lambda Function URL** instead of API Gateway: simpler, with no 30-second limit and IAM
  authentication. Rejected because the plan names API Gateway and it gives throttling and a place to
  attach an authorizer; it would be the first thing to try if the API had to be simpler.
- **A single synchronous function:** answers fit in 30 seconds only most of the time; the questions
  that do not are the hard ones.
- **Bedrock AgentCore Runtime now:** see above.
