# Build status

Last updated: 2026-08-14 CEST

## Goal

Submit a public, licensed Cloud AI entry that builds generic and KleidiAI CPU backends from one
pinned `llama.cpp` revision, tunes a quality-safe strong-model deployment, and publishes replayable
Arm64 Linux evidence without substituting fixture or macOS numbers.

## Implemented

- [x] Apache-2.0 repository, English README, third-party notices, locked Python environment, and
  stable CLI/Make targets.
- [x] Sixty original incident-triage cases, frozen 40/20 calibration/held-out split, strict schema,
  read-only tool policy, deterministic quality score, and 100% safety gate.
- [x] Arm hardware/topology doctor, CPU affinity, pinned same-source generic/KleidiAI builds, and
  explicit CPU-only cache/runtime verification.
- [x] Official Qwen GGUF registry with immutable revisions, SHA-256, byte size, and parsed tensor
  inventory verification. The primary strong Q4_0 file's single reviewed Q6_K `output.weight`
  fallback is disclosed and exact-inventory bound; any drift or additional fallback fails.
- [x] Real `llama-server` lifecycle, streaming timing, RSS collection, raw integrity receipts,
  bounded calibration-only tuning, frozen finalists, and full twenty-case held-out admission.
- [x] Fail-closed evidence/claim/report/submission gates with command, request, response, binary,
  model, source, host, split, and runtime-marker replay.
- [x] Validated strong-only OpenAI-compatible API, local report UI, metrics, fixture smoke mode, and
  strict measured-profile deployment loader.
- [x] Official Arm64 GitHub Actions pipeline for build, model download, benchmark, report, target
  demo receipt, final video, redaction, provenance attestation, and 90-day artifacts.
- [x] Devpost write-up template, compliance checklist, thumbnail, target-bound video renderer, and
  a clearly watermarked non-publishable local draft.
- [x] Local source suite: 183 tests plus Ruff, formatting, shell syntax, workflow YAML, wheel, data,
  fixture, secret/redaction, and fail-closed media checks.
- [x] Chrome click-through of every report navigation/skip link; desktop visual inspection; loaded
  figures; non-streaming/streaming fixture API; health/models/metrics; traversal rejection; and
  clean server shutdown.

## Publication status

- [ ] Make the GitHub repository public and push the first verified commit.
- [ ] Obtain a successful official `ubuntu-24.04-arm` workflow run and inspect/download its signed,
  sanitized evidence.
- [ ] Re-render the narrated final video from that exact evidence and upload it publicly.
- [ ] Create and review the Devpost draft, upload the thumbnail/video URL, then submit only after the
  required final explicit confirmation.

## Honest current limitation

The development host is Apple Silicon/macOS. It is valid for source, package, UI, and fixture
verification but not for the Cloud AI performance claim. Until the official Arm64 Linux workflow
produces a positive, confidence-bounded same-machine A1/A2 result, generated local reports remain
`measurement-pending` and the final video/submission commands intentionally return nonzero.

Optional Performix and RK3588 profiling remain non-blocking supporting evidence; their absence is
reported as unavailable rather than silently fabricated.
