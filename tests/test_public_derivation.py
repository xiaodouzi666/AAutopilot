from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from a64pilot.benchmark.store import ArtifactStore
from a64pilot.cli import app
from a64pilot.provenance import write_json
from a64pilot.report.public_derivation import verify_public_derivation
from a64pilot.schemas import BenchmarkRecord

ROOT = Path(__file__).resolve().parents[1]
REDACTOR: dict[str, Any] = runpy.run_path(str(ROOT / "scripts/redact-artifacts.py"))
RUN_IDS = {
    "main": "1" * 32,
    "calibration_weak": "2" * 32,
    "calibration_strong": "3" * 32,
    "held_out_strong": "4" * 32,
}
CALIBRATION_SESSION = "calibration-" + "a" * 32
HELD_OUT_SESSION = "held-out-" + "b" * 32


def _write_run(
    store: ArtifactStore,
    run_id: str,
    *,
    candidate_id: str,
    role: str,
    split: str,
    stage: str,
) -> None:
    record = BenchmarkRecord(
        run_id=run_id,
        candidate_id=candidate_id,
        stage=stage,
        case_id=f"case-{run_id[0]}",
        repetition=0,
        split=split,
        backend="kleidiai",
        model_role=role,
        model_file_sha256=run_id[0] * 64,
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
        end_ns=4_000_000,
        ttft_ms=1.0,
        e2e_ms=3.0,
        prompt_tokens=11,
        completion_tokens=7,
        generation_tok_s=3500.0,
        peak_rss_mb=128.0,
        route=role,
        schema_valid=True,
        quality_score=88.0,
        safety_score=100.0,
        command=["build/kleidiai/bin/llama-server", "--model", "models/model.gguf"],
        errors=[],
    )
    candidate = {
        "affinity": [],
        "backend": "kleidiai",
        "batch": 128,
        "binary": "build/kleidiai/bin/llama-server",
        "candidate_id": candidate_id,
        "cmake_cache": "build/kleidiai/CMakeCache.txt",
        "context": 2048,
        "model": "models/model.gguf",
        "model_role": role,
        "parallel": 1,
        "quantization": "Q4_0",
        "stage": "a4" if stage == "cascade" else "a3",
        "threads": 4,
        "ubatch": 64,
    }
    store.append_record(record)
    store.write_metadata(
        run_id,
        "run-config.json",
        {
            "backend_proof": {},
            "candidate": candidate,
            "cpu_only_proof": {},
            "dataset": {"cases_sha256": "5" * 64, "split_sha256": "6" * 64},
            "prompt_sha256": "7" * 64,
            "triage_schema": {},
        },
    )
    store.write_metadata(run_id, "runtime-proof.txt", "peer=10.42.0.7\n")
    store.write_metadata(
        run_id,
        "request.json",
        {
            "case_id": record.case_id,
            "messages": [],
            "model": candidate_id,
            "repetition": 0,
            "response_format": {},
        },
    )
    store.write_metadata(
        run_id,
        "response.json",
        {
            "content": "{}",
            "finish_reason": "stop",
            "score": {
                "issues": [],
                "quality_score": 88.0,
                "safety_score": 100.0,
                "schema_valid": True,
            },
            "timing": {
                "e2e_ms": 3.0,
                "end_ns": 4_000_000,
                "first_content_token_ns": 2_000_000,
                "start_ns": 1_000_000,
                "ttft_ms": 1.0,
            },
            "usage": {"completion_tokens": 7, "prompt_tokens": 11},
        },
    )
    store.finalize(run_id)


def _private_bundle(tmp_path: Path) -> Path:
    artifacts = tmp_path / "artifacts"
    main = ArtifactStore(artifacts / "raw")
    _write_run(
        main,
        RUN_IDS["main"],
        candidate_id="a3-strong",
        role="strong",
        split="test",
        stage="tuned",
    )

    calibration = ArtifactStore(artifacts / "a4" / "runs" / CALIBRATION_SESSION / "raw")
    _write_run(
        calibration,
        RUN_IDS["calibration_weak"],
        candidate_id="a4-calibration-weak",
        role="weak",
        split="calibration",
        stage="cascade",
    )
    _write_run(
        calibration,
        RUN_IDS["calibration_strong"],
        candidate_id="a4-calibration-strong",
        role="strong",
        split="calibration",
        stage="cascade",
    )
    held_out = ArtifactStore(artifacts / "a4" / "runs" / HELD_OUT_SESSION / "raw")
    _write_run(
        held_out,
        RUN_IDS["held_out_strong"],
        candidate_id="a4-held-out-strong",
        role="strong",
        split="test",
        stage="cascade",
    )

    write_json(
        artifacts / "search-plan.json",
        {"calibration_results": [{"source_run_ids": [RUN_IDS["main"]]}]},
    )
    (artifacts / "optimized-profile.yaml").write_text(
        yaml.safe_dump({"source_run_ids": [RUN_IDS["main"]]}, sort_keys=True),
        encoding="utf-8",
    )
    write_json(
        artifacts / "a4-frozen-policy.json",
        {
            "source_evidence": {
                "session_dir": f"artifacts/a4/runs/{CALIBRATION_SESSION}",
                "strong_run_ids": [RUN_IDS["calibration_strong"]],
                "weak_run_ids": [RUN_IDS["calibration_weak"]],
            }
        },
    )
    write_json(
        artifacts / "quality-results.json",
        {
            "held_out": {
                "source_evidence": {
                    "session_dir": f"artifacts/a4/runs/{HELD_OUT_SESSION}",
                    "strong_run_ids": [RUN_IDS["held_out_strong"]],
                    "weak_run_ids": [],
                }
            }
        },
    )
    write_json(artifacts / "cascade-status.json", {"status": "fixture"})
    return artifacts


def _public_bundle(tmp_path: Path) -> tuple[Path, Path]:
    private = _private_bundle(tmp_path)
    public = tmp_path / "artifacts-public"
    findings, _ = REDACTOR["sanitized_copy"](private, public)
    assert {finding["path"] for finding in findings} == {
        f"raw/{RUN_IDS['main']}/runtime-proof.txt",
        (f"a4/runs/{CALIBRATION_SESSION}/raw/{RUN_IDS['calibration_weak']}/runtime-proof.txt"),
        (f"a4/runs/{CALIBRATION_SESSION}/raw/{RUN_IDS['calibration_strong']}/runtime-proof.txt"),
        (f"a4/runs/{HELD_OUT_SESSION}/raw/{RUN_IDS['held_out_strong']}/runtime-proof.txt"),
    }
    return private, public


def test_complete_public_derivation_replays_in_pair_and_public_only_modes(
    tmp_path: Path,
) -> None:
    private, public = _public_bundle(tmp_path)

    assert verify_public_derivation(public, private_root=private) == []
    assert verify_public_derivation(public) == []
    receipt = json.loads((public / "public-derivation.json").read_text(encoding="utf-8"))
    assert receipt["complete"] is True
    assert {store["namespace"] for store in receipt["stores"]} == {
        "raw",
        f"a4/runs/{CALIBRATION_SESSION}/raw",
        f"a4/runs/{HELD_OUT_SESSION}/raw",
    }
    assert all(
        "model" not in change["path"] and "build" not in change["path"]
        for change in receipt["changes"]
    )


@pytest.mark.parametrize("target", ("requests.jsonl", "response.json"))
def test_typed_public_tamper_fails_even_after_manifest_refresh(
    tmp_path: Path,
    target: str,
) -> None:
    private, public = _public_bundle(tmp_path)
    run = public / "raw" / RUN_IDS["main"]
    path = run / target
    if target == "requests.jsonl":
        record = json.loads(path.read_text(encoding="utf-8"))
        record["quality_score"] = 87.0
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    else:
        response = json.loads(path.read_text(encoding="utf-8"))
        response["content"] = '{"tampered":true}'
        path.write_text(json.dumps(response) + "\n", encoding="utf-8")
    REDACTOR["_refresh_public_integrity"](public)

    errors = verify_public_derivation(public, private_root=private)

    assert errors


def test_runtime_log_tamper_and_refreshed_manifest_still_fail_derivation(
    tmp_path: Path,
) -> None:
    private, public = _public_bundle(tmp_path)
    proof = public / "raw" / RUN_IDS["main"] / "runtime-proof.txt"
    proof.write_text(proof.read_text(encoding="utf-8") + "fabricated marker\n", encoding="utf-8")
    REDACTOR["_refresh_public_integrity"](public)

    errors = verify_public_derivation(public, private_root=private)

    assert errors
    assert any("digest" in error or "receipt" in error for error in errors)


def test_public_run_inventory_is_fail_closed(tmp_path: Path) -> None:
    private, public = _public_bundle(tmp_path)
    run = public / "raw" / RUN_IDS["main"]
    (run / "unlisted.txt").write_text("not part of a run\n", encoding="utf-8")
    REDACTOR["_refresh_public_integrity"](public)

    errors = verify_public_derivation(public, private_root=private)

    assert errors
    assert any("inventory" in error for error in errors)


def test_verifier_does_not_collect_host_or_rehash_models_and_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, public = _public_bundle(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("public derivation touched host/model/build verification")

    monkeypatch.setattr("a64pilot.hardware.detect.collect_system_info", forbidden)
    monkeypatch.setattr("a64pilot.models.checksum.sha256_file", forbidden)

    assert verify_public_derivation(public, private_root=private) == []


def test_cli_and_workflow_put_derivation_before_public_semantic_replays() -> None:
    result = CliRunner().invoke(app, ["verify-public-derivation", "--help"])
    assert result.exit_code == 0
    assert "--artifacts-dir" in result.stdout
    assert "--private-artifacts" in result.stdout

    workflow = (ROOT / ".github/workflows/arm64-evidence.yml").read_text(encoding="utf-8")
    derivation = workflow.index("Verify sanitized A0-A4 evidence derivation")
    probes = workflow.index("Replay sanitized probe semantics")
    package = workflow.index("Package the verified public evidence")
    assert derivation < probes < package
