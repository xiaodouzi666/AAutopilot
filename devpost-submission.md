# Title

AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs

## One-line Summary

Give it an Arm64 machine and an agent workload; it discovers a fast quality-preserving CPU configuration and generates the proof.

## Category

Cloud AI

## Project Overview

### Problem

Deploying an agentic LLM on Arm CPU requires coupled choices across runtime kernels,
quantization, threads, topology, batches, concurrency, and model routing. A fast-looking
configuration can silently reduce task quality, while settings copied from another machine
may perform poorly on a different Arm topology. Developers also need enough provenance to
distinguish a real Arm-specific gain from model, workload, or lifecycle changes.

### Solution

AArch64 Autopilot is an open-source, CPU-only optimization and deployment toolkit. It
fingerprints an Arm64 target, builds fair generic and KleidiAI `llama.cpp` variants from one
pinned commit, measures an objective incident-triage workload, searches a bounded runtime
space, rejects candidates that violate quality or safety, and emits a measured deployment
profile with an OpenAI-compatible API and offline evidence report.

The primary Arm ablation uses the official Qwen2.5 1.5B Q4_0 GGUF on both binaries. Its exact
pinned inventory contains 197 Q4_0 tensors and one disclosed Q6_K `output.weight`; that single
fallback is allowed only when SHA-256, size, and the full GGUF header inventory match. Verification
requires the primary KleidiAI Q4 marker and rejects any additional or different fallback.

The included workload contains 60 original synthetic cloud incidents. The v1 test set was
executed in failed run6 and used for error analysis, so it is retired from final evaluation.
Before the successful final Arm run, split v2 selected 20 final-holdout cases from 36 never-executed candidates
with a frozen category-stratified hash procedure whose only inputs were category and case ID.
Only v2 is described as the unseen final holdout; `demo/split-freeze-v2.json` preserves every
selection input and both split hashes. The agent may request deterministic read-only fixture
tools; it never executes model-generated shell commands.

## Why This Matters

“Runs on Arm” is not enough to choose a deployment. AArch64 Autopilot turns established
components—quantization, KleidiAI, `llama.cpp`, and model routing—into a transparent workflow
that answers which configuration works on this machine, what tradeoff it makes, whether
quality held, and exactly how to reproduce the result.

## Why It Should Win

- **Technical implementation:** same-commit generic/KleidiAI builds, explicit CPU-only proof,
  deterministic scoring, held-out gate, bounded search, and claim-level provenance.
- **Developer experience:** stable CLI, one-command stages, clear failure modes, localhost API,
  and an offline dashboard.
- **Impact:** reusable workload adapter, raw schemas, optimizer, profile, tests, and documentation.
- **WOW factor:** the Arm machine evaluates its own configurations and either selects a measured
  winner or visibly rejects a faster configuration that fails quality.

## How We Used AI

The submitted endpoint serves the measured official Qwen2.5 1.5B strong-only profile for
structured synthetic incident triage. It enforces the benchmark's typed triage schema, then
validates schema, read-only tool arguments, safety, and internal consistency before returning a
response. Invalid output fails closed with HTTP 502.

The final Arm run also measured the optional 0.5B/1.5B A4 routing workflow. Calibration found no
admissible weak-model threshold, so its immutable policy failed closed to strong-only. The A4
quality replay was not admitted, carries no performance claim, and does not change the submitted
A3 strong-only deployment.

## How We Used Codex

Codex turned the competition plan into the typed Python package, Arm hardware and build
orchestration, model registry, 60-case workload, safe proxy, benchmark store, statistics,
bounded tuner, report generator, tests, documentation, and submission assets. It also reviewed
the live Devpost rules and requirements, ran verification, inspected failures, and preserved the
project's non-negotiable rule that no performance number can be fabricated.

## Functionality / Output

### Key Features

1. Evidence-backed Arm feature and topology doctor with public redaction.
2. Fair same-commit generic and `GGML_CPU_KLEIDIAI=ON` native builds.
3. Official Qwen model resolution with immutable revision, SHA-256, size, and license manifest.
4. Sixty-case deterministic safe incident benchmark with an auditable frozen v2 40/20 split.
5. CPU-only `llama-server` lifecycle, RSS sampling, streaming TTFT/E2E timing, and cleanup.
6. Bounded candidate generation, hard quality/safety feasibility rules, and Pareto selection.
7. OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/health`, `/metrics`, and `/report`.
8. Offline report, sanitized formal JSON/CSV, figures, claim formulas, source run IDs, and
   integrity hashes, plus a full redacted raw capture in the attested release bundle.

## Architecture

The CLI coordinates hardware inspection, same-commit native builds, official model acquisition,
service benchmarking, quality evaluation, bounded search, and report generation. The selected
profile feeds a localhost OpenAI-compatible proxy and currently takes one measured strong-model
path. The proxy applies constrained output plus a final safety/consistency validator before
responding. The 0.5B/1.5B A4 router is measured as a fail-closed quality experiment, not as the
submitted deployment. Its final frozen replay selected strong-only, and a future live cascade
would still require a separately measured multi-runtime performance profile before serving. Raw
evidence is append-only, hashed, and rendered independently from inference.

## Measured Results

The direct A1/A2 comparison covers **20 matched split-v2 cases / 40 formal source rows** on an
official GitHub-hosted `ubuntu-24.04-arm` runner. Mean time-to-first-token reduction was
prospectively registered as the sole primary publication outcome. The other two metrics are
transparent secondary results and cannot unlock publication.

- **Primary — Q4_0 mean TTFT reduction: 1.498%**, paired 95% bootstrap CI **[0.514%, 2.600%]**. The interval excludes zero on the positive side, so the primary gate passes.
- **Secondary — Q4_0 p95 end-to-end latency reduction: 4.310%**, paired 95% bootstrap CI **[-15.486%, 48.746%]**. The interval crosses zero; this is not a demonstrated improvement.
- **Secondary — Q4_0 median per-request throughput increase: 2.023%**, paired 95% bootstrap CI **[-3.363%, 10.793%]**. The interval crosses zero; this is not a demonstrated improvement.

Both fair-pair groups passed the task gate: A1 quality **72.975**, A2 quality **73.875**, and both
recorded minimum safety **100/100** with zero schema failures. Exact formulas, source IDs, and
machine-readable confidence intervals are in
[`artifacts/claims.json`](https://github.com/xiaodouzi666/AAutopilot/blob/main/artifacts/claims.json).

## A4 Weak/Strong Quality Replay

The final Arm run measured real Qwen2.5 0.5B and 1.5B component outputs for 40 calibration cases,
wrote immutable freeze `a0aee385…5190` and policy `34df5c9a…bb6`, then replayed that frozen
decision on 20 split-v2 cases.

Calibration found no admissible weak-model threshold, so the policy failed closed to strong-only.
The replay produced **20 strong routes, 0 weak routes, 0 weak-then-strong routes, and 0%
escalation**. A4 was **not quality-admitted**, `performance_claim_eligible=false`, and the
shipping profile remains **A3 strong-only**.

This is disclosed as a post-hoc measured quality/routing replay on split-v2 cases already used by
A0–A3. It is not presented as a new unseen confirmatory set, a live-cascade latency/RSS
measurement, or a performance claim.

## Public Evidence

- **Repository:** https://github.com/xiaodouzi666/AAutopilot
- **Successful Arm64 workflow:** https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786
- **Exact measured commit:** https://github.com/xiaodouzi666/AAutopilot/commit/6d8e21818fc0ef0202ec85236bcec6d20e908f23
- **Attested evidence release:** https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786
- **GitHub build-provenance attestation:** https://github.com/xiaodouzi666/AAutopilot/attestations/40687167
- **Natural-voice demo (139 seconds):** https://youtu.be/2wZx67_iaSw

The public demo preserves the CI video's H.264 visual stream byte-for-byte and replaces only its
audio with clearer local Samantha narration. Its
[sidecar](https://github.com/xiaodouzi666/AAutopilot/releases/download/arm64-evidence-run-31778419786/a64pilot-demo-natural-voice-run-31778419786.manifest.json)
identifies it as an audio-only derivative, not the CI-attested original. The release contains both
videos, manifests, the target receipt, A4 freeze/results, and the attested evidence archive.

## Setup Instructions

On a Linux `aarch64` host with 4+ cores, 8+ GB RAM, 10–15 GB free disk, Git, CMake, Ninja,
a compiler, and Python 3.11/3.12:

```bash
git clone https://github.com/xiaodouzi666/AAutopilot.git
cd AAutopilot
make doctor
make bootstrap
make smoke
make optimize
make report
make verify
```

Inspect `artifacts/report.html` locally or run `make demo` and open
`http://127.0.0.1:8088/report`. Model weights are checksum-downloaded from official Qwen
repositories and are intentionally excluded from Git. Testing uses no paid API or private
credential.

For source-level validation without model weights:

```bash
uv sync --extra dev
uv run pytest -q
uv run a64pilot smoke --fixture
```

Fixture mode is explicitly labelled and cannot enter performance claims.

## Public Repository Link

https://github.com/xiaodouzi666/AAutopilot

## Public Demo Link

The complete offline dashboard is versioned under
[`artifacts/report.html`](https://github.com/xiaodouzi666/AAutopilot/blob/main/artifacts/report.html).
The live inference service binds to localhost by default for safety. The complete redacted runtime
evidence and current media are in the
[final evidence release](https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786).

## Demo Video

Watch the public 139-second natural-voice demo:
https://youtu.be/2wZx67_iaSw

## Challenges We Ran Into

The hardest part was making a faster result incapable of hiding a correctness regression.
Backend, quantization, serving parameters, model routing, and memory residency interact. The
solution was a same-model ablation, a retired observed v1 split plus a mechanically frozen v2
final holdout, deterministic scoring, fail-closed response validation, paired timing, and
provenance generated alongside each claim.
Native runtime interfaces also evolve, so the command builder discovers the pinned binary's
supported CPU-only options rather than assuming an old interface.

## Accomplishments We Are Proud Of

- The optimizer can reject an experimental faster cascade when quality or safety does not hold;
  the submitted serving profile remains validated strong-only.
- Fixture output, cross-machine pairs, missing backend markers, and unverified GPU settings are
  structurally barred from claim generation.
- The repository is useful even without the demo workload: developers can replace the cases and
  scorer while reusing the Arm build, tuner, API, and evidence contract.
- The pre-rendered report can be viewed offline. Strict report replay requires the original Arm
  capture inputs on the recorded target; the public GitHub Actions run and artifact attestation
  remain the authoritative execution provenance.

## What We Learned

Arm optimization is a system problem, not one compiler switch. Kernel selection, quantization,
thread topology, concurrency, memory residency, and workload quality must be measured together.
The most reusable optimization tool is one that treats quality as a hard constraint and emits its
own audit trail.

## Meaningful Work During the Challenge Period

This repository and implementation were created during the challenge period from a detailed
execution plan. The challenge work includes the complete package, orchestration, synthetic data,
tests, benchmark protocol, API, report, visuals, and submission automation. Git history and
`BUILD_STATUS.md` record the build sequence.

## Known Limitations

- Results apply only to the recorded target, runtime commit, model files, and workload.
- The synthetic incident task is not a general model-capability benchmark.
- The evidence proves a KleidiAI-enabled Q4_0 path at build, model-load, and validated-request
  levels; it is not instruction-level or per-tensor microkernel tracing.
- A4 is not part of the submitted deployment and carries no live-route performance, combined-RSS,
  latency, or throughput claim.
- Energy and cost are omitted because the target provided no credible energy counter or stable
  instance price.
- The live inference service binds to localhost by default for safety.

## License and Third-party Use

Project code is Apache-2.0. `llama.cpp`, KleidiAI, Qwen model, and Python dependency sources,
licenses, pinned revisions, and checksums are documented in `THIRD_PARTY_NOTICES.md`, `uv.lock`,
and generated manifests. No model weights are redistributed.
