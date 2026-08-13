"""Hard feasibility rules for candidate selection."""

from __future__ import annotations

from dataclasses import dataclass

from a64pilot.schemas import CandidateResult
from a64pilot.settings import QualityGateConfig


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    reasons: tuple[str, ...]


def evaluate_gate(
    candidate: CandidateResult,
    baseline: CandidateResult,
    config: QualityGateConfig,
) -> GateDecision:
    reasons: list[str] = []
    if not candidate.measured:
        reasons.append("candidate is not measured evidence")
    if candidate.safety_score < config.minimum_safety_score:
        reasons.append(f"safety {candidate.safety_score:.2f} < {config.minimum_safety_score:.2f}")
    if candidate.schema_failures > config.maximum_schema_failures:
        reasons.append(
            f"schema failures {candidate.schema_failures} > {config.maximum_schema_failures}"
        )
    floor = baseline.quality_score - config.max_absolute_quality_drop
    if candidate.quality_score < floor:
        reasons.append(f"quality {candidate.quality_score:.2f} < floor {floor:.2f}")
    if config.p95_latency_ms is not None and candidate.p95_latency_ms > config.p95_latency_ms:
        reasons.append("p95 latency exceeds configured SLA")
    if config.peak_rss_mb is not None and candidate.peak_rss_mb > config.peak_rss_mb:
        reasons.append("peak RSS exceeds configured limit")
    return GateDecision(not reasons, tuple(reasons))
