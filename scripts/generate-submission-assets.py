#!/usr/bin/env python3
"""Generate and verify the final quality summary and screenshot evidence.

The script is intentionally downstream-only: it never runs inference or changes benchmark
evidence.  ``quality-summary.json`` is compiled from the measured candidate records, while the
four submission screenshots are exact frames from the attested CI demo video.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_GITHUB_REPOSITORY = "xiaodouzi666/AAutopilot"

SCREENSHOT_SPECS = (
    {
        "id": "headline-evidence",
        "filename": "01-headline-evidence.png",
        "timecode_seconds": 66.0,
        "scene_window_seconds": [58.904, 74.277],
        "requirement": "headline evidence card",
        "scene": "Primary Q4_0 mean-TTFT claim with the measured ablation chart",
        "data_sources": [
            "artifacts/claims.json",
            "artifacts/report-data.json",
            "artifacts/figures/ablation.png",
        ],
    },
    {
        "id": "optimization-pipeline",
        "filename": "02-optimization-pipeline.png",
        "timecode_seconds": 20.0,
        "scene_window_seconds": [12.330, 34.903],
        "requirement": "architecture or optimization stages",
        "scene": "Same-commit generic/KleidiAI pipeline and frozen split-v2 protocol",
        "data_sources": [
            "artifacts/build-manifest.json",
            "artifacts/model-manifest.json",
            "demo/split.json",
        ],
    },
    {
        "id": "fair-pareto",
        "filename": "03-fair-pareto.png",
        "timecode_seconds": 82.0,
        "scene_window_seconds": [74.277, 91.076],
        "requirement": "fair ablation or Pareto chart",
        "scene": "Transparent secondary p95 result over the measured Pareto chart",
        "data_sources": [
            "artifacts/claims.json",
            "artifacts/benchmark-results.json",
            "artifacts/figures/pareto.png",
        ],
    },
    {
        "id": "api-deployment-status",
        "filename": "04-api-route-metadata.png",
        "timecode_seconds": 112.0,
        "scene_window_seconds": [106.642, 118.793],
        "requirement": "agent API demo and deployed-profile status",
        "scene": "Validated strong-only API, strict schema, and fail-closed response policy",
        "data_sources": [
            "artifacts/optimized-profile.yaml",
            "artifacts/cascade-status.json",
            "scripts/render-demo-video.py",
        ],
    },
)


class SubmissionAssetError(RuntimeError):
    """Raised when a downstream artifact cannot be proven from its sources."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmissionAssetError(f"cannot read JSON source {path}: {exc}") from exc


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SubmissionAssetError(f"cannot read YAML source {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SubmissionAssetError(f"cannot hash source {path}: {exc}") from exc
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SubmissionAssetError(f"{label} must be an object")
    return value


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SubmissionAssetError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SubmissionAssetError(f"{label} must be finite")
    return result


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _run_id(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SubmissionAssetError(f"{label} must be a numeric GitHub run ID") from exc
    if result < 1:
        raise SubmissionAssetError(f"{label} must be positive")
    return result


def _commit_sha(value: Any, *, label: str) -> str:
    result = str(value or "").lower()
    if len(result) != 40 or any(character not in "0123456789abcdef" for character in result):
        raise SubmissionAssetError(f"{label} must be a 40-character commit SHA")
    return result


def _source_video_provenance(
    root: Path,
    video_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a screenshot set to its actual workflow without carrying a prior run forward."""

    target = _mapping_or_empty(video_manifest.get("target_device_footage"))
    evidence_index_path = root / "artifacts" / "evidence-index.json"
    evidence_index = (
        _mapping_or_empty(_read_json(evidence_index_path)) if evidence_index_path.is_file() else {}
    )
    indexed_workflow = _mapping_or_empty(evidence_index.get("workflow"))
    indexed_release = _mapping_or_empty(evidence_index.get("release"))

    env_run_value = os.environ.get("GITHUB_RUN_ID")
    env_sha_value = os.environ.get("GITHUB_SHA")
    if bool(env_run_value) != bool(env_sha_value):
        raise SubmissionAssetError("GITHUB_RUN_ID and GITHUB_SHA must be provided together")

    target_run_value = target.get("github_run_id")
    target_sha_value = target.get("github_sha")
    if bool(target_run_value) != bool(target_sha_value):
        raise SubmissionAssetError(
            "video receipt github_run_id and github_sha must be provided together"
        )
    run_value = env_run_value or target_run_value or indexed_workflow.get("run_id")
    sha_value = env_sha_value or target_sha_value or indexed_workflow.get("head_sha")
    run_id = _run_id(run_value, label="source video run")
    head_sha = _commit_sha(sha_value, label="source video commit")

    if (
        env_run_value
        and target_run_value
        and _run_id(target_run_value, label="video receipt run") != run_id
    ):
        raise SubmissionAssetError("rendered video receipt disagrees with GITHUB_RUN_ID")
    if (
        env_sha_value
        and target_sha_value
        and _commit_sha(target_sha_value, label="video receipt commit") != head_sha
    ):
        raise SubmissionAssetError("rendered video receipt disagrees with GITHUB_SHA")

    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPOSITORY)
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    target_run_url = target.get("github_run_url")
    indexed_run_url = indexed_workflow.get("run_url")
    if not env_run_value:
        if isinstance(target_run_url, str) and target_run_url.endswith(f"/runs/{run_id}"):
            run_url = target_run_url
        elif isinstance(indexed_run_url, str) and indexed_run_url.endswith(f"/runs/{run_id}"):
            run_url = indexed_run_url

    attempt_value = (
        os.environ.get("GITHUB_RUN_ATTEMPT")
        or target.get("github_run_attempt")
        or indexed_workflow.get("attempt")
        or 1
    )
    try:
        run_attempt = int(attempt_value)
    except (TypeError, ValueError) as exc:
        raise SubmissionAssetError("source video run attempt must be an integer") from exc
    if run_attempt < 1:
        raise SubmissionAssetError("source video run attempt must be positive")

    indexed_run_matches = False
    try:
        indexed_run_matches = (
            _run_id(indexed_workflow.get("run_id"), label="indexed run") == run_id
            and _commit_sha(indexed_workflow.get("head_sha"), label="indexed commit") == head_sha
        )
    except SubmissionAssetError:
        indexed_run_matches = False
    release_url = indexed_release.get("url") if indexed_run_matches and not env_run_value else None
    if not isinstance(release_url, str) or not release_url:
        release_url = None

    return {
        "github_run_id": run_id,
        "github_run_attempt": run_attempt,
        "github_sha": head_sha,
        "github_run_url": run_url,
        "release_asset": "a64pilot-demo-final.mp4",
        "release_status": "published" if release_url else "pending_post_workflow",
        "release_url": release_url,
    }


def build_current_run_index(root: Path) -> dict[str, Any]:
    """Create the pre-attestation index embedded in the current workflow's signed bundle."""

    if not os.environ.get("GITHUB_RUN_ID") or not os.environ.get("GITHUB_SHA"):
        raise SubmissionAssetError("--current-run-index-only requires GITHUB_RUN_ID and GITHUB_SHA")
    artifacts = root / "artifacts"
    claims_path = artifacts / "claims.json"
    report_path = artifacts / "report-data.json"
    video_manifest_path = artifacts / "submission" / "a64pilot-demo-final.manifest.json"
    video_path = artifacts / "submission" / "a64pilot-demo-final.mp4"
    screenshots_path = artifacts / "screenshots" / "manifest.json"
    quality_summary_path = artifacts / "quality-summary.json"
    quality_results_path = artifacts / "quality-results.json"

    claims = _read_json(claims_path)
    report = _require_mapping(_read_json(report_path), label=str(report_path))
    video_manifest = _require_mapping(
        _read_json(video_manifest_path), label=str(video_manifest_path)
    )
    if not isinstance(claims, list) or not claims:
        raise SubmissionAssetError("current run index requires measured claims")
    if report.get("evidence_status") != "measured":
        raise SubmissionAssetError("current run index requires a measured report")
    if (
        video_manifest.get("mode") != "final_measured"
        or video_manifest.get("publishable") is not True
    ):
        raise SubmissionAssetError("current run index requires a publishable final video")
    if not video_path.is_file() or _sha256(video_path) != video_manifest.get("output_sha256"):
        raise SubmissionAssetError("current final video does not match its manifest")
    if not screenshots_path.is_file() or not quality_summary_path.is_file():
        raise SubmissionAssetError("current run screenshots and quality summary are missing")

    provenance = _source_video_provenance(root, video_manifest)
    screenshots = _require_mapping(_read_json(screenshots_path), label=str(screenshots_path))
    screenshot_source = _require_mapping(
        screenshots.get("source_video"), label="screenshot source video"
    )
    if screenshot_source.get("sha256") != video_manifest.get("output_sha256"):
        raise SubmissionAssetError("current screenshots do not derive from the final video")
    if screenshot_source.get("github_run_id") != provenance["github_run_id"]:
        raise SubmissionAssetError("current screenshots do not belong to this workflow run")
    if screenshot_source.get("github_sha") != provenance["github_sha"]:
        raise SubmissionAssetError("current screenshots do not belong to this workflow commit")
    claim_index: dict[str, Any] = {}
    for value in claims:
        claim = _require_mapping(value, label="claim")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise SubmissionAssetError("current run index found a claim without an ID")
        source_rows = claim.get("source_rows")
        if not isinstance(source_rows, list):
            raise SubmissionAssetError(f"claim {claim_id} has invalid source rows")
        claim_index[claim_id] = {
            "value": claim.get("value"),
            "unit": claim.get("unit"),
            "confidence_interval": claim.get("confidence_interval"),
            "demonstrated": claim.get("demonstrated"),
            "source_row_count": len(source_rows),
        }

    source_files = [
        claims_path,
        report_path,
        video_manifest_path,
        screenshots_path,
        quality_summary_path,
    ]
    if quality_results_path.is_file():
        source_files.append(quality_results_path)
    run_id = provenance["github_run_id"]
    return {
        "schema_version": 3,
        "generated_at": datetime.now(UTC).isoformat(),
        "index_state": "generated_before_release_and_attestation",
        "workflow": {
            "run_id": run_id,
            "attempt": provenance["github_run_attempt"],
            "head_sha": provenance["github_sha"],
            "run_url": provenance["github_run_url"],
        },
        "release": {
            "status_at_bundle_creation": "pending_post_workflow",
            "expected_tag": f"arm64-evidence-run-{run_id}",
        },
        "attestation": {
            "status_at_bundle_creation": "pending_post_workflow",
            "subject_name": "aarch64-autopilot-evidence.tar.gz",
        },
        "results": {
            "evidence_status": "measured",
            "claims": claim_index,
        },
        "video": {
            "attested_source": {
                "release_asset": "a64pilot-demo-final.mp4",
                "manifest_path": "submission/a64pilot-demo-final.manifest.json",
                "manifest_sha256": _sha256(video_manifest_path),
                "video_sha256": _sha256(video_path),
                "included_in_evidence_bundle": True,
                "attestation_status_at_index_creation": "pending_post_workflow",
            }
        },
        "source_files": {
            _relative(artifacts, path): _sha256(path) for path in sorted(source_files)
        },
    }


def _case_ids_digest(case_scores: Sequence[Mapping[str, Any]]) -> str:
    case_ids = sorted(str(item.get("case_id", "")) for item in case_scores)
    if not case_ids or not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise SubmissionAssetError("quality record case IDs must be non-empty and unique")
    payload = json.dumps(case_ids, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def build_quality_summary(root: Path) -> dict[str, Any]:
    """Compile an auditable aggregate without copying or modifying raw evidence."""

    artifacts = root / "artifacts"
    dataset_path = artifacts / "quality-dataset.json"
    report_path = artifacts / "report-data.json"
    policy_path = root / "configs" / "quality-gate.yaml"
    plan_path = artifacts / "search-plan.json"
    profile_path = artifacts / "optimized-profile.yaml"
    cascade_path = artifacts / "cascade-status.json"
    dataset = _require_mapping(_read_json(dataset_path), label=str(dataset_path))
    report = _require_mapping(_read_json(report_path), label=str(report_path))
    policy = _require_mapping(_read_yaml(policy_path), label=str(policy_path))
    plan = _require_mapping(_read_json(plan_path), label=str(plan_path))
    profile = _require_mapping(_read_yaml(profile_path), label=str(profile_path))
    cascade = _require_mapping(_read_json(cascade_path), label=str(cascade_path))

    if dataset.get("valid") is not True or dataset.get("mode") != "validation-only":
        raise SubmissionAssetError("quality dataset validation receipt is not valid")
    if report.get("evidence_status") != "measured":
        raise SubmissionAssetError("quality summary requires measured report evidence")
    for field in ("cases_sha256", "split_sha256"):
        if dataset.get(field) != report.get(field):
            raise SubmissionAssetError(f"quality dataset and report disagree on {field}")

    calibration_results = {
        item["candidate_id"]: item
        for item in plan.get("calibration_results", [])
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    held_out_results = {
        item["candidate"]["candidate_id"]: item
        for item in plan.get("held_out_results", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("candidate"), Mapping)
        and isinstance(item["candidate"].get("candidate_id"), str)
    }
    final_gates = _require_mapping(profile.get("gate", {}), label="optimized profile gate")
    report_summaries = {
        item["candidate_id"]: item
        for item in report.get("summaries", [])
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }

    candidates: list[dict[str, Any]] = []
    source_paths = [dataset_path, report_path, policy_path, plan_path, profile_path, cascade_path]
    quality_paths = sorted(
        path
        for path in artifacts.glob("quality-*.json")
        if path.name not in {"quality-dataset.json", "quality-results.json", "quality-summary.json"}
    )
    if not quality_paths:
        raise SubmissionAssetError("no candidate quality records were found")
    prompt_hashes: set[str] = set()
    for path in quality_paths:
        payload = _require_mapping(_read_json(path), label=str(path))
        candidate_id = payload.get("candidate_id")
        split = payload.get("split")
        prompt_sha256 = payload.get("prompt_sha256")
        summary = _require_mapping(payload.get("summary"), label=f"{path} summary")
        case_scores = summary.get("case_scores")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise SubmissionAssetError(f"{path} has no candidate_id")
        if split not in {"calibration", "test"}:
            raise SubmissionAssetError(f"{path} has unsupported split {split!r}")
        if not isinstance(prompt_sha256, str) or len(prompt_sha256) != 64:
            raise SubmissionAssetError(f"{path} has invalid prompt hash")
        if not isinstance(case_scores, list) or not all(
            isinstance(item, Mapping) for item in case_scores
        ):
            raise SubmissionAssetError(f"{path} has invalid case_scores")
        case_count = summary.get("case_count")
        if not isinstance(case_count, int) or case_count != len(case_scores) or case_count < 1:
            raise SubmissionAssetError(f"{path} case_count does not match case_scores")
        candidate: dict[str, Any] = {
            "candidate_id": candidate_id,
            "split": split,
            "case_count": case_count,
            "quality_score": _finite(
                summary.get("quality_score"), label=f"{candidate_id} quality_score"
            ),
            "safety_score": _finite(
                summary.get("safety_score"), label=f"{candidate_id} safety_score"
            ),
            "schema_failure_count": summary.get("schema_failure_count"),
            "minimum_case_score": _finite(
                summary.get("minimum_case_score"), label=f"{candidate_id} minimum_case_score"
            ),
            "prompt_sha256": prompt_sha256,
            "case_ids_sha256": _case_ids_digest(case_scores),
            "source": {
                "path": _relative(root, path),
                "sha256": _sha256(path),
            },
        }
        if not isinstance(candidate["schema_failure_count"], int):
            raise SubmissionAssetError(f"{candidate_id} schema_failure_count must be an integer")
        if candidate_id in calibration_results:
            calibration = calibration_results[candidate_id]
            candidate["calibration_gate"] = {
                "passed": calibration.get("feasible") is True,
                "reasons": calibration.get("reasons", []),
            }
        if candidate_id in held_out_results:
            held_out = held_out_results[candidate_id]
            candidate["held_out_gate"] = {
                "passed": held_out.get("gate_passed") is True,
                "reasons": held_out.get("gate_reasons", []),
                "held_out_case_count": held_out.get("held_out_case_count"),
            }
        if candidate_id in final_gates:
            gate = _require_mapping(final_gates[candidate_id], label=f"{candidate_id} gate")
            candidate["final_gate"] = {
                "passed": gate.get("passed") is True,
                "reasons": gate.get("reasons", []),
            }
        report_summary = report_summaries.get(candidate_id)
        if report_summary is not None:
            for field in ("sample_count", "quality_score", "safety_score"):
                quality_field = "case_count" if field == "sample_count" else field
                if report_summary.get(field) != candidate[quality_field]:
                    raise SubmissionAssetError(
                        f"{candidate_id} quality record disagrees with report summary on {field}"
                    )
        prompt_hashes.add(prompt_sha256)
        candidates.append(candidate)
        source_paths.append(path)

    if len(prompt_hashes) != 1:
        raise SubmissionAssetError("candidate quality records do not share one prompt hash")

    profile_id = profile.get("profile_id")
    selected = next(
        (
            item
            for item in held_out_results.values()
            if item["candidate"]["candidate_id"] == profile_id
        ),
        None,
    )
    if selected is None or selected.get("gate_passed") is not True:
        raise SubmissionAssetError("selected profile is not a gate-passed held-out finalist")
    selected_result = _require_mapping(selected.get("result"), label="selected held-out result")
    profile_gate = _require_mapping(
        final_gates.get(profile_id), label="selected profile final gate"
    )
    for field in ("quality_score", "safety_score"):
        if profile.get(field) != selected_result.get(field):
            raise SubmissionAssetError(
                f"selected profile disagrees with held-out result on {field}"
            )

    baseline = report_summaries.get("a1-generic-q4-0")
    if baseline is None:
        raise SubmissionAssetError("fair A1 quality baseline is absent from report summaries")
    quality_floor = _finite(baseline.get("quality_score"), label="A1 baseline quality") - _finite(
        policy.get("max_absolute_quality_drop"), label="quality drop policy"
    )

    summary = {
        "schema_version": "1.0",
        "evidence_status": "measured",
        "compiled_from_report_at": report.get("generated_at"),
        "dataset": {
            "mode": dataset.get("mode"),
            "valid": dataset.get("valid"),
            "cases": dataset.get("cases"),
            "calibration_cases": dataset.get("calibration"),
            "held_out_cases": dataset.get("held_out"),
            "cases_sha256": dataset.get("cases_sha256"),
            "split_sha256": dataset.get("split_sha256"),
            "split_schema_version": report.get("split_schema_version"),
        },
        "gate_policy": {
            **dict(policy),
            "fair_a1_quality_reference": baseline.get("quality_score"),
            "fair_held_out_quality_floor": quality_floor,
            "split_rule": "calibration decisions freeze before final held-out evaluation",
        },
        "evaluation_counts": {
            "candidate_files": len(candidates),
            "calibration_candidate_files": sum(
                item["split"] == "calibration" for item in candidates
            ),
            "held_out_candidate_files": sum(item["split"] == "test" for item in candidates),
            "formal_measured_rows": report.get("measurement_count"),
        },
        "shared_prompt_sha256": next(iter(prompt_hashes)),
        "candidates": sorted(candidates, key=lambda item: item["candidate_id"]),
        "selected_profile": {
            "candidate_id": profile_id,
            "backend": profile.get("backend"),
            "model_role": profile.get("model_role"),
            "shipping_mode": "strong-only",
            "quality_score": selected_result.get("quality_score"),
            "safety_score": selected_result.get("safety_score"),
            "schema_failure_count": selected_result.get("schema_failures"),
            "held_out_case_count": selected.get("held_out_case_count"),
            "gate_passed": profile_gate.get("passed") is True,
            "gate_reasons": profile_gate.get("reasons", []),
            "selection_basis": profile.get("selection_basis"),
            "source_run_ids": selected_result.get("source_run_ids", []),
        },
        "cascade": {
            "status": cascade.get("status"),
            "reason": cascade.get("reason"),
            "a4_admitted_by_quality_gate": cascade.get("a4_admitted_by_quality_gate"),
            "shipping_profile": cascade.get("shipping_profile") or cascade.get("shipping_fallback"),
            "performance_claim_eligible": cascade.get("performance_claim_eligible", False),
            "claim_policy": (
                "A4 component replay is not presented as live performance or deployment; "
                "the shipping profile remains measured strong-only"
            ),
        },
        "source_files": {
            _relative(root, path): _sha256(path) for path in sorted(set(source_paths))
        },
    }
    return summary


def _run(command: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(command), check=True, text=True, capture_output=capture)
    except FileNotFoundError as exc:
        raise SubmissionAssetError(f"required executable is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-800:]
        raise SubmissionAssetError(f"media command failed ({command[0]}): {detail}") from exc


def _probe_image(path: Path, *, ffprobe: str) -> tuple[int, int]:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    payload = _read_json_text(result.stdout, label=str(path))
    streams = payload.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_name") != "png":
        raise SubmissionAssetError(f"{path} is not exactly one PNG image stream")
    width, height = streams[0].get("width"), streams[0].get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise SubmissionAssetError(f"{path} has no valid dimensions")
    return width, height


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Parse a complete PNG chunk stream without requiring media tools."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SubmissionAssetError(f"cannot read PNG evidence {path}: {exc}") from exc
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise SubmissionAssetError(f"{path} has an invalid PNG signature")

    offset = 8
    chunk_index = 0
    saw_ihdr = False
    saw_nonempty_idat = False
    saw_iend = False
    width = height = 0
    valid_bit_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    known_critical_chunks = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}

    while offset < len(payload):
        if len(payload) - offset < 12:
            raise SubmissionAssetError(f"{path} has a truncated PNG chunk header")
        chunk_length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        if chunk_length > 0x7FFFFFFF:
            raise SubmissionAssetError(f"{path} has an oversized PNG chunk")
        if not all(
            ord("A") <= value <= ord("Z") or ord("a") <= value <= ord("z") for value in chunk_type
        ):
            raise SubmissionAssetError(f"{path} has an invalid PNG chunk type")
        if not ord("A") <= chunk_type[2] <= ord("Z"):
            raise SubmissionAssetError(f"{path} has an invalid reserved PNG chunk bit")
        if chunk_type[0] & 0x20 == 0 and chunk_type not in known_critical_chunks:
            name = chunk_type.decode("ascii")
            raise SubmissionAssetError(f"{path} has unknown critical PNG chunk {name}")

        data_start = offset + 8
        data_end = data_start + chunk_length
        chunk_end = data_end + 4
        if chunk_end > len(payload):
            name = chunk_type.decode("ascii")
            raise SubmissionAssetError(f"{path} has truncated PNG chunk {name}")
        chunk_data = payload[data_start:data_end]
        stored_crc = struct.unpack(">I", payload[data_end:chunk_end])[0]
        calculated_crc = zlib.crc32(chunk_type)
        calculated_crc = zlib.crc32(chunk_data, calculated_crc) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            name = chunk_type.decode("ascii")
            raise SubmissionAssetError(f"{path} has a CRC mismatch in PNG chunk {name}")

        if chunk_index == 0 and chunk_type != b"IHDR":
            raise SubmissionAssetError(f"{path} does not begin with the PNG IHDR chunk")
        if chunk_type == b"IHDR":
            if saw_ihdr or chunk_index != 0 or chunk_length != 13:
                raise SubmissionAssetError(f"{path} has an invalid PNG IHDR chunk")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if not (0 < width <= 0x7FFFFFFF and 0 < height <= 0x7FFFFFFF):
                raise SubmissionAssetError(f"{path} has invalid PNG dimensions")
            if bit_depth not in valid_bit_depths.get(color_type, set()):
                raise SubmissionAssetError(f"{path} has an invalid PNG color type or bit depth")
            if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise SubmissionAssetError(f"{path} has unsupported PNG IHDR fields")
            saw_ihdr = True
        elif not saw_ihdr:
            raise SubmissionAssetError(f"{path} has a PNG chunk before IHDR")
        elif chunk_type == b"IDAT" and chunk_length > 0:
            saw_nonempty_idat = True
        elif chunk_type == b"IEND":
            if chunk_length != 0:
                raise SubmissionAssetError(f"{path} has a non-empty PNG IEND chunk")
            saw_iend = True
            if chunk_end != len(payload):
                raise SubmissionAssetError(f"{path} has trailing data after PNG IEND")

        offset = chunk_end
        chunk_index += 1
        if saw_iend:
            break

    if not saw_ihdr:
        raise SubmissionAssetError(f"{path} has no PNG IHDR chunk")
    if not saw_nonempty_idat:
        raise SubmissionAssetError(f"{path} has no non-empty PNG IDAT chunk")
    if not saw_iend:
        raise SubmissionAssetError(f"{path} has no terminal PNG IEND chunk")
    return width, height


def _read_json_text(value: str, *, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SubmissionAssetError(f"cannot parse JSON output for {label}: {exc}") from exc
    return _require_mapping(payload, label=label)


def _video_duration(path: Path, *, ffprobe: str) -> float:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture=True,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise SubmissionAssetError(f"cannot parse video duration for {path}") from exc
    if not math.isfinite(duration) or duration <= max(
        spec["timecode_seconds"] for spec in SCREENSHOT_SPECS
    ):
        raise SubmissionAssetError("source video is too short for the reviewed screenshot scenes")
    return duration


def _captions(root: Path) -> str:
    claims = _read_json(root / "artifacts" / "claims.json")
    if not isinstance(claims, list) or len(claims) < 2:
        raise SubmissionAssetError("captions require the measured primary and secondary claims")
    claim_by_id = {item.get("claim_id"): item for item in claims if isinstance(item, Mapping)}
    primary = _require_mapping(
        claim_by_id.get("fair_q4_0_mean_ttft_reduction"), label="primary claim"
    )
    p95 = _require_mapping(claim_by_id.get("fair_q4_0_p95_latency_reduction"), label="p95 claim")
    profile = _require_mapping(
        _read_yaml(root / "artifacts" / "optimized-profile.yaml"), label="optimized profile"
    )
    cascade = _require_mapping(
        _read_json(root / "artifacts" / "cascade-status.json"), label="cascade status"
    )
    primary_ci = primary["confidence_interval"]
    p95_ci = p95["confidence_interval"]
    return f"""# Submission Screenshot Captions

These are exact 1920×1080 frames from the attested CI demo video. Values are copied from the
measured claim and profile artifacts identified in `manifest.json`; no value was typed into an
image after capture.

## 1. Headline evidence

**Caption:** On 20 paired split-v2 final-holdout cases, KleidiAI reduced Q4_0 mean time to first
token by **{float(primary["value"]):.2f}%** versus the same-model generic backend. The paired 95%
interval is **{float(primary_ci[0]):.2f}% to {float(primary_ci[1]):.2f}%**, so the preregistered
primary gate passes.

## 2. Optimization pipeline

**Caption:** One pinned `llama.cpp` revision and one Qwen2.5 1.5B Q4_0 model feed same-machine
generic and verified KleidiAI builds, a bounded search, a held-out quality/safety gate, and an
evidence-producing endpoint. Split-v2 decisions were frozen before final evaluation.

## 3. Fair Pareto evidence

**Caption:** The Pareto view keeps the fair A1/A2 comparison visible while disclosing the secondary
p95 end-to-end latency result: **{float(p95["value"]):.2f}%** reduction, paired 95% interval
**{float(p95_ci[0]):.2f}% to {float(p95_ci[1]):.2f}%**. Because that interval crosses zero, it is
reported transparently and does not unlock publication.

## 4. Validated API and deployed-profile status

**Caption:** The submitted API serves the measured **strong-only** profile
`{profile.get("profile_id")}` and enforces the benchmark's strict triage schema, read-only tool
policy, and fail-closed HTTP 502 behavior. A4 routing is **{cascade.get("status")}**; the shipping
fallback is `{cascade.get("shipping_fallback")}`, so no unmeasured cascade claim is made.
"""


def generate_screenshots(root: Path, video: Path) -> dict[str, Any]:
    """Extract reviewed static scenes and return the screenshot manifest."""

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SubmissionAssetError("ffmpeg and ffprobe are required to extract screenshots")
    if not video.is_file():
        raise SubmissionAssetError(f"attested source video does not exist: {video}")
    video_manifest_path = root / "artifacts" / "submission" / "a64pilot-demo-final.manifest.json"
    video_manifest = _require_mapping(
        _read_json(video_manifest_path), label=str(video_manifest_path)
    )
    video_sha256 = _sha256(video)
    if (
        video_manifest.get("mode") != "final_measured"
        or video_manifest.get("publishable") is not True
        or video_manifest.get("output_sha256") != video_sha256
    ):
        raise SubmissionAssetError("source video does not match the publishable measured manifest")
    duration = _video_duration(video, ffprobe=ffprobe)
    screenshots_dir = root / "artifacts" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for spec in SCREENSHOT_SPECS:
        destination = screenshots_dir / spec["filename"]
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{spec['timecode_seconds']:.3f}",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=1920:1080:flags=lanczos,format=rgb24",
                "-compression_level",
                "6",
                str(destination),
            ]
        )
        width, height = _probe_image(destination, ffprobe=ffprobe)
        if (width, height) != (1920, 1080):
            raise SubmissionAssetError(f"unexpected screenshot dimensions: {destination}")
        data_sources = {}
        for relative in spec["data_sources"]:
            path = root / relative
            if not path.is_file():
                raise SubmissionAssetError(f"screenshot provenance source is missing: {relative}")
            data_sources[relative] = _sha256(path)
        entries.append(
            {
                **spec,
                "path": f"artifacts/screenshots/{spec['filename']}",
                "sha256": _sha256(destination),
                "width": width,
                "height": height,
                "extraction": "exact video frame; no post-capture text or metric edits",
                "data_sources": data_sources,
            }
        )

    captions_path = screenshots_dir / "captions.md"
    captions_path.write_text(_captions(root), encoding="utf-8")
    source_provenance = _source_video_provenance(root, video_manifest)
    manifest = {
        "schema_version": "1.0",
        "evidence_status": "measured",
        "source_video": {
            "kind": "CI demo source with workflow-bound provenance",
            **source_provenance,
            "duration_seconds": round(duration, 3),
            "sha256": video_sha256,
            "manifest_path": _relative(root, video_manifest_path),
            "manifest_sha256": _sha256(video_manifest_path),
        },
        "capture_policy": {
            "frame_dimensions": "1920x1080",
            "frame_editing": "none",
            "timecodes": "reviewed static scene interiors, away from scene transitions",
            "captions_path": "artifacts/screenshots/captions.md",
            "captions_sha256": _sha256(captions_path),
        },
        "screenshots": entries,
    }
    return manifest


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_assets(root: Path, *, video: Path) -> None:
    artifacts = root / "artifacts"
    quality = build_quality_summary(root)
    (artifacts / "quality-summary.json").write_text(_canonical_json(quality), encoding="utf-8")
    screenshots = generate_screenshots(root, video)
    (artifacts / "screenshots" / "manifest.json").write_text(
        _canonical_json(screenshots), encoding="utf-8"
    )


def write_current_run_index(root: Path) -> Path:
    destination = root / "artifacts" / "evidence-index.json"
    destination.write_text(
        _canonical_json(build_current_run_index(root)),
        encoding="utf-8",
    )
    return destination


def verify_assets(root: Path) -> None:
    """Prove generated JSON, source hashes, screenshot hashes, and dimensions."""

    expected_quality = build_quality_summary(root)
    quality_path = root / "artifacts" / "quality-summary.json"
    if _read_json(quality_path) != expected_quality:
        raise SubmissionAssetError(
            "quality-summary.json is stale; regenerate with generate-submission-assets.py"
        )
    screenshots_dir = root / "artifacts" / "screenshots"
    manifest = _require_mapping(
        _read_json(screenshots_dir / "manifest.json"), label="screenshot manifest"
    )
    source_video = _require_mapping(manifest.get("source_video"), label="screenshot source video")
    video_manifest_value = source_video.get("manifest_path")
    if not isinstance(video_manifest_value, str) or not video_manifest_value:
        raise SubmissionAssetError("screenshot source video has no manifest path")
    video_manifest_path = (root / video_manifest_value).resolve()
    try:
        video_manifest_path.relative_to(root.resolve())
    except ValueError as exc:
        raise SubmissionAssetError("screenshot source manifest escapes the project root") from exc
    if _sha256(video_manifest_path) != source_video.get("manifest_sha256"):
        raise SubmissionAssetError("screenshot source video manifest hash mismatch")
    video_manifest = _require_mapping(
        _read_json(video_manifest_path), label=str(video_manifest_path)
    )
    if (
        video_manifest.get("mode") != "final_measured"
        or video_manifest.get("publishable") is not True
        or video_manifest.get("output_sha256") != source_video.get("sha256")
    ):
        raise SubmissionAssetError("screenshots are not bound to a publishable final video")
    target = _mapping_or_empty(video_manifest.get("target_device_footage"))
    if "github_run_id" in source_video and _run_id(
        source_video.get("github_run_id"), label="screenshot source run"
    ) != _run_id(target.get("github_run_id"), label="video receipt run"):
        raise SubmissionAssetError("screenshot source run disagrees with the final video receipt")
    if "github_sha" in source_video and _commit_sha(
        source_video.get("github_sha"), label="screenshot source commit"
    ) != _commit_sha(target.get("github_sha"), label="video receipt commit"):
        raise SubmissionAssetError(
            "screenshot source commit disagrees with the final video receipt"
        )
    entries = manifest.get("screenshots")
    if not isinstance(entries, list) or len(entries) != len(SCREENSHOT_SPECS):
        raise SubmissionAssetError("screenshot manifest must contain exactly four reviewed frames")
    expected_ids = {spec["id"] for spec in SCREENSHOT_SPECS}
    actual_ids: set[str] = set()
    for entry in entries:
        item = _require_mapping(entry, label="screenshot manifest entry")
        screenshot_id = item.get("id")
        path_value = item.get("path")
        if not isinstance(screenshot_id, str) or not isinstance(path_value, str):
            raise SubmissionAssetError("screenshot manifest entry has no id or path")
        path = root / path_value
        if _sha256(path) != item.get("sha256"):
            raise SubmissionAssetError(f"screenshot hash mismatch: {path_value}")
        if _png_dimensions(path) != (1920, 1080):
            raise SubmissionAssetError(f"screenshot dimensions changed: {path_value}")
        data_sources = _require_mapping(
            item.get("data_sources"), label=f"{screenshot_id} data sources"
        )
        for relative, expected_hash in data_sources.items():
            if not isinstance(relative, str) or _sha256(root / relative) != expected_hash:
                raise SubmissionAssetError(f"screenshot provenance changed: {relative}")
        actual_ids.add(screenshot_id)
    if actual_ids != expected_ids:
        raise SubmissionAssetError("screenshot IDs do not match the four checklist requirements")
    captions_path = screenshots_dir / "captions.md"
    capture_policy = _require_mapping(
        manifest.get("capture_policy"), label="screenshot capture policy"
    )
    if _sha256(captions_path) != capture_policy.get("captions_sha256"):
        raise SubmissionAssetError("screenshot captions hash mismatch")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--video",
        type=Path,
        help="local copy of the attested a64pilot-demo-final.mp4 release asset",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify committed downstream artifacts without requiring the source MP4",
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="regenerate only quality-summary.json from the current measured artifacts",
    )
    parser.add_argument(
        "--current-run-index-only",
        action="store_true",
        help="replace a stale curated index with the current pre-attestation workflow index",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    try:
        modes = (args.verify_only, args.quality_only, args.current_run_index_only)
        if sum(bool(value) for value in modes) > 1:
            raise SubmissionAssetError(
                "--verify-only, --quality-only, and --current-run-index-only are mutually exclusive"
            )
        if args.verify_only:
            verify_assets(root)
            print("submission assets verified: quality summary + 4 screenshot frames")
            return 0
        if args.quality_only:
            quality_path = root / "artifacts" / "quality-summary.json"
            quality_path.write_text(_canonical_json(build_quality_summary(root)), encoding="utf-8")
            print(f"quality summary generated: {quality_path}")
            return 0
        if args.current_run_index_only:
            index_path = write_current_run_index(root)
            print(f"current pre-attestation evidence index generated: {index_path}")
            return 0
        if args.video is None:
            raise SubmissionAssetError(
                "--video is required unless a verify/quality/index-only mode is used"
            )
        video = args.video if args.video.is_absolute() else (Path.cwd() / args.video)
        write_assets(root, video=video.resolve())
        verify_assets(root)
    except SubmissionAssetError as exc:
        print(f"submission assets failed: {exc}", file=sys.stderr)
        return 1
    print("submission assets generated and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
