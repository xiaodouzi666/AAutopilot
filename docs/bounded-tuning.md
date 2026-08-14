# Bounded device-specific tuning

The A3 stage is a real two-phase search derived from the target CPU topology. It does not
assume that half of the cores is optimal.

## Candidate generation

`a64pilot benchmark tune` first strictly loads `artifacts/performance-probes.json`. The complete
KleidiAI-Q4_0 micro cells are ranked by `tg64` tokens/s, then `pp128` tokens/s and thread count;
only those measured thread counts advance. The service generator combines them with bounded batch
and micro-batch choices. Parallelism stays at one because the formal quality harness issues
requests serially. True p1/p2 concurrency is covered by the independent probe rounds, so the
tuner never selects a multi-slot deployment from single-request latency.

The deadline-safe defaults are:

- at most 8 calibration candidates for the standalone command (6 in `benchmark all --quick`);
- 4 calibration cases per candidate;
- at most 2 admitted finalists;
- a 45-minute admission budget;
- one process at a time, always using the official strong Q4_0 model and verified KleidiAI
  CPU-only runtime.

The complete generated space, micro ranking, scheduled subset, topology, budget, raw source run
IDs, metrics, finalists, and held-out receipts are written to `artifacts/search-plan.json`. A
redaction-stable semantic digest binds the plan to the probe measurements while private and public
bundles each verify their own raw-log digests. Startup, inference, incomplete matrix, or timeout
errors fail the tuning command closed; a complete plan cannot hide an unproved candidate failure.

Resume accepts only a complete candidate receipt whose cited IDs exactly replay to the frozen
case/repetition matrix and candidate settings. A completed plan is strictly replayed and returned
without making another request. Partial or unreceipted raw rows stop the command rather than
repeating uncertain inference. Elapsed execution is accumulated across resume, and one absolute
monotonic deadline is passed into server startup and every individual request.

## No held-out tuning

Candidate ranking reads only the calibration split. A candidate must have the expected row
count, verified CPU-only/KleidiAI execution, zero unhandled schema failures, full safety, and
quality within the configured drop from the best complete calibration candidate. Feasible
candidates are ranked deterministically by p95 latency, throughput, RSS, quality, and ID.

Only after ordered `scheduled_finalists` are frozen does the tuner run them over all 20 held-out
cases. A candidate is appended to `admitted_finalists` only after every case and requested
repetition has a complete raw receipt. The same configured quality gate is then evaluated against
the complete A1 generic baseline. If no finalist passes, profile selection falls back specifically
to measured A2. Held-out metrics can reject a scheduled finalist but never reorder A2/A3 or
discover a new winner. A timeout interrupts the candidate, leaves it unadmitted, and fails the
command; it never produces a truncated formal result.

Strict verification regenerates the candidate set from target topology and the semantic probe
receipt, recalculates every calibration summary and rank from raw responses, recalculates each
held-out result and quality gate, and derives selected A3 as the first scheduled passing receipt.
The optimized profile therefore cannot be changed by editing matching summary fields in both the
profile and search plan.

## Commands

Run the fair A1/A2 measurement first, then tune independently:

```bash
a64pilot benchmark fair --repetitions 1
a64pilot benchmark tune \
  --max-candidates 8 \
  --calibration-cases 4 \
  --finalists 2 \
  --max-minutes 45
```

The normal CI path uses the integrated workflow:

```bash
a64pilot benchmark all --quick --repetitions 1
```

A1/A2 always cover all 20 held-out cases and remain the only source of the formal Arm-backend
headline claims. Calibration and A3 results cannot alter or fill gaps in that fair pair.
