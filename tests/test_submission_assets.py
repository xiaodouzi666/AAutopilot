from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assets = _load_script("generate-submission-assets.py")
placeholders = _load_script("check-final-placeholders.py")


def test_quality_summary_is_recomputed_from_measured_sources() -> None:
    summary = assets.build_quality_summary(ROOT)

    assert summary["evidence_status"] == "measured"
    assert summary["dataset"]["calibration_cases"] == 40
    assert summary["dataset"]["held_out_cases"] == 20
    assert summary["evaluation_counts"] == {
        "candidate_files": 9,
        "calibration_candidate_files": 4,
        "held_out_candidate_files": 5,
        "formal_measured_rows": 90,
    }
    assert summary["gate_policy"]["fair_held_out_quality_floor"] == pytest.approx(71.975)
    candidates = {item["candidate_id"]: item for item in summary["candidates"]}
    assert candidates["a1-generic-q4-0"]["quality_score"] == pytest.approx(72.975)
    assert candidates["a2-kleidiai-q4-0"]["quality_score"] == pytest.approx(73.875)
    assert candidates["a2-kleidiai-q4-0"]["final_gate"] == {
        "passed": True,
        "reasons": [],
    }
    assert summary["selected_profile"]["held_out_case_count"] == 20
    assert summary["selected_profile"]["shipping_mode"] == "strong-only"
    assert summary["selected_profile"]["gate_passed"] is True
    assert summary["cascade"]["status"] in {
        "not-run",
        "calibration-fallback-strong-only",
        "held-out-rejected",
        "held-out-quality-accepted",
    }


def test_committed_submission_assets_pass_integrity_verification() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS")
        and os.environ.get("A64PILOT_VERIFY_COMMITTED_ASSETS") != "1"
    ):
        pytest.skip(
            "committed screenshots are a prior-run snapshot; the new final video is rendered "
            "after live evidence verification"
        )
    assets.verify_assets(ROOT)


def test_current_publishable_surfaces_pass_placeholder_policy() -> None:
    assert placeholders.scan(ROOT) == []


def test_source_video_provenance_prefers_current_workflow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "987654321"
    commit = "ab" * 20
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    monkeypatch.setenv("GITHUB_SHA", commit)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    provenance = assets._source_video_provenance(
        ROOT,
        {
            "target_device_footage": {
                "github_run_id": run_id,
                "github_run_attempt": 3,
                "github_sha": commit,
            }
        },
    )

    assert provenance == {
        "github_run_id": int(run_id),
        "github_run_attempt": 3,
        "github_sha": commit,
        "github_run_url": f"https://github.com/example/project/actions/runs/{run_id}",
        "release_asset": "a64pilot-demo-final.mp4",
        "release_status": "pending_post_workflow",
        "release_url": None,
    }


def test_source_video_provenance_falls_back_to_matching_evidence_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)
    run_id = 246801357
    commit = "ef" * 20
    index_path = tmp_path / "artifacts" / "evidence-index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "workflow": {
                    "run_id": run_id,
                    "attempt": 4,
                    "head_sha": commit,
                    "run_url": f"https://github.com/example/project/actions/runs/{run_id}",
                },
                "release": {"url": f"https://github.com/example/project/releases/tag/run-{run_id}"},
            }
        ),
        encoding="utf-8",
    )

    provenance = assets._source_video_provenance(tmp_path, {})

    assert provenance["github_run_id"] == run_id
    assert provenance["github_run_attempt"] == 4
    assert provenance["github_sha"] == commit
    assert provenance["release_status"] == "published"
    assert provenance["release_url"].endswith(f"/tag/run-{run_id}")


def test_current_run_index_does_not_carry_prior_release_or_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "123456789"
    commit = "cd" * 20
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SHA", commit)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    artifacts_dir = tmp_path / "artifacts"
    submission_dir = artifacts_dir / "submission"
    screenshots_dir = artifacts_dir / "screenshots"
    submission_dir.mkdir(parents=True)
    screenshots_dir.mkdir(parents=True)
    video_path = submission_dir / "a64pilot-demo-final.mp4"
    video_path.write_bytes(b"current-run-video")
    video_sha = hashlib.sha256(video_path.read_bytes()).hexdigest()
    video_manifest_path = submission_dir / "a64pilot-demo-final.manifest.json"
    video_manifest_path.write_text(
        json.dumps(
            {
                "mode": "final_measured",
                "publishable": True,
                "output_sha256": video_sha,
                "target_device_footage": {
                    "github_run_id": run_id,
                    "github_run_attempt": 2,
                    "github_sha": commit,
                },
            }
        ),
        encoding="utf-8",
    )
    (artifacts_dir / "claims.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "primary",
                    "value": 1.0,
                    "unit": "%",
                    "confidence_interval": [0.5, 1.5],
                    "demonstrated": True,
                    "source_rows": ["a", "b"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifacts_dir / "report-data.json").write_text(
        json.dumps({"evidence_status": "measured"}), encoding="utf-8"
    )
    (screenshots_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_video": {
                    "sha256": video_sha,
                    "github_run_id": int(run_id),
                    "github_sha": commit,
                    "manifest_path": "artifacts/submission/a64pilot-demo-final.manifest.json",
                    "manifest_sha256": hashlib.sha256(video_manifest_path.read_bytes()).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    (artifacts_dir / "quality-summary.json").write_text("{}\n", encoding="utf-8")

    index = assets.build_current_run_index(tmp_path)

    assert index["workflow"]["run_id"] == int(run_id)
    assert index["workflow"]["head_sha"] == commit
    assert index["release"] == {
        "status_at_bundle_creation": "pending_post_workflow",
        "expected_tag": f"arm64-evidence-run-{run_id}",
    }
    assert index["attestation"] == {
        "status_at_bundle_creation": "pending_post_workflow",
        "subject_name": "aarch64-autopilot-evidence.tar.gz",
    }
    assert "url" not in index["release"]
    assert "url" not in index["attestation"]


def test_workflow_rebuilds_current_screenshots_and_index_before_redaction() -> None:
    workflow = (ROOT / ".github" / "workflows" / "arm64-evidence.yml").read_text(encoding="utf-8")
    render = workflow.index("python scripts/render-demo-video.py")
    screenshots = workflow.index("--video artifacts/submission/a64pilot-demo-final.mp4", render)
    current_index = workflow.index("--current-run-index-only", screenshots)
    verify = workflow.index("--verify-only", current_index)
    stale_index_exclusion = workflow.index("rm -f artifacts/evidence-index.json", verify)
    redact = workflow.index("--output artifacts-public artifacts", verify)

    assert render < screenshots < current_index < verify < stale_index_exclusion < redact
    assert 'if [[ "${{ job.status }}" != "success" ]]' in workflow
    assert "/artifacts/a4/runs/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def _write_policy_fixture(root: Path) -> None:
    for relative in placeholders.FINAL_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("final text\n", encoding="utf-8")
    for relative in placeholders.LEGACY_ALLOWLIST:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Historical planning only\n\nlegacy [[AUTO:value]]\n", encoding="utf-8")
    (root / "docs" / "clean.md").write_text("clean documentation\n", encoding="utf-8")


def test_placeholder_policy_rejects_final_token_but_allows_bannered_legacy(
    tmp_path: Path,
) -> None:
    _write_policy_fixture(tmp_path)
    assert placeholders.scan(tmp_path) == []

    (tmp_path / "README.md").write_text("result: TBD\n", encoding="utf-8")
    errors = placeholders.scan(tmp_path)
    assert any("README.md:1" in error for error in errors)
    assert not any("05-devpost-submission-draft" in error for error in errors)


def test_placeholder_policy_rejects_unbannered_or_stale_legacy_file(tmp_path: Path) -> None:
    _write_policy_fixture(tmp_path)
    legacy = tmp_path / "docs" / "06-video-script.md"
    legacy.write_text("ordinary final text\n", encoding="utf-8")

    errors = placeholders.scan(tmp_path)
    assert any("no required banner" in error for error in errors)
    assert any("allowlist is stale" in error for error in errors)
