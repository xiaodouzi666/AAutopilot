# Novelty and Positioning

## Honest novelty statement

The project must not claim that quantization, KleidiAI, `llama.cpp`, model routing, or hardware-aware optimization are individually new. They are established techniques and tools.

The submission’s differentiator is their evidence-driven integration for **Arm CPU agent deployment**:

1. a fair generic-versus-KleidiAI build ablation from one pinned runtime commit;
2. device-specific search over model and serving parameters;
3. an objective held-out agent-quality and safety gate;
4. transparent weak/strong routing with automatic escalation;
5. Pareto selection across latency, throughput, memory, and quality;
6. automatic generation of a deployable profile and claim-level provenance.

Use language such as:

> “AArch64 Autopilot combines existing Arm-optimized kernels and open-source runtimes with a new, workload-aware optimization, quality-gating, and evidence-generation workflow.”

Do not use:

> “the world’s first,” “the fastest Arm LLM,” or “guaranteed production quality.”

## Relationship to nearby projects

### `llama.cpp`

Provides the native inference runtime, OpenAI-compatible server, and benchmark tools. AArch64 Autopilot does not replace it; it builds and compares variants, orchestrates device-specific search, applies workload quality constraints, and renders deployment evidence.

### KleidiAI

Provides optimized Arm AI microkernels and runtime feature selection. AArch64 Autopilot verifies actual use, measures the isolated contribution, and combines it with model/runtime/routing decisions.

### Microsoft Olive

Olive performs hardware-targeted ONNX model optimization under accuracy/latency constraints. AArch64 Autopilot is narrower and different: it targets GGUF/`llama.cpp` agent serving on Arm CPUs, includes a generic-versus-KleidiAI ablation, service parameters, model cascade calibration, and Devpost-ready provenance.

### RouteLLM

RouteLLM studies routing between strong and weak models to reduce cost while preserving benchmark quality. AArch64 Autopilot uses a transparent local-device routing rule calibrated on the submitted workload, routes between small local GGUF models, validates structured tool output, and incorporates routing into Arm CPU/memory/latency optimization.

### Arm Performix

Performix profiles Arm workloads and can expose structured hotspot data through MCP. AArch64 Autopilot uses it optionally as evidence and an engineering loop, while the core product owns bounded configuration search, quality validation, deployment profile selection, and report generation.

## Competitive framing

### What a weaker submission may show

- a chatbot running on an Arm machine;
- one before/after tokens-per-second screenshot;
- an unexplained quantized model;
- no quality measurement;
- setup instructions tied to one host;
- claims that cannot be reproduced.

### What AArch64 Autopilot should show

- an actual Arm-specific technical variable isolated fairly;
- multiple optimization layers with ablations;
- task quality and safety as hard constraints;
- raw evidence and statistics;
- a reusable CLI, profile, endpoint, dataset, templates, and report;
- a visually immediate “machine optimized itself” demonstration.

## Judge-facing one-sentence differentiator

> “Instead of hand-picking a fast-looking Arm configuration, AArch64 Autopilot discovers one under an objective quality gate and produces the exact evidence needed to reproduce it.”
