from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

import a64pilot.benchmark.cascade as cascade_module
from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import (
    IncidentCase,
    ToolName,
    triage_json_schema,
    triage_openai_response_format,
)
from a64pilot.benchmark.cascade import (
    CascadeComponentEvidence,
    CascadeRuntimePlan,
    CascadeWorkflowError,
    MeasuredOutputCollection,
    evaluate_held_out,
    freeze_calibration,
    load_frozen_calibration,
    preflight_held_out_evaluation,
    reserve_held_out_evaluation,
    verify_cascade_evidence,
)
from a64pilot.benchmark.quality import load_cases, load_split, score_case
from a64pilot.benchmark.runner import (
    REAL_BENCHMARK_MAX_TOKENS,
    REAL_BENCHMARK_SEED,
    BenchmarkEnvironmentError,
    RuntimeCandidate,
    _reviewed_model_proof,
)
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.build.verify_backend import verify_backend_log, verify_cpu_only
from a64pilot.models.checksum import sha256_file
from a64pilot.models.registry import get_model
from a64pilot.schemas import BenchmarkRecord

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


def test_held_out_reservation_blocks_retry_without_canonical_results(tmp_path: Path) -> None:
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
    status_path.write_text(json.dumps({"status": "not-run"}), encoding="utf-8")

    reservation = reserve_held_out_evaluation(
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )

    assert reservation["status"] == "held-out-in-progress"
    assert reservation["shipping_profile"] == "a3-strong-only"
    assert not results_path.exists()
    with pytest.raises(CascadeWorkflowError, match="refusing to repeat held-out inference"):
        preflight_held_out_evaluation(
            policy_path=policy_path,
            cases_path=ROOT / "demo/cases.jsonl",
            split_path=ROOT / "demo/split.json",
            results_path=results_path,
            status_path=status_path,
        )


def test_preflight_recovers_terminal_status_from_reservation_only_after_result(
    tmp_path: Path,
) -> None:
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
    status_path.write_text(json.dumps({"status": "not-run"}), encoding="utf-8")
    reservation = reserve_held_out_evaluation(
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )
    expected = evaluate_held_out(
        _collection(split.test),
        policy_path=policy_path,
        cases_path=ROOT / "demo/cases.jsonl",
        split_path=ROOT / "demo/split.json",
        results_path=results_path,
        status_path=status_path,
    )
    status_path.write_text(json.dumps(reservation), encoding="utf-8")

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


def test_reviewed_model_proof_rejects_registry_artifact_with_swapped_role() -> None:
    strong = get_model("strong-q4-0")
    candidate = RuntimeCandidate(
        candidate_id="a4-calibration-weak",
        stage="a4",
        backend="kleidiai",
        binary=Path("build/llama-kleidiai/bin/llama-server"),
        cmake_cache=Path("build/llama-kleidiai/CMakeCache.txt"),
        model=Path("models") / strong.expected_filename,
        model_role="weak",
        quantization=strong.quantization,
        threads=4,
    )

    with pytest.raises(BenchmarkEnvironmentError, match="declared weak role"):
        _reviewed_model_proof(candidate, strong.expected_sha256)


def _strict_nested_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ArtifactStore, BenchmarkRecord, RuntimeCandidate, IncidentCase, dict[str, str]]:
    case = next(
        case
        for case in load_cases(ROOT / "demo/cases.jsonl")
        if case.case_id in set(load_split(ROOT / "demo/split.json").test)
    )
    model = tmp_path / "weak.gguf"
    model.write_bytes(b"reviewed-model-placeholder")
    cache = tmp_path / "CMakeCache.txt"
    cache.write_text(
        (ROOT / "tests/fixtures/cmake-cpu-only.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"binary")
    candidate = RuntimeCandidate(
        candidate_id="a4-held-out-weak",
        stage="a4",
        backend="kleidiai",
        binary=binary,
        cmake_cache=cache,
        model=model,
        model_role="weak",
        quantization="Q4_0",
        threads=4,
        batch=128,
        ubatch=64,
        parallel=1,
        context=2048,
    )
    command = cascade_module._expected_component_command(candidate, "18180") + [
        "--metrics",
        "--no-webui",
    ]
    runtime_log = (ROOT / "tests/fixtures/llama-kleidiai.log").read_text(encoding="utf-8")
    backend_proof = verify_backend_log(runtime_log, "kleidiai", quantization="Q4_0")
    cpu_proof = verify_cpu_only(command, cmake_cache=cache.read_text(), runtime_log=runtime_log)
    output = json.dumps(_perfect_output(case))
    score = score_case(case, output)
    run_id = "1" * 32
    record = BenchmarkRecord(
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        stage="cascade",
        case_id=case.case_id,
        repetition=0,
        split="test",
        backend="kleidiai",
        model_role="weak",
        model_file_sha256=sha256_file(model),
        quantization="Q4_0",
        threads=4,
        batch=128,
        ubatch=64,
        parallel=1,
        context=2048,
        affinity=[],
        cpu_only_verified=True,
        kleidiai_verified=True,
        start_ns=1_000_000,
        first_token_ns=2_000_000,
        end_ns=3_000_000,
        ttft_ms=1.0,
        e2e_ms=2.0,
        prompt_tokens=10,
        completion_tokens=20,
        generation_tok_s=10.0,
        peak_rss_mb=100.0,
        route="weak",
        schema_valid=score.schema_valid,
        quality_score=score.quality_score,
        safety_score=score.safety_score,
        command=command,
        errors=list(score.issues),
    )
    dataset = {"cases_sha256": "a" * 64, "split_sha256": "b" * 64}
    store = ArtifactStore(tmp_path / "raw")
    store.append_record(record)
    store.write_metadata(
        run_id,
        "run-config.json",
        {
            "candidate": json.loads(json.dumps(asdict(candidate), default=str, sort_keys=True)),
            "dataset": dataset,
            "prompt_sha256": prompt_fingerprint(),
            "triage_schema": triage_json_schema(),
            "backend_proof": backend_proof.to_dict(),
            "cpu_only_proof": cpu_proof.to_dict(),
        },
    )
    store.write_metadata(
        run_id,
        "request.json",
        {
            "case_id": case.case_id,
            "repetition": 0,
            "messages": build_messages(case.incident),
            "model": candidate.candidate_id,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": REAL_BENCHMARK_MAX_TOKENS,
            "seed": REAL_BENCHMARK_SEED,
            "stream": True,
            "response_format": triage_openai_response_format(),
        },
    )
    store.write_metadata(
        run_id,
        "response.json",
        {
            "content": output,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "finish_reason": "stop",
            "timing": {
                "start_ns": 1_000_000,
                "first_content_token_ns": 2_000_000,
                "end_ns": 3_000_000,
                "ttft_ms": 1.0,
                "e2e_ms": 2.0,
            },
            "score": score.as_dict(),
        },
    )
    store.write_metadata(run_id, "runtime-proof.txt", runtime_log)
    store.finalize(run_id)
    monkeypatch.setattr(cascade_module, "_reviewed_model_proof", lambda *_args: None)
    return store, record, candidate, case, dataset


def _load_strict_nested_receipt(
    store: ArtifactStore,
    record: BenchmarkRecord,
    candidate: RuntimeCandidate,
    case: IncidentCase,
    dataset: dict[str, str],
) -> MeasuredOutputCollection:
    return cascade_module._load_measured_outputs(
        store,
        [record],
        expected_case_ids=(case.case_id,),
        expected_split="test",
        expected_role="weak",
        expected_candidate=candidate,
        expected_dataset=dataset,
        expected_cases={case.case_id: case},
    )


def test_nested_receipt_rejects_tampered_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, record, candidate, case, dataset = _strict_nested_receipt(tmp_path, monkeypatch)
    assert _load_strict_nested_receipt(store, record, candidate, case, dataset).outputs
    request = json.loads((store.root / record.run_id / "request.json").read_text(encoding="utf-8"))
    request["messages"] = [{"role": "user", "content": "leaked held-out labels"}]
    store.write_metadata(record.run_id, "request.json", request)
    store.finalize(record.run_id)

    with pytest.raises(CascadeWorkflowError, match="frozen prompt"):
        _load_strict_nested_receipt(store, record, candidate, case, dataset)


def test_nested_receipt_rejects_tampered_runtime_proof_despite_true_booleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, record, candidate, case, dataset = _strict_nested_receipt(tmp_path, monkeypatch)
    store.write_metadata(record.run_id, "runtime-proof.txt", "no runtime markers")
    store.finalize(record.run_id)

    with pytest.raises(CascadeWorkflowError, match="CPU/KleidiAI proof does not replay"):
        _load_strict_nested_receipt(store, record, candidate, case, dataset)


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
