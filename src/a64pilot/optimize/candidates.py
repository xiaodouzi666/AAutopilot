"""Host-derived optimization candidate generation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from a64pilot.benchmark.plan import BenchmarkCandidate, service_candidates, thread_candidates
from a64pilot.benchmark.probes import PerformanceProbeEvidence


def generate_candidates(
    *,
    allowed_cores: int,
    physical_cores: int | None,
    quick: bool,
) -> list[BenchmarkCandidate]:
    threads = thread_candidates(allowed_cores, physical_cores)
    bases = [
        BenchmarkCandidate(
            candidate_id=f"kleidiai-q4-0-t{thread}",
            stage="tuned",
            backend="kleidiai",
            model_role="strong",
            quantization="Q4_0",
            threads=thread,
            batch=128,
            ubatch=64,
            parallel=1,
            context=2048,
        )
        for thread in threads
    ]
    result: list[BenchmarkCandidate] = []
    for base in bases:
        service_matrix = service_candidates(
            base,
            batches=(128, 256) if quick else (128, 256, 512),
            ubatches=(64, 128) if quick else (64, 128, 256),
            # A3 quality calls are sequential, so only p1 is eligible for
            # selection. True p1/p2 concurrency is measured independently by
            # the supporting performance-probe matrix.
            parallels=(1,),
            limit=8 if quick else 24,
        )
        result.extend(service_matrix)
    return result


def rank_micro_threads(evidence: PerformanceProbeEvidence) -> list[dict[str, float | int]]:
    """Rank KleidiAI Q4 thread cells using frozen generation then prompt throughput."""

    ranked: list[dict[str, float | int]] = []
    for run in evidence.micro_runs:
        if run.backend != "kleidiai" or run.quantization != "Q4_0":
            continue
        metrics = {metric.test: metric.tokens_per_second for metric in run.metrics}
        if set(metrics) != {"pp128", "tg64"}:
            raise ValueError("KleidiAI Q4 micro cell lacks the frozen pp128/tg64 pair")
        ranked.append(
            {
                "threads": run.threads,
                "tg64_tokens_per_second": metrics["tg64"],
                "pp128_tokens_per_second": metrics["pp128"],
            }
        )
    if len(ranked) != len(evidence.micro_threads):
        raise ValueError("KleidiAI Q4 micro thread ranking is incomplete")
    ranked.sort(
        key=lambda row: (
            -float(row["tg64_tokens_per_second"]),
            -float(row["pp128_tokens_per_second"]),
            int(row["threads"]),
        )
    )
    if {int(row["threads"]) for row in ranked} != set(evidence.micro_threads):
        raise ValueError("KleidiAI Q4 micro ranking disagrees with the frozen thread matrix")
    return ranked


def staged_candidate_subset(
    candidates: Sequence[BenchmarkCandidate],
    *,
    micro_ranking: Sequence[dict[str, float | int]],
    limit: int,
    quick: bool,
) -> list[BenchmarkCandidate]:
    """Constrain service tuning to micro-ranked threads and cover planned parallel widths."""

    required_parallel = (1,)
    if limit < len(required_parallel):
        raise ValueError(
            f"candidate limit must be at least {len(required_parallel)} to cover parallel plan"
        )
    ranked_threads = [int(row["threads"]) for row in micro_ranking]
    if len(ranked_threads) != len(set(ranked_threads)) or not ranked_threads:
        raise ValueError("micro thread ranking must be non-empty and unique")
    by_thread = {
        threads: [candidate for candidate in candidates if candidate.threads == threads]
        for threads in ranked_threads
    }
    if any(not rows for rows in by_thread.values()):
        raise ValueError("micro-ranked thread cell has no generated service candidates")

    ordered: list[BenchmarkCandidate] = []
    # The best micro thread is serviced first, but every planned parallel width
    # appears before deeper batch/ubatch variants consume the bounded budget.
    for threads in ranked_threads:
        for parallel in required_parallel:
            match = next(
                (candidate for candidate in by_thread[threads] if candidate.parallel == parallel),
                None,
            )
            if match is None:
                raise ValueError(f"generated service matrix lacks parallel={parallel}")
            ordered.append(match)
    for threads in ranked_threads:
        for candidate in by_thread[threads]:
            if candidate not in ordered:
                ordered.append(candidate)
    selected = ordered[: min(limit, len(ordered))]
    if {candidate.parallel for candidate in selected} != set(required_parallel):
        raise ValueError("bounded service subset does not cover the frozen parallel plan")
    return selected


def bounded_candidate_subset(
    candidates: Sequence[BenchmarkCandidate],
    limit: int,
) -> list[BenchmarkCandidate]:
    """Select a deterministic, thread-diverse subset from a generated matrix.

    ``generate_candidates`` groups service settings by thread count. Taking a
    simple prefix would therefore benchmark only the first (usually one-thread)
    group. Round-robin selection preserves the generator order within each
    thread group while covering the host-derived thread choices first.
    """

    if limit < 1:
        raise ValueError("candidate limit must be positive")
    groups: dict[int, list[BenchmarkCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.threads].append(candidate)
    selected: list[BenchmarkCandidate] = []
    depth = 0
    while len(selected) < min(limit, len(candidates)):
        added = False
        for threads in sorted(groups):
            group = groups[threads]
            if depth < len(group):
                selected.append(group[depth])
                added = True
                if len(selected) == min(limit, len(candidates)):
                    break
        if not added:
            break
        depth += 1
    return selected


__all__ = [
    "bounded_candidate_subset",
    "generate_candidates",
    "rank_micro_threads",
    "staged_candidate_subset",
]
