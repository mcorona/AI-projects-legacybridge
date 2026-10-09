# C1 — System context

Who uses LegacyBridge and what it depends on. Everything inside the dashed box is this repository.

```mermaid
flowchart TB
    analyst["<b>Analyst</b><br/>asks business questions<br/>in plain language"]
    reviewer["<b>Reviewer</b><br/>a person who approves<br/>or rejects write requests"]
    dev["<b>Developer or agent client</b><br/>Claude Code, Claude Desktop<br/>through MCP"]

    subgraph lb["LegacyBridge"]
        system["<b>LegacyBridge</b><br/>an agent that answers over a legacy database,<br/>says how sure it is, and can be measured"]
    end

    db[("<b>Legacy database</b><br/>PostgreSQL: invoicing and inventory<br/>no foreign keys, text dates, logical deletes<br/>synthetic data")]
    llm["<b>Language models</b><br/>LM Studio (local), OmniRoute (free tier),<br/>Amazon Bedrock (Claude)"]
    guard["<b>Amazon Bedrock Guardrails</b><br/>prompt attacks, PII on output"]
    kb["<b>Amazon Bedrock Knowledge Base</b><br/>documentation passages<br/>for terms the ontology lacks"]
    obs["<b>Observability</b><br/>Phoenix (local), CloudWatch and X-Ray (AWS)"]

    analyst -- "asks a question, reads an answer<br/>with its SQL, rows and confidence" --> system
    dev -- "calls the read-only tools" --> system
    system -- "proposes a change<br/>and waits" --> reviewer
    reviewer -- "approves or rejects" --> system
    system -- "reads with a SELECT-only role,<br/>applies only approved writes" --> db
    system -- "asks for completions<br/>and tool calls" --> llm
    system -. "optional" .-> guard
    system -. "optional" .-> kb
    system -- "OpenTelemetry spans,<br/>one metrics record a question" --> obs

    classDef person fill:#1f3a5f,stroke:#9ec1ff,color:#fff
    classDef ext fill:#2b2b2b,stroke:#999,color:#eee
    classDef sys fill:#0b5d3b,stroke:#7ee2b8,color:#fff
    class analyst,reviewer,dev person
    class db,llm,guard,kb,obs ext
    class system sys
```

## Trust boundaries

- The **model** is not trusted: it never sees credentials, cannot approve a write, and everything it
  asks for goes through the tools (the SQL guard, the roles, the rule engine).
- The **data** is not trusted either: free-text columns hold text written to look like instructions
  (defect FAC-10), and results are returned as data, flagged, never as instructions.
- A **person** is the only thing that can change business data (ADR-005).

See `docs/threat-model.md` for the threats and the controls, and `c2-containers.md` for the inside.
