# Reproducible demo video

The repository renders a static-slide demo directly from the generated evidence. It uses only
repository-owned visuals, no stock media, and no music. On macOS the narration is generated with
the built-in `say` voice; the Linux evidence workflow uses the locally installed eSpeak NG English
voice. Final mode refuses to render without one of those offline narrators. Only a watermarked,
non-publishable draft may fall back to silence, so no network TTS or untracked copyrighted audio
enters the asset.

## Final render

Run this only after the same-machine Arm64 evidence, report, and final claims have been generated:

```bash
python scripts/render-demo-video.py
```

Final mode reads and cross-checks:

- `artifacts/claims.json` against the claims embedded in `artifacts/report-data.json`;
- `artifacts/figures/ablation.png` and `artifacts/figures/pareto.png`;
- `assets/submission/aarch64-autopilot-thumbnail.png`.
- `artifacts/submission/arm-target-demo-receipt.json`, emitted by the official Arm64 evidence
  workflow from one validated real-model response that is also a headline-claim source row.

The architecture slide also discloses the exact pinned strong-model tensor inventory: 197 Q4_0
tensors plus the single reviewed Q6_K `output.weight` fallback. The primary KleidiAI Q4 marker is
required and any additional fallback is rejected; the narration must not claim that every tensor
is Q4_0 or that no fallback exists.

It refuses to render if evidence is pending, claims are empty or malformed, report claims differ,
or a required figure is missing. Final mode also reuses the strict evidence-bundle verifier,
recomputes claims from raw records, verifies their complete split-v2 source coverage, and requires
the preregistered primary mean-TTFT claim to be positive with a confidence-interval lower bound
above zero. Secondary p95 E2E and throughput outcomes remain visible but cannot unlock final
publication. It never invents a metric. The output is strictly shorter than three minutes and its adjacent JSON
manifest records mode, duration, claim data, narration settings, and SHA-256 hashes for every
source and the video. Inspect that manifest before upload.

## Final screenshot set and aggregate quality receipt

The four Devpost screenshots are exact frames from the attested CI video, not reconstructed
graphics. Given a local copy of the `a64pilot-demo-final.mp4` release asset, regenerate the
quality summary, screenshots, timecodes, captions, and provenance manifest with:

```bash
uv run --frozen --no-editable python scripts/generate-submission-assets.py \
  --video /path/to/a64pilot-demo-final.mp4
```

The command refuses a video whose SHA-256 does not match the measured publishable manifest. It
also cross-checks all nine candidate quality records against the report, search plan, frozen
split, selected profile, and cascade status. A clean checkout can verify the committed downstream
artifacts without downloading the video:

```bash
uv run --frozen --no-editable python scripts/generate-submission-assets.py --verify-only
```

`artifacts/screenshots/manifest.json` records the source video/release/run, the four reviewed
timecodes and scene windows, each 1920×1080 frame hash, and the data-source hashes behind its
caption. `artifacts/quality-summary.json` records the A0–A3 candidate quality summary and selected
strong-only profile. When A4 is executed, `artifacts/quality-results.json` separately records its
frozen 40-case calibration, 20-case final-holdout gate, and route distribution without claiming
live-cascade performance.

The final video includes a dedicated “Functioning on the Arm target” scene. It shows the official
GitHub Arm runner run ID, Arm64 Linux/kernel provenance, real backend and case ID, validated model
diagnosis, and read-only tool calls. The receipt generator itself refuses non-Linux/non-Arm hosts,
fixture or calibration rows, rows outside headline claim provenance, schema failures, non-100%
safety, and unverified CPU-only runs. A static report chart alone does not satisfy this media gate.

The temporary `report-pending-screenshot.png` is deliberately not a final-video input: it is an
honest evidence-pending browser capture retained only for layout review. A final screenshot must
instead be captured from the validated report on the Arm run, while the reproducible video uses
the report figures directly.

## Draft preview

While measurement is pending, final mode must return exit code 2. A layout/audio preview is still
available:

```bash
python scripts/render-demo-video.py --draft
```

Every draft frame has a large `DRAFT — MEASUREMENT PENDING` watermark, the manifest says
`publishable: false`, and the evidence slide contains no performance figure. Do not upload or
submit this draft as benchmark evidence. Draft mode does not fabricate a target-device scene.

## Verification

```bash
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 \
  artifacts/submission/a64pilot-demo-final.mp4
python -m json.tool artifacts/submission/a64pilot-demo-final.manifest.json >/dev/null
```

The final Devpost/YouTube upload remains a manual external action after reviewing the video,
manifest, thumbnail, redactions, and submission checklist.
