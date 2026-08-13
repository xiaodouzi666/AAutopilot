from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_capture_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "capture-arm-target-demo.py"
    spec = importlib.util.spec_from_file_location("a64pilot_capture_target_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def capture_module() -> ModuleType:
    return load_capture_module()


def write_bundle(root: Path, *, safety: float = 100.0, source_in_claim: bool = True) -> str:
    run_id = "a" * 32
    artifacts = root / "artifacts"
    run_dir = artifacts / "raw" / run_id
    run_dir.mkdir(parents=True)
    claim_rows = [run_id] if source_in_claim else ["b" * 32]
    claims = [{"claim_id": "claim", "source_rows": claim_rows}]
    report = {
        "evidence_status": "measured",
        "claims": claims,
        "system": {
            "architecture": "aarch64",
            "operating_system": "Linux",
            "kernel": "6.11.0-arm64",
        },
    }
    (artifacts / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    (artifacts / "report-data.json").write_text(json.dumps(report), encoding="utf-8")
    record = {
        "run_id": run_id,
        "case_id": "incident-041",
        "candidate_id": "strong-kleidiai-q4-0",
        "backend": "kleidiai",
        "model_role": "strong",
        "evidence_kind": "measured",
        "split": "test",
        "schema_valid": True,
        "safety_score": safety,
        "cpu_only_verified": True,
    }
    (run_dir / "requests.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    response = {
        "content": json.dumps(
            {
                "diagnosis": "disk_pressure",
                "severity": "high",
                "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
            }
        )
    }
    (run_dir / "response.json").write_text(json.dumps(response), encoding="utf-8")
    return run_id


def github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "123456789")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000000000")
    monkeypatch.setenv("GITHUB_REPOSITORY", "xiaodouzi666/AAutopilot")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "xiaodouzi666/AAutopilot/.github/workflows/arm64-evidence.yml@refs/heads/main",
    )
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", "f" * 40)


def test_capture_uses_only_claim_bound_validated_real_response(
    tmp_path: Path,
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = write_bundle(tmp_path)
    github_env(monkeypatch)
    monkeypatch.setattr(capture_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(capture_module.platform, "machine", lambda: "aarch64")
    payload = capture_module.capture(tmp_path)
    assert payload["source_run_id"] == run_id
    assert payload["diagnosis"] == "disk_pressure"
    assert payload["tool_calls"] == ["check_disk"]
    assert payload["workflow"] == "arm64-evidence"
    assert payload["workflow_sha"] == "f" * 40


def test_capture_refuses_non_arm_host(
    tmp_path: Path,
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_bundle(tmp_path)
    github_env(monkeypatch)
    monkeypatch.setattr(capture_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(capture_module.platform, "machine", lambda: "arm64")
    with pytest.raises(capture_module.CaptureError, match="must run on Arm64 Linux"):
        capture_module.capture(tmp_path)


def test_capture_refuses_noncanonical_workflow_identity(
    tmp_path: Path,
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_bundle(tmp_path)
    github_env(monkeypatch)
    monkeypatch.setattr(capture_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(capture_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "xiaodouzi666/AAutopilot/.github/workflows/arm64-evidence.yml@" + "f" * 40,
    )
    with pytest.raises(capture_module.CaptureError, match="official repository and workflow ref"):
        capture_module.capture(tmp_path)

    github_env(monkeypatch)
    monkeypatch.delenv("GITHUB_WORKFLOW_SHA")
    with pytest.raises(capture_module.CaptureError, match="GITHUB_WORKFLOW_SHA"):
        capture_module.capture(tmp_path)


def test_capture_refuses_unsafe_or_unclaimed_rows(
    tmp_path: Path,
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_bundle(tmp_path, safety=99.0)
    github_env(monkeypatch)
    monkeypatch.setattr(capture_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(capture_module.platform, "machine", lambda: "aarch64")
    with pytest.raises(capture_module.CaptureError, match="no validated claim-source"):
        capture_module.capture(tmp_path)
