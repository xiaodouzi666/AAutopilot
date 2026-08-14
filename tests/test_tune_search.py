from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest
from click import unstyle
from typer.testing import CliRunner

import a64pilot.optimize.replay as replay_module
import a64pilot.optimize.tune as tune_module
from a64pilot.benchmark.plan import BenchmarkCandidate, thread_candidates
from a64pilot.benchmark.runner import BenchmarkEnvironmentError, _chat_with_deadline
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.cli import app
from a64pilot.hardware.detect import SystemInfo
from a64pilot.hardware.topology import AffinityCandidate, CoreInfo, Topology
from a64pilot.optimize.candidates import (
    bounded_candidate_subset,
    generate_candidates,
    staged_candidate_subset,
)
from a64pilot.optimize.quality_gate import evaluate_gate
from a64pilot.optimize.replay import compute_search_fingerprint, verify_search_plan
from a64pilot.optimize.search import (
    candidate_result_from_records,
    rank_calibration_candidates,
    select_frozen_deployment,
)
from a64pilot.schemas import SYSTEM_INFO_SCHEMA_VERSION, BenchmarkRecord, CandidateResult
from a64pilot.settings import QualityGateConfig


def patch_probe_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tune_module, "load_performance_probes", lambda path: object())
    monkeypatch.setattr(
        tune_module,
        "rank_micro_threads",
        lambda evidence: [
            {
                "threads": 4,
                "tg64_tokens_per_second": 20.0,
                "pp128_tokens_per_second": 100.0,
            },
            {
                "threads": 2,
                "tg64_tokens_per_second": 15.0,
                "pp128_tokens_per_second": 80.0,
            },
        ],
    )
    monkeypatch.setattr(
        tune_module,
        "performance_probe_semantic_sha256",
        lambda evidence: "f" * 64,
    )


def record(
    candidate_id: str,
    case_id: str,
    *,
    run_id: str | None = None,
    repetition: int = 0,
    split: str = "calibration",
    latency: float = 100.0,
    quality: float = 100.0,
    safety: float = 100.0,
    schema_valid: bool = True,
) -> BenchmarkRecord:
    return BenchmarkRecord(
        run_id=run_id or f"{candidate_id}-{case_id}",
        candidate_id=candidate_id,
        stage="tuned",
        case_id=case_id,
        repetition=repetition,
        split=split,
        backend="kleidiai",
        model_role="strong",
        model_file_sha256="a" * 64,
        quantization="Q4_0",
        threads=4,
        batch=128,
        ubatch=64,
        parallel=1,
        cpu_only_verified=True,
        kleidiai_verified=True,
        start_ns=1,
        first_token_ns=2,
        end_ns=3,
        ttft_ms=1,
        e2e_ms=latency,
        completion_tokens=10,
        generation_tok_s=10,
        peak_rss_mb=100,
        schema_valid=schema_valid,
        quality_score=quality,
        safety_score=safety,
        command=["llama-server", "--device", "none", "--n-gpu-layers", "0"],
    )


def test_thread_candidates_never_exceed_cpuset() -> None:
    assert thread_candidates(allowed_cores=4, physical_cores=32) == [1, 2, 4]
    with pytest.raises(ValueError, match="positive"):
        thread_candidates(0, 8)


@pytest.mark.asyncio
async def test_single_inference_is_cancelled_at_hard_deadline() -> None:
    class SlowClient:
        async def chat_completion(self, **request):
            await asyncio.sleep(1)

    with pytest.raises(BenchmarkEnvironmentError, match="exceeded runtime budget"):
        await _chat_with_deadline(
            SlowClient(),
            deadline=time.monotonic() + 0.01,
            model="test",
        )


def test_bounded_subset_covers_host_thread_choices_before_depth() -> None:
    generated = generate_candidates(allowed_cores=16, physical_cores=8, quick=True)
    selected = bounded_candidate_subset(generated, 6)
    assert len(selected) == 6
    assert {candidate.threads for candidate in selected[:4]} == {1, 2, 4, 8}
    assert len({candidate.candidate_id for candidate in selected}) == 6
    assert all(candidate.quantization == "Q4_0" for candidate in selected)
    assert all(candidate.parallel == 1 and candidate.context == 2048 for candidate in generated)


def test_staged_subset_is_constrained_and_ordered_by_micro_ranking() -> None:
    generated = generate_candidates(allowed_cores=8, physical_cores=8, quick=True)
    selected = staged_candidate_subset(
        generated,
        micro_ranking=[
            {
                "threads": 8,
                "tg64_tokens_per_second": 30.0,
                "pp128_tokens_per_second": 120.0,
            },
            {
                "threads": 4,
                "tg64_tokens_per_second": 20.0,
                "pp128_tokens_per_second": 100.0,
            },
        ],
        limit=4,
        quick=True,
    )
    assert [candidate.threads for candidate in selected[:2]] == [8, 4]
    assert {candidate.threads for candidate in selected} <= {4, 8}
    assert {candidate.parallel for candidate in selected} == {1}


def test_calibration_ranking_is_complete_quality_gated_and_deterministic() -> None:
    plans = bounded_candidate_subset(
        generate_candidates(allowed_cores=4, physical_cores=4, quick=True), 3
    )
    rows = {
        plans[0].candidate_id: [
            record(plans[0].candidate_id, "incident-001", latency=90),
            record(plans[0].candidate_id, "incident-002", latency=100),
        ],
        plans[1].candidate_id: [
            record(plans[1].candidate_id, "incident-001", latency=50, quality=97),
            record(plans[1].candidate_id, "incident-002", latency=55, quality=97),
        ],
        plans[2].candidate_id: [
            record(plans[2].candidate_id, "incident-001", latency=70),
            record(plans[2].candidate_id, "incident-002", latency=75),
        ],
    }
    evaluations, ranked = rank_calibration_candidates(
        plans,
        rows,
        expected_samples=2,
        max_quality_drop=1,
        minimum_safety_score=100,
        maximum_schema_failures=0,
    )
    assert ranked == [plans[2].candidate_id, plans[0].candidate_id]
    rejected = next(item for item in evaluations if item.candidate_id == plans[1].candidate_id)
    assert not rejected.feasible
    assert any("quality" in reason for reason in rejected.reasons)


def test_candidate_result_requires_formal_test_rows() -> None:
    test_rows = [record("finalist", "incident-001", split="test")]
    result = candidate_result_from_records(test_rows)
    assert result.candidate_id == "finalist"
    assert result.config["quantization"] == "Q4_0"
    with pytest.raises(ValueError, match="formal-test"):
        candidate_result_from_records([record("calibration", "incident-001", split="calibration")])


def test_candidate_result_canonicalizes_shuffled_formal_rows_before_run_ids() -> None:
    case_1_rep_0 = record(
        "finalist",
        "incident-001",
        run_id="f" * 32,
        split="test",
    )
    case_2_rep_0 = record(
        "finalist",
        "incident-002",
        run_id="0" * 32,
        split="test",
    )
    case_1_rep_1 = record(
        "finalist",
        "incident-001",
        run_id="1" * 32,
        repetition=1,
        split="test",
    )

    result = candidate_result_from_records([case_1_rep_1, case_2_rep_0, case_1_rep_0])

    assert result.source_run_ids == ["f" * 32, "0" * 32, "1" * 32]


def test_benchmark_tune_command_is_exposed() -> None:
    result = CliRunner().invoke(
        app,
        ["benchmark", "tune", "--help"],
        terminal_width=160,
        color=False,
    )
    assert result.exit_code == 0
    help_text = unstyle(result.stdout)
    assert "topology-derived" in help_text
    assert "--max-candidates" in help_text
    assert "--calibration-cases" in help_text


@pytest.mark.parametrize("max_minutes", [float("nan"), float("inf"), 0.0, -1.0])
def test_bounded_tune_rejects_non_finite_or_non_positive_budget(
    tmp_path, max_minutes: float
) -> None:
    class Split:
        calibration = ("cal-0", "cal-1")
        test = ("test-0",)

    class Benchmark:
        split = Split()

    with pytest.raises(ValueError, match="finite and positive"):
        tune_module.run_bounded_tune(
            benchmark=Benchmark(),  # type: ignore[arg-type]
            system_info=None,  # type: ignore[arg-type]
            binary=tmp_path / "llama-server",
            model=tmp_path / "model.gguf",
            gate=QualityGateConfig(),
            max_candidates=2,
            calibration_cases=1,
            finalists=1,
            max_minutes=max_minutes,
            artifacts_dir=tmp_path / "artifacts",
        )


def test_bounded_tune_records_plan_and_validates_full_finalists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology = Topology(
        logical_cpus=8,
        physical_cores=4,
        allowed_cpus=tuple(range(8)),
        cores=tuple(CoreInfo(cpu_id=cpu) for cpu in range(8)),
        affinity_candidates=(AffinityCandidate("all_allowed", tuple(range(8))),),
    )
    info = SystemInfo(
        schema_version=SYSTEM_INFO_SCHEMA_VERSION,
        captured_at="2026-08-14T00:00:00+00:00",
        architecture="aarch64",
        architecture_raw="aarch64",
        operating_system="Linux",
        kernel="test",
        python_version="3.11",
        arm64=True,
        real_benchmark_eligible=True,
        cpu_features={},
        topology=topology,
        memory_total_bytes=16 * 1024**3,
        filesystem_free_bytes=20 * 1024**3,
        tool_versions={},
    )

    class Split:
        calibration = tuple(f"cal-{index}" for index in range(40))
        test = tuple(f"test-{index}" for index in range(20))

    class Benchmark:
        split = Split()

    baseline = CandidateResult(
        candidate_id="a1-generic-q4-0",
        stage="baseline",
        backend="generic",
        model="strong",
        quality_score=100,
        safety_score=100,
        schema_failures=0,
        p95_latency_ms=100,
        requests_per_second=10,
        peak_rss_mb=100,
        source_run_ids=["baseline"],
    )
    monkeypatch.setattr(tune_module, "_formal_baseline", lambda *args, **kwargs: baseline)
    patch_probe_stage(monkeypatch)
    calls: list[tuple[str, str, int | None, float]] = []

    def fake_run(benchmark, candidate, **options):
        split = str(options["split"])
        limit = options.get("limit")
        calls.append((candidate.candidate_id, split, limit, float(options["deadline"])))
        case_ids = Split.calibration[: int(limit)] if split == "calibration" else Split.test
        latency = 120.0 / candidate.threads + candidate.batch / 1000
        rows = [
            record(
                candidate.candidate_id,
                case_id,
                split=split,
                latency=latency,
            ).model_copy(
                update={
                    "run_id": f"{candidate.candidate_id}-{split}-{index}",
                    "threads": candidate.threads,
                    "batch": candidate.batch,
                    "ubatch": candidate.ubatch,
                    "parallel": candidate.parallel,
                }
            )
            for index, case_id in enumerate(case_ids)
        ]
        ArtifactStore(tmp_path / "artifacts/raw").import_records(rows)
        return rows

    monkeypatch.setattr(tune_module, "run_candidate_sync", fake_run)
    plan = tune_module.run_bounded_tune(
        benchmark=Benchmark(),
        system_info=info,
        binary=tmp_path / "build/llama-kleidiai/bin/llama-server",
        model=tmp_path / "models/qwen-q4_0.gguf",
        gate=QualityGateConfig(),
        max_candidates=4,
        calibration_cases=4,
        finalists=2,
        repetitions=1,
        max_minutes=45,
        quick=True,
        artifacts_dir=tmp_path / "artifacts",
    )
    assert plan["status"] == "complete"
    assert plan["selected_a3_candidate_id"] is not None
    assert len([call for call in calls if call[1] == "calibration"]) == 4
    assert len([call for call in calls if call[1] == "test"]) == 2
    assert all(call[2] is None for call in calls if call[1] == "test")
    assert all(call[3] > 0 for call in calls)
    persisted = json.loads((tmp_path / "artifacts/search-plan.json").read_text())
    assert persisted["budget"]["candidate_space"] > persisted["budget"]["max_candidates"]
    assert all(row["held_out_case_count"] == 20 for row in persisted["held_out_results"])
    assert persisted["tuned_parallel_plan"] == [1]
    assert persisted["concurrency_probe_plan"] == [1, 2]
    assert persisted["admitted_finalists"] == persisted["scheduled_finalists"]

    first_call_count = len(calls)
    replayed_raw_counts: list[int] = []

    def fake_verify(plan, rows, **options):
        replayed_raw_counts.append(len(rows))
        return []

    monkeypatch.setattr(tune_module, "verify_search_plan", fake_verify)
    resumed = tune_module.run_bounded_tune(
        benchmark=Benchmark(),
        system_info=info,
        binary=tmp_path / "build/llama-kleidiai/bin/llama-server",
        model=tmp_path / "models/qwen-q4_0.gguf",
        gate=QualityGateConfig(),
        max_candidates=4,
        calibration_cases=4,
        finalists=2,
        repetitions=1,
        max_minutes=45,
        quick=True,
        artifacts_dir=tmp_path / "artifacts",
    )
    assert resumed == plan
    assert len(calls) == first_call_count
    assert replayed_raw_counts == [4 * 4 + 2 * 20]


def test_bounded_tune_fails_closed_without_admitting_incomplete_candidate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology = Topology(
        logical_cpus=4,
        physical_cores=4,
        allowed_cpus=tuple(range(4)),
        cores=tuple(CoreInfo(cpu_id=cpu) for cpu in range(4)),
        affinity_candidates=(AffinityCandidate("all_allowed", tuple(range(4))),),
    )
    info = SystemInfo(
        schema_version=SYSTEM_INFO_SCHEMA_VERSION,
        captured_at="2026-08-14T00:00:00+00:00",
        architecture="aarch64",
        architecture_raw="aarch64",
        operating_system="Linux",
        kernel="test",
        python_version="3.11",
        arm64=True,
        real_benchmark_eligible=True,
        cpu_features={},
        topology=topology,
        memory_total_bytes=16 * 1024**3,
        filesystem_free_bytes=20 * 1024**3,
        tool_versions={},
    )

    class Split:
        calibration = tuple(f"cal-{index}" for index in range(4))
        test = tuple(f"test-{index}" for index in range(20))

    class Benchmark:
        split = Split()

    baseline = candidate_result("a1-generic-q4-0", stage="baseline", latency=120, backend="generic")
    monkeypatch.setattr(tune_module, "_formal_baseline", lambda *args, **kwargs: baseline)
    patch_probe_stage(monkeypatch)
    calls = 0

    def fake_run(benchmark, candidate, **options):
        nonlocal calls
        calls += 1
        split = str(options["split"])
        if calls == 1:
            raise RuntimeError("candidate-only failure")
        limit = options.get("limit")
        case_ids = Split.calibration[: int(limit)] if split == "calibration" else Split.test
        return [
            record(candidate.candidate_id, case_id, split=split).model_copy(
                update={
                    "run_id": f"{candidate.candidate_id}-{split}-{index}",
                    "threads": candidate.threads,
                    "batch": candidate.batch,
                    "ubatch": candidate.ubatch,
                    "parallel": candidate.parallel,
                    "context": candidate.context,
                }
            )
            for index, case_id in enumerate(case_ids)
        ]

    monkeypatch.setattr(tune_module, "run_candidate_sync", fake_run)
    with pytest.raises(tune_module.TuneSearchError, match="failed closed"):
        tune_module.run_bounded_tune(
            benchmark=Benchmark(),
            system_info=info,
            binary=tmp_path / "build/llama-kleidiai/bin/llama-server",
            model=tmp_path / "models/qwen-q4_0.gguf",
            gate=QualityGateConfig(),
            max_candidates=3,
            calibration_cases=4,
            finalists=1,
            artifacts_dir=tmp_path / "artifacts",
        )
    plan = json.loads((tmp_path / "artifacts/search-plan.json").read_text())
    assert plan["status"] == "failed-calibration-execution"
    assert plan["admitted_finalists"] == []
    assert plan["candidate_failures"][0]["phase"] == "calibration"
    assert plan["candidate_failures"][0]["error_type"] == "RuntimeError"


def candidate_result(
    candidate_id: str,
    *,
    stage: str,
    latency: float,
    backend: str = "kleidiai",
) -> CandidateResult:
    return CandidateResult(
        candidate_id=candidate_id,
        stage=stage,
        backend=backend,
        model="strong",
        quality_score=100,
        safety_score=100,
        schema_failures=0,
        p95_latency_ms=latency,
        requests_per_second=1000 / latency,
        peak_rss_mb=100,
        source_run_ids=[candidate_id],
        config={"quantization": "Q4_0"},
    )


@pytest.mark.parametrize("repetitions", [1, 2, 3])
def test_strict_search_replay_recomputes_raw_ranking_gate_and_selection(
    repetitions: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = QualityGateConfig()
    calibration_ids = ("cal-0", "cal-1")
    test_ids = ("test-0", "test-1")
    candidates = [
        BenchmarkCandidate(
            candidate_id="tuned-fast",
            stage="tuned",
            backend="kleidiai",
            model_role="strong",
            quantization="Q4_0",
            threads=4,
            batch=128,
            ubatch=64,
            parallel=1,
            context=2048,
        ),
        BenchmarkCandidate(
            candidate_id="tuned-slow",
            stage="tuned",
            backend="kleidiai",
            model_role="strong",
            quantization="Q4_0",
            threads=2,
            batch=128,
            ubatch=64,
            parallel=1,
            context=2048,
        ),
    ]
    baseline_rows = [
        record("a1-generic-q4-0", case_id, split="test", latency=150).model_copy(
            update={
                "run_id": f"baseline-{case_id}-r{repetition}",
                "repetition": repetition,
                "stage": "baseline",
                "backend": "generic",
                "kleidiai_verified": False,
            }
        )
        for repetition in range(repetitions)
        for case_id in test_ids
    ]
    calibration_rows = {
        candidate.candidate_id: [
            record(
                candidate.candidate_id,
                case_id,
                latency=60 if candidate.candidate_id == "tuned-fast" else 90,
            ).model_copy(
                update={
                    "run_id": f"{candidate.candidate_id}-{case_id}-r{repetition}",
                    "repetition": repetition,
                    "threads": candidate.threads,
                }
            )
            for repetition in range(repetitions)
            for case_id in calibration_ids
        ]
        for candidate in candidates
    }
    held_out_rows = {
        candidate.candidate_id: [
            record(
                candidate.candidate_id,
                case_id,
                split="test",
                latency=70 if candidate.candidate_id == "tuned-fast" else 100,
            ).model_copy(
                update={
                    "run_id": f"{candidate.candidate_id}-{case_id}-r{repetition}",
                    "repetition": repetition,
                    "threads": candidate.threads,
                }
            )
            for repetition in range(repetitions)
            for case_id in test_ids
        ]
        for candidate in candidates
    }
    held_out_rows["tuned-slow"][-1] = held_out_rows["tuned-slow"][-1].model_copy(
        update={"end_ns": 2_000_000_001}
    )
    evaluations, ranked = rank_calibration_candidates(
        candidates,
        calibration_rows,
        expected_samples=len(calibration_ids) * repetitions,
        max_quality_drop=gate.max_absolute_quality_drop,
        minimum_safety_score=gate.minimum_safety_score,
        maximum_schema_failures=gate.maximum_schema_failures,
        expected_case_ids=calibration_ids,
        repetitions=repetitions,
    )
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    scheduled = [by_id[candidate_id] for candidate_id in ranked]
    baseline = candidate_result_from_records(baseline_rows)
    held_out_receipts = []
    for candidate in scheduled:
        result = candidate_result_from_records(held_out_rows[candidate.candidate_id])
        decision = evaluate_gate(result, baseline, gate)
        held_out_receipts.append(
            {
                "candidate": candidate.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "gate_passed": decision.passed,
                "gate_reasons": list(decision.reasons),
                "held_out_case_count": len(test_ids),
                "held_out_sample_count": len(held_out_rows[candidate.candidate_id]),
            }
        )
    micro_ranking = [
        {
            "threads": 4,
            "tg64_tokens_per_second": 20.0,
            "pp128_tokens_per_second": 100.0,
        },
        {
            "threads": 2,
            "tg64_tokens_per_second": 15.0,
            "pp128_tokens_per_second": 80.0,
        },
    ]
    probe_hash = "f" * 64
    plan = {
        "schema_version": "2.0.0",
        "generated_at": "2026-08-14T00:00:00Z",
        "status": "complete",
        "generator": "a64pilot.optimize.candidates.staged_candidate_subset",
        "selection_policy": "frozen test policy",
        "target": {
            "architecture": "aarch64",
            "logical_cpus": 4,
            "physical_cores": 4,
            "allowed_cpus": [0, 1, 2, 3],
        },
        "budget": {
            "quick": True,
            "candidate_space": 2,
            "max_candidates": 2,
            "calibration_cases_per_candidate": 2,
            "finalists": 2,
            "repetitions": repetitions,
            "max_minutes": 45.0,
        },
        "inputs": {
            "binary_sha256": "b" * 64,
            "model_sha256": "a" * 64,
            "cases_sha256": "c" * 64,
            "split_sha256": "d" * 64,
            "baseline": baseline.model_dump(mode="json"),
        },
        "probe_semantic_sha256": probe_hash,
        "quality_gate": gate.model_dump(mode="json"),
        "micro_ranking": micro_ranking,
        "tuned_parallel_plan": [1],
        "concurrency_probe_plan": [1, 2],
        "calibration_candidates": [item.model_dump(mode="json") for item in candidates],
        "calibration_receipts": [
            {
                "candidate_id": candidate.candidate_id,
                "source_run_ids": sorted(
                    row.run_id for row in calibration_rows[candidate.candidate_id]
                ),
                "sample_count": len(calibration_rows[candidate.candidate_id]),
            }
            for candidate in candidates
        ],
        "calibration_results": [item.to_dict() for item in evaluations],
        "ranked_candidate_ids": ranked,
        "scheduled_finalists": [item.model_dump(mode="json") for item in scheduled],
        "admitted_finalists": [item.model_dump(mode="json") for item in scheduled],
        "held_out_results": held_out_receipts,
        "candidate_failures": [],
        "selected_a3_candidate_id": ranked[0],
        "elapsed_seconds": 10.0,
    }
    plan["search_fingerprint"] = compute_search_fingerprint(plan)
    all_rows = baseline_rows + [
        row
        for candidate in candidates
        for row in calibration_rows[candidate.candidate_id] + held_out_rows[candidate.candidate_id]
    ]
    monkeypatch.setattr(
        replay_module, "performance_probe_semantic_sha256", lambda evidence: probe_hash
    )
    monkeypatch.setattr(replay_module, "rank_micro_threads", lambda evidence: micro_ranking)
    monkeypatch.setattr(replay_module, "generate_candidates", lambda **options: candidates)
    monkeypatch.setattr(
        replay_module,
        "staged_candidate_subset",
        lambda generated, **options: list(generated),
    )
    replay_options = {
        "probes": object(),
        "probe_semantic_sha256": probe_hash,
        "architecture": "aarch64",
        "logical_cpus": 4,
        "physical_cores": 4,
        "allowed_cpus": [0, 1, 2, 3],
        "calibration_case_ids": calibration_ids,
        "test_case_ids": test_ids,
        "gate": gate,
        "binary_sha256": "b" * 64,
        "cases_sha256": "c" * 64,
        "split_sha256": "d" * 64,
    }
    assert verify_search_plan(plan, all_rows, **replay_options) == []

    ranking_tamper = copy.deepcopy(plan)
    ranking_tamper["ranked_candidate_ids"] = list(reversed(ranked))
    assert any(
        "ranking does not replay" in error
        for error in verify_search_plan(ranking_tamper, all_rows, **replay_options)
    )
    held_out_tamper = copy.deepcopy(plan)
    held_out_tamper["held_out_results"][0]["gate_passed"] = False
    held_out_tamper["selected_a3_candidate_id"] = ranked[1]
    assert any(
        "held-out receipt does not replay" in error
        for error in verify_search_plan(held_out_tamper, all_rows, **replay_options)
    )
    input_tamper = copy.deepcopy(plan)
    input_tamper["inputs"]["binary_sha256"] = "0" * 64
    input_tamper["search_fingerprint"] = compute_search_fingerprint(input_tamper)
    assert any(
        "inputs do not replay" in error
        for error in verify_search_plan(input_tamper, all_rows, **replay_options)
    )
    budget_tamper = copy.deepcopy(plan)
    budget_tamper["elapsed_seconds"] = 45 * 60 + 0.001
    assert any(
        "elapsed time exceeds" in error
        for error in verify_search_plan(budget_tamper, all_rows, **replay_options)
    )
    elapsed_tamper = copy.deepcopy(plan)
    elapsed_tamper["elapsed_seconds"] = 0.5
    assert any(
        "under-reports" in error
        for error in verify_search_plan(elapsed_tamper, all_rows, **replay_options)
    )


def test_frozen_selection_cannot_be_overridden_by_faster_held_out_candidate() -> None:
    baseline = candidate_result("a1-generic-q4-0", stage="baseline", latency=120, backend="generic")
    a2 = candidate_result("a2-kleidiai-q4-0", stage="kleidiai", latency=100)
    frozen = candidate_result("frozen-from-calibration", stage="tuned", latency=90)
    test_faster = candidate_result("test-only-faster", stage="tuned", latency=20)
    search_plan = {
        "status": "complete",
        "ranked_candidate_ids": ["frozen-from-calibration", "test-only-faster"],
        "admitted_finalists": [
            {"candidate_id": "frozen-from-calibration"},
            {"candidate_id": "test-only-faster"},
        ],
        "held_out_results": [
            {
                "candidate": {"candidate_id": "frozen-from-calibration"},
                "gate_passed": True,
            },
            {
                "candidate": {"candidate_id": "test-only-faster"},
                "gate_passed": True,
            },
        ],
        "selected_a3_candidate_id": "frozen-from-calibration",
    }
    selection = select_frozen_deployment(
        {
            a2.candidate_id: a2,
            frozen.candidate_id: frozen,
            test_faster.candidate_id: test_faster,
        },
        baseline,
        QualityGateConfig(),
        search_plan=search_plan,
    )
    assert selection.selected.candidate_id == "frozen-from-calibration"
    assert selection.basis == "frozen_calibration_finalist"


def test_frozen_selection_uses_fixed_a2_when_search_has_no_pass() -> None:
    baseline = candidate_result("a1-generic-q4-0", stage="baseline", latency=120, backend="generic")
    a2 = candidate_result("a2-kleidiai-q4-0", stage="kleidiai", latency=100)
    unselected_fast = candidate_result("unselected-fast-a3", stage="tuned", latency=10)
    selection = select_frozen_deployment(
        {a2.candidate_id: a2, unselected_fast.candidate_id: unselected_fast},
        baseline,
        QualityGateConfig(),
        search_plan={
            "status": "complete-no-held-out-feasible-finalist",
            "selected_a3_candidate_id": None,
        },
    )
    assert selection.selected.candidate_id == "a2-kleidiai-q4-0"
    assert selection.basis == "fixed_a2_strong_fallback"
