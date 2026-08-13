from __future__ import annotations

from a64pilot.optimize.pareto import dominates, frontier, select_knee
from a64pilot.schemas import CandidateResult


def candidate(
    name: str, latency: float, rps: float, memory: float, quality: float
) -> CandidateResult:
    return CandidateResult(
        candidate_id=name,
        stage="tuned",
        backend="kleidiai",
        model="strong",
        quality_score=quality,
        safety_score=100,
        schema_failures=0,
        p95_latency_ms=latency,
        requests_per_second=rps,
        peak_rss_mb=memory,
        source_run_ids=[name],
    )


def test_dominated_candidate_is_removed() -> None:
    good = candidate("good", 10, 10, 100, 99)
    bad = candidate("bad", 20, 5, 200, 98)
    assert dominates(good, bad)
    assert frontier([good, bad]) == [good]


def test_knee_is_deterministic() -> None:
    choices = [
        candidate("latency", 5, 8, 180, 98),
        candidate("balanced", 8, 10, 130, 99),
        candidate("memory", 12, 7, 90, 100),
    ]
    assert select_knee(choices).candidate_id in {"latency", "balanced", "memory"}
    assert select_knee(choices).candidate_id == select_knee(list(reversed(choices))).candidate_id
