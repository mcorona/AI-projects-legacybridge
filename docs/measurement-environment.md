# Where the timings come from

Every time quoted in this repository was measured on one laptop or on one AWS deployment, under the
conditions below. They are not benchmarks of a model or of a machine: change the hardware, the network or
the day and they change. Read each number together with its row.

Written on 2026-10-09 from the machine and the CI logs. What could not be recorded run by run is said so.

## The laptop

| | |
|---|---|
| Machine | MacBook Pro (`Mac17,6`) |
| Chip | Apple M5 Max: 18 cores (6 super and 12 performance), unified memory |
| Memory | 36 GB |
| Operating system | macOS 26.7.1 |
| Docker | Docker Desktop (Engine 29.8.2, aarch64) with 18 CPUs and 7.75 GiB of memory allotted; PostgreSQL 16 (`postgres:16`) in a container |
| Python | 3.12.13, through `uv` 0.11.28 |
| Local model | LM Studio 0.4.25 serving `qwen/qwen3.6-35b-a3b`: the installed build is `lmstudio-community/Qwen3.6-35B-A3B-MLX-4bit` (MLX, 4-bit), 20.43 GB loaded, a 94,720-token context and 4 parallel slots, on the Apple GPU through unified memory |

The model's load settings (context, parallel slots) are the ones read on the day this document was written.
The runs used the same model id, but the settings were not recorded run by run. The machine was **not
dedicated**: a browser, an editor, Docker (the database, Phoenix and the OpenTelemetry collector) and, in one
case, the test suite were running at the same time. Power mode and temperature were not controlled.

The weights alone take about 20 GB of memory, so the local figure below is a statement about a machine with
that much memory and an Apple GPU, not about a typical laptop.

## The AWS deployment

| | |
|---|---|
| Region | `us-east-1` |
| Worker | a Lambda container image, arm64, 2 GB of memory, in a VPC, reserved concurrency 2 |
| Database | RDS PostgreSQL 16, `db.t4g.micro` (2 vCPU, 1 GiB, burstable), Single-AZ, 20 GB gp3 |
| Models | Bedrock, through interface endpoints, with no NAT gateway |
| Client | `curl` and a small script from the laptop, polling `GET /ask/{id}` |

It existed only for short periods on 2026-10-09 (it was deployed twice) and was destroyed each time (`docs/adr/ADR-014-aws-deployment.md`).

## GitHub Actions

The CI jobs run on GitHub-hosted runners: Ubuntu 24.04.5 LTS, image `ubuntu-24.04` (version
20261004.327.1), hosted on Azure in `centralus`. The job log does not print the core count or the memory;
GitHub documents 4 vCPU and 16 GB for the standard Linux runner in a public repository.

## Each timing, and what it is

| Timing | Value | Measured where | What it includes | What it does not |
|---|---|---|---|---|
| Latency p50, `local-qwen` | 29.0 s (mean 70.1 s) | the laptop; Qwen in LM Studio on the same machine | the whole agent run for one question: every model call, every tool call, the local database | any other machine; the mean is well above the median: some questions take much longer than most |
| Latency p50, `omniroute-free` | 50.1 s (mean 64.8 s) | the laptop, through OmniRoute running on it, to one free-tier model (`nvidia/nemotron-3-ultra-550b-a55b:free`) over the internet | the same, plus the provider's queue and rate limits | a stable number: a free tier changes from one day to the next (ADR-010) |
| Latency p50, `bedrock-claude`, `-sonnet`, `-opus` | 12.0 s, 18.4 s, 25.6 s (means 12.0, 17.2, 23.2 s) | the laptop, calling Bedrock in `us-east-1` over the author's internet connection; tools and PostgreSQL local; the Guardrail and the knowledge base off | the same, plus the network between the laptop and the region | the deployed stack: a worker inside the region does not pay that leg, and that was not measured over the 80 questions |
| One question on the deployed API | 5 to 22 s; a cold container in the VPC adds about 13 s; the first question after a cold start took 34 s against 18 s warm | the AWS deployment above | the front function, the job in DynamoDB, the worker, RDS, Bedrock and the Guardrail | a distribution: it is the range seen over the questions asked during the checks, not a percentile |
| Quickstart on a clean clone | 134 s (4 s `make db-up`, 4 s `make seed`, 6 s `make demo`, 121 s `make test`) | the laptop, in a fresh clone with an empty `uv` cache and the Postgres image already downloaded | the five commands of the README | the first run's image download (about 660 MB) |
| The same quickstart in CI | 222 s for the step, 231 s for the job | a GitHub-hosted runner | checkout, `uv` setup and the five commands, on a runner that has to download the Postgres image | |
| Test suite | about 80 to 120 s for 957 tests | the laptop | the 45 attacks against the real database | the 5 skipped tests |
| Other CI jobs | lint 7 s, eval gate 45 s, infrastructure tests 41 s, tests 142 s | a GitHub-hosted runner | | |
| First deploy | 627 s | `cdk deploy` run from the laptop to `us-east-1` | the time ADR-014 records for the first deploy | the second deploy, whose time was not recorded |

`Latency p50` in the README and the evaluation report is the wall-clock time of one question
(`usage.ms` in `evals/metrics.py`), over the 80 golden cases, **one question at a time** (the questions share
the database and a free tier rate-limits), p50 across cases.

## What follows from it

- **The local and the cloud latencies are not comparable.** One includes only the machine; the other includes
  a network leg. The README says so in a footnote.
- **The cost model's capacity figures** (questions per day a worker can carry) use the mean latencies above.
  Because those include the laptop's network leg, they are conservative for a worker inside the region; how
  much was not measured.
- **Nothing here was measured on a second machine,** on Linux, on an Intel chip or on a GPU of another kind.
  Rerunning on other hardware needs the 20 GB model to fit; the cloud profiles run anywhere.
- **The run overlapped other work once:** the Sonnet 4.6 run shared the local database with a full test run
  during its write cases (ADR-014); the write labels were checked and match.
