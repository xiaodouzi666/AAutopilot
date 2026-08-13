# Submission Checklist

Generated 2026-08-13T22:56:55.924162+00:00 for **AArch64 Autopilot: Self-Optimizing Agentic AI on Arm CPUs**.

- [ ] Same-machine Arm64 Linux evidence exists and produced validated claims.
- [ ] Devpost project thumbnail uploaded.
- [ ] Public repository opens when signed out: https://github.com/xiaodouzi666/AAutopilot.
- [ ] GitHub recognizes the root Apache-2.0 license.
- [ ] Generic-Q4_0 and KleidiAI-Q4_0 use the same model checksum and settings.
- [ ] KleidiAI logs contain the Q4 kernel marker and, for the exact pinned strong Q4_0 model,
  only the reviewed Q6_K `output.weight` fallback warning; any other fallback is rejected.
- [ ] CPU-only flags, GPU backend disablement, and KleidiAI runtime marker are visible.
- [ ] Calibration and held-out results are separated.
- [ ] Claim intervals, sample counts, memory, quality, safety, and source run IDs are visible.
- [ ] `make verify` and `make submission` pass from the final commit.
- [ ] Secret scan and public-artifact redaction pass.
- [ ] All Devpost materials are English.
- [ ] Required custom questions use the reviewed answers in the write-up.
- [ ] Public demo video is under three minutes and uses real values only.
- [ ] Final Devpost submit action explicitly confirmed and live receipt verified before 15 August 2026 01:00 CEST.

Evidence status: **measurement pending — not ready for final submit**.