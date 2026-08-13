"""Host-derived optimization candidate generation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from a64pilot.benchmark.plan import BenchmarkCandidate, service_candidates, thread_candidates


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
            # The benchmark currently issues requests serially, so parallel>1
            # would change context partitioning without measuring concurrency.
            parallels=(1,),
            limit=8 if quick else 24,
        )
        result.extend(service_matrix)
    return result


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
