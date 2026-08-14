"""Independent replay of the bounded tuning plan from measured raw evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from a64pilot.benchmark.plan import BenchmarkCandidate
from a64pilot.benchmark.probes import (
    PerformanceProbeEvidence,
    performance_probe_semantic_sha256,
)
from a64pilot.optimize.candidates import (
    generate_candidates,
    rank_micro_threads,
    staged_candidate_subset,
)
from a64pilot.optimize.quality_gate import evaluate_gate
from a64pilot.optimize.search import (
    candidate_result_from_records,
    rank_calibration_candidates,
    validate_candidate_records,
)
from a64pilot.provenance import sha256_json
from a64pilot.schemas import BenchmarkRecord
from a64pilot.settings import QualityGateConfig

SEARCH_PLAN_SCHEMA_VERSION = "2.0.0"


def search_fingerprint_payload(plan: Mapping[str, object]) -> dict[str, object]:
    """Return only immutable search inputs, never generated rankings or selections."""

    fields = (
        "schema_version",
        "generator",
        "selection_policy",
        "target",
        "budget",
        "inputs",
        "probe_semantic_sha256",
        "quality_gate",
        "micro_ranking",
        "tuned_parallel_plan",
        "concurrency_probe_plan",
        "calibration_candidates",
    )
    return {field: plan.get(field) for field in fields}


def compute_search_fingerprint(plan: Mapping[str, object]) -> str:
    return sha256_json(search_fingerprint_payload(plan))


def _integer(value: object, name: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _candidate_list(value: object, name: str) -> list[BenchmarkCandidate]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    candidates = [BenchmarkCandidate.model_validate(item) for item in value]
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name} contains duplicate candidate IDs")
    return candidates


def _rows_for(
    records: Sequence[BenchmarkRecord], candidate_id: str, split: str
) -> list[BenchmarkRecord]:
    return [
        row
        for row in records
        if row.candidate_id == candidate_id
        and row.split == split
        and row.evidence_kind == "measured"
    ]


def measured_candidate_span_seconds(
    records: Sequence[BenchmarkRecord], candidate_ids: Sequence[str]
) -> float:
    """Return a process-independent lower bound for completed candidate time.

    Candidate calls are serial, but monotonic clocks cannot be compared across
    resume processes. Summing each candidate/split span preserves a verifiable
    lower bound without treating downtime between resumptions as search time.
    """

    allowed_ids = set(candidate_ids)
    groups: dict[tuple[str, str], list[BenchmarkRecord]] = {}
    for row in records:
        if (
            row.candidate_id in allowed_ids
            and row.split in {"calibration", "test"}
            and row.evidence_kind == "measured"
        ):
            groups.setdefault((row.candidate_id, row.split), []).append(row)
    return (
        sum(
            max(row.end_ns for row in rows) - min(row.start_ns for row in rows)
            for rows in groups.values()
        )
        / 1_000_000_000
    )


def verify_search_plan(
    plan: Mapping[str, object],
    records: Sequence[BenchmarkRecord],
    *,
    probes: PerformanceProbeEvidence,
    probe_semantic_sha256: str,
    architecture: str,
    logical_cpus: int,
    physical_cores: int | None,
    allowed_cpus: Sequence[int],
    calibration_case_ids: Sequence[str],
    test_case_ids: Sequence[str],
    gate: QualityGateConfig,
    binary_sha256: str,
    cases_sha256: str,
    split_sha256: str,
) -> list[str]:
    """Recompute calibration ranking, held-out gates, and the frozen A3 selection."""

    errors: list[str] = []

    def reject(message: str) -> None:
        errors.append(f"search plan: {message}")

    try:
        if plan.get("schema_version") != SEARCH_PLAN_SCHEMA_VERSION:
            raise ValueError("schema version is not the strict replay version")
        if plan.get("search_fingerprint") != compute_search_fingerprint(plan):
            raise ValueError("immutable input fingerprint does not replay")
        if plan.get("probe_semantic_sha256") != probe_semantic_sha256:
            raise ValueError("probe semantic hash does not match")
        if performance_probe_semantic_sha256(probes) != probe_semantic_sha256:
            raise ValueError("probe semantic fingerprint does not replay")
        if plan.get("quality_gate") != gate.model_dump(mode="json"):
            raise ValueError("quality gate disagrees with frozen configuration")
        target = plan.get("target")
        expected_target = {
            "architecture": architecture,
            "logical_cpus": logical_cpus,
            "physical_cores": physical_cores,
            "allowed_cpus": list(allowed_cpus),
        }
        if target != expected_target:
            raise ValueError("target topology disagrees with verified system evidence")
        budget = plan.get("budget")
        if not isinstance(budget, dict):
            raise ValueError("budget must be a mapping")
        quick = budget.get("quick")
        if type(quick) is not bool:
            raise ValueError("budget.quick must be boolean")
        max_candidates = _integer(budget.get("max_candidates"), "max_candidates", minimum=2)
        calibration_cases = _integer(
            budget.get("calibration_cases_per_candidate"), "calibration_cases"
        )
        repetitions = _integer(budget.get("repetitions"), "repetitions")
        finalists = _integer(budget.get("finalists"), "finalists")
        if calibration_cases > len(calibration_case_ids):
            raise ValueError("calibration case budget exceeds the frozen split")
        if finalists > max_candidates:
            raise ValueError("finalist budget exceeds max candidates")
        max_minutes = budget.get("max_minutes")
        if (
            not isinstance(max_minutes, (int, float))
            or isinstance(max_minutes, bool)
            or not 0 < float(max_minutes) < float("inf")
        ):
            raise ValueError("max_minutes is malformed")
        elapsed = plan.get("elapsed_seconds")
        if (
            not isinstance(elapsed, (int, float))
            or isinstance(elapsed, bool)
            or not 0 <= float(elapsed) <= float(max_minutes) * 60.0
        ):
            raise ValueError("elapsed time exceeds the frozen hard budget")

        micro_ranking = rank_micro_threads(probes)
        if plan.get("micro_ranking") != micro_ranking:
            raise ValueError("micro ranking does not replay from performance probes")
        if plan.get("tuned_parallel_plan") != [1]:
            raise ValueError("sequential A3 tuner must remain constrained to parallel=1")
        if plan.get("concurrency_probe_plan") != [1, 2]:
            raise ValueError("independent concurrency probe plan is incomplete")
        allowed_count = max(1, len(allowed_cpus))
        generated = generate_candidates(
            allowed_cores=allowed_count,
            physical_cores=(
                min(physical_cores, allowed_count) if physical_cores is not None else None
            ),
            quick=quick,
        )
        if budget.get("candidate_space") != len(generated):
            raise ValueError("candidate-space count does not replay")
        expected_candidates = staged_candidate_subset(
            generated,
            micro_ranking=micro_ranking,
            limit=max_candidates,
            quick=quick,
        )
        candidates = _candidate_list(plan.get("calibration_candidates"), "candidates")
        if [item.model_dump(mode="json") for item in candidates] != [
            item.model_dump(mode="json") for item in expected_candidates
        ]:
            raise ValueError("service candidates do not replay from micro-ranked generation")

        calibration_ids = tuple(calibration_case_ids[:calibration_cases])
        calibration_records: dict[str, list[BenchmarkRecord]] = {}
        for candidate in candidates:
            rows = _rows_for(records, candidate.candidate_id, "calibration")
            if rows:
                row_errors = validate_candidate_records(
                    candidate,
                    rows,
                    expected_split="calibration",
                    expected_case_ids=calibration_ids,
                    repetitions=repetitions,
                )
                if row_errors:
                    raise ValueError(
                        f"{candidate.candidate_id} calibration raw: {'; '.join(row_errors)}"
                    )
                calibration_records[candidate.candidate_id] = rows
        evaluations, ranked_ids = rank_calibration_candidates(
            candidates,
            calibration_records,
            expected_samples=len(calibration_ids) * repetitions,
            max_quality_drop=gate.max_absolute_quality_drop,
            minimum_safety_score=gate.minimum_safety_score,
            maximum_schema_failures=gate.maximum_schema_failures,
            expected_case_ids=calibration_ids,
            repetitions=repetitions,
        )
        expected_evaluations = [item.to_dict() for item in evaluations]
        if plan.get("calibration_results") != expected_evaluations:
            raise ValueError("calibration results do not replay from raw responses")
        if plan.get("ranked_candidate_ids") != ranked_ids:
            raise ValueError("calibration ranking does not replay")

        measured_lower_bound = measured_candidate_span_seconds(
            records, [candidate.candidate_id for candidate in candidates]
        )
        if float(elapsed) + 0.001 < measured_lower_bound:
            raise ValueError("elapsed time under-reports measured A3 candidate spans")

        receipts = plan.get("calibration_receipts")
        if not isinstance(receipts, list) or any(not isinstance(item, dict) for item in receipts):
            raise ValueError("calibration receipts are malformed")
        receipt_by_id = {item.get("candidate_id"): item for item in receipts}
        if len(receipt_by_id) != len(receipts):
            raise ValueError("calibration receipts contain duplicate candidate IDs")
        expected_receipt_ids = {candidate.candidate_id for candidate in candidates}
        if set(calibration_records) != expected_receipt_ids:
            raise ValueError("complete plan lacks raw calibration for a planned candidate")
        if set(receipt_by_id) != expected_receipt_ids:
            raise ValueError("calibration receipts do not exactly cover completed raw candidates")
        for candidate_id, rows in calibration_records.items():
            receipt = receipt_by_id[candidate_id]
            if receipt.get("source_run_ids") != sorted(row.run_id for row in rows):
                raise ValueError(f"{candidate_id} calibration receipt run IDs do not replay")
            if receipt.get("sample_count") != len(rows):
                raise ValueError(f"{candidate_id} calibration receipt count does not replay")

        scheduled = _candidate_list(plan.get("scheduled_finalists"), "scheduled finalists")
        expected_scheduled_ids = ranked_ids[:finalists]
        if [candidate.candidate_id for candidate in scheduled] != expected_scheduled_ids:
            raise ValueError("scheduled finalists are not the frozen calibration prefix")

        baseline_rows = [
            row
            for row in records
            if row.split == "test"
            and row.stage == "baseline"
            and row.backend == "generic"
            and row.evidence_kind == "measured"
        ]
        baseline_groups = {row.candidate_id for row in baseline_rows}
        if baseline_groups != {"a1-generic-q4-0"}:
            raise ValueError("strict replay requires exactly the formal A1 baseline")
        expected_test_keys = {
            (case_id, repetition) for repetition in range(repetitions) for case_id in test_case_ids
        }
        actual_baseline_keys = [(row.case_id, row.repetition) for row in baseline_rows]
        if set(actual_baseline_keys) != expected_test_keys or len(actual_baseline_keys) != len(
            set(actual_baseline_keys)
        ):
            raise ValueError("formal A1 baseline does not cover the frozen held-out matrix")
        baseline = candidate_result_from_records(baseline_rows)
        inputs = plan.get("inputs")
        if not isinstance(inputs, dict):
            raise ValueError("search inputs are malformed")
        calibration_model_hashes = {
            row.model_file_sha256 for rows in calibration_records.values() for row in rows
        }
        if len(calibration_model_hashes) != 1:
            raise ValueError("search model hash does not replay from calibration raw rows")
        expected_inputs = {
            "binary_sha256": binary_sha256,
            "model_sha256": next(iter(calibration_model_hashes)),
            "cases_sha256": cases_sha256,
            "split_sha256": split_sha256,
            "baseline": baseline.model_dump(mode="json"),
        }
        if inputs != expected_inputs:
            raise ValueError("frozen search inputs do not replay from verified evidence")

        held_out = plan.get("held_out_results")
        if not isinstance(held_out, list) or any(not isinstance(item, dict) for item in held_out):
            raise ValueError("held-out receipts are malformed")
        held_out_by_id: dict[str, dict[str, Any]] = {}
        scheduled_by_id = {candidate.candidate_id: candidate for candidate in scheduled}
        for receipt in held_out:
            candidate_payload = receipt.get("candidate")
            candidate = BenchmarkCandidate.model_validate(candidate_payload)
            if candidate.candidate_id in held_out_by_id:
                raise ValueError("held-out receipts contain duplicate candidates")
            if (
                candidate.candidate_id not in scheduled_by_id
                or candidate != scheduled_by_id[candidate.candidate_id]
            ):
                raise ValueError("held-out receipt is not an exact scheduled finalist")
            rows = _rows_for(records, candidate.candidate_id, "test")
            row_errors = validate_candidate_records(
                candidate,
                rows,
                expected_split="test",
                expected_case_ids=test_case_ids,
                repetitions=repetitions,
            )
            if row_errors:
                raise ValueError(f"{candidate.candidate_id} held-out raw: {'; '.join(row_errors)}")
            result = candidate_result_from_records(rows)
            decision = evaluate_gate(result, baseline, gate)
            expected_receipt = {
                "candidate": candidate.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "gate_passed": decision.passed,
                "gate_reasons": list(decision.reasons),
                "held_out_case_count": len(set(test_case_ids)),
                "held_out_sample_count": len(rows),
            }
            if receipt != expected_receipt:
                raise ValueError(
                    f"{candidate.candidate_id} held-out receipt does not replay from raw"
                )
            held_out_by_id[candidate.candidate_id] = receipt

        admitted = _candidate_list(plan.get("admitted_finalists"), "admitted finalists")
        if set(held_out_by_id) != set(scheduled_by_id):
            raise ValueError("complete plan lacks a full held-out receipt for a scheduled finalist")
        expected_admitted = list(scheduled)
        if [item.model_dump(mode="json") for item in admitted] != [
            item.model_dump(mode="json") for item in expected_admitted
        ]:
            raise ValueError("admitted finalists are not exactly the complete held-out receipts")
        selected = next(
            (
                candidate.candidate_id
                for candidate in scheduled
                if candidate.candidate_id in held_out_by_id
                and held_out_by_id[candidate.candidate_id].get("gate_passed") is True
            ),
            None,
        )
        if plan.get("selected_a3_candidate_id") != selected:
            raise ValueError("selected A3 does not replay from frozen order and held-out gates")
        expected_status = "complete" if selected else "complete-no-held-out-feasible-finalist"
        if plan.get("status") != expected_status:
            raise ValueError("final status does not replay")
        failures = plan.get("candidate_failures")
        if not isinstance(failures, list) or any(not isinstance(item, dict) for item in failures):
            raise ValueError("candidate failures are malformed")
        if failures:
            raise ValueError("a complete search plan may not hide execution failures")
    except (KeyError, TypeError, ValueError) as exc:
        reject(str(exc))
    return errors


__all__ = [
    "SEARCH_PLAN_SCHEMA_VERSION",
    "compute_search_fingerprint",
    "measured_candidate_span_seconds",
    "search_fingerprint_payload",
    "verify_search_plan",
]
