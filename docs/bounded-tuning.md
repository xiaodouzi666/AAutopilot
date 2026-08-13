# Bounded device-specific tuning

The A3 stage is a real two-phase search derived from the target CPU topology. It does not
assume that half of the cores is optimal.

## Candidate generation

`a64pilot benchmark tune` calls `a64pilot.optimize.candidates.generate_candidates` with the
target's allowed and physical core counts. The generator combines a small thread set
(`1`, quarter, half, and the physical/allowed ceiling) with bounded batch and micro-batch
choices. Parallelism stays at one because the harness currently issues requests serially; it
does not label an unmeasured server-slot setting as concurrency tuning. A deterministic round-robin subset covers all generated thread counts
before taking a second service configuration from any thread group.

The deadline-safe defaults are:

- at most 8 calibration candidates for the standalone command (6 in `benchmark all --quick`);
- 4 calibration cases per candidate;
- at most 2 admitted finalists;
- a 45-minute admission budget;
- one process at a time, always using the official strong Q4_0 model and verified KleidiAI
  CPU-only runtime.

The complete generated space, admitted subset, topology, budget, raw source run IDs, metrics,
rejection reasons, finalists, and held-out receipts are written to `artifacts/search-plan.json`.
An individual startup or execution failure is recorded as a rejected candidate and the bounded
search continues; it cannot erase the rest of the candidate matrix.

## No held-out tuning

Candidate ranking reads only the calibration split. A candidate must have the expected row
count, verified CPU-only/KleidiAI execution, zero unhandled schema failures, full safety, and
quality within the configured drop from the best complete calibration candidate. Feasible
candidates are ranked deterministically by p95 latency, throughput, RSS, quality, and ID.

Only after finalist IDs are frozen does the tuner run each admitted A3 candidate over all 20
held-out cases. The same configured quality gate is then evaluated against the complete A1
generic baseline. If no finalist passes, the search records a fail-closed status and profile
selection falls back specifically to the measured A2 strong-model candidate. Held-out metrics
can reject a calibration-frozen finalist but never reorder A2/A3 or discover a new winner. A formal candidate is
never stopped halfway merely because the wall-time admission budget expired.

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
