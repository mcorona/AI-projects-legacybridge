# ADR-009: Infrastructure as code with AWS CDK in Python

- **Status:** Accepted
- **Date:** 2026-10-09 (recorded in Phase 8; the decision was taken and built in Phase 7)
- **Related:** ADR-008, ADR-014, `infra/`

## Context

The plan named this decision before Phase 7 ("AWS CDK (Python); alternative Terraform") without an
ADR. The stack to describe is small but unusual for its age: a VPC with endpoints and no NAT, an
RDS instance, three Lambda functions from one image, an HTTP API, a DynamoDB table, a Bedrock
guardrail, a Bedrock knowledge base on S3 Vectors, secrets, a dashboard and IAM for all of it. The
repository is Python and has one maintainer.

## Decision

**AWS CDK in Python**, as its own project in `infra/` (its own `pyproject.toml` and lock file), so
that the runtime and its image do not carry the CDK libraries.

- The CLI is pinned and run with `npx aws-cdk@<version>` from the Makefile.
- The new Bedrock and S3 Vectors resources are used through their **L1 constructs**
  (`CfnGuardrail`, `CfnKnowledgeBase`, `CfnDataSource`, `CfnVectorBucket`, `CfnIndex`): they were
  there when the higher-level ones were not, and they map one to one to CloudFormation.
- The stack is **tested without AWS**: `aws_cdk.assertions` checks the synthesized template (37
  assertions: the database is private and encrypted, no permission is a wildcard, only the
  initializer holds the admin secret, the front cannot call Bedrock, every resource that keeps data
  is deleted by `destroy`). Those tests run in CI with no credentials and no Docker.
- `make destroy` is followed by a check that looks at the account directly (`aws-leftovers`).

## Consequences

- The security properties of the stack are tests, so a change that weakens one fails CI.
- Deploying needs Node (for the CLI), Docker (for the image) and a one-time `cdk bootstrap`, which
  creates a shared stack in the account that `destroy` does not remove.
- jsii starts slowly and a synthesis takes seconds; acceptable at this size.
- CloudFormation, not CDK, decides the order and speed of deletion: the 25-minute delete of a Lambda
  in a VPC is CloudFormation waiting for the service (ADR-014).

## Alternatives considered

- **Terraform:** a fine tool and a common one. It needs a state backend (a bucket and a lock) for a
  single-person demo, is a second language beside the Python code, and its template cannot be
  asserted on from Python without more tooling. Its main advantages (multi-cloud, a wide provider
  ecosystem) do not apply here.
- **Raw CloudFormation or SAM:** SAM is centered on functions and APIs and the stack also needs the
  Bedrock and network resources; raw YAML for roughly 70 resources is hard to review and gives no
  tests.
