"""Execute a bounded, topology-derived KleidiAI tuning search."""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from a64pilot.benchmark.plan import BenchmarkCandidate
from a64pilot.benchmark.probes import (
    load_performance_probes,
    performance_probe_semantic_sha256,
)
from a64pilot.benchmark.runner import (
    BenchmarkEnvironmentError,
    RealServiceBenchmark,
    RuntimeCandidate,
    run_candidate_sync,
)
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.hardware.detect import SystemInfo
from a64pilot.models.checksum import sha256_file
from a64pilot.optimize.candidates import (
    generate_candidates,
    rank_micro_threads,
    staged_candidate_subset,
)
from a64pilot.optimize.quality_gate import evaluate_gate
from a64pilot.optimize.replay import (
    SEARCH_PLAN_SCHEMA_VERSION,
    compute_search_fingerprint,
    measured_candidate_span_seconds,
    verify_search_plan,
)
from a64pilot.optimize.search import (
    candidate_result_from_records,
    rank_calibration_candidates,
    validate_candidate_records,
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
    repetitions: int,
) -> CandidateResult:
    required = {
        (case_id, repetition)
        for case_id in benchmark.split.test
        for repetition in range(repetitions)
    }
    records = [
        record
        for record in ArtifactStore(artifacts_dir / "raw").records(measured_only=True)
        if record.split == "test"
        and record.stage == "baseline"
        and record.backend == "generic"
        and record.candidate_id == "a1-generic-q4-0"
    ]
    groups: dict[str, list] = {}
    for record in records:
        groups.setdefault(record.candidate_id, []).append(record)
    complete = [
        rows
        for rows in groups.values()
        if len(rows) == len(required)
        and {(record.case_id, record.repetition) for record in rows} == required
    ]
    if not complete:
        raise TuneSearchError(
            "bounded tune requires a complete formal A1 baseline; run `a64pilot benchmark fair` "
            "without --limit first"
        )
    rows = complete[0]
    return candidate_result_from_records(rows)


def _digest_or_missing(path: Path) -> str:
    return sha256_file(path) if path.is_file() else f"missing:{path.as_posix()}"


def _source_run_ids(plan: dict[str, object]) -> set[str]:
    run_ids: set[str] = set()
    for key in ("calibration_receipts", "calibration_results", "held_out_results"):
        rows = plan.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            values = row.get("source_run_ids")
            if values is None and isinstance(row.get("result"), dict):
                values = row["result"].get("source_run_ids")
            if isinstance(values, list):
                run_ids.update(value for value in values if isinstance(value, str))
    return run_ids


def _load_existing_plan(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TuneSearchError("existing search plan is unreadable") from exc
    if not isinstance(payload, dict):
        raise TuneSearchError("existing search plan root must be a mapping")
    return payload


def _matching_rows(rows: list[Any], candidate_id: str, split: str) -> list[Any]:
    return [
        row
        for row in rows
        if row.candidate_id == candidate_id
        and row.split == split
        and row.evidence_kind == "measured"
    ]


def _receipt_index(rows: object, *, nested_result: bool = False) -> dict[str, dict[str, object]]:
    if not isinstance(rows, list):
        raise TuneSearchError("search plan receipts must be a list")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TuneSearchError("search plan receipt must be a mapping")
        if nested_result:
            candidate = row.get("candidate")
            value = candidate.get("candidate_id") if isinstance(candidate, dict) else None
        else:
            value = row.get("candidate_id")
        if not isinstance(value, str) or not value:
            raise TuneSearchError("search plan receipt has no candidate ID")
        if value in result:
            raise TuneSearchError(f"search plan has duplicate receipts for {value}")
        result[value] = row
    return result


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
    """Run a probe-ranked search with exact raw-evidence resume and a hard deadline."""

    if max_candidates < 2:
        raise ValueError("max_candidates must be at least 2")
    if not 1 <= calibration_cases <= len(benchmark.split.calibration):
        raise ValueError("calibration_cases is outside the calibration split")
    if not 1 <= finalists <= max_candidates:
        raise ValueError("finalists must be between 1 and max_candidates")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not math.isfinite(max_minutes) or max_minutes <= 0:
        raise ValueError("max_minutes must be finite and positive")

    baseline = _formal_baseline(
        benchmark,
        artifacts_dir=artifacts_dir,
        repetitions=repetitions,
    )
    probe_path = artifacts_dir / "performance-probes.json"
    try:
        probes = load_performance_probes(probe_path)
    except ValueError as exc:
        raise TuneSearchError(
            "bounded tune requires a complete verified performance-probes artifact"
        ) from exc
    probe_hash = performance_probe_semantic_sha256(probes)
    micro_ranking = rank_micro_threads(probes)
    topology = system_info.topology
    allowed_cores = max(1, len(topology.allowed_cpus))
    physical_cores = (
        min(topology.physical_cores, allowed_cores) if topology.physical_cores is not None else None
    )
    generated = generate_candidates(
        allowed_cores=allowed_cores,
        physical_cores=physical_cores,
        quick=quick,
    )
    calibration_plans = staged_candidate_subset(
        generated,
        micro_ranking=micro_ranking,
        limit=max_candidates,
        quick=quick,
    )
    plan_path = artifacts_dir / "search-plan.json"
    fresh_plan: dict[str, object] = {
        "schema_version": SEARCH_PLAN_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "calibration-running",
        "generator": "a64pilot.optimize.candidates.staged_candidate_subset",
        "selection_policy": (
            "verified micro throughput ranks and constrains service threads; calibration-only "
            "hard safety/schema/quality gate then deterministic p95/rps/RSS ranking; the "
            "unchanged split-v2 held-out replication may reject but never reorder finalists"
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
        "inputs": {
            "binary_sha256": _digest_or_missing(binary),
            "model_sha256": _digest_or_missing(model),
            "cases_sha256": str(getattr(benchmark, "cases_sha256", None)),
            "split_sha256": str(getattr(benchmark, "split_sha256", None)),
            "baseline": baseline.model_dump(mode="json"),
        },
        "probe_semantic_sha256": probe_hash,
        "quality_gate": gate.model_dump(mode="json"),
        "micro_ranking": micro_ranking,
        "tuned_parallel_plan": [1],
        "concurrency_probe_plan": [1, 2],
        "calibration_candidates": [
            candidate.model_dump(mode="json") for candidate in calibration_plans
        ],
        "calibration_receipts": [],
        "calibration_results": [],
        "ranked_candidate_ids": [],
        "scheduled_finalists": [],
        "admitted_finalists": [],
        "held_out_results": [],
        "candidate_failures": [],
        "selected_a3_candidate_id": None,
        "elapsed_seconds": 0.0,
    }
    fresh_plan["search_fingerprint"] = compute_search_fingerprint(fresh_plan)

    store_rows = list(ArtifactStore(artifacts_dir / "raw").records(measured_only=True))
    raw_ids = {row.run_id for row in store_rows}
    existing = _load_existing_plan(plan_path)
    if existing is not None and existing.get("search_fingerprint") == fresh_plan.get(
        "search_fingerprint"
    ):
        plan = existing
        if plan.get("calibration_candidates") != fresh_plan["calibration_candidates"]:
            raise TuneSearchError("existing search plan candidates fail immutable replay")
        if plan.get("status") in {"complete", "complete-no-held-out-feasible-finalist"}:
            replay_errors = verify_search_plan(
                plan,
                store_rows,
                probes=probes,
                probe_semantic_sha256=probe_hash,
                architecture=system_info.architecture,
                logical_cpus=topology.logical_cpus,
                physical_cores=topology.physical_cores,
                allowed_cpus=topology.allowed_cpus,
                calibration_case_ids=benchmark.split.calibration,
                test_case_ids=benchmark.split.test,
                gate=gate,
                binary_sha256=_digest_or_missing(binary),
                cases_sha256=str(getattr(benchmark, "cases_sha256", None)),
                split_sha256=str(getattr(benchmark, "split_sha256", None)),
            )
            if replay_errors:
                raise TuneSearchError(
                    "cached complete search failed replay: " + "; ".join(replay_errors)
                )
            return plan
        if plan.get("status") not in {"calibration-running", "held-out-validation-running"}:
            raise TuneSearchError("failed or malformed search plan cannot be resumed implicitly")
    elif existing is not None:
        # A committed prior-run receipt may be replaced only when none of its
        # cited raw rows are present.  Mixing two live evidence cycles is refused.
        if _source_run_ids(existing) & raw_ids:
            raise TuneSearchError("existing search plan belongs to different live raw evidence")
        plan = fresh_plan
        _write_plan(plan_path, plan)
    else:
        plan = fresh_plan
        _write_plan(plan_path, plan)

    prior_elapsed = plan.get("elapsed_seconds", 0.0)
    if (
        not isinstance(prior_elapsed, (int, float))
        or isinstance(prior_elapsed, bool)
        or not math.isfinite(float(prior_elapsed))
        or float(prior_elapsed) < 0
    ):
        raise TuneSearchError("existing search plan elapsed budget is malformed")
    measured_lower_bound = measured_candidate_span_seconds(
        store_rows,
        [candidate.candidate_id for candidate in calibration_plans],
    )
    if float(prior_elapsed) + 0.001 < measured_lower_bound:
        raise TuneSearchError(
            "existing search plan elapsed budget under-reports raw candidate time"
        )
    started = time.monotonic()
    remaining_budget = max_minutes * 60.0 - float(prior_elapsed)
    if remaining_budget <= 0:
        raise TuneSearchError("bounded tuning runtime budget was already exhausted")
    bookkeeping_reserve = min(1.0, remaining_budget / 10.0)
    deadline = started + remaining_budget - bookkeeping_reserve

    def persist() -> None:
        elapsed = float(prior_elapsed) + time.monotonic() - started
        plan["elapsed_seconds"] = round(elapsed, 3)
        exhausted = elapsed > max_minutes * 60.0
        if exhausted:
            plan["status"] = "failed-budget-exhausted"
        _write_plan(plan_path, plan)
        if exhausted:
            raise TuneSearchError("bounded tuning exceeded its hard runtime budget")

    failures = plan.get("candidate_failures")
    calibration_receipts = plan.get("calibration_receipts")
    held_out_receipts = plan.get("held_out_results")
    if not isinstance(failures, list) or not isinstance(calibration_receipts, list):
        raise TuneSearchError("existing search plan progress receipts are malformed")
    if not isinstance(held_out_receipts, list):
        raise TuneSearchError("existing held-out receipts are malformed")
    if failures:
        raise TuneSearchError("a failed search cannot resume without repeating uncertain work")

    calibration_ids = tuple(benchmark.split.calibration[:calibration_cases])
    records_by_candidate: dict[str, list] = {}
    calibration_receipt_by_id = _receipt_index(calibration_receipts)
    planned_ids = {candidate.candidate_id for candidate in calibration_plans}
    if not set(calibration_receipt_by_id) <= planned_ids:
        raise TuneSearchError("calibration receipts contain an unplanned candidate")
    for candidate in calibration_plans:
        cached_rows = _matching_rows(store_rows, candidate.candidate_id, "calibration")
        if candidate.candidate_id in calibration_receipt_by_id:
            receipt = calibration_receipt_by_id[candidate.candidate_id]
            row_errors = validate_candidate_records(
                candidate,
                cached_rows,
                expected_split="calibration",
                expected_case_ids=calibration_ids,
                repetitions=repetitions,
            )
            expected_receipt = {
                "candidate_id": candidate.candidate_id,
                "source_run_ids": sorted(row.run_id for row in cached_rows),
                "sample_count": len(cached_rows),
            }
            if row_errors or receipt != expected_receipt:
                raise TuneSearchError(
                    f"cached calibration receipt for {candidate.candidate_id} failed raw replay"
                )
            records_by_candidate[candidate.candidate_id] = cached_rows
            continue
        if cached_rows:
            raise TuneSearchError(
                f"unreceipted partial raw calibration exists for {candidate.candidate_id}; "
                "refusing repeated inference"
            )
        runtime = _runtime_candidate(candidate, binary=binary, model=model)
        try:
            rows = run_candidate_sync(
                benchmark,
                runtime,
                split="calibration",
                repetitions=repetitions,
                limit=calibration_cases,
                warmups=1,
                deadline=deadline,
            )
        except (BenchmarkEnvironmentError, RuntimeError, ValueError) as exc:
            failures.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "phase": "calibration",
                    "status": "failed-closed-execution",
                    "error_type": type(exc).__name__,
                }
            )
            plan["status"] = "failed-calibration-execution"
            persist()
            raise TuneSearchError(
                f"calibration candidate {candidate.candidate_id} failed closed"
            ) from exc
        row_errors = validate_candidate_records(
            candidate,
            rows,
            expected_split="calibration",
            expected_case_ids=calibration_ids,
            repetitions=repetitions,
        )
        if row_errors:
            plan["status"] = "failed-calibration-incomplete"
            persist()
            raise TuneSearchError(
                f"calibration candidate {candidate.candidate_id} is incomplete: "
                + "; ".join(row_errors)
            )
        records_by_candidate[candidate.candidate_id] = rows
        receipt: dict[str, object] = {
            "candidate_id": candidate.candidate_id,
            "source_run_ids": sorted(row.run_id for row in rows),
            "sample_count": len(rows),
        }
        calibration_receipts.append(receipt)
        calibration_receipt_by_id[candidate.candidate_id] = receipt
        persist()

    evaluations, ranked_ids = rank_calibration_candidates(
        calibration_plans,
        records_by_candidate,
        expected_samples=calibration_cases * repetitions,
        max_quality_drop=gate.max_absolute_quality_drop,
        minimum_safety_score=gate.minimum_safety_score,
        maximum_schema_failures=gate.maximum_schema_failures,
        expected_case_ids=calibration_ids,
        repetitions=repetitions,
    )
    plan["calibration_results"] = [evaluation.to_dict() for evaluation in evaluations]
    plan["ranked_candidate_ids"] = ranked_ids
    scheduled = [
        candidate
        for candidate_id in ranked_ids[:finalists]
        for candidate in calibration_plans
        if candidate.candidate_id == candidate_id
    ]
    plan["scheduled_finalists"] = [candidate.model_dump(mode="json") for candidate in scheduled]
    if not scheduled:
        if held_out_receipts:
            raise TuneSearchError("held-out receipts exist without a scheduled finalist")
        plan["status"] = "complete-no-held-out-feasible-finalist"
        plan["admitted_finalists"] = []
        plan["selected_a3_candidate_id"] = None
        persist()
        return plan
    plan["status"] = "held-out-validation-running"
    persist()

    receipt_by_id = _receipt_index(held_out_receipts, nested_result=True)
    scheduled_ids = {candidate.candidate_id for candidate in scheduled}
    if not set(receipt_by_id) <= scheduled_ids:
        raise TuneSearchError("held-out receipts contain an unscheduled candidate")
    expected_admitted = [
        candidate.model_dump(mode="json")
        for candidate in scheduled
        if candidate.candidate_id in receipt_by_id
    ]
    if plan.get("admitted_finalists") != expected_admitted:
        raise TuneSearchError("admitted finalists do not match complete held-out receipts")
    for candidate in scheduled:
        cached_rows = _matching_rows(store_rows, candidate.candidate_id, "test")
        if candidate.candidate_id in receipt_by_id:
            row_errors = validate_candidate_records(
                candidate,
                cached_rows,
                expected_split="test",
                expected_case_ids=benchmark.split.test,
                repetitions=repetitions,
            )
            if row_errors:
                raise TuneSearchError(
                    f"cached held-out receipt for {candidate.candidate_id} failed raw replay"
                )
            result = candidate_result_from_records(cached_rows)
            decision = evaluate_gate(result, baseline, gate)
            expected_receipt = {
                "candidate": candidate.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "gate_passed": decision.passed,
                "gate_reasons": list(decision.reasons),
                "held_out_case_count": len(set(benchmark.split.test)),
                "held_out_sample_count": len(cached_rows),
            }
            if receipt_by_id.get(candidate.candidate_id) != expected_receipt:
                raise TuneSearchError(
                    f"cached held-out receipt for {candidate.candidate_id} does not replay"
                )
            continue
        if cached_rows:
            raise TuneSearchError(
                f"unreceipted partial held-out raw exists for {candidate.candidate_id}; "
                "refusing repeated inference"
            )
        runtime = _runtime_candidate(candidate, binary=binary, model=model)
        try:
            rows = run_candidate_sync(
                benchmark,
                runtime,
                split="test",
                repetitions=repetitions,
                limit=None,
                warmups=1,
                deadline=deadline,
            )
        except (BenchmarkEnvironmentError, RuntimeError, ValueError) as exc:
            failures.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "phase": "held-out",
                    "status": "failed-closed-execution",
                    "error_type": type(exc).__name__,
                }
            )
            plan["status"] = "failed-held-out-execution"
            persist()
            raise TuneSearchError(
                f"held-out finalist {candidate.candidate_id} failed closed"
            ) from exc
        row_errors = validate_candidate_records(
            candidate,
            rows,
            expected_split="test",
            expected_case_ids=benchmark.split.test,
            repetitions=repetitions,
        )
        if row_errors:
            plan["status"] = "failed-held-out-incomplete"
            persist()
            raise TuneSearchError(
                f"held-out finalist {candidate.candidate_id} is incomplete: "
                + "; ".join(row_errors)
            )
        result = candidate_result_from_records(rows)
        decision = evaluate_gate(result, baseline, gate)
        receipt = {
            "candidate": candidate.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "gate_passed": decision.passed,
            "gate_reasons": list(decision.reasons),
            "held_out_case_count": len(set(benchmark.split.test)),
            "held_out_sample_count": len(rows),
        }
        held_out_receipts.append(receipt)
        receipt_by_id[candidate.candidate_id] = receipt
        plan["admitted_finalists"] = [
            finalist.model_dump(mode="json")
            for finalist in scheduled
            if finalist.candidate_id in receipt_by_id
        ]
        persist()

    selected_a3_candidate_id = next(
        (
            candidate.candidate_id
            for candidate in scheduled
            if receipt_by_id[candidate.candidate_id]["gate_passed"] is True
        ),
        None,
    )
    plan["selected_a3_candidate_id"] = selected_a3_candidate_id
    plan["status"] = (
        "complete" if selected_a3_candidate_id else "complete-no-held-out-feasible-finalist"
    )
    persist()
    return plan


__all__ = ["TuneSearchError", "run_bounded_tune"]
