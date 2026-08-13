from __future__ import annotations

import json

import pytest
from click import unstyle
from typer.testing import CliRunner

import a64pilot.optimize.tune as tune_module
from a64pilot.benchmark.plan import thread_candidates
from a64pilot.cli import app
from a64pilot.hardware.detect import SystemInfo
from a64pilot.hardware.topology import AffinityCandidate, CoreInfo, Topology
from a64pilot.optimize.candidates import bounded_candidate_subset, generate_candidates
from a64pilot.optimize.search import (
    candidate_result_from_records,
    rank_calibration_candidates,
    select_frozen_deployment,
)
from a64pilot.schemas import BenchmarkRecord, CandidateResult
from a64pilot.settings import QualityGateConfig


def record(
    candidate_id: str,
    case_id: str,
    *,
    split: str = "calibration",
    latency: float = 100.0,
    quality: float = 100.0,
    safety: float = 100.0,
    schema_valid: bool = True,
) -> BenchmarkRecord:
    return BenchmarkRecord(
        run_id=f"{candidate_id}-{case_id}",
        candidate_id=candidate_id,
        stage="tuned",
        case_id=case_id,
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


def test_bounded_subset_covers_host_thread_choices_before_depth() -> None:
    generated = generate_candidates(allowed_cores=16, physical_cores=8, quick=True)
    selected = bounded_candidate_subset(generated, 6)
    assert len(selected) == 6
    assert {candidate.threads for candidate in selected[:4]} == {1, 2, 4, 8}
    assert len({candidate.candidate_id for candidate in selected}) == 6
    assert all(candidate.quantization == "Q4_0" for candidate in selected)
    assert all(candidate.parallel == 1 and candidate.context == 2048 for candidate in generated)


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
        schema_version="1.0",
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
    calls: list[tuple[str, str, int | None]] = []

    def fake_run(benchmark, candidate, **options):
        split = str(options["split"])
        limit = options.get("limit")
        calls.append((candidate.candidate_id, split, limit))
        case_ids = Split.calibration[: int(limit)] if split == "calibration" else Split.test
        latency = 120.0 / candidate.threads + candidate.batch / 1000
        return [
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
    persisted = json.loads((tmp_path / "artifacts/search-plan.json").read_text())
    assert persisted["budget"]["candidate_space"] > persisted["budget"]["max_candidates"]
    assert all(row["held_out_case_count"] == 20 for row in persisted["held_out_results"])


def test_bounded_tune_records_failed_candidate_and_continues(
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
        schema_version="1.0",
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
    plan = tune_module.run_bounded_tune(
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
    assert plan["status"] == "complete"
    assert len(plan["candidate_failures"]) == 1
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
