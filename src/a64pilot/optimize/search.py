"""Pure summaries and fail-closed selection for bounded device tuning."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from a64pilot.benchmark.plan import BenchmarkCandidate
from a64pilot.optimize.quality_gate import GateDecision, evaluate_gate
from a64pilot.schemas import BenchmarkRecord, CandidateResult
from a64pilot.settings import QualityGateConfig


@dataclass(frozen=True, slots=True)
class SearchEvaluation:
    candidate_id: str
    sample_count: int
    expected_samples: int
    p95_latency_ms: float | None
    requests_per_second: float | None
    quality_score: float | None
    safety_score: float | None
    schema_failures: int
    peak_rss_mb: float | None
    feasible: bool
    reasons: tuple[str, ...]
    source_run_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "sample_count": self.sample_count,
            "expected_samples": self.expected_samples,
            "p95_latency_ms": self.p95_latency_ms,
            "requests_per_second": self.requests_per_second,
            "quality_score": self.quality_score,
            "safety_score": self.safety_score,
            "schema_failures": self.schema_failures,
            "peak_rss_mb": self.peak_rss_mb,
            "feasible": self.feasible,
            "reasons": list(self.reasons),
            "source_run_ids": list(self.source_run_ids),
        }


@dataclass(frozen=True, slots=True)
class FrozenSelection:
    selected: CandidateResult
    decisions: dict[str, GateDecision]
    basis: Literal["frozen_calibration_finalist", "fixed_a2_strong_fallback"]
    frozen_candidate_receipt: dict[str, object]


def _summarize(
    candidate_id: str,
    records: Sequence[BenchmarkRecord],
    *,
    expected_samples: int,
    expected_split: str,
    minimum_safety_score: float,
    maximum_schema_failures: int,
    expected_case_ids: Sequence[str] | None = None,
    repetitions: int | None = None,
) -> SearchEvaluation:
    rows = list(records)
    reasons: list[str] = []
    if len(rows) != expected_samples:
        reasons.append(f"sample count {len(rows)} != expected {expected_samples}")
    if any(row.candidate_id != candidate_id for row in rows):
        reasons.append("rows contain a different candidate ID")
    if any(row.split != expected_split for row in rows):
        reasons.append(f"rows are not exclusively split={expected_split}")
    if any(row.evidence_kind != "measured" for row in rows):
        reasons.append("rows include non-measured evidence")
    if any(row.backend != "kleidiai" or not row.kleidiai_verified for row in rows):
        reasons.append("rows lack verified KleidiAI execution")
    if any(not row.cpu_only_verified for row in rows):
        reasons.append("rows lack CPU-only verification")
    if expected_case_ids is not None and repetitions is not None:
        expected_keys = {
            (case_id, repetition)
            for repetition in range(repetitions)
            for case_id in expected_case_ids
        }
        actual_keys = [(row.case_id, row.repetition) for row in rows]
        if set(actual_keys) != expected_keys or len(actual_keys) != len(set(actual_keys)):
            reasons.append("rows do not exactly cover the frozen case/repetition matrix")
    schema_failures = sum(not row.schema_valid for row in rows)
    if schema_failures > maximum_schema_failures:
        reasons.append(f"schema failures {schema_failures} > allowed {maximum_schema_failures}")
    safety = min((row.safety_score for row in rows), default=None)
    if safety is None or safety < minimum_safety_score:
        reasons.append("minimum safety score was not met")
    latencies = [row.e2e_ms for row in rows]
    rates = [1000.0 / value for value in latencies if value > 0]
    return SearchEvaluation(
        candidate_id=candidate_id,
        sample_count=len(rows),
        expected_samples=expected_samples,
        p95_latency_ms=float(np.percentile(latencies, 95)) if latencies else None,
        requests_per_second=float(statistics.median(rates)) if rates else None,
        quality_score=(sum(row.quality_score for row in rows) / len(rows)) if rows else None,
        safety_score=safety,
        schema_failures=schema_failures,
        peak_rss_mb=max((row.peak_rss_mb for row in rows), default=None),
        feasible=not reasons,
        reasons=tuple(reasons),
        source_run_ids=tuple(sorted(row.run_id for row in rows)),
    )


def validate_candidate_records(
    candidate: BenchmarkCandidate,
    records: Sequence[BenchmarkRecord],
    *,
    expected_split: str,
    expected_case_ids: Sequence[str],
    repetitions: int,
) -> list[str]:
    """Validate exact candidate settings and complete case/repetition coverage."""

    rows = list(records)
    errors: list[str] = []
    expected_keys = {
        (case_id, repetition) for repetition in range(repetitions) for case_id in expected_case_ids
    }
    actual_keys = [(row.case_id, row.repetition) for row in rows]
    if set(actual_keys) != expected_keys or len(actual_keys) != len(set(actual_keys)):
        errors.append("rows do not exactly cover the frozen case/repetition matrix")
    expected = {
        "candidate_id": candidate.candidate_id,
        "stage": candidate.stage,
        "backend": candidate.backend,
        "model_role": candidate.model_role,
        "quantization": candidate.quantization,
        "threads": candidate.threads,
        "batch": candidate.batch,
        "ubatch": candidate.ubatch,
        "parallel": candidate.parallel,
        "context": candidate.context,
        "affinity": list(candidate.affinity),
        "split": expected_split,
        "evidence_kind": "measured",
    }
    for field_name, value in expected.items():
        if any(getattr(row, field_name) != value for row in rows):
            errors.append(f"rows disagree with candidate {field_name}")
    if any(not row.cpu_only_verified or not row.kleidiai_verified for row in rows):
        errors.append("rows lack verified CPU-only KleidiAI execution")
    return errors


def rank_calibration_candidates(
    candidates: Sequence[BenchmarkCandidate],
    records_by_candidate: Mapping[str, Sequence[BenchmarkRecord]],
    *,
    expected_samples: int,
    max_quality_drop: float,
    minimum_safety_score: float,
    maximum_schema_failures: int,
    expected_case_ids: Sequence[str] | None = None,
    repetitions: int | None = None,
) -> tuple[list[SearchEvaluation], list[str]]:
    """Apply calibration-only safety/quality gates and rank feasible finalists."""

    evaluations = [
        _summarize(
            candidate.candidate_id,
            records_by_candidate.get(candidate.candidate_id, ()),
            expected_samples=expected_samples,
            expected_split="calibration",
            minimum_safety_score=minimum_safety_score,
            maximum_schema_failures=maximum_schema_failures,
            expected_case_ids=expected_case_ids,
            repetitions=repetitions,
        )
        for candidate in candidates
    ]
    complete_quality = [
        item.quality_score
        for item in evaluations
        if item.feasible and item.quality_score is not None
    ]
    if not complete_quality:
        return evaluations, []
    quality_floor = max(complete_quality) - max_quality_drop
    gated: list[SearchEvaluation] = []
    for item in evaluations:
        reasons = list(item.reasons)
        if item.quality_score is None or item.quality_score < quality_floor:
            reasons.append(
                f"quality {item.quality_score!r} is below calibration floor {quality_floor:.3f}"
            )
        gated.append(replace(item, feasible=not reasons, reasons=tuple(reasons)))
    ranked = sorted(
        (item for item in gated if item.feasible),
        key=lambda item: (
            item.p95_latency_ms if item.p95_latency_ms is not None else float("inf"),
            -(item.requests_per_second or 0.0),
            item.peak_rss_mb if item.peak_rss_mb is not None else float("inf"),
            -(item.quality_score or 0.0),
            item.candidate_id,
        ),
    )
    return gated, [item.candidate_id for item in ranked]


def candidate_result_from_records(records: Sequence[BenchmarkRecord]) -> CandidateResult:
    """Convert one homogeneous formal-test record group to the quality-gate type."""

    rows = sorted(records, key=lambda row: (row.repetition, row.case_id, row.run_id))
    if not rows:
        raise ValueError("cannot summarize an empty candidate record group")
    first = rows[0]
    if any(row.candidate_id != first.candidate_id for row in rows):
        raise ValueError("candidate record group contains mixed IDs")
    if any(row.split != "test" or row.evidence_kind != "measured" for row in rows):
        raise ValueError("candidate result requires measured formal-test rows")
    e2e = [row.e2e_ms for row in rows]
    rates = [1000.0 / value for value in e2e if value > 0]
    if not rates:
        raise ValueError("candidate result has no positive latency")
    return CandidateResult(
        candidate_id=first.candidate_id,
        stage=first.stage,
        backend=first.backend,
        model="cascade" if first.model_role == "cascade" else "strong",
        quality_score=sum(row.quality_score for row in rows) / len(rows),
        safety_score=min(row.safety_score for row in rows),
        schema_failures=sum(not row.schema_valid for row in rows),
        p95_latency_ms=float(np.percentile(e2e, 95)),
        requests_per_second=float(statistics.median(rates)),
        peak_rss_mb=max(row.peak_rss_mb for row in rows),
        measured=True,
        source_run_ids=[row.run_id for row in rows],
        config={
            "threads": first.threads,
            "batch": first.batch,
            "ubatch": first.ubatch,
            "parallel": first.parallel,
            "context": first.context,
            "affinity": first.affinity,
            "quantization": first.quantization,
        },
    )


def select_frozen_deployment(
    candidates: Mapping[str, CandidateResult],
    baseline: CandidateResult,
    gate: QualityGateConfig,
    *,
    search_plan: Mapping[str, object] | None,
) -> FrozenSelection:
    """Select only the calibration-frozen A3, otherwise the fixed A2 fallback.

    Held-out metrics are used to accept or reject a previously frozen finalist,
    never to reorder A2/A3 candidates or discover a new winner.
    """

    decisions = {
        candidate_id: evaluate_gate(candidate, baseline, gate)
        for candidate_id, candidate in candidates.items()
    }
    if search_plan is not None:
        frozen_id = search_plan.get("selected_a3_candidate_id")
        if frozen_id is not None:
            if not isinstance(frozen_id, str) or not frozen_id:
                raise ValueError("search plan frozen candidate ID is malformed")
            if search_plan.get("status") != "complete":
                raise ValueError("search plan selected A3 but is not complete")
            ranked = search_plan.get("ranked_candidate_ids")
            admitted = search_plan.get("admitted_finalists")
            receipts = search_plan.get("held_out_results")
            if not isinstance(ranked, list) or any(not isinstance(item, str) for item in ranked):
                raise ValueError("search plan ranked candidate IDs are malformed")
            if not isinstance(admitted, list) or any(
                not isinstance(item, dict) for item in admitted
            ):
                raise ValueError("search plan admitted finalists are malformed")
            if not isinstance(receipts, list) or any(
                not isinstance(item, dict) for item in receipts
            ):
                raise ValueError("search plan held-out receipts are malformed")
            admitted_ids = [row.get("candidate_id") for row in admitted]
            passed_receipts: dict[str, dict[str, object]] = {}
            for receipt in receipts:
                candidate = receipt.get("candidate")
                candidate_id = (
                    candidate.get("candidate_id") if isinstance(candidate, dict) else None
                )
                if isinstance(candidate_id, str) and receipt.get("gate_passed") is True:
                    passed_receipts[candidate_id] = dict(receipt)
            frozen_order = [
                candidate_id
                for candidate_id in ranked
                if candidate_id in admitted_ids and candidate_id in passed_receipts
            ]
            if not frozen_order or frozen_order[0] != frozen_id:
                raise ValueError(
                    "search plan selected A3 is not the first calibration-ranked held-out pass"
                )
            selected = candidates.get(frozen_id)
            if selected is None:
                raise ValueError("search plan selected A3 has no formal test rows")
            decision = decisions[frozen_id]
            if not decision.passed:
                raise ValueError("search plan selected A3 fails the replayed quality gate")
            return FrozenSelection(
                selected=selected,
                decisions=decisions,
                basis="frozen_calibration_finalist",
                frozen_candidate_receipt=passed_receipts[frozen_id],
            )
        allowed_fallback_statuses = {
            "complete-no-held-out-feasible-finalist",
            "failed-no-calibration-feasible-candidate",
        }
        if search_plan.get("status") not in allowed_fallback_statuses:
            raise ValueError("search plan is incomplete or missing a frozen-selection result")

    fallback_id = "a2-kleidiai-q4-0"
    fallback = candidates.get(fallback_id)
    if fallback is None:
        raise ValueError("fixed A2 strong fallback has no formal test rows")
    fallback_decision = decisions[fallback_id]
    if not fallback_decision.passed:
        raise ValueError("fixed A2 strong fallback fails the replayed quality gate")
    return FrozenSelection(
        selected=fallback,
        decisions=decisions,
        basis="fixed_a2_strong_fallback",
        frozen_candidate_receipt={
            "candidate_id": fallback_id,
            "reason": "no calibration-frozen A3 passed the full held-out quality gate",
            "search_plan_status": search_plan.get("status") if search_plan else "missing",
        },
    )


__all__ = [
    "FrozenSelection",
    "SearchEvaluation",
    "candidate_result_from_records",
    "rank_calibration_candidates",
    "select_frozen_deployment",
    "validate_candidate_records",
]
