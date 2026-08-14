# Three-Minute Demo Video Script

> **Historical planning script only.** The publishable split-v2 video is generated from
> `templates/video-script.md.j2` and `scripts/render-demo-video.py`; this v1 placeholder script
> must never be uploaded.
> This is one of two explicit legacy exceptions documented in
> [`placeholder-policy.md`](placeholder-policy.md); it is never a narration or upload source.

> Target length: 165–175 seconds. Hard maximum: 180 seconds. Use the real Arm64 terminal and generated report. No copyrighted music is needed.

## Preparation

Before recording:

- complete the final benchmark;
- render all `[[AUTO:...]]` values from claim artifacts;
- open a terminal on the target with sensitive details redacted;
- open the offline report at the headline section;
- prelaunch or cache model downloads so the video shows product behavior, not network waiting;
- have one simple and one complex incident request ready;
- show actual device/system information briefly.

## 0:00–0:15 — Hook

**Visual:** Title, Arm64 system card, then the final evidence card blurred until the reveal.

**Narration:**

> “Running an AI agent on Arm is easy. Proving that it is actually optimized—without sacrificing quality—is much harder. AArch64 Autopilot turns one Arm64 CPU into its own benchmark lab, optimizer, and deployable agent endpoint. No GPU and no model training.”

## 0:15–0:35 — The problem and architecture

**Visual:** One clean architecture diagram highlighting generic build, KleidiAI build, tuner, quality gate, and API.

**Narration:**

> “The tool builds a fair generic `llama.cpp` baseline and a KleidiAI-enabled variant from the same pinned commit. It fingerprints the CPU, searches a bounded set of model and serving configurations, then calibrates a small-to-large model cascade. Every candidate must pass a held-out quality floor and one hundred percent safety compliance.”

## 0:35–1:05 — One-command optimization

**Visual:** Terminal:

```bash
make doctor
make optimize
```

Show condensed live output or a speed-controlled cut:

- detected CPU features;
- generic/KleidiAI verification;
- candidate stages;
- quality gate;
- profile selection.

**Narration:**

> “On this `[[AUTO:cpu_model]]` target, the optimizer detected `[[AUTO:cpu_features_short]]`. It verified CPU-only execution and the KleidiAI runtime marker, then tested quantization, threads, batching, concurrency, and routing thresholds. The search is staged and resumable, so it avoids an uncontrolled Cartesian product.”

## 1:05–1:35 — Evidence reveal

**Visual:** Generated report headline and ablation chart.

**Narration:**

> “Here is the fair Arm-specific comparison: the same official Q4_0 model and runtime settings, changing only the backend. KleidiAI changed p95 latency by `[[AUTO:arm_p95_change]]` and throughput by `[[AUTO:arm_throughput_change]]`, with a `[[AUTO:arm_ci]]` confidence interval. Runtime tuning then contributed the next step. The full selected profile changed p95 latency by `[[AUTO:full_p95_change]]` while quality changed by only `[[AUTO:full_quality_change]]` and safety remained `[[AUTO:full_safety]]`.”

If the cascade failed, replace the last sentence with:

> “The proposed cascade was faster, but it failed the configured quality gate, so Autopilot rejected it and deployed the best strong-only KleidiAI profile. The failed candidate remains visible in the report.”

## 1:35–2:10 — Real agent API demo

**Visual:** Run a simple request, then a complex request with debug routing metadata.

```bash
python demo/demo-client.py --case simple-disk-pressure --debug
python demo/demo-client.py --case ambiguous-dependency-failure --debug
```

**Narration:**

> “The output is a normal OpenAI-compatible service. A simple incident can use the smaller model when the calibrated router and validator allow it. A complex or invalid response escalates automatically to the strong model. The agent only calls deterministic read-only fixture tools; it never executes model-generated shell commands.”

Show valid structured JSON and route: weak / strong / weak-then-strong.

## 2:10–2:38 — Reusability and auditability

**Visual:** Repository tree, `optimized-profile.yaml`, raw run folder, claim JSON, one reproduction command.

**Narration:**

> “The result is reusable beyond this demo. Replace the incident cases with another objectively scored agent workload, set quality, latency, and memory constraints, and run the same pipeline. Every chart links back to raw requests, exact commands, model and binary hashes, CPU features, and run IDs.”

## 2:38–2:55 — Why it matters

**Visual:** Four judging-value badges: Arm-specific, quality-preserving, one-command, open source.

**Narration:**

> “AArch64 Autopilot makes Arm migration measurable: not merely ‘it runs,’ but which configuration wins on this device, what tradeoff it makes, and whether the result is safe to deploy.”

## 2:55–3:00 — Close

**Visual:** Final evidence card and repository name.

**Narration:**

> “AArch64 Autopilot: self-optimizing agentic AI on Arm CPUs—measured, reproducible, and GPU-free.”

## Required shots checklist

- [ ] Real target architecture and CPU summary.
- [ ] `CPU_KLEIDIAI` verification line.
- [ ] Explicit CPU-only/no-GPU proof.
- [ ] Candidate search output.
- [ ] Final profile and quality gate.
- [ ] Fair backend ablation chart.
- [ ] Agent API request and structured response.
- [ ] Raw evidence/provenance path.
- [ ] Public repository name/URL.
- [ ] Total duration below 180 seconds.
