# AArch64 Autopilot

**Self-optimizing agentic AI on Arm CPUs — measured, quality-gated, and GPU-free.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-38bdf8.svg)](LICENSE)
[![Target: Arm64 Linux](https://img.shields.io/badge/Target-Arm64%20Linux-3ddc97.svg)](docs/architecture.md)
[![Track: Cloud AI](https://img.shields.io/badge/Arm%20Create-Cloud%20AI-8b5cf6.svg)](https://arm-ai-optimization-challenge.devpost.com/)

AArch64 Autopilot accepts an Arm64 Linux machine and an objectively scored agent workload,
then discovers a fast CPU-only deployment configuration without silently trading away task
quality. It builds fair generic and KleidiAI-enabled `llama.cpp` variants from the same
pinned commit, searches a bounded runtime space, applies a held-out quality and safety gate,
and emits an OpenAI-compatible endpoint plus claim-level evidence.

This project targets the **Cloud AI** category of the 2026 Arm Create: AI Optimization
Challenge. The submission deadline is **14 August 2026 at 16:00 Pacific Time / 15 August
2026 at 01:00 CEST**.

## The 30-second version

```text
Arm64 target  →  hardware doctor  →  fair generic/KleidiAI builds
                                      ↓
synthetic incident workload  →  bounded search  →  quality + safety gate
                                      ↓
OpenAI-compatible API  ←  selected profile  →  raw evidence + offline report
```

The included workload is safe cloud incident triage over 60 original synthetic fixtures.
Each case has an expected diagnosis, severity, read-only tool set, prohibited actions, and
escalation behavior. Forty cases calibrate tuning and the optional A4 routing experiment;
twenty stay held out until final evaluation. The agent never executes model-generated shell
commands.

## Why this is Arm-specific

- Both CPU runtimes come from one pinned `llama.cpp` commit and use the same compiler,
  build type, model checksum, prompt set, sampling, affinity, and serving configuration.
- The intended A1/A2 difference is only `GGML_CPU_KLEIDIAI=OFF` versus `ON`.
- The runtime must expose the `CPU_KLEIDIAI` marker before optimized rows are accepted.
- The primary model is official Qwen2.5 1.5B `Q4_0`: its pinned inventory contains 197
  `Q4_0` tensors and one disclosed `Q6_K` `output.weight`. That single fallback is allowed only
  when SHA-256, size, and full GGUF header inventory match; the primary KleidiAI Q4 marker is
  required and any additional or different fallback is rejected.
- GPU backends are disabled; runtime commands select zero GPU layers and no device where
  the pinned interface supports it.
- The tuner derives thread and affinity candidates from the actual Arm topology.

No result is hard-coded into this README. Public performance claims are generated only
from paired raw rows on the recorded Arm64 Linux target. Fixture/demo responses carry an
explicit `fixture` label and cannot support a claim.

## Outputs

After a measured run, `artifacts/` contains:

```text
system-info.json              redacted hardware and software provenance
build-manifest.json           pinned source, flags, binary hashes, backend proof
model-manifest.json           repositories, revisions, filenames, sizes, SHA-256
raw/<run-id>/                 commands, requests, logs, RSS and integrity hashes
benchmark-results.{json,csv}  validated measured rows
ablation-results.csv          A0–A4 summary
quality-results.json          calibration/test separation and gate result
optimized-profile.yaml        selected measured deployment profile
claims.json                   formulas, candidates, confidence intervals, source rows
report.{html,md}              offline evidence dashboard
figures/                      generated ablation and Pareto charts
devpost-writeup-final.md      English submission copy rendered from evidence
submission-checklist.md       final compliance receipt
```

## Requirements

Final benchmarking requires:

- Linux on `aarch64`/`arm64`;
- 4 CPU cores and 8 GB RAM minimum; 8–16 cores and 16 GB RAM recommended;
- roughly 10–15 GB of free disk for source, dual builds, and downloaded GGUF files;
- Git, a C/C++ compiler, CMake, Ninja, Python 3.11/3.12, and normal network access;
- no GPU, paid API, database, or private production data.

Apple Silicon can run unit tests, the hardware doctor, and explicit fixture smoke tests,
but it is not accepted as the final Cloud AI benchmark target.

## Quickstart on Arm64 Linux

The stable CLI is repository-oriented: clone the project and run commands from the checkout root
so its versioned workload, configs, templates, scripts, and lockfile remain part of the evidence.
The wheel is used for a locked non-editable install inside that checkout; it is not advertised as
a standalone data bundle.

```bash
git clone https://github.com/xiaodouzi666/AAutopilot.git
cd AAutopilot

make bootstrap
make smoke
make optimize
make report
make verify
```

Start the selected OpenAI-compatible proxy and offline report:

```bash
make demo
curl -fsS http://127.0.0.1:8088/health
python demo/demo-client.py --smoke --debug
```

The server binds to localhost by default. See [benchmark methodology](docs/benchmark-methodology.md)
before interpreting or reproducing a number.

## Development without model weights

Model binaries are intentionally excluded from Git. Source-level development and the
safe fixture responder remain available on any supported Python host:

```bash
uv sync --extra dev
uv run pytest -q
uv run a64pilot doctor
uv run a64pilot smoke --fixture
uv run a64pilot report --allow-pending
```

Fixture mode demonstrates schema validation, API debug metadata, API compatibility, report
layout, and cleanup. It is visibly labelled and excluded from `claims.json`.

## Evidence integrity and signed provenance

The strict local gate replays internal consistency from source/model registries, downloaded
hashes, native binary hashes, CMake caches, CPU-only commands, runtime logs, fixed prompts,
constrained-response schemas, response scores, timings, and all 20 held-out case pairs. It is
designed to catch missing, inconsistent, edited, or unsupported evidence; it is not described
as a cryptographic proof of physical execution by itself.

The public Arm64 GitHub Actions job additionally packages the sanitized evidence into
`aarch64-autopilot-evidence.tar.gz` and signs its digest with GitHub artifact attestation.
That attestation binds the bundle to the public repository, workflow, commit, event, and
official `ubuntu-24.04-arm` run. After downloading the bundle from the successful workflow:

```bash
gh attestation verify aarch64-autopilot-evidence.tar.gz \
  -R xiaodouzi666/AAutopilot
```

The GitHub Actions run and attestation URL are the authoritative execution provenance; the
committed report and raw rows remain the human- and machine-reviewable evidence.

## Stable commands

| Command | Purpose |
|---|---|
| `make doctor` | Record Arm features, topology, memory, and redacted provenance |
| `make bootstrap` | Sync dependencies, pin/build both runtimes, and download models |
| `make smoke` | Fast real-model, CPU-only end-to-end check |
| `make benchmark` | Run fair baselines, ablations, and held-out evaluation |
| `make optimize` | Execute bounded search and select a quality-feasible profile |
| `make report` | Replay the report from raw inputs in the original capture environment |
| `make serve` | Launch the selected OpenAI-compatible endpoint |
| `make demo` | Launch the endpoint and local evidence dashboard |
| `make verify` | Run tests, schemas, provenance, secret, and claim checks |
| `make submission` | Render final English Devpost assets; fails if evidence is missing |

Run `make help` for flags and fixture alternatives.

## Benchmark contract

The final protocol freezes the target and software stack, separates calibration from the
held-out split, retains failures and outliers, reports p50/p95/dispersion, and uses fixed-seed
paired bootstrap intervals. The required ablations are:

1. A0 — generic Q8 strong-model reference;
2. A1 — generic Q4_0 strong-model fair baseline;
3. A2 — the same Q4_0 model/settings with verified KleidiAI;
4. A3 — device-tuned KleidiAI strong-only profile;
5. A4 — quality-gated weak/strong cascade, only if it passes.

If A4 fails the safety, schema, or quality gate, the project ships A3 and preserves A4 as
a rejected experiment. A confidence interval crossing zero is described as “no demonstrated
improvement,” never as a win.

The submitted deployment implementation is the measured **strong-only** profile. A4 remains an
experimental candidate and is not described as deployed unless a future version calibrates it,
passes the complete held-out gate, and adds a measured multi-runtime serving profile.

## API

The selected profile exposes:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `GET /metrics`
- `GET /report`

Standard chat messages, temperature, maximum tokens, and non-streaming/streaming responses
are supported where the selected backend supports them. For incident triage, the proxy replaces
any client `response_format` with the same strict JSON schema used by the benchmark. It validates
the returned schema, read-only tool arguments, safety policy, and internal consistency before
responding; rejected upstream output receives a clear HTTP 502 and is never forwarded verbatim.
The local-only `X-A64Pilot-Debug: 1` header exposes the strong-only route, selected profile, and
validation status without changing completion content.

## Meaningful work during the challenge

The repository was created during the challenge period from a detailed execution plan. The
implementation added the typed CLI and schemas, Arm hardware/provenance inspection, fair dual
build orchestration, official model registry, 60-case deterministic workload, fail-closed proxy
and optional routing experiment, raw benchmark store, statistics, bounded tuner, evidence report,
tests, and submission automation. Git history and `BUILD_STATUS.md` record the work performed.

## Security, privacy, and safety

- Runtime services bind to `127.0.0.1` unless explicitly overridden.
- Authorization headers, tokens, usernames, home paths, hostnames, IP addresses, and SSH
  arguments are redacted from public artifacts.
- No real infrastructure tool is invoked; the incident tools read deterministic fixture data.
- Model-generated shell fragments and destructive actions are rejected.
- Model weights, build output, credentials, and private target configuration are ignored.
- Submission rendering scans for high-confidence secrets and unresolved claim placeholders.

## Limitations

- Results are target-, runtime-, model-, and workload-specific; this is not a universal best
  Arm configuration.
- The synthetic incident suite is objective and useful for regression testing, not a general
  language-model capability benchmark.
- Energy and cloud-cost claims are omitted unless a credible target counter or price is
  available.
- KleidiAI availability depends on the pinned runtime and target instruction support.
- Optional Arm Performix and RK3588 portability evidence never block the core pipeline.

## Documentation

- [Architecture](docs/architecture.md)
- [Benchmark methodology](docs/benchmark-methodology.md)
- [Model and KleidiAI compatibility](docs/model-compatibility.md)
- [Adapting another agent workload](docs/adapting-your-agent.md)
- [RK3588 notes](docs/rk3588-notes.md)
- [Performix integration](docs/performix.md)
- [Competition brief](docs/00-competition-brief.md)
- [Full technical specification](docs/02-technical-spec.md)

## License and acknowledgements

AArch64 Autopilot is released under the [Apache License 2.0](LICENSE). `llama.cpp`,
KleidiAI, Qwen GGUF models, and Python libraries retain their own licenses; sources and
notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Model weights are
downloaded from their official repositories and never redistributed here.
