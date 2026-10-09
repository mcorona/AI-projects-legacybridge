# C2 — Containers

What runs inside LegacyBridge, and how a question moves through it. Names match the code under
`src/legacybridge/`.

```mermaid
flowchart LR
    user(["Analyst"])
    reviewer(["Reviewer"])
    client(["Claude Code or Desktop"])

    subgraph entry["Entry points"]
        cli["<b>CLI</b><br/>legacybridge ask / approvals<br/><i>cli.py</i>"]
        api["<b>HTTP API</b><br/>FastAPI: /ask, /approvals<br/><i>api/app.py</i>"]
    end

    subgraph agent["Agent  (agent/)"]
        guardin["Text guard<br/>(optional)"]
        planner["<b>planner</b><br/>query, clarify,<br/>abstain, write"]
        writer["<b>sql_writer</b><br/>tool loop, step limit"]
        verifier["<b>verifier</b><br/>independent re-derivation"]
        swar["<b>SWAR detector</b><br/>risk from raw signals,<br/>early warning, act"]
        evidence["<b>Evidence</b><br/>SQL, rows, tables,<br/>calibrated confidence"]
    end

    subgraph llmlayer["Model layer  (llm/)"]
        oai["OpenAI-compatible client<br/>LM Studio, OmniRoute"]
        bed["Bedrock Converse client<br/>tool use, retries"]
    end

    subgraph mcp["MCP server  (mcp_server/)"]
        tools["<b>Tools</b><br/>list_tables, describe_table,<br/>sample_rows, run_query,<br/>explain_business_term,<br/>propose_write"]
        sqlguard["<b>SQL guard</b><br/>sqlglot AST: SELECT only,<br/>allowlist, LIMIT, no catalogs"]
        semantic["<b>Semantic layer</b><br/>ontology.yaml +<br/>28 defect rules<br/><i>semantic/</i>"]
    end

    approvals["<b>Approval service</b><br/>list, approve, reject;<br/>reviewer is not the proposer<br/><i>agent/hitl.py</i>"]
    db[("<b>PostgreSQL</b><br/>lb_reader: SELECT only<br/>lb_proposer: queue only<br/>lb_writer: approved writes")]
    providers["Model providers"]
    otel["<b>OpenTelemetry</b><br/>spans: agent, chat, tool<br/><i>observability/</i>"]
    evals["<b>Eval harness</b><br/>golden set, 45 attacks,<br/>CI gate  <i>evals/</i>"]

    user --> cli
    user --> api
    cli --> agent
    api --> agent
    guardin --> planner --> writer --> verifier --> evidence
    writer -. "risk signals" .-> swar
    verifier -. "verdict" .-> swar
    swar -. "clarify, escalate, abstain" .-> evidence
    agent --> llmlayer --> providers
    agent -- "MCP (stdio or in-process)" --> tools
    client -- "MCP (stdio)" --> tools
    tools --> sqlguard --> db
    tools --> semantic
    tools -- "queue a request" --> db
    reviewer --> approvals
    approvals -- "apply once approved" --> db
    agent -. spans .-> otel
    tools -. spans .-> otel
    evals -- "runs the real agent<br/>with a model or an oracle" --> agent

    classDef box fill:#1e293b,stroke:#64748b,color:#e2e8f0
    classDef store fill:#3b2f0b,stroke:#e0b84a,color:#fff
    class cli,api,guardin,planner,writer,verifier,swar,evidence,oai,bed,tools,sqlguard,semantic,approvals,otel,evals box
    class db store
```

## One question, in order

1. The entry point passes the question to the agent. If a guardrail is configured, the question is
   checked first; a block ends the run before any model is called.
2. The **planner** decides: answer from data, ask for clarification, abstain, or propose a write.
3. The **sql_writer** loops over the tools (`describe_table`, `explain_business_term`, `run_query`)
   until it submits an answer that points at a query that really ran. The facts of the answer
   (the SQL, the rows, the tables) come from the tool calls, never from what the model says.
4. The **verifier** re-derives the figure with its own queries and says whether it agrees.
5. The **SWAR detector** scores the run from raw signals (result, trajectory, semantics), never from
   the agent's confidence, and may ask for clarification, escalate to a second profile, or abstain.
6. The answer leaves with its **evidence**: the SQL that ran, the rows, the tables, the defects
   touched and a calibrated confidence.

## Where the safety lives

| Layer | Where | What it stops |
|---|---|---|
| SQL guard | `mcp_server/sql_guard.py` | anything but one SELECT on allowed tables and columns |
| Database roles | `db/init/03_roles.sh` | writes, even if the guard failed (11 of 11 attacks with a database check) |
| Rule engine | `semantic/defect_rules.py` | queries that read a defect wrongly: it warns, with the fix |
| Human approval | `agent/hitl.py` | any change to business data |
| Text guardrail | `llm/bedrock_guardrails.py` | prompt attacks in, personal data out (optional) |

The decisions behind each box are in `docs/adr/`.
