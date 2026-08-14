# Submission Screenshot Captions

These are exact 1920×1080 frames from the attested CI demo video. Values are copied from the
measured claim and profile artifacts identified in `manifest.json`; no value was typed into an
image after capture.

## 1. Headline evidence

**Caption:** On 20 paired split-v2 final-holdout cases, KleidiAI reduced Q4_0 mean time to first
token by **1.79%** versus the same-model generic backend. The paired 95%
interval is **0.65% to 2.96%**, so the preregistered
primary gate passes.

## 2. Optimization pipeline

**Caption:** One pinned `llama.cpp` revision and one Qwen2.5 1.5B Q4_0 model feed same-machine
generic and verified KleidiAI builds, a bounded search, a held-out quality/safety gate, and an
evidence-producing endpoint. Split-v2 decisions were frozen before final evaluation.

## 3. Fair Pareto evidence

**Caption:** The Pareto view keeps the fair A1/A2 comparison visible while disclosing the secondary
p95 end-to-end latency result: **2.77%** reduction, paired 95% interval
**-18.45% to 49.45%**. Because that interval crosses zero, it is
reported transparently and does not unlock publication.

## 4. Validated API and deployed-profile status

**Caption:** The submitted API serves the measured **strong-only** profile
`kleidiai-q4-0-t4-b128-u64-p1-c2048` and enforces the benchmark's strict triage schema, read-only tool
policy, and fail-closed HTTP 502 behavior. A4 routing is **not-run**; the shipping
fallback is `best measured strong-only profile`, so no unmeasured cascade claim is made.
