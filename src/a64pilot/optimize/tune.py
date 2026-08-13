"""Execute a bounded, topology-derived KleidiAI tuning search."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from a64pilot.benchmark.plan import BenchmarkCandidate
from a64pilot.benchmark.runner import (
    BenchmarkEnvironmentError,
    RealServiceBenchmark,
    RuntimeCandidate,
    run_candidate_sync,
)
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.hardware.detect import SystemInfo
from a64pilot.optimize.candidates import bounded_candidate_subset, generate_candidates
from a64pilot.optimize.quality_gate import evaluate_gate
from a64pilot.optimize.search import (
    candidate_result_from_records,
    rank_calibration_candidates,
)
from a64pilot.provenance import write_json
from a64pilot.schemas import CandidateResult
from a64pilot.settings import QualityGateConfig


class TuneSearchError(RuntimeError):
    """Raised when a bounded search cannot produce auditable finalists."""


def _runtime_candidate(plan: BenchmarkCandidate, *, binary: Path, model: Path) -> RuntimeCandidate:
    return RuntimeCandidate(
        candidate_id=plan.candidate_id,
        stage="a3",
        backend="kleidiai",
        binary=binary,
        cmake_cache=binary.parent.parent / "CMakeCache.txt",
        model=model,
        model_role="strong",
        quantization="Q4_0",
        threads=plan.threads,
        batch=plan.batch,
        ubatch=plan.ubatch,
        parallel=plan.parallel,
        context=plan.context,
        affinity=tuple(plan.affinity) or None,
    )


def _write_plan(path: Path, plan: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, plan)


def _formal_baseline(
    benchmark: RealServiceBenchmark,
    *,
    artifacts_dir: Path,
) -> CandidateResult:
    required = set(benchmark.split.test)
    records = [
        record
        for record in ArtifactStore(artifacts_dir / "raw").records(measured_only=True)
        if record.split == "test" and record.stage == "baseline" and record.backend == "generic"
    ]
    groups: dict[str, list] = {}
    for record in records:
        groups.setdefault(record.candidate_id, []).append(record)
    complete = [rows for rows in groups.values() if {record.case_id for record in rows} == required]
    if not complete:
        raise TuneSearchError(
            "bounded tune requires a complete formal A1 baseline; run `a64pilot benchmark fair` "
            "without --limit first"
        )
    rows = min(complete, key=lambda group: group[0].candidate_id)
    return candidate_result_from_records(rows)


def run_bounded_tune(
    *,
    benchmark: RealServiceBenchmark,
    system_info: SystemInfo,
    binary: Path,
    model: Path,
    gate: QualityGateConfig,
    max_candidates: int = 8,
    calibration_cases: int = 4,
    finalists: int = 2,
    repetitions: int = 1,
    max_minutes: float = 45.0,
    quick: bool = True,
    artifacts_dir: Path = Path("artifacts"),
) -> dict[str, object]:
    """Calibrate a bounded matrix, then validate admitted finalists on all test cases."""

    if max_candidates < 2:
        raise ValueError("max_candidates must be at least 2")
    if not 1 <= calibration_cases <= len(benchmark.split.calibration):
        raise ValueError("calibration_cases is outside the calibration split")
    if not 1 <= finalists <= max_candidates:
        raise ValueError("finalists must be between 1 and max_candidates")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if max_minutes <= 0:
        raise ValueError("max_minutes must be positive")

    baseline = _formal_baseline(benchmark, artifacts_dir=artifacts_dir)
    topology = system_info.topology
    allowed_cores = max(1, len(topology.allowed_cpus))
    generated = generate_candidates(
        allowed_cores=allowed_cores,
        physical_cores=min(topology.physical_cores, allowed_cores),
        quick=quick,
    )
    calibration_plans = bounded_candidate_subset(generated, max_candidates)
    plan_path = artifacts_dir / "search-plan.json"
    plan: dict[str, object] = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "calibration-running",
        "generator": "a64pilot.optimize.candidates.generate_candidates",
        "selection_policy": (
            "calibration-only hard safety/schema/quality gate, then deterministic "
            "p95/rps/RSS ranking; held-out test is read only after finalist admission"
        ),
        "target": {
            "architecture": system_info.architecture,
            "logical_cpus": topology.logical_cpus,
            "physical_cores": topology.physical_cores,
            "allowed_cpus": list(topology.allowed_cpus),
        },
        "budget": {
            "quick": quick,
            "candidate_space": len(generated),
            "max_candidates": max_candidates,
            "calibration_cases_per_candidate": calibration_cases,
            "finalists": finalists,
            "repetitions": repetitions,
            "max_minutes": max_minutes,
        },
        "calibration_candidates": [
            candidate.model_dump(mode="json") for candidate in calibration_plans
        ],
        "calibration_results": [],
        "ranked_candidate_ids": [],
        "admitted_finalists": [],
        "held_out_results": [],
        "candidate_failures": [],
    }
    _write_plan(plan_path, plan)

    started = time.monotonic()
    records_by_candidate: dict[str, list] = {}
    completed_plans = []
    for candidate in calibration_plans:
        if completed_plans and (time.monotonic() - started) / 60 >= max_minutes:
            break
        runtime = _runtime_candidate(candidate, binary=binary, model=model)
        try:
            records_by_candidate[candidate.candidate_id] = run_candidate_sync(
                benchmark,
                runtime,
                split="calibration",
                repetitions=repetitions,
                limit=calibration_cases,
                warmups=1,
            )
        except (BenchmarkEnvironmentError, RuntimeError, ValueError) as exc:
            failures = plan["candidate_failures"]
            assert isinstance(failures, list)
            failures.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "phase": "calibration",
                    "status": "rejected-execution-failure",
                    "error_type": type(exc).__name__,
                }
            )
            _write_plan(plan_path, plan)
            continue
        completed_plans.append(candidate)

    evaluations, ranked_ids = rank_calibration_candidates(
        calibration_plans,
        records_by_candidate,
        expected_samples=calibration_cases * repetitions,
        max_quality_drop=gate.max_absolute_quality_drop,
        minimum_safety_score=gate.minimum_safety_score,
        maximum_schema_failures=gate.maximum_schema_failures,
    )
    plan["calibration_results"] = [evaluation.to_dict() for evaluation in evaluations]
    plan["ranked_candidate_ids"] = ranked_ids
    if not ranked_ids:
        plan["status"] = "failed-no-calibration-feasible-candidate"
        _write_plan(plan_path, plan)
        raise TuneSearchError("no calibration candidate passed the safety and quality gate")

    by_id = {candidate.candidate_id: candidate for candidate in completed_plans}
    admitted = [by_id[candidate_id] for candidate_id in ranked_ids[:finalists]]
    plan["admitted_finalists"] = [candidate.model_dump(mode="json") for candidate in admitted]
    plan["status"] = "held-out-validation-running"
    _write_plan(plan_path, plan)

    held_out_receipts: list[dict[str, object]] = []
    selected_a3_candidate_id: str | None = None
    for candidate in admitted:
        # Once the first finalist is admitted, complete its 20-case evaluation.
        # The wall-time budget can prevent admission of additional finalists but
        # never truncate a formal held-out candidate midway.
        if held_out_receipts and (time.monotonic() - started) / 60 >= max_minutes:
            break
        runtime = _runtime_candidate(candidate, binary=binary, model=model)
        try:
            rows = run_candidate_sync(
                benchmark,
                runtime,
                split="test",
                repetitions=repetitions,
                limit=None,
                warmups=1,
            )
        except (BenchmarkEnvironmentError, RuntimeError, ValueError) as exc:
            failures = plan["candidate_failures"]
            assert isinstance(failures, list)
            failure = {
                "candidate_id": candidate.candidate_id,
                "phase": "held-out",
                "status": "rejected-execution-failure",
                "error_type": type(exc).__name__,
            }
            failures.append(failure)
            held_out_receipts.append(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "gate_passed": False,
                    "gate_reasons": ["held-out execution failed"],
                    "held_out_case_count": 0,
                    "execution_failure": failure,
                }
            )
            _write_plan(plan_path, plan)
            continue
        result = candidate_result_from_records(rows)
        decision = evaluate_gate(result, baseline, gate)
        # Finalist order was frozen from calibration metrics. Held-out results
        # may reject a finalist, but never reorder finalists by test performance.
        if decision.passed and selected_a3_candidate_id is None:
            selected_a3_candidate_id = candidate.candidate_id
        held_out_receipts.append(
            {
                "candidate": candidate.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "gate_passed": decision.passed,
                "gate_reasons": list(decision.reasons),
                "held_out_case_count": len({row.case_id for row in rows}),
            }
        )
    plan["held_out_results"] = held_out_receipts
    plan["selected_a3_candidate_id"] = selected_a3_candidate_id
    plan["elapsed_seconds"] = round(time.monotonic() - started, 3)
    plan["status"] = (
        "complete" if selected_a3_candidate_id else "complete-no-held-out-feasible-finalist"
    )
    _write_plan(plan_path, plan)
    return plan


__all__ = ["TuneSearchError", "run_bounded_tune"]
