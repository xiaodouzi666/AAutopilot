# Competition Brief — Arm Create: AI Optimization Challenge 2026

_Last verified: 2026-08-13._

## Official position

The challenge asks participants to create, migrate, or optimize an AI solution on Arm architecture. Merely showing that an application runs on Arm is insufficient; the submission should make the optimization work and resulting improvement visible.

### Categories

- **Physical AI:** robotics, embedded devices, sensors, autonomy, simulation, and real-world actuation.
- **Cloud AI:** Arm64 cloud or on-prem server inference, frameworks, agents, throughput, latency, and production workflows.
- **Mobile AI:** local AI on Arm-powered phones, tablets, and laptops under privacy, latency, battery, and memory constraints.

### Selected category

**Cloud AI.**

The official track details explicitly include:

- inference on AWS Graviton, Microsoft Cobalt, Google Axion, or Ampere-based Arm servers;
- quantized or pruned AI on standard CPU-only instances;
- CPU-optimized runtimes including `llama.cpp`;
- agentic workloads combining multiple models, MCP servers, and integrations.

This makes AArch64 Autopilot a direct rather than interpretive fit.

## Judging weights

| Criterion | Weight | What this project must show |
|---|---:|---|
| Technological Implementation | 40 | Fair Arm-specific ablation, quality-gated autotuning, sound code, reproducible CPU-only execution |
| User / Developer Experience | 15 | One-command workflow, clear CLI, stable API, generated report, complete documentation |
| Potential Impact | 20 | Reusable optimizer, benchmark suite, deployment profile, templates, raw data, adaptation guide |
| WOW Factor | 25 | A live system that discovers its own best configuration and proves the result visually in minutes |

Tie-breaking begins with the first criterion, so technical implementation must not be sacrificed for UI polish.

## Prize structure

- Overall winner: USD 3,000 and Arm Community Blog feature.
- Overall runner-up: USD 2,000 and blog feature.
- Best Physical AI: USD 1,000 and blog feature.
- Best Cloud AI: USD 1,000 and blog feature.
- Best Mobile AI: USD 1,000 and blog feature.

## Deadline

- Submission closes: **2026-08-14 16:00 PDT**.
- Equivalent in Amsterdam: **2026-08-15 01:00 CEST**.
- Judging: 2026-08-17 through 2026-09-04.
- Winners announced on or around 2026-09-15.

Once the submission period ends, submitted judging materials cannot normally be substantively changed.

## Mandatory submission requirements

The final Devpost entry must include:

- a clearly selected category;
- a public code repository;
- all necessary source, assets, and build/run/validation instructions;
- a visible root-level MIT or Apache 2.0 license;
- a project overview and explanation of why the project is interesting;
- a functionality/output description;
- step-by-step instructions for an Arm-powered target;
- a clear account of what was optimized and how it was measured;
- confirmation that the work was created or meaningfully updated during the challenge period;
- English materials, or an English translation of every submitted element.

A public demo video is optional but strongly recommended. It must be under three minutes, show the project functioning on its intended device, and avoid unauthorized music/trademarks.

## Rules that affect engineering

- The project must install and run consistently as described.
- Judges may evaluate only the write-up, images, and video; the evidence must be understandable without running the code.
- The working project must remain available free of charge for judging.
- Existing projects must have been significantly updated during the challenge period, with the new work explained.
- Third-party code, models, APIs, and data must be used under valid licenses.
- The submission must be original work and must not include secrets, malware, or rights-infringing material.

## Official resources to preserve in the repository

- Challenge overview: `https://arm-ai-optimization-challenge.devpost.com/`
- Track details: `https://arm-ai-optimization-challenge.devpost.com/details/trackdetails`
- Rules: `https://arm-ai-optimization-challenge.devpost.com/rules`
- Arm Developer Program: `https://developer.arm.com/`
- Arm Learning Paths: `https://learn.arm.com/`
- Arm Developer Ecosystem GitHub: `https://github.com/ArmDeveloperEcosystem`
- `llama.cpp` build documentation: `https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md`
- KleidiAI mirror: `https://github.com/ARM-software/kleidiai`
- Arm Performix: `https://developer.arm.com/servers-and-cloud-computing/arm-performix`

The official website and rules prevail over this helper document if anything changes.
