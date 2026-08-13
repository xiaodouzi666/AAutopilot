# AArch64 Autopilot Evidence Report

**Evidence status:** Arm64 measurement pending; fixture output is excluded from claims.

## Headline claims

No performance claim is available. Complete the fair same-machine Arm64 Linux generic-Q4_0/KleidiAI-Q4_0 benchmark first.

## Candidate summary

| Candidate | Stage | Backend | n | p50 ms | p95 ms | Quality | Safety | Peak RSS MB |
|---|---|---|---:|---:|---:|---:|---:|---:|
| _none_ | measurement pending | — | 0 | — | — | — | — | — |

## Evidence contract

The fair Arm-specific comparison holds the target, source commit, official Q4_0 model checksum, prompt set, sampling, affinity, threads, batch, micro-batch, concurrency, and lifecycle constant. The intended variable is only the generic versus KleidiAI CPU backend. The exact pinned strong-model inventory contains 197 Q4_0 tensors and one disclosed Q6_K `output.weight`; that single fallback is allowed only when SHA-256, size, and the full GGUF header inventory match. CPU-only proof and the primary KleidiAI Q4 marker are required, and any additional or different fallback is rejected.

The submitted API profile is strong-only. Its public boundary applies constrained triage JSON and
fails closed when schema, read-only tool policy, safety, or consistency validation fails. A4
weak/strong routing remains a future experiment unless calibration and the complete held-out gate
approve a measured multi-runtime profile.

## Limitations

- Results apply only to the recorded target, model files, runtime commit, and workload.
- The synthetic incident suite is not a general LLM capability benchmark.
- No energy or cloud-cost claim is made without a credible counter or supplied price.
- Fixture responses are excluded from every performance claim.

Generated at 2026-08-13T23:00:57.375499+00:00. See `claims.json`, `benchmark-results.json`, and `raw/` for provenance.