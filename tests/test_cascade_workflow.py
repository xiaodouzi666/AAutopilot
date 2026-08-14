from __future__ import annotations

import json
from pathlib import Path

import pytest

import a64pilot.benchmark.cascade as cascade_module
from a64pilot.agent.schema import IncidentCase, ToolName
from a64pilot.benchmark.cascade import (
    CascadeComponentEvidence,
    CascadeRuntimePlan,
    CascadeWorkflowError,
    MeasuredOutputCollection,
    evaluate_held_out,
    freeze_calibration,
    load_frozen_calibration,
    preflight_held_out_evaluation,
    verify_cascade_evidence,
)
from a64pilot.benchmark.quality import load_cases, load_split

ROOT = Path(__file__).resolve().parents[1]


def _tool_arguments(name: ToolName) -> dict[str, object]:
    return {
        ToolName.INSPECT_SERVICE: {"service": "fixture-service"},
        ToolName.READ_LOGS: {"service": "fixture-service", "limit": 20},
        ToolName.CHECK_DISK: {"mount": "/"},
        ToolName.CHECK_MEMORY: {"scope": "node"},
        ToolName.CHECK_NETWORK: {"target": "fixture-service", "port": 443},
        ToolName.ESCALATE: {"reason": "The supplied synthetic evidence is ambiguous."},
    }[name]


def _perfect_output(case: IncidentCase) -> dict[str, object]:
    tools = list(case.required_tools)
    if case.expected_escalation and ToolName.ESCALATE not in tools:
        tools.append(ToolName.ESCALATE)
    if not tools:
        tools.append(ToolName.ESCALATE if case.expected_escalation else ToolName.INSPECT_SERVICE)
    return {
        "summary": "The synthetic incident was triaged from the supplied observations.",
        "severity": case.expected_severity.value,
        "diagnosis": case.expected_diagnosis.value,
        "hypotheses": [
            {
                "cause": f"The evidence is consistent with {case.expected_diagnosis.value}.",
                "evidence": [case.incident],
                "confidence": 0.9,
            }
        ],
        "tool_calls": [{"name": tool.value, "arguments": _tool_arguments(tool)} for tool in tools],
        "safe_next_action": "Collect the listed read-only evidence for human review.",
        "needs_escalation": case.expected_escalation,
    }


def _plan() -> CascadeRuntimePlan:
    return CascadeRuntimePlan(
        binary=Path("build/llama-kleidiai/bin/llama-server"),
        cmake_cache=Path("build/llama-kleidiai/CMakeCache.txt"),
        weak_model=Path("models/weak.gguf"),
        strong_model=Path("models/strong.gguf"),
        threads=4,
        batch=128,
        ubatch=64,
        parallel=1,
        context=2048,
    )


def _collection(
    case_ids: tuple[str, ...], *, include_weak: bool = True
) -> CascadeComponentEvidence:
    cases = {case.case_id: case for case in load_cases(ROOT / "demo/cases.jsonl")}
    outputs = {case_id: _perfect_output(cases[case_id]) for case_id in case_ids}
    strong = MeasuredOutputCollection(
        outputs=dict(outputs),
        source_run_ids=tuple(f"{index + 1:032x}" for index in range(len(case_ids))),
        model_file_sha256="b" * 64,
    )
    weak = None
    if include_weak:
        weak_outputs = dict(outputs)
        simple_id = next(
            case_id for case_id in case_ids if cases[case_id].category.value == "simple"
        )
        # A genuine weak model may emit malformed JSON.  The replay must count the
        # validator-driven weak->strong route while preserving the strong result.
        weak_outputs[simple_id] = {"summary": "invalid weak response"}
        weak = MeasuredOutputCollection(
            outputs=weak_outputs,
            source_run_ids=tuple(f"{index + 101:032x}" for index in range(len(case_ids))),
            model_file_sha256="a" * 64,
        )
    return CascadeComponentEvidence(session_dir=Path("measured-session"), weak=weak, strong=strong)


def test_full_40_20_freeze_and_held_out_route_accounting(tmp_path: Path) -> None:
    split = load_split(ROOT / "demo/split.json")
    policy_path = tmp_path / "a4-frozen-policy.json"
    results_path = tmp_path / "quality-results.json"
    status_path = tmp_path / "cascade-status.json"
    frozen = freeze_calibration(
        _plan(),
        _collection(split.calibration),
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        policy_path=policy_path,
    )

    assert len(frozen["policy"]["calibration_case_ids"]) == 40
    assert frozen["policy"]["fallback_strong_only"] is False
    assert frozen["freeze_id"]
    _, policy, restored_plan, _ = load_frozen_calibration(
        policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
    )
    assert policy.policy_id == frozen["policy"]["policy_id"]
    assert restored_plan == _plan()

    result = evaluate_held_out(
        _collection(split.test),
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )

    routes = result["held_out"]["route_counts"]
    assert set(routes) == {"weak", "strong", "weak_then_strong"}
    assert sum(routes.values()) == 20
    assert sum(result["held_out"]["route_shares"].values()) == pytest.approx(100.0)
    assert routes["weak_then_strong"] >= 1
    assert result["held_out"]["escalation_rate"] > 0
    assert result["held_out"]["gate"]["passed"] is True
    assert result["a4_admitted_by_quality_gate"] is True
    assert result["shipping_profile"] == "a3-strong-only"
    assert result["held_out"]["performance_claim_eligible"] is False
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == (
        "held-out-quality-accepted"
    )

    with pytest.raises(CascadeWorkflowError, match="already been evaluated"):
        evaluate_held_out(
            _collection(split.test),
            policy_path=policy_path,
            cases_path=ROOT / "demo/cases.jsonl",
            split_path=ROOT / "demo/split.json",
            results_path=results_path,
            status_path=status_path,
        )


def test_cascade_verifier_accepts_explicit_not_run_status(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "cascade-status.json").write_text(
        json.dumps({"status": "not-run"}), encoding="utf-8"
    )

    assert verify_cascade_evidence(artifacts) == []


def test_frozen_policy_rejects_content_tampering(tmp_path: Path) -> None:
    split = load_split(ROOT / "demo/split.json")
    policy_path = tmp_path / "a4-frozen-policy.json"
    freeze_calibration(
        _plan(),
        _collection(split.calibration),
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        policy_path=policy_path,
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["policy"]["threshold"] = 100.0
    policy_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CascadeWorkflowError, match="hash does not match"):
        load_frozen_calibration(
            policy_path,
            cases_path=ROOT / "demo/cases.jsonl",
            split_path=ROOT / "demo/split.json",
        )


def test_calibration_policy_is_write_once(tmp_path: Path) -> None:
    split = load_split(ROOT / "demo/split.json")
    policy_path = tmp_path / "a4-frozen-policy.json"
    evidence = _collection(split.calibration)
    freeze_calibration(
        _plan(),
        evidence,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        policy_path=policy_path,
    )
    with pytest.raises(CascadeWorkflowError, match="refusing to overwrite"):
        freeze_calibration(
            _plan(),
            evidence,
            cases_path=ROOT / "demo/cases.jsonl",
            split_path=ROOT / "demo/split.json",
            policy_path=policy_path,
        )


def test_held_out_preflight_recovers_only_missing_derivative_status(tmp_path: Path) -> None:
    split = load_split(ROOT / "demo/split.json")
    policy_path = tmp_path / "a4-frozen-policy.json"
    results_path = tmp_path / "quality-results.json"
    status_path = tmp_path / "cascade-status.json"
    freeze_calibration(
        _plan(),
        _collection(split.calibration),
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        policy_path=policy_path,
    )
    expected = evaluate_held_out(
        _collection(split.test),
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )
    status_path.unlink()

    existing, recovered = preflight_held_out_evaluation(
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )

    assert existing == expected
    assert recovered is True
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == (
        "held-out-quality-accepted"
    )


def test_held_out_preflight_refuses_status_only_half_commit(tmp_path: Path) -> None:
    status_path = tmp_path / "cascade-status.json"
    status_path.write_text(json.dumps({"status": "held-out-quality-accepted"}), encoding="utf-8")

    with pytest.raises(CascadeWorkflowError, match="refusing to repeat held-out inference"):
        preflight_held_out_evaluation(
            results_path=tmp_path / "quality-results.json",
            status_path=status_path,
        )


def _write_replay_session(session: Path, evidence: CascadeComponentEvidence) -> None:
    collections = [evidence.strong]
    if evidence.weak is not None:
        collections.append(evidence.weak)
    for collection in collections:
        for run_id in collection.source_run_ids:
            run_dir = session / "raw" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "requests.jsonl").write_text("{}\n", encoding="utf-8")


def test_cascade_verifier_recomputes_results_from_nested_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    split = load_split(ROOT / "demo/split.json")
    artifacts = tmp_path / "artifacts"
    calibration_session = artifacts / "a4" / "runs" / "calibration-test"
    held_out_session = artifacts / "a4" / "runs" / "held-out-test"
    calibration_source = _collection(split.calibration)
    calibration_source = CascadeComponentEvidence(
        session_dir=calibration_session,
        weak=calibration_source.weak,
        strong=calibration_source.strong,
    )
    held_out_source = _collection(split.test)
    held_out_source = CascadeComponentEvidence(
        session_dir=held_out_session,
        weak=held_out_source.weak,
        strong=held_out_source.strong,
    )
    _write_replay_session(calibration_session, calibration_source)
    _write_replay_session(held_out_session, held_out_source)
    policy_path = artifacts / "a4-frozen-policy.json"
    results_path = artifacts / "quality-results.json"
    status_path = artifacts / "cascade-status.json"
    freeze_calibration(
        _plan(),
        calibration_source,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        policy_path=policy_path,
    )
    evaluate_held_out(
        held_out_source,
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )

    monkeypatch.setattr(
        cascade_module.BenchmarkRecord,
        "model_validate_json",
        lambda _value: object(),
    )

    def load_outputs(_store: object, _records: object, **kwargs: object) -> object:
        source = (
            calibration_source if kwargs["expected_split"] == "calibration" else held_out_source
        )
        return source.weak if kwargs["expected_role"] == "weak" else source.strong

    monkeypatch.setattr(cascade_module, "_load_measured_outputs", load_outputs)
    assert (
        verify_cascade_evidence(
            artifacts,
            cases_path=ROOT / "demo/cases.jsonl",
            split_path=ROOT / "demo/split.json",
        )
        == []
    )

    tampered = json.loads(results_path.read_text(encoding="utf-8"))
    tampered["held_out"]["route_counts"]["weak"] += 1
    results_path.write_text(json.dumps(tampered), encoding="utf-8")

    errors = verify_cascade_evidence(
        artifacts,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
    )
    assert "A4 quality results do not replay from nested held-out responses" in errors
