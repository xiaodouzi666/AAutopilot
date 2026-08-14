# Scope and Product Requirements

## Product name

**AArch64 Autopilot**

### Submission title

**AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**

### Tagline

**Give it an Arm64 machine and an agent workload; it discovers the fastest quality-preserving CPU configuration and generates the proof.**

## Problem

Deploying a small agentic LLM on Arm CPU currently requires developers to make many coupled choices:

- runtime/backend build options;
- model and quantization;
- thread count and CPU affinity;
- batch, micro-batch, context, and parallel slots;
- whether a smaller model can handle a request safely;
- how to compare speed without silently sacrificing quality;
- how to turn raw performance logs into reproducible deployment evidence.

A configuration copied from another machine may be slower or use more memory because Arm CPUs differ in core count, topology, and available features such as DotProd, I8MM, SVE, SME, and SME2.

## Product promise

AArch64 Autopilot performs device-specific optimization without a GPU and without training a model. It finds a deployable configuration that satisfies explicit quality, safety, latency, and memory constraints, then publishes the exact evidence needed to reproduce or challenge the result.

## Primary users

1. Developers migrating an agent or local LLM service from x86 to Arm64 cloud.
2. Teams deciding which quantization/runtime settings to deploy on an Arm CPU.
3. Educators and performance engineers who need a transparent Arm AI optimization example.
4. Hackathon judges who need to validate the optimization quickly.

## Core user journey

1. The user obtains an Arm64 Linux host and clones the repository.
2. `make doctor` reports compatibility and CPU features.
3. `make bootstrap` builds the generic and KleidiAI variants and downloads model files.
4. `make optimize` runs a bounded search and quality evaluation.
5. The tool prints a final summary such as:

   ```text
   Selected profile: artifacts/optimized-profile.yaml
   Backend: KleidiAI / CPU only
   Quality gate: PASS
   Safety: PASS
   Headline metrics: generated from report.json
   Reproduce: make benchmark PROFILE=artifacts/optimized-profile.yaml
   ```

6. `make demo` starts the OpenAI-compatible endpoint and evidence dashboard.
7. The user can inspect every candidate, raw request, command line, and calculation.

## Wow moment

The demo begins with a real Arm64 system report, launches a visible candidate search, and ends with a single evidence card:

```text
QUALITY HELD  |  p95 LATENCY ↓ ...  |  THROUGHPUT ↑ ...
PEAK RAM ↓ ...  |  GPU: NONE  |  ARM KLEIDIAI: VERIFIED
```

Every ellipsis is filled only after measurement. The card links to a four-stage ablation chart and raw JSON.

## Functional requirements

### FR-1: Arm target inspection

The CLI shall:

- reject non-Arm targets for real benchmarks unless an explicit mock/test flag is used;
- capture architecture, CPU model, logical/physical cores, sockets, NUMA nodes, cache layout, kernel, distro, compiler, and memory;
- identify relevant instruction features from multiple sources where possible;
- identify heterogeneous CPU clusters and available affinity controls;
- save the result as JSON and human-readable Markdown.

**Acceptance:** `make doctor` exits zero on a compatible target and produces `artifacts/system-info.json` conforming to a checked schema.

### FR-2: Reproducible dual build

The bootstrap process shall build from one pinned `llama.cpp` commit:

- a generic CPU build with KleidiAI disabled;
- an Arm-optimized build with KleidiAI enabled;
- the same required binaries in each build (`llama-server`, `llama-cli`, `llama-bench`, or pinned equivalents).

**Acceptance:** both binaries run; build manifests contain source commit and compiler flags; optimized startup output proves KleidiAI use.

### FR-3: Licensed model acquisition

The project shall acquire official Apache-2.0 GGUF artifacts for:

- Qwen2.5-0.5B-Instruct;
- Qwen2.5-1.5B-Instruct.

Default quant candidates:

- weak model: Q4_0 for a future calibrated cascade;
- strong model: Q4_0 for the primary KleidiAI ablation and Q8_0 for reference.

Models are downloaded, not committed.

**Acceptance:** every file has source repository, revision, filename, SHA-256, byte size, and license in `model-manifest.json`.

### FR-4: Safe incident-triage agent demo

The demo shall accept synthetic cloud incident descriptions and return structured JSON containing:

- summary;
- severity;
- diagnosis category;
- hypotheses;
- read-only tool calls;
- safe next action;
- escalation flag.

Allowed tools are deterministic mocks such as `inspect_service`, `read_logs`, `check_disk`, `check_memory`, `check_network`, and `escalate`. The system shall never execute destructive host commands.

**Acceptance:** the schema validates; all tool names are allowlisted; the demo endpoint handles at least three representative cases.

### FR-5: Objective quality suite

The repository shall contain at least 60 original synthetic cases:

- 20 simple/single-symptom cases;
- 20 multi-symptom cases;
- 10 noisy or contradictory cases;
- 10 ambiguous cases where escalation is expected.

Each case shall encode objective expected labels, required/acceptable tools, prohibited actions, and severity.

Use 40 cases only for calibration and 20 held-out cases only for final reporting.

**Acceptance:** a leakage check confirms held-out expected labels are not used by the routing decision; scoring is deterministic.

### FR-6: Benchmark and instrumentation

The system shall measure:

- model bytes;
- startup time;
- prompt processing speed where exposed;
- time to first token;
- end-to-end latency;
- generation tokens per second;
- requests per second under bounded concurrency;
- p50 and p95 latency;
- post-readiness idle and peak resident memory;
- quality score;
- safety score;
- weak/strong route share;
- repeated-run dispersion or confidence intervals.

**Acceptance:** raw per-request rows and summarized CSV/JSON are both present and internally consistent.

The minimum deadline-safe implementation may use one formal quality repetition per held-out case
only when it also emits a separate fail-closed supporting artifact with at least three measured
micro/service repetitions. That artifact must cover generic Q8_0, generic Q4_0, and KleidiAI Q4_0
at two topology-derived thread counts, plus fresh-start Q4_0 p1/p2 concurrency for both backends.
It must preserve equal per-request context, request streaming usage explicitly, and never be
counted as extra held-out or headline-claim rows. Strict verification reparses every
`llama-bench` stdout table, request/response receipt, concurrency-round interval, startup counter,
and RSS sample; it also binds commands and hashes to the current build/model manifests. The
sanitized public copy must pass the same semantic replay after its raw hashes are refreshed.

### FR-7: Bounded auto-tuning

The optimizer shall search in stages rather than running an uncontrolled Cartesian product:

1. microbenchmark backend, quantization, and thread candidates;
2. service benchmark the best few candidates with batch/micro-batch/parallel settings;
3. calibrate routing thresholds under the quality gate;
4. evaluate only the final candidates on held-out data;
5. produce a Pareto frontier and select a balanced feasible profile.

**Acceptance:** candidate count and maximum runtime are configurable; the optimizer can resume from cached raw data.

The staged tuner must consume the verified performance-probe artifact before constructing A3
service candidates. It ranks the two KleidiAI Q4_0 micro cells by `tg64` throughput, then `pp128`
throughput and thread count as deterministic tie-breakers, and constrains service search to those
measured thread counts. Because formal A3 quality requests are sequential, only `parallel=1` is
eligible for selection; the independent true-concurrency probe matrix supplies the required p1/p2
evidence without presenting single-request latency as aggregate concurrency throughput.

Resume is valid only when a candidate has both a complete raw case/repetition matrix and a receipt
that cites exactly those run IDs. A complete plan is returned without inference only after strict
raw replay. Partial or unreceipted raw rows fail closed instead of repeating a request or claiming
a cached result. The monotonic `max_minutes` deadline is passed into startup and every inference,
so one candidate or held-out call cannot silently overrun the search budget. Calibration ranking
freezes `scheduled_finalists`; a candidate enters `admitted_finalists` only after all held-out
cases and requested repetitions complete, and every admitted candidate therefore has a complete
replayable receipt.

Strict verification independently regenerates the probe-ranked candidate set and recomputes the
calibration ranking, held-out candidate summaries, quality-gate decisions, and selected A3 from
raw request/response evidence. Editing `search-plan.json` and the optimized profile together is
not sufficient to change the selected deployment.

Quick mode reduces calibration search depth only. It does not truncate the 20-case formal A0/A1/A2
ablations, any admitted A3 held-out finalist, or the complete minimum micro/concurrency matrix.

### FR-8: Quality-gated cascade

The baseline uses the strong model for every request. The optimized path may use the weak model only when a calibrated complexity rule predicts it is safe. Invalid or unsafe weak-model output must automatically escalate to the strong model.

Default constraints:

- safety score = 100%;
- total quality >= strong-model baseline minus one absolute point;
- no unhandled schema violations;
- optional user p95 and memory limits.

**Acceptance:** final held-out results pass the configured gate or the system falls back to the best strong-only profile.

### FR-9: Deployment endpoint

The selected profile shall launch behind an OpenAI-compatible endpoint with:

- `/health`;
- `/v1/chat/completions`;
- `/v1/models` where practical;
- `/metrics` or a documented lightweight status endpoint;
- routing metadata available in debug mode without breaking client compatibility.

**Acceptance:** a supplied curl command and Python client both complete a request.

### FR-10: Evidence report

The report generator shall produce offline-viewable HTML, Markdown, JSON, CSV, and PNG figures. It shall include:

- hardware and software provenance;
- CPU-only and KleidiAI proof;
- benchmark protocol;
- headline before/after values;
- four or more ablation stages;
- a quality/latency Pareto chart;
- routing distribution;
- memory and concurrency results;
- limitations and exact reproduction commands.

For the supporting probes, the report must disclose the exact generic/KleidiAI build variants and
source commit, CPU-only configuration, KleidiAI runtime marker, model filenames/bytes/checksums and
tensor inventories, formal versus probe sample counts and failures, and p1/p2 idle/peak RSS.

**Acceptance:** every headline value is programmatically traced to raw data; unresolved placeholders fail the build.

### FR-11: Submission materials

`make submission` shall render:

- English README sections;
- final Devpost write-up;
- benchmark table;
- challenge/learning answers;
- screenshot manifest;
- final checklist;
- three-minute video script populated with real values.

**Acceptance:** a linter rejects fabricated placeholders, private data, broken relative links, missing license, or inaccessible assets.

## Non-functional requirements

- Works natively on Linux `aarch64`.
- Core workflow does not require a GPU, paid API, database, Kubernetes, or external web service.
- Safe failure: an unavailable model or profiling tool produces a clear fallback, not corrupted results.
- The default full run is bounded and resumable.
- Unit tests run on non-Arm CI using mocks; real performance claims are only generated on Arm.
- Logs are structured and redact secrets.
- Code is typed and formatted; errors return actionable messages.

## Non-goals

- Training or fine-tuning an LLM.
- Implementing new matrix microkernels under the deadline.
- Claiming a universal best configuration for every Arm CPU.
- Building a general-purpose autonomous SRE product.
- Executing remediation commands against a real production system.
- Supporting every model family/runtime/provider.
- A React-heavy frontend or user account system.
- Making the optional Performix integration a hard dependency.

## Success definition

The project succeeds when a judge can understand within 30 seconds:

1. what was optimized;
2. why the change is Arm-specific;
3. whether quality was preserved;
4. the measured before/after result;
5. how to reproduce it in one command.
