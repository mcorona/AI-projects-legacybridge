# Deployment on AWS

The shape of the stack that `infra/` deploys (ADR-014). It was deployed, tested and destroyed;
nothing here is running. Costs are in `docs/cost-model.md`.

```mermaid
flowchart LR
    client(["Client with a bearer token"])

    subgraph edge["No VPC"]
        apigw["<b>API Gateway</b><br/>HTTP API, 3 routes,<br/>throttled"]
        front["<b>Front Lambda</b><br/>checks the token,<br/>writes a job, starts the worker"]
        jobs[("<b>DynamoDB</b><br/>jobs, expire in a day")]
    end

    subgraph vpc["VPC: isolated subnets, no NAT gateway"]
        worker["<b>Worker Lambda</b><br/>arm64, 2 GB, concurrency 2<br/>runs the agent on the<br/>in-process tools"]
        init["<b>DbInit Lambda</b><br/>builds the schema, roles<br/>and seed once"]
        rds[("<b>RDS PostgreSQL 16</b><br/>db.t4g.micro, private,<br/>encrypted")]
        epbed["endpoint<br/>bedrock-runtime"]
        epkb["endpoint<br/>bedrock-agent-runtime"]
        epsec["endpoint<br/>secretsmanager"]
        epxray["endpoint xray<br/>(optional)"]
    end

    subgraph managed["Managed services"]
        bedrock["<b>Amazon Bedrock</b><br/>Converse (Claude),<br/>ApplyGuardrail"]
        guardrail["<b>Guardrail</b><br/>STANDARD tier"]
        kb["<b>Knowledge Base</b><br/>S3 Vectors + Titan<br/>embeddings"]
        docs[("<b>S3 documents</b><br/>glossary, defects, schema")]
        secrets["<b>Secrets Manager</b><br/>role passwords, token"]
        cw["<b>CloudWatch</b><br/>EMF metrics, dashboard, logs"]
        xray["<b>X-Ray / Transaction Search</b><br/>spans in aws/spans"]
    end

    client -- "POST /ask, GET /ask/{id}" --> apigw --> front
    front -- "job" --> jobs
    front -. "async invoke, no retries" .-> worker
    worker -- "status, result" --> jobs
    worker --> rds
    init --> rds
    worker --> epbed --> bedrock
    bedrock --- guardrail
    worker --> epkb --> kb
    docs --> kb
    worker --> epsec --> secrets
    init --> epsec
    worker -. "EMF record per question" .-> cw
    worker -. "SigV4 OTLP" .-> epxray -.-> xray

    classDef fn fill:#1e293b,stroke:#64748b,color:#e2e8f0
    classDef store fill:#3b2f0b,stroke:#e0b84a,color:#fff
    classDef svc fill:#0b3b3b,stroke:#4ad0e0,color:#fff
    class apigw,front,worker,init fn
    class jobs,rds,docs store
    class bedrock,guardrail,kb,secrets,cw,xray svc
```

## Why it is shaped this way

- **Two functions.** API Gateway ends a request at 30 seconds and an agent run can take longer, so
  `POST /ask` answers `202` with a job id and the client polls. The function that takes the request
  has no VPC, no database and no model access: whoever breaks into it can read jobs and start
  questions, nothing else. Only the worker sees the database and the model.
- **No NAT gateway.** The worker reaches AWS through interface endpoints, which cost less than a
  NAT gateway and open no route to the internet. The price is a timeout, silent, if one endpoint is
  missing.
- **Fail closed.** The front refuses to start without its token; a guardrail that cannot run ends
  the question instead of letting it through; approvals are off in the cloud, so a write request is
  only queued.
- **Everything is removed by `make destroy`**, and `make aws-leftovers` checks it directly, not
  through the tag index (which lags).

What the first deploy taught is recorded in ADR-014: the guardrail's cross-region profile needs its
own permissions, RDS refuses the synthetic marker, and deleting a Lambda in a VPC takes about 25
minutes.
