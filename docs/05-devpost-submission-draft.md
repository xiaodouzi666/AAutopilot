# Devpost Submission Draft

> This is a template. The build must replace every `[[AUTO:...]]` token from verified claim artifacts. Never type an unmeasured number manually.

## Project title

**AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**

## Tagline

**Give it an Arm64 machine and an agent workload; it discovers the fastest quality-preserving CPU configuration and generates the proof.**

## Selected category

**Cloud AI**

## Project overview

Running an AI agent on Arm is easy to claim and surprisingly hard to optimize responsibly. A faster quantization, a different thread count, or a smaller model may improve a benchmark while silently reducing task quality. Settings copied from one machine may also perform poorly on another because Arm systems differ in topology and instruction support.

AArch64 Autopilot is an open-source, CPU-only optimization and deployment toolkit for agentic LLM workloads on Arm64. It fingerprints the target CPU, builds a fair generic `llama.cpp` baseline and an Arm KleidiAI variant from the same pinned source, searches a bounded runtime configuration space, calibrates a weak/strong model cascade, enforces an objective quality and safety gate, and emits a deployable OpenAI-compatible endpoint plus an auditable evidence report.

The included demonstration is a safe cloud incident-triage agent over original synthetic fixtures. The workload is deliberately structured: each case has expected diagnosis, severity, allowed read-only tools, prohibited actions, and escalation behavior. This lets the optimizer prove that speed did not come from silently accepting worse or unsafe answers.

## Why it should win

AArch64 Autopilot turns Arm AI optimization into a reproducible product rather than a one-off benchmark:

- **Arm-specific:** it verifies KleidiAI at build and runtime and isolates its contribution using the same model, machine, prompts, and settings.
- **Quality-aware:** every candidate must satisfy a held-out task-quality floor and 100% safety compliance.
- **Autonomous:** one command performs feature detection, benchmarking, staged search, routing calibration, Pareto selection, and report generation.
- **Reusable:** developers can replace the incident suite with their own JSON-scored agent workload and receive a target-specific deployment profile.
- **Auditable:** every headline claim links to raw request rows, command lines, binary/model hashes, system provenance, and confidence intervals.
- **CPU-only:** no GPU, training job, or paid inference API is required.

The visible “wow” moment is not a canned animation. The tool discovers the best feasible configuration on the actual Arm machine, then shows exactly what changed and how much each optimization stage contributed.

## What it does

1. **Inspects the Arm64 target**
   - records CPU topology, caches, NUMA, memory, OS, toolchain, and relevant features such as DotProd, I8MM, SVE, SME, and SME2 where exposed;
   - derives safe thread and affinity candidates.

2. **Builds a fair baseline and Arm backend**
   - compiles two `llama.cpp` variants from one pinned commit;
   - keeps build settings identical except for KleidiAI;
   - proves CPU-only execution and records the KleidiAI runtime marker.

3. **Tests licensed quantized models**
   - uses official Apache-2.0 Qwen2.5 0.5B and 1.5B GGUF models;
   - evaluates official Q4_0 and Q8_0 candidates supported by the pinned KleidiAI kernels.

4. **Runs a bounded staged search**
   - ranks backend/model/thread candidates with microbenchmarks;
   - service-tests the best few batch, micro-batch, concurrency, context, and affinity configurations;
   - caches results and resumes safely.

5. **Preserves agent quality**
   - calibrates a transparent request-complexity threshold on 40 cases;
   - evaluates the frozen profile on 20 held-out cases;
   - escalates invalid or unsafe weak-model output to the strong model;
   - rejects a cascade that fails its quality or safety gate.

6. **Deploys and explains the result**
   - launches an OpenAI-compatible proxy;
   - generates offline HTML, Markdown, JSON, CSV, charts, raw evidence, and Devpost-ready claims.

## Final output

The final output is not just an optimized model file. It is a reusable **optimization output plus migration workflow**:

- `optimized-profile.yaml` with the selected model/backend/runtime/router configuration;
- an OpenAI-compatible CPU-only agent endpoint;
- an original deterministic agent benchmark suite;
- generic-vs-KleidiAI and full-system ablation data;
- raw request/latency/memory/quality records;
- a self-contained evidence dashboard;
- scripts and documentation for adapting another agent workload.

## Measured results

### Target

- Architecture: `[[AUTO:architecture]]`
- CPU: `[[AUTO:cpu_model]]`
- Cores available: `[[AUTO:allowed_cores]]`
- Relevant detected features: `[[AUTO:cpu_features]]`
- OS/kernel: `[[AUTO:os_kernel]]`
- `llama.cpp` commit: `[[AUTO:llama_commit]]`
- GPU used: **No**
- KleidiAI runtime verification: **[[AUTO:kleidiai_verified]]**

### Headline evidence

| Comparison | Quality | p95 latency | Throughput | Peak RSS | Notes |
|---|---:|---:|---:|---:|---|
| Generic Q4_0 strong baseline | `[[AUTO:a1_quality]]` | `[[AUTO:a1_p95]]` | `[[AUTO:a1_rps]]` | `[[AUTO:a1_rss]]` | Same Q4_0 model and fixed settings |
| KleidiAI Q4_0, same settings | `[[AUTO:a2_quality]]` | `[[AUTO:a2_p95]]` | `[[AUTO:a2_rps]]` | `[[AUTO:a2_rss]]` | Isolates Arm backend |
| Autotuned strong-only | `[[AUTO:a3_quality]]` | `[[AUTO:a3_p95]]` | `[[AUTO:a3_rps]]` | `[[AUTO:a3_rss]]` | Device-specific runtime profile |
| Full quality-gated system | `[[AUTO:a4_quality_or_na]]` | `[[AUTO:a4_p95_or_na]]` | `[[AUTO:a4_rps_or_na]]` | `[[AUTO:a4_rss_or_na]]` | Cascade included only if feasible |

Primary fair Arm-specific result:

- p95 latency change from generic Q4_0 to KleidiAI Q4_0: **[[AUTO:arm_p95_change]]**
- throughput change from generic Q4_0 to KleidiAI Q4_0: **[[AUTO:arm_throughput_change]]**
- 95% confidence interval: **[[AUTO:arm_ci]]**

Full selected profile versus strong-only baseline:

- p95 latency change: **[[AUTO:full_p95_change]]**
- throughput change: **[[AUTO:full_throughput_change]]**
- quality change: **[[AUTO:full_quality_change]]**
- safety score: **[[AUTO:full_safety]]**
- weak-model route share: **[[AUTO:weak_route_share]]**

All numbers above are generated from committed raw evidence. The report includes run IDs, exact commands, hashes, sample counts, dispersion, and limitations.

## How it uses and improves Arm-powered platforms

The project directly targets Arm CPU inference. KleidiAI supplies optimized AI microkernels and lets `llama.cpp` select kernels based on runtime-detected Arm capabilities. AArch64 Autopilot adds the missing deployment layer around that backend:

- it verifies which CPU capabilities and kernels are actually in use;
- produces a fair generic-versus-KleidiAI comparison;
- tunes threads, batches, concurrency, context, and affinity for the specific target;
- calibrates model routing under a task-quality constraint;
- packages the result as a reproducible service and evidence bundle.

This matters because “works on Arm” does not tell a developer which model, quantization, backend, or serving configuration is safe to deploy on their machine.

## How we built it

- Python 3.11, Typer, FastAPI, HTTPX, Pydantic, NumPy, Matplotlib, and Jinja2;
- `ggml-org/llama.cpp`, pinned and built in generic CPU and KleidiAI variants;
- official Qwen2.5 0.5B and 1.5B GGUF models;
- Arm-aware hardware and topology inspection;
- staged multi-objective search and Pareto selection;
- deterministic structured-agent quality suite;
- optional Linux `perf` and Arm Performix MCP evidence.

## Setup and validation

### Requirements

- Linux `aarch64` target;
- 4 cores / 8 GB RAM minimum, 8–16 cores / 16 GB recommended;
- approximately 10–15 GB free disk;
- normal compiler and network access;
- no GPU required.

### Quickstart

```bash
git clone [[AUTO:public_repo_url]]
cd aarch64-autopilot
make doctor
make bootstrap
make smoke
make optimize
make demo
```

Open the local report URL printed by `make demo`, or validate without a browser:

```bash
make verify
curl -s http://127.0.0.1:8088/health
python demo/demo-client.py
```

### Reproduce the selected result

```bash
make benchmark PROFILE=artifacts/optimized-profile.yaml
make report
make verify-claims
```

The model files are downloaded by checksum-aware scripts and are intentionally not stored in Git.

## What was the hardest part?

Recommended form selections, adjusted to actual experience:

- Measuring performance
- Improving model speed or latency
- Reducing model size or memory usage
- Understanding Arm-specific guidance
- Debugging runtime or compatibility issues

Narrative:

The hardest part was designing a benchmark where a faster result could not hide a quality regression. Backend, quantization, serving settings, model routing, and structured-agent correctness interact. We addressed this with fair same-model ablations, a fixed calibration/held-out split, deterministic quality scoring, automatic safety escalation, repeated timing, and claim-level provenance.

## What would have made it easier?

Recommended form selections, adjusted to actual experience:

- More Arm-specific optimization guidance
- More benchmarking examples
- More starter templates
- Clearer setup instructions

## Did this challenge change your likelihood of building on Arm in the future?

**Yes, significantly more likely.**

## How likely are you to continue the project?

**Very likely.**

## One thing Arm could improve

A versioned, machine-readable reference benchmark that pairs Arm backend performance with a task-quality gate would make it easier to compare optimizations responsibly. Examples often show how to enable an optimized backend, while developers still need to design their own fair baseline, parameter search, provenance capture, and regression threshold. A standard schema and starter harness for those pieces would accelerate trustworthy adoption.

## Challenges we ran into

- Current runtimes expose many interacting flags, so the tool discovers supported options from the pinned binary rather than assuming an old interface.
- Default thread choices are not treated as a benchmark baseline; the project explicitly sweeps a bounded host-derived set.
- Both weak and strong models residing in memory can improve latency while increasing RSS, so the optimizer reports a Pareto frontier instead of hiding the tradeoff.
- Some performance counters require host permissions, so `perf` and Performix enrich but do not block the core benchmark.

## Accomplishments we are proud of

- One command turns a clean Arm64 machine into a measured deployment profile.
- Every headline metric is traceable to raw evidence and exact binary/model hashes.
- The optimizer can reject its own “faster” cascade if held-out quality or safety is insufficient.
- The same report shows model-size, backend, runtime, and routing contributions separately.
- The service remains a drop-in OpenAI-compatible endpoint after optimization.

## What we learned

Arm optimization is not a single compiler switch. Backend kernels, quantization, thread topology, serving concurrency, memory residency, and workload quality form one system. The largest practical lesson was that optimization tooling becomes much more reusable when it treats quality as a hard constraint and produces provenance automatically.

## What is next

- add adapters for vision/audio and non-LLM Arm workloads;
- support additional runtimes such as ExecuTorch and LiteRT;
- add more workload-defined quality plugins;
- expand topology-aware scheduling for heterogeneous edge CPUs;
- integrate richer Performix recipes and continuous regression tracking;
- publish community profiles for common Arm cloud instance families.

## Meaningful work completed during the challenge period

`[[AUTO:challenge_period_work_summary]]`

## License and third-party use

The project is released under Apache 2.0. Third-party runtime and model licenses, pinned revisions, notices, and checksums are documented in `THIRD_PARTY_NOTICES.md` and generated manifests.
