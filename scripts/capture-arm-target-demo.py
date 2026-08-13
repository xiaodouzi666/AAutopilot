#!/usr/bin/env python3
"""Capture one validated real-model response as official Arm-run demo footage data."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any


class CaptureError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"cannot read required JSON: {path}") from exc


def capture(root: Path) -> dict[str, Any]:
    artifacts = root / "artifacts"
    report = _read_json(artifacts / "report-data.json")
    claims = _read_json(artifacts / "claims.json")
    if not isinstance(report, dict) or report.get("evidence_status") != "measured":
        raise CaptureError("target demo capture requires a measured report")
    if not isinstance(claims, list) or not claims or report.get("claims") != claims:
        raise CaptureError("target demo capture requires matching non-empty claims")
    system = report.get("system")
    if not isinstance(system, dict):
        raise CaptureError("target demo capture requires system provenance")
    machine = platform.machine().lower()
    if platform.system().lower() != "linux" or machine not in {"aarch64", "arm64"}:
        raise CaptureError("target demo capture must run on Arm64 Linux")
    if (
        system.get("architecture") != "aarch64"
        or str(system.get("operating_system")).lower() != "linux"
    ):
        raise CaptureError("report is not for an Arm64 Linux target")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    commit = os.getenv("GITHUB_SHA", "")
    runner = os.getenv("RUNNER_NAME", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    workflow_ref = os.getenv("GITHUB_WORKFLOW_REF", "")
    workflow_sha = os.getenv("GITHUB_WORKFLOW_SHA", "")
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise CaptureError("target demo capture must run inside GitHub Actions")
    if not re.fullmatch(r"[0-9]+", run_id):
        raise CaptureError("GITHUB_RUN_ID is required")
    if not attempt.isdigit() or int(attempt) < 1:
        raise CaptureError("GITHUB_RUN_ATTEMPT is required")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise CaptureError("GITHUB_SHA must be a full lowercase commit")
    if not runner.strip():
        raise CaptureError("RUNNER_NAME is required")
    expected_workflow_ref = (
        "xiaodouzi666/AAutopilot/.github/workflows/arm64-evidence.yml@refs/heads/main"
    )
    if repository != "xiaodouzi666/AAutopilot" or workflow_ref != expected_workflow_ref:
        raise CaptureError("official repository and workflow ref are required")
    if not re.fullmatch(r"[0-9a-f]{40}", workflow_sha):
        raise CaptureError("GITHUB_WORKFLOW_SHA must be a full lowercase commit")
    if workflow_sha != commit:
        raise CaptureError("workflow and checked-out commit SHA must match")

    claim_rows = {
        row
        for claim in claims
        if isinstance(claim, dict)
        for row in claim.get("source_rows", [])
        if isinstance(row, str)
    }
    selected: tuple[dict[str, Any], dict[str, Any]] | None = None
    for source_run_id in sorted(claim_rows):
        run_dir = artifacts / "raw" / source_run_id
        response_path = run_dir / "response.json"
        record_path = run_dir / "requests.jsonl"
        if not response_path.is_file() or not record_path.is_file():
            continue
        lines = [line for line in record_path.read_text(encoding="utf-8").splitlines() if line]
        if len(lines) != 1:
            continue
        record = json.loads(lines[0])
        response = _read_json(response_path)
        if (
            not isinstance(record, dict)
            or not isinstance(response, dict)
            or record.get("run_id") != source_run_id
            or record.get("evidence_kind") != "measured"
            or record.get("split") != "test"
            or not record.get("schema_valid")
            or record.get("safety_score") != 100.0
            or not record.get("cpu_only_verified")
        ):
            continue
        try:
            content = json.loads(response["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(content, dict):
            continue
        selected = (record, content)
        break
    if selected is None:
        raise CaptureError("no validated claim-source real response is available for footage")
    record, content = selected
    calls = content.get("tool_calls")
    if (
        not isinstance(calls, list)
        or not calls
        or any(
            not isinstance(call, dict) or not isinstance(call.get("name"), str) for call in calls
        )
    ):
        raise CaptureError("selected target response has invalid tool calls")
    return {
        "schema_version": "1.0",
        "evidence_status": "measured",
        "workflow": "arm64-evidence",
        "github_run_id": run_id,
        "github_run_attempt": int(attempt),
        "github_sha": commit,
        "runner_name": runner,
        "repository": repository,
        "workflow_ref": workflow_ref,
        "workflow_sha": workflow_sha,
        "architecture": "aarch64",
        "operating_system": "Linux",
        "kernel": str(system["kernel"]),
        "case_id": record["case_id"],
        "candidate_id": record["candidate_id"],
        "backend": record["backend"],
        "model": record["model_role"],
        "diagnosis": content["diagnosis"],
        "severity": content["severity"],
        "tool_calls": [call["name"] for call in calls],
        "output_validation": "schema_safety_consistency_passed",
        "source_run_id": record["run_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/submission/arm-target-demo-receipt.json"),
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    if root not in output.resolve().parents:
        print("target demo capture refused: output must remain inside project", file=sys.stderr)
        return 2
    try:
        payload = capture(root)
    except CaptureError as exc:
        print(f"target demo capture refused: {exc}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
