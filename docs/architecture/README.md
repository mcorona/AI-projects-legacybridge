# Architecture

Three views, from outside in. The diagrams are Mermaid and render on GitHub.

| View | Answers |
|---|---|
| [`c1-context.md`](c1-context.md) | Who uses it, what it depends on, and where the trust boundaries are |
| [`c2-containers.md`](c2-containers.md) | What runs inside, how a question moves, and where the safety lives |
| [`deployment-aws.md`](deployment-aws.md) | The AWS stack: why two functions, no NAT gateway, and what fails closed |

The decisions behind them are in [`../adr/`](../adr/README.md); the threats and controls in
[`../threat-model.md`](../threat-model.md); the cost in [`../cost-model.md`](../cost-model.md).
