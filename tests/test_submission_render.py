from __future__ import annotations

import json
import shutil
from pathlib import Path

from a64pilot.report.submission import render_submission


def _project(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "templates", tmp_path / "templates")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "report-data.json").write_text(
        json.dumps(
            {
                "claims": [],
                "evidence_status": "measurement-pending",
                "system": {},
                "build": {},
                "models": {},
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "evidence-index.json").write_text(
        json.dumps(
            {
                "workflow": {
                    "run_id": 123,
                    "head_sha": "a" * 40,
                    "run_url": "https://example.test/actions/runs/123",
                },
                "devpost": {
                    "state": "published",
                    "url": "https://example.test/project",
                    "video_url": "https://youtu.be/current",
                    "submitted_at": "2026-08-14T02:04:58.469-04:00",
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_finalized_checklist_replays_public_receipt(tmp_path: Path) -> None:
    outputs = render_submission(project_root=_project(tmp_path), allow_pending=True)
    checklist = outputs["checklist"].read_text(encoding="utf-8")

    assert "[demo video](https://youtu.be/current)" in checklist
    assert "submitted and verified live" in checklist
    assert "2026-08-14T02:04:58.469-04:00" in checklist
    assert "Devpost project: https://example.test/project" in checklist


def test_current_workflow_never_inherits_prior_publication(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)

    outputs = render_submission(project_root=_project(tmp_path), allow_pending=True)
    checklist = outputs["checklist"].read_text(encoding="utf-8")

    assert "https://youtu.be/current" not in checklist
    assert (
        "Formal Devpost submission receipt and submitted timestamp are not recorded yet"
        in checklist
    )
    assert "Public video: not recorded" in checklist
