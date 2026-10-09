# One question as traced in AWS X-Ray

A question sent to the deployed API (`bedrock-claude`, Guardrail on) reached CloudWatch Logs
(`aws/spans`) through the X-Ray OTLP endpoint with Transaction Search on, from the worker Lambda in
the VPC. The worker signs the export with SigV4 and uses X-Ray trace ids (ADR-014). This is one trace,
29 spans, as stored; the question and the answer are not in it (content capture is off).

Trace id `6ac959324a3aefb0711691033b42bc1b` (its first four bytes are the start time in epoch seconds, as X-Ray requires).

```
invoke_agent legacybridge  18,704 ms  status=answered confidence=0.99 swar_risk=0.0 verifier_agrees=True
  invoke_agent planner  948 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  947 ms  in=1130 out=62
  invoke_agent sql_writer  6,539 ms
    tools/list  1 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,162 ms  in=2414 out=100
    execute_tool explain_business_term  385 ms
      tools/call explain_business_term  383 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  884 ms  in=2566 out=69
    execute_tool explain_business_term  2 ms
      tools/call explain_business_term  1 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,247 ms  in=3040 out=105
    execute_tool describe_table  83 ms
      tools/call describe_table  82 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,063 ms  in=3633 out=90
    execute_tool run_query  80 ms
      tools/call run_query  80 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,630 ms  in=3809 out=158
  invoke_agent verifier  9,062 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,676 ms  in=1554 out=84
    execute_tool describe_table  30 ms
      tools/call describe_table  29 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,413 ms  in=2126 out=124
    execute_tool run_query  41 ms
      tools/call run_query  40 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  1,041 ms  in=2340 out=99
    execute_tool run_query  38 ms
      tools/call run_query  37 ms
    chat us.anthropic.claude-haiku-4-5-20251001-v1:0  4,822 ms  in=2600 out=602
```
