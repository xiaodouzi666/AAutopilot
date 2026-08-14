# Final Submission Checklist

This file is the reusable preflight. The evidence-backed state for the current entry, including
public URLs and the one still-open receipt item, is `artifacts/submission-checklist.md`.

## Eligibility and accounts

- [ ] Devpost account is active.
- [ ] Entrant has joined the hackathon.
- [ ] Arm Developer Program registration is complete if required by the entry flow.
- [ ] Entrant/team is eligible under official geography, age, occupation, and conflict rules.
- [ ] Team representative is identified if entering as a team.
- [ ] Submission category is explicitly **Cloud AI**.

## Deadline

- [ ] Final Devpost submission is completed before **2026-08-14 16:00 PDT / 2026-08-15 01:00 CEST**.
- [ ] A screenshot or confirmation page proves successful receipt.
- [ ] No assumption is made that materials can be edited after the deadline.

## Repository

- [ ] Repository is public and accessible in a logged-out browser.
- [ ] Root-level Apache-2.0 or MIT `LICENSE` is visible.
- [ ] GitHub About/license metadata recognizes the license where applicable.
- [ ] `README.md` explains category, purpose, optimization, results, setup, validation, and limitations.
- [ ] All project source, templates, synthetic data, and scripts are present.
- [ ] Model binaries are not committed; download instructions work.
- [ ] `THIRD_PARTY_NOTICES.md` lists runtime, model, and library licenses.
- [ ] Exact `llama.cpp` commit and model hashes are recorded.
- [ ] No secret, token, private key, username, home path, private/public host address, or paid credential exists in repository/history.
- [ ] Clean clone plus documented commands reproduces smoke setup.

## Optimization evidence

- [ ] Generic Q4_0 versus KleidiAI Q4_0 uses the same model checksum and settings.
- [ ] KleidiAI logs show the matching primary Q4 kernel. Only the exact pinned strong-Q4_0
      inventory's single reviewed Q6_K `output.weight` fallback is allowed; any inventory drift or
      additional unsupported/not-accelerated fallback fails.
- [ ] `CPU_KLEIDIAI` or pinned equivalent is captured.
- [ ] CPU-only execution is explicitly verified; GPU layers/devices are disabled.
- [ ] A0–A3 are complete; A4 is included only if it passes.
- [ ] Quality and safety gates are reported.
- [ ] Calibration and held-out results are separated.
- [ ] Sample counts, p50/p95, dispersion/confidence intervals, memory, and errors are visible.
- [ ] Every headline number has claim provenance and raw source rows.
- [ ] No cross-machine comparison is presented as a speedup.
- [ ] Limitations state that results are target/workload-specific.

## Devpost text

- [ ] Project overview explains purpose and why it should win.
- [ ] Functionality/output section names the profile, endpoint, benchmark suite, and report.
- [ ] Setup instructions are step-by-step for Arm64 Linux.
- [ ] Meaningful work during challenge period is described.
- [ ] All materials are in English.
- [ ] Public repository URL is correct.
- [ ] Testing instructions are free and do not require private credentials.
- [ ] Custom required questions have answers.
- [ ] The final placeholder-policy scanner passes; no unresolved token or invented result remains
  on a publishable surface.

## Images and video

- [ ] Main thumbnail communicates “self-optimizing Arm CPU AI” clearly.
- [ ] Screenshot 1: headline evidence card.
- [ ] Screenshot 2: architecture or optimization stages.
- [ ] Screenshot 3: fair ablation/Pareto chart.
- [ ] Screenshot 4: agent API demo and route metadata.
- [ ] Optional video is public, under three minutes, and shows the project on the intended Arm target.
- [ ] Video contains no unauthorized music or third-party marks.
- [ ] Captions/narration use real measured values and readable text.

## Final commands

Run from the final commit:

```bash
make verify
make submission
make smoke
make report

git status --short

uv run --frozen --no-editable python scripts/check-final-placeholders.py
uv run --frozen --no-editable python scripts/generate-submission-assets.py --verify-only
```

Record:

```text
Final commit: __________________________
Public repo: ___________________________
Devpost URL: ___________________________
Submission timestamp: _________________
Receipt/screenshot: ____________________
Video URL (optional): _________________
Main report: ___________________________
```
