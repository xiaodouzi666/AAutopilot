# Submission Checklist

Generated 2026-08-14T07:49:35.048123+00:00 for **AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**.

Status legend: checked items have a named artifact or public URL below. An unchecked item is not
claimed complete.

## Local and Arm evidence gates

- [x] Same-machine Arm64 Linux evidence produced validated claims:
  [run 31778419786](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786),
  measured source SHA `6d8e21818fc0ef0202ec85236bcec6d20e908f23`.
- [x] Generic-Q4_0 and KleidiAI-Q4_0 use the same model checksum and
  settings; `artifacts/build-manifest.json`, `artifacts/model-manifest.json`, and formal rows are
  included in the attested release.
- [x] KleidiAI Q4 kernel proof, reviewed one-tensor Q6_K
  `output.weight` fallback, CPU-only flags, and disabled GPU devices passed the strict verifier.
- [x] Calibration and final holdout are separated; quality, safety,
  sample counts, confidence intervals, memory, and source run IDs are recorded in
  `artifacts/quality-summary.json`, `artifacts/report-data.json`, and `artifacts/claims.json`.
  A4 quality/routing evidence is stored separately in `artifacts/quality-results.json` when run.
- [x] Strict `make verify` and `make submission` completed inside the
  official Arm run. Every measured artifact names the exact runtime source revision; later source
  changes require a new target run.
- [x] A4 ran 40-case calibration plus a frozen 20-case quality replay. It failed closed to 20 strong routes, was not performance-claim eligible, and left shipping on A3 strong-only.
- [x] Secret scan and public-artifact redaction passed. The final Release and GitHub build-provenance attestation are publicly verified.
## Public submission surfaces

- [x] Repository opens publicly: https://github.com/xiaodouzi666/AAutopilot.
- [x] Root [Apache-2.0 license](https://github.com/xiaodouzi666/AAutopilot/blob/main/LICENSE) is visible and recognized.
- [x] Devpost thumbnail is uploaded and publicly readable. The 1536×1024 source is rendered by
  Devpost as this [333×222 preview](https://d112y698adiu2z.cloudfront.net/photos/production/software_thumbnail_photos/005/097/643/datas/medium.png).
- [x] All final Devpost materials and required custom answers are in English in
  `artifacts/devpost-writeup-final.md`.
- [x] Four evidence screenshots, their captions, timecodes, source hashes, and frame hashes are in
  `artifacts/screenshots/manifest.json`.
- [x] The public [demo video](https://youtu.be/2wZx67_iaSw) is under three minutes, contains no
  music, and uses only measured values. Its unchanged visual stream derives from the attested CI
  video; the natural narration derivative is disclosed in `artifacts/evidence-index.json`.
- [x] The [final evidence release](https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786) and
  [workflow run](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786) are linked from the published Devpost project.
- [x] Devpost submission is live and was read back with submitted timestamp `2026-08-14T02:04:58.469-04:00`.

## Reproducible final checks

```bash
uv run --frozen --no-editable python scripts/generate-submission-assets.py --verify-only
uv run --frozen --no-editable python scripts/check-final-placeholders.py
```

Evidence status: **measured**.
Formal Devpost status: **submitted and verified live**.

Measured source commit: `6d8e21818fc0ef0202ec85236bcec6d20e908f23`

Public repository: https://github.com/xiaodouzi666/AAutopilot

Devpost project: https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus

Public video: https://youtu.be/2wZx67_iaSw
