"""Select a measured feasible profile, with an explicit strong-only fallback."""

from __future__ import annotations

from a64pilot.optimize.pareto import select_knee
from a64pilot.optimize.quality_gate import GateDecision, evaluate_gate
from a64pilot.schemas import CandidateResult
from a64pilot.settings import QualityGateConfig


def select_profile(
    candidates: list[CandidateResult],
    baseline: CandidateResult,
    gate: QualityGateConfig,
) -> tuple[CandidateResult, dict[str, GateDecision]]:
    decisions = {
        candidate.candidate_id: evaluate_gate(candidate, baseline, gate) for candidate in candidates
    }
    feasible = [candidate for candidate in candidates if decisions[candidate.candidate_id].passed]
    if feasible:
        return select_knee(feasible), decisions
    fallback = [
        candidate
        for candidate in candidates
        if candidate.measured
        and candidate.model == "strong"
        and candidate.safety_score >= gate.minimum_safety_score
        and candidate.schema_failures <= gate.maximum_schema_failures
    ]
    if not fallback:
        raise ValueError("no measured safe candidate exists; benchmark must be completed")
    return min(fallback, key=lambda item: (item.p95_latency_ms, item.candidate_id)), decisions
