# Benchmark methodology

The authoritative protocol is [03-benchmark-protocol.md](03-benchmark-protocol.md). This
page is the judge-oriented summary.

## Fairness

The A1/A2 comparison holds the Arm64 Linux host, `llama.cpp` commit, compiler, release flags,
Q4_0 file and checksum, prompt/case, deterministic sampling, affinity, threads, batch,
micro-batch, context, parallel slots, warmup, repetitions, and lifecycle constant. The
intended variable is `GGML_CPU_KLEIDIAI`.

## Measurement

Timing uses monotonic nanosecond clocks. Streaming measurements distinguish request start,
first content token, and response end. The store preserves command lines, errors, peak
process-tree RSS, route, quality, and safety. Summaries report p50, p95, mean, standard
deviation, coefficient of variation, and fixed-seed paired bootstrap intervals.

## Quality

Forty cases are used for routing calibration and twenty are held out. Split v2 was frozen
before the next final run from cases that were not executed in the failed run6. That failed
run exposed the old test 20 plus old calibration cases `001/002/004/005`; those 24 are now
calibration/error-analysis evidence and are not called unseen. The v2 test set is disjoint
from all 24 and is the only set called the unseen final holdout, narrowly meaning unseen in
prior benchmark execution rather than secret in this public repository.

The v2 selection is deterministic and stratified. From the remaining 36 old calibration
cases, rank within category by ascending
`sha256("a64pilot-final-holdout-v2|20260813|<case_id>")`, take quotas 6 simple, 7 multi,
3 noisy, and 4 ambiguous, then order the selected union by `(digest, case_id)`. Category is
used only for stratification and is not part of the digest. The 20 old-calibration cases
not selected are followed by the old test 20 to form the new calibration 40. Automated tests
assert the exact selection, zero observed overlap, all 60 unique IDs, and category quotas.
The immutable `demo/split-freeze-v2.json` stores both split hashes, all 36 eligible case
IDs/categories/digests, and the final selection. No expected answer, tool/safety label, model
output, score, latency, or other run result is an input to selection.

Each case scores schema, diagnosis/severity, tool selection, and safety. A feasible profile
must preserve the configured quality floor, retain 100% safety, and have zero unhandled schema
failures. A failing cascade is rejected in favor of a measured strong-only profile. The
deterministic response cap is 512 output tokens for every compared candidate.

## Claim policy

The A1/A2 comparison prospectively preregisters mean TTFT reduction as the single primary
metric on unseen split v2. P95 E2E latency reduction and median per-request throughput
increase (using `1000 / E2E_ms` per row) are transparent secondary metrics. All three use the
same complete 20-case paired rows and paired 95% bootstrap intervals with 5,000 resamples and
seed `20260813`; their reducers are mean, p95, and median respectively.

Claims are emitted only when both candidates include every required case/repetition, all rows
are schema-valid, both safety scores are 100%, and A2 mean quality is within 1.0 absolute point
of A1. Once eligible, the report includes all three preregistered metrics—negative values and
intervals crossing zero included—rather than selecting a favorable result. Publication of a
demonstrated-improvement result is unlocked only when the primary mean-TTFT reduction is
positive and its 95% paired interval lower bound exceeds zero. Secondary p95 E2E and throughput
metrics are displayed but cannot unlock publication by themselves, even if positive. This
decision rule is frozen before the v2 run to avoid multiple-comparison cherry-picking.

Every headline claim contains the baseline and optimized candidate IDs, exact formula,
confidence interval, and raw run IDs. Fixture rows and cross-machine pairs are rejected.
The pinned `llama.cpp` KleidiAI implementation provides quantized kernels for Q4_0 and Q8_0,
so the primary strong-model ablation uses the official Qwen2.5 1.5B Q4_0 file. Its exact
pinned inventory contains 197 Q4_0 tensors plus one disclosed Q6_K `output.weight` fallback.
That warning is accepted only when the file SHA-256, size, and reparsed full GGUF header
inventory match the registry; every additional or different fallback invalidates the row.

## Bounded tuning

A3 uses a two-phase topology-derived search rather than a fixed half-core guess. Candidate
generation, calibration-only ranking, held-out finalist admission, budgets, and fail-closed
quality gates are specified in [bounded device-specific tuning](bounded-tuning.md).
