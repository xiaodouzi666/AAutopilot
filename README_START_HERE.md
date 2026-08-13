# AArch64 Autopilot — CC Autonomous Build Bundle

> **Target competition:** Arm Create: AI Optimization Challenge 2026
> **Recommended category:** Cloud AI
> **Official deadline:** 2026-08-14 16:00 PDT = 2026-08-15 01:00 CEST (Amsterdam)
> **Primary objective:** Build and submit a reproducible, CPU-only Arm64 AI optimization system with real before/after evidence.

## 1. What this bundle is

This is a complete execution package for Claude Code, Codex CLI, or another autonomous coding agent. It contains:

- a fixed project concept designed around the official judging criteria;
- product requirements and non-goals;
- a detailed technical architecture;
- an experimentally rigorous benchmark protocol;
- a sequenced autonomous build checklist;
- a Devpost write-up draft and a three-minute video script;
- an optional Arm Performix MCP profiling prompt.

The recommended project is **AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**.

It accepts one Arm64 Linux machine and a small set of official GGUF models, then automatically:

1. fingerprints the CPU and its Arm instruction-set capabilities;
2. builds a fair generic `llama.cpp` baseline and a KleidiAI-enabled variant;
3. searches quantization, thread, batch, micro-batch, and concurrency settings;
4. calibrates a quality-gated small/large-model cascade on an objective agent benchmark;
5. selects a Pareto-optimal deployment profile under quality, latency, and memory constraints;
6. launches an OpenAI-compatible endpoint; and
7. generates raw evidence, charts, an HTML report, screenshots, and Devpost-ready claims.

## 2. GPU decision

**Do not use a GPU in the primary submission.**

The competition’s Cloud AI track explicitly welcomes quantized inference using `llama.cpp` on standard **CPU-only Arm instances**. The technical story is stronger when every result is demonstrably attributable to Arm CPU optimization and KleidiAI, rather than to an unrelated accelerator.

The build must therefore:

- compile with GPU backends disabled where relevant;
- run `llama.cpp` with `--device none` or the pinned version’s equivalent;
- set `--n-gpu-layers 0` when supported;
- record backend startup logs proving CPU-only execution;
- include `gpu_used: false` in the machine-readable report.

No model training is required. Use official pre-quantized GGUF artifacts.

## 3. Hardware

### Recommended final benchmark target

- `aarch64` Linux host;
- Ubuntu 22.04/24.04 or another current Linux distribution;
- 8–16 Arm vCPUs or physical cores;
- 16 GB RAM or more;
- 15 GB free disk space;
- SSH and passwordless `sudo` for the autonomous agent.

Suitable classes include AWS Graviton, Google Axion, Azure Cobalt, Ampere-based servers, or an on-prem Arm64 server. Do not couple the repository to one provider.

### Minimum viable target

- 4 Arm vCPUs;
- 8 GB RAM;
- 10 GB disk.

### Existing RK3588S board

The project should support the user’s RK3588S board as an **optional portability bonus**. It is not the primary benchmark target because the selected category is Cloud AI. On heterogeneous big.LITTLE systems, add a best-effort affinity experiment comparing big cores only versus all cores, but do not let this block the cloud submission.

### Apple Silicon

Apple Silicon is acceptable for development and smoke tests, with Metal explicitly disabled. The final benchmark should preferably be produced on Arm64 Linux so the Cloud AI positioning and Performix workflow are unambiguous.

## 4. How to hand this to CC

1. Put this bundle in an empty project directory.
2. Give CC access to the target Arm64 Linux host, or launch CC directly there.
3. Ensure `git`, Python, CMake/Ninja, a C/C++ compiler, and normal network access are available.
4. Start the agent with the text in `START_PROMPT.txt`.
5. Let it execute `CLAUDE.md` without redesigning the concept.

Only three human gates should remain:

- supplying cloud/SSH credentials if they are not already present;
- recording or approving the final demo video;
- pressing the final Devpost submission button after checking generated claims.

## 5. Required final outputs

A successful run must leave at least:

```text
artifacts/
├── system-info.json
├── build-manifest.json
├── model-manifest.json
├── raw/
├── benchmark-results.csv
├── benchmark-results.json
├── ablation-results.csv
├── quality-results.json
├── optimized-profile.yaml
├── report.html
├── report.md
├── figures/
├── screenshots/
├── devpost-writeup-final.md
└── submission-checklist.md
```

No benchmark number may be written into README, Devpost materials, or video captions unless it was generated from committed raw data on the named Arm64 target.
