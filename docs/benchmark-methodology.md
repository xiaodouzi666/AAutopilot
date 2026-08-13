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

Forty cases are used for routing calibration and twenty are held out. Each case scores schema,
diagnosis/severity, tool selection, and safety. A feasible profile must preserve the configured
quality floor, retain 100% safety, and have zero unhandled schema failures. A failing cascade
is rejected in favor of a measured strong-only profile.

## Claim policy

Every headline claim contains the baseline and optimized candidate IDs, exact formula,
confidence interval, and raw run IDs. Fixture rows and cross-machine pairs are rejected. An
interval crossing zero is reported as no demonstrated improvement.
The pinned `llama.cpp` KleidiAI implementation provides quantized kernels for Q4_0 and Q8_0,
so the primary strong-model ablation uses the official Qwen2.5 1.5B Q4_0 file. Its exact
pinned inventory contains 197 Q4_0 tensors plus one disclosed Q6_K `output.weight` fallback.
That warning is accepted only when the file SHA-256, size, and reparsed full GGUF header
inventory match the registry; every additional or different fallback invalidates the row.

## Bounded tuning

A3 uses a two-phase topology-derived search rather than a fixed half-core guess. Candidate
generation, calibration-only ranking, held-out finalist admission, budgets, and fail-closed
quality gates are specified in [bounded device-specific tuning](bounded-tuning.md).
