from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from a64pilot.provenance import sha256_file
from a64pilot.runtime.deployment import DeploymentProfileError, load_measured_profile
from a64pilot.schemas import BenchmarkRecord

A1_RUN_ID = "1" * 32
A2_RUN_ID = "2" * 32
A3_RUN_ID = "3" * 32


def record(
    run_id: str,
    candidate_id: str,
    stage: str,
    *,
    threads: int = 8,
) -> BenchmarkRecord:
    return BenchmarkRecord(
        run_id=run_id,
        candidate_id=candidate_id,
        stage=stage,
        case_id="incident-001",
        repetition=0,
        split="test",
        backend="generic" if stage == "baseline" else "kleidiai",
        model_role="strong",
        model_file_sha256="a" * 64,
        quantization="Q4_0",
        threads=threads,
        batch=256,
        ubatch=128,
        parallel=1,
        context=2048,
        affinity=[0, 1, 2, 3],
        cpu_only_verified=True,
        kleidiai_verified=stage != "baseline",
        start_ns=1,
        first_token_ns=2,
        end_ns=3,
        ttft_ms=0.001,
        e2e_ms=100,
        peak_rss_mb=100,
        schema_valid=True,
        quality_score=100,
        safety_score=100,
        command=["llama-server"],
    )


def formal_records() -> list[BenchmarkRecord]:
    return [
        record(A1_RUN_ID, "a1-generic-q4-0", "baseline"),
        record(A2_RUN_ID, "a2-kleidiai-q4-0", "kleidiai"),
    ]


@pytest.fixture(autouse=True)
def strict_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a64pilot.report.integrity.validate_evidence_bundle",
        lambda *_args, **_kwargs: (formal_records(), []),
    )


def measured_profile() -> dict[str, object]:
    return {
        "profile_id": "a2-kleidiai-q4-0",
        "status": "measured",
        "backend": "kleidiai",
        "model_role": "strong",
        "cpu_only": True,
        "config": {
            "threads": 8,
            "batch": 256,
            "ubatch": 128,
            "parallel": 1,
            "context": 2048,
            "affinity": [0, 1, 2, 3],
            "quantization": "Q4_0",
        },
        "source_run_ids": [A2_RUN_ID],
        "selection_basis": "fixed_a2_strong_fallback",
        "search_plan_sha256": None,
        "calibration_plan_sha256": None,
        "frozen_candidate_receipt": {
            "candidate_id": "a2-kleidiai-q4-0",
            "reason": "no calibration-frozen A3 passed the full held-out quality gate",
            "search_plan_status": "missing",
        },
    }


def write_profile(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_measured_profile_resolves_reviewed_runtime_inputs(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    write_profile(path, measured_profile())
    profile = load_measured_profile(path, project_root=tmp_path)
    assert profile.backend == "kleidiai"
    assert profile.model.name == "qwen2.5-1.5b-instruct-q4_0.gguf"
    assert profile.binary == tmp_path / "build/llama-kleidiai/bin/llama-server"
    assert profile.affinity == (0, 1, 2, 3)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "unmeasured-fallback", "status must be measured"),
        ("cpu_only", False, "CPU-only"),
        ("backend", "cuda", "generic or kleidiai"),
        ("model_role", "weak", "strong-only"),
        ("model_role", "cascade", "strong-only"),
    ],
)
def test_profile_fails_closed(field: str, value: object, message: str, tmp_path: Path) -> None:
    payload = measured_profile()
    payload[field] = value
    path = tmp_path / "profile.yaml"
    write_profile(path, payload)
    with pytest.raises(DeploymentProfileError, match=message):
        load_measured_profile(path, project_root=tmp_path)


def test_profile_requires_measured_source_rows(tmp_path: Path) -> None:
    payload = measured_profile()
    payload["source_run_ids"] = []
    path = tmp_path / "profile.yaml"
    write_profile(path, payload)
    with pytest.raises(DeploymentProfileError, match="source_run_ids"):
        load_measured_profile(path, project_root=tmp_path)


def test_profile_rejects_strict_evidence_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "a64pilot.report.integrity.validate_evidence_bundle",
        lambda *_args, **_kwargs: (formal_records(), ["tampered bundle"]),
    )
    path = tmp_path / "profile.yaml"
    write_profile(path, measured_profile())

    with pytest.raises(DeploymentProfileError, match="strict evidence validation failed"):
        load_measured_profile(path, project_root=tmp_path)


def test_profile_source_rows_and_settings_must_match_evidence(tmp_path: Path) -> None:
    payload = measured_profile()
    payload["source_run_ids"] = ["f" * 32]
    path = tmp_path / "profile.yaml"
    write_profile(path, payload)
    with pytest.raises(DeploymentProfileError, match="do not exactly match"):
        load_measured_profile(path, project_root=tmp_path)

    payload = measured_profile()
    config = payload["config"]
    assert isinstance(config, dict)
    config["threads"] = 4
    write_profile(path, payload)
    with pytest.raises(DeploymentProfileError, match="threads disagrees"):
        load_measured_profile(path, project_root=tmp_path)


def test_profile_rejects_search_plan_hash_mismatch(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "search-plan.json").write_text("{}\n", encoding="utf-8")
    payload = measured_profile()
    payload["search_plan_sha256"] = "0" * 64
    payload["calibration_plan_sha256"] = "0" * 64
    path = tmp_path / "profile.yaml"
    write_profile(path, payload)

    with pytest.raises(DeploymentProfileError, match="SHA-256 does not match"):
        load_measured_profile(path, project_root=tmp_path)


def test_frozen_a3_profile_replays_search_plan_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = {
        "candidate": {"candidate_id": "a3-tuned"},
        "gate_passed": True,
    }
    plan = {
        "status": "complete",
        "selected_a3_candidate_id": "a3-tuned",
        "ranked_candidate_ids": ["a3-tuned"],
        "admitted_finalists": [{"candidate_id": "a3-tuned"}],
        "held_out_results": [receipt],
    }
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    plan_path = artifacts / "search-plan.json"
    plan_path.write_text(__import__("json").dumps(plan) + "\n", encoding="utf-8")
    plan_hash = sha256_file(plan_path)
    monkeypatch.setattr(
        "a64pilot.report.integrity.validate_evidence_bundle",
        lambda *_args, **_kwargs: (
            [*formal_records(), record(A3_RUN_ID, "a3-tuned", "tuned")],
            [],
        ),
    )
    payload = measured_profile()
    payload.update(
        {
            "profile_id": "a3-tuned",
            "source_run_ids": [A3_RUN_ID],
            "selection_basis": "frozen_calibration_finalist",
            "search_plan_sha256": plan_hash,
            "calibration_plan_sha256": plan_hash,
            "frozen_candidate_receipt": receipt,
        }
    )
    path = tmp_path / "profile.yaml"
    write_profile(path, payload)

    profile = load_measured_profile(path, project_root=tmp_path)

    assert profile.profile_id == "a3-tuned"
