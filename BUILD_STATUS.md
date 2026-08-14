# Build status

Last updated: 2026-08-14 CEST

## Goal

Submit a public, licensed Cloud AI entry that builds generic and KleidiAI CPU backends from one
pinned `llama.cpp` revision, tunes a quality-safe strong-model deployment, and publishes replayable
Arm64 Linux evidence without substituting fixture or macOS numbers.

## Implemented

- [x] Apache-2.0 repository, English README, third-party notices, locked Python environment, and
  stable CLI/Make targets.
- [x] Sixty original incident-triage cases, strict schema, read-only tool policy, deterministic
  quality score, and 100% safety gate. The observed v1 test set is retired after failed-run error
  analysis; the audited v2 40/20 split was frozen before the successful final run from 36
  never-executed candidates using only category and case ID.
- [x] Arm hardware/topology doctor, CPU affinity, pinned same-source generic/KleidiAI builds, and
  explicit CPU-only cache/runtime verification.
- [x] Official Qwen GGUF registry with immutable revisions, SHA-256, byte size, and parsed tensor
  inventory verification. The primary strong Q4_0 file's single reviewed Q6_K `output.weight`
  fallback is disclosed and exact-inventory bound; any drift or additional fallback fails.
- [x] Real `llama-server` lifecycle, streaming timing, RSS collection, raw integrity receipts,
  bounded calibration-only tuning, frozen finalists, and full twenty-case held-out admission.
- [x] Fail-closed evidence/claim/report/submission gates with command, request, response, binary,
  model, source, host, split, and runtime-marker replay.
- [x] Prospectively registered primary mean-TTFT reduction plus transparent secondary p95 E2E and
  throughput outcomes; only a positive primary confidence interval can unlock final publication.
- [x] Validated strong-only OpenAI-compatible API, local report UI, metrics, fixture smoke mode, and
  strict measured-profile deployment loader.
- [x] Real A4 weak/strong component measurement, immutable 40-case calibration freeze, once-only
  20-case frozen quality replay, strict nested provenance replay, and fail-closed A3 shipping.
- [x] Official Arm64 GitHub Actions pipeline for build, model download, benchmark, report, target
  demo receipt, final video, redaction, provenance attestation, and 90-day artifacts.
- [x] Devpost write-up template, compliance checklist, thumbnail, target-bound video renderer, and
  a clearly watermarked non-publishable local draft.
- [x] Final source gate: 270 tests plus Ruff, formatting, shell syntax, workflow YAML, wheel, data,
  fixture, secret/redaction, and fail-closed media checks.
- [x] Chrome click-through of every report navigation/skip link; desktop visual inspection; loaded
  figures; non-streaming/streaming fixture API; health/models/metrics; traversal rejection; and
  clean server shutdown.

## Publication status

- [x] Make the GitHub repository public and push the verified implementation.
- [x] Preserve failed run6 as diagnostic evidence only, retire its observed v1 test set, and freeze
  the auditable split-v2 manifest before final measurement.
- [x] Complete and inspect official `ubuntu-24.04-arm`
  [run 31778419786](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786) at
  commit `6d8e21818fc0ef0202ec85236bcec6d20e908f23`.
- [x] Run A4 calibration/freeze/replay; record fail-closed strong-only routing and keep shipping on
  A3 because A4 was not quality-admitted or performance-claim eligible.
- [x] Publish the sanitized evidence in the
  [final release](https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786)
  and verify its [GitHub build-provenance attestation](https://github.com/xiaodouzi666/AAutopilot/attestations/40687167).
- [x] Publish the [natural-voice demo](https://youtu.be/2wZx67_iaSw) as an audio-only derivative
  whose visual stream is byte-identical to the CI-rendered source, with an explicit non-attested
  derivative manifest in the release.
- [x] Submit to Devpost and verify the live published project:
  https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus.

## Honest current limitation

The development host is Apple Silicon/macOS, so it remains valid for source, package, UI, and
fixture verification but not for the Cloud AI performance claim. The competition evidence comes
from successful public Arm64 Linux run 31778419786. Across 20 matched split-v2 cases, the primary
mean-TTFT reduction was **1.498%** with paired 95% CI **[0.514%, 2.600%]**, so the preregistered
publication gate passed. A1/A2 quality was **72.975/73.875**, both minimum safety scores were
**100/100**, and neither group had a schema failure. Secondary p95 end-to-end reduction
(**4.310%**, CI **[-15.486%, 48.746%]**) and median per-request throughput increase
(**2.023%**, CI **[-3.363%, 10.793%]**) were not demonstrated and are not promoted as wins.

A4's 40-case calibration found no admissible weak-model threshold. Its frozen 20-case quality
replay routed 20 strong / 0 weak / 0 weak-then-strong, was not quality-admitted, and is explicitly
not performance-claim eligible. Shipping remains A3 strong-only. This replay is not a new unseen
confirmatory set and provides no live-cascade latency, throughput, combined-RSS, or deployment
claim.

The YouTube demo replaces only the narration of the CI-rendered video. Its H.264 visual stream and
benchmark content are unchanged, but the derived YouTube MP4 is not itself covered by the GitHub
attestation. The original CI video remains inside the attested evidence bundle as the authoritative
source.

Optional Performix and RK3588 profiling remain non-blocking supporting evidence; their absence is
reported as unavailable rather than silently fabricated.
