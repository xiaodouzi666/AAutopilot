"""Pareto frontier and normalized ideal-distance selection."""

from __future__ import annotations

import math
from collections.abc import Sequence

from a64pilot.schemas import CandidateResult

MINIMIZE = ("p95_latency_ms", "peak_rss_mb")
MAXIMIZE = ("requests_per_second", "quality_score")


def dominates(left: CandidateResult, right: CandidateResult) -> bool:
    no_worse = all(getattr(left, field) <= getattr(right, field) for field in MINIMIZE)
    no_worse &= all(getattr(left, field) >= getattr(right, field) for field in MAXIMIZE)
    strictly_better = any(getattr(left, field) < getattr(right, field) for field in MINIMIZE)
    strictly_better |= any(getattr(left, field) > getattr(right, field) for field in MAXIMIZE)
    return no_worse and strictly_better


def frontier(candidates: Sequence[CandidateResult]) -> list[CandidateResult]:
    return [
        candidate
        for candidate in candidates
        if not any(dominates(other, candidate) for other in candidates if other is not candidate)
    ]


def _normalize(value: float, values: list[float], *, maximize: bool) -> float:
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return 0.0
    scaled = (value - low) / (high - low)
    return 1.0 - scaled if maximize else scaled


def select_knee(candidates: Sequence[CandidateResult]) -> CandidateResult:
    if not candidates:
        raise ValueError("cannot select from an empty candidate set")
    nondominated = frontier(candidates)
    metric_values = {
        field: [float(getattr(candidate, field)) for candidate in nondominated]
        for field in (*MINIMIZE, *MAXIMIZE)
    }

    def distance(candidate: CandidateResult) -> tuple[float, str]:
        squared = 0.0
        for field in MINIMIZE:
            squared += (
                _normalize(float(getattr(candidate, field)), metric_values[field], maximize=False)
                ** 2
            )
        for field in MAXIMIZE:
            squared += (
                _normalize(float(getattr(candidate, field)), metric_values[field], maximize=True)
                ** 2
            )
        return math.sqrt(squared), candidate.candidate_id

    return min(nondominated, key=distance)
