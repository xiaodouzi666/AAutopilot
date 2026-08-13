# Autonomous Engineering Contract — AArch64 Autopilot

## Role

You are the lead engineer, performance analyst, QA owner, technical writer, and submission-preparation agent for **AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**.

Your job is to produce a working, public-repository-ready hackathon project from this specification. Execute autonomously. Do not spend time brainstorming alternative products. Do not ask routine implementation questions. Make conservative engineering decisions, document them, verify them, and continue.

## Competition clock

- Official submission deadline: **2026-08-14 16:00 PDT / 2026-08-15 01:00 CEST**.
- Optimize for a complete, reproducible submission before adding optional polish.
- Create a Devpost draft as early as possible and keep the final materials continuously renderable.

## Fixed product decision

Build a reusable CLI and OpenAI-compatible service that automatically optimizes an agentic LLM workload on an Arm64 CPU. It must compare a fair generic CPU baseline with an Arm KleidiAI build, tune runtime parameters, calibrate a quality-preserving weak/strong model cascade, and generate auditable before/after evidence.

The included sample workload is **safe cloud incident triage** over synthetic fixtures. It exists because it provides:

- an understandable agent demo;
- deterministic structured outputs;
- objective tool-selection and safety scoring;
- a credible Cloud AI use case;
- no private data or external API dependency.

## Mandatory technical pillars

1. **Arm-specific backend proof**
   - Build `llama.cpp` twice from the same pinned commit.
   - Generic build: KleidiAI disabled.
   - Optimized build: `-DGGML_CPU_KLEIDIAI=ON`.
   - Verify the optimized runtime log contains `CPU_KLEIDIAI` or the pinned version’s equivalent.
   - Record CPU features and backend logs.

2. **CPU-only proof**
   - Disable Metal/CUDA/HIP/Vulkan/OpenCL accelerator use where applicable.
   - Pass `--device none` and/or `--n-gpu-layers 0` as supported by the pinned version.
   - Never install or depend on a GPU runtime.
   - Emit machine-readable proof that no GPU backend was selected.

3. **Fair ablation**
   - Same model file, prompt set, sampling parameters, process lifecycle, and target machine when comparing generic versus KleidiAI.
   - Isolate contributions from quantization, KleidiAI, runtime tuning, and model cascading.
   - Preserve every raw record.

4. **Quality-preserving optimization**
   - Define an objective quality score over held-out incident cases.
   - Require 100% safety compliance.
   - Default feasibility gate: optimized quality is no more than one absolute point below the strong-model baseline.
   - Never optimize speed alone.

5. **One-command developer experience**
   - `make optimize` or an equivalent single command must perform the full benchmark/autotune/report pipeline after dependencies and models are present.
   - `make demo` must launch the selected endpoint and local report/demo UI.
   - `make verify` must run unit tests plus artifact-integrity checks.

6. **Reproducibility**
   - Pin the `llama.cpp` commit after the initial compatibility check.
   - Record model repository, filename, revision, SHA-256, size, and license.
   - Record OS, kernel, compiler, CMake, Python, CPU topology, flags, and command lines.
   - Commit raw results small enough for Git.

## Non-negotiable honesty rules

- Do not fabricate, estimate, smooth, or manually improve benchmark results.
- Do not claim a speedup without a paired baseline from the same target.
- Do not claim “first,” “fastest,” “best,” or “production ready” without evidence.
- If a metric cannot be measured reliably, omit it and explain why.
- If a candidate fails the quality gate, retain it in raw results but do not label it optimized.
- Every headline claim must be traceable to a JSON/CSV row and the report generator.

## Autonomy policy

- Read all files under `docs/` before coding.
- Maintain `BUILD_STATUS.md` with completed checklist items, current blockers, commands run, and artifact paths.
- Use small commits after each completed checklist item.
- Prefer standard-library or mature dependencies over novel plumbing.
- Run tests after every material change.
- When an optional integration fails, use its specified fallback and continue.
- Do not pause for aesthetic approval. Use a clean, restrained, evidence-first design.
- Do not expose credentials, hostnames, public IPs, usernames, or tokens in committed artifacts.

## Priority order

1. Eligibility-compliant public repository structure and license.
2. Working generic and KleidiAI CPU builds.
3. Objective baseline and raw evidence.
4. Autotuner and quality gate.
5. Reproducible report and API demo.
6. README/Devpost text.
7. Screenshots and video.
8. Optional RK3588 and Performix extras.

## Required repository commands

Implement these stable entry points even if internal commands differ:

```bash
make doctor       # environment and Arm feature report
make bootstrap    # dependencies, pinned llama.cpp builds, model download
make smoke        # fastest end-to-end sanity check
make benchmark    # baseline + ablation + held-out evaluation
make optimize     # search + select profile + render report
make report       # regenerate offline evidence from raw artifacts
make serve        # launch selected OpenAI-compatible endpoint
make demo         # serve endpoint plus local evidence dashboard
make verify       # tests, schema checks, provenance checks
make submission   # render final English Devpost materials from measured data
```

## Exit conditions

The project is complete only when:

- all mandatory checklist items in `docs/04-build-checklist.md` pass;
- `make smoke`, `make optimize`, `make verify`, and `make submission` pass on the Arm64 target;
- the generated report contains a fair baseline, at least four ablation stages, confidence intervals or repeated-run dispersion, quality and safety metrics, memory, and CPU-only proof;
- the repository has root-level `LICENSE` (Apache-2.0 preferred), `README.md`, `THIRD_PARTY_NOTICES.md`, and setup instructions;
- the Devpost draft contains no unresolved numeric placeholders;
- all public claims are generated from actual artifacts.
