# Submission Checklist

Audited 2026-08-14T05:13:49Z for **AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**.

Status legend: checked items have a named artifact or public URL below. An unchecked item is not
claimed complete.

## Local and Arm evidence gates

- [x] Same-machine Arm64 Linux evidence produced validated claims:
  [run 31766912155](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31766912155),
  evidence SHA `20f6c0e1e925350d94d04ebb50ede0e0591136b4`.
- [x] Generic-Q4_0 and KleidiAI-Q4_0 use the same model checksum and settings;
  `artifacts/build-manifest.json`, `artifacts/model-manifest.json`, and formal rows are included in
  the attested release.
- [x] KleidiAI Q4 kernel proof, reviewed one-tensor Q6_K `output.weight` fallback, CPU-only flags,
  and disabled GPU devices passed the strict verifier.
- [x] Calibration and final holdout are separated; quality, safety, sample counts, confidence
  intervals, memory, and source run IDs are recorded in `artifacts/quality-summary.json`,
  `artifacts/report-data.json`, and `artifacts/claims.json`; A4 quality/routing evidence is kept in
  the separate `artifacts/quality-results.json` after its real Arm run.
- [x] Strict `make verify` and `make submission` completed inside the official Arm run. The later
  publication commit changes documentation/curated artifacts only, not the measured runtime
  revision.
- [x] Secret scan and public-artifact redaction passed; the release bundle is attested at
  [attestation 40655497](https://github.com/xiaodouzi666/AAutopilot/attestations/40655497).

## Public submission surfaces

- [x] Repository opens publicly: https://github.com/xiaodouzi666/AAutopilot.
- [x] Root [Apache-2.0 license](https://github.com/xiaodouzi666/AAutopilot/blob/main/LICENSE) is
  visible and recognized.
- [x] Devpost thumbnail is uploaded and publicly readable. The 1536×1024 source is rendered by
  Devpost as this [333×222 preview](https://d112y698adiu2z.cloudfront.net/photos/production/software_thumbnail_photos/005/097/643/datas/medium.png).
- [x] All final Devpost materials and required custom answers are in English in
  `artifacts/devpost-writeup-final.md`.
- [x] Four evidence screenshots, their captions, timecodes, source hashes, and frame hashes are in
  `artifacts/screenshots/manifest.json`.
- [x] The public [demo video](https://youtu.be/RT0ORZ3iIpE) is under three minutes, contains no
  music, and uses only measured values. Its unchanged visual stream derives from the attested CI
  video; the natural narration derivative is disclosed in `artifacts/evidence-index.json`.
- [x] The [evidence release](https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31766912155)
  and [workflow run](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31766912155) are linked
  from the Devpost draft.
- [ ] Formal Devpost submission receipt and submitted timestamp are not recorded yet.

## Reproducible final checks

```bash
uv run --frozen --no-editable python scripts/generate-submission-assets.py --verify-only
uv run --frozen --no-editable python scripts/check-final-placeholders.py
```

Evidence status: **measured**.

Formal Devpost status: **not submitted until a receipt and timestamp are recorded**.

Final publication commit: `ecd9bf226cc6824dc068b36d7a3f60e68c62775c`

Public repository: https://github.com/xiaodouzi666/AAutopilot

Devpost draft: https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus

Public video: https://youtu.be/RT0ORZ3iIpE
