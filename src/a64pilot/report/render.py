"""Deterministically render public evidence artifacts from raw records."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from a64pilot.benchmark.probes import load_performance_probes, summarize_performance_probes
from a64pilot.benchmark.statistics import summarize
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.provenance import sha256_file, write_json
from a64pilot.report.claims import (
    PRIMARY_CLAIM_ID,
    generate_claims,
    verify_claim_held_out_coverage,
)
from a64pilot.report.figures import render_ablation, render_pareto
from a64pilot.report.integrity import validate_evidence_bundle, verify_claim_sources
from a64pilot.schemas import BenchmarkRecord
from a64pilot.schemas import SystemInfo as SystemInfoSchema


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _workload_provenance(root: Path) -> dict[str, str]:
    """Fingerprint the exact public cases and split used for this report."""

    cases_path = root / "demo" / "cases.jsonl"
    split_path = root / "demo" / "split.json"
    split = _load_json(split_path)
    if not isinstance(split, dict) or not isinstance(split.get("schema_version"), str):
        raise ValueError(f"split manifest has no schema_version: {split_path}")
    return {
        "cases_sha256": sha256_file(cases_path),
        "split_sha256": sha256_file(split_path),
        "split_schema_version": split["schema_version"],
    }


def _summaries(records: list[BenchmarkRecord]) -> list[dict[str, Any]]:
    groups: dict[str, list[BenchmarkRecord]] = {}
    for record in records:
        if record.evidence_kind == "measured":
            groups.setdefault(record.candidate_id, []).append(record)
    output: list[dict[str, Any]] = []
    for candidate_id, rows in sorted(groups.items()):
        latency = summarize([row.e2e_ms for row in rows])
        output.append(
            {
                "candidate_id": candidate_id,
                "stage": rows[0].stage,
                "backend": rows[0].backend,
                "sample_count": len(rows),
                "p50_latency_ms": latency.p50,
                "p95_latency_ms": latency.p95,
                "quality_score": sum(row.quality_score for row in rows) / len(rows),
                "safety_score": sum(row.safety_score for row in rows) / len(rows),
                "peak_rss_mb": max(row.peak_rss_mb for row in rows),
                "source_run_ids": [row.run_id for row in rows],
            }
        )
    return output


def _formal_repetitions_per_case(records: list[BenchmarkRecord]) -> int | None:
    """Return the uniform fair A1/A2 quality repetition count, if available."""

    selected = [
        record
        for record in records
        if (record.backend, record.stage)
        in {
            ("generic", "baseline"),
            ("kleidiai", "kleidiai"),
        }
    ]
    if not selected:
        return None
    repetitions: dict[tuple[str, str], set[int]] = {}
    for record in selected:
        repetitions.setdefault((record.candidate_id, record.case_id), set()).add(record.repetition)
    values = list(repetitions.values())
    expected = values[0]
    if not expected or expected != set(range(max(expected) + 1)):
        return None
    if any(value != expected for value in values[1:]):
        return None
    return len(expected)


def _formal_sample_disclosure(records: list[BenchmarkRecord]) -> dict[str, Any] | None:
    """Disclose measured A1/A2 rows, paired cases, and fail-closed omissions."""

    selected = [
        record
        for record in records
        if (record.backend, record.stage) in {("generic", "baseline"), ("kleidiai", "kleidiai")}
    ]
    repetitions = _formal_repetitions_per_case(selected)
    if not selected or repetitions is None:
        return None
    by_backend = {
        backend: {record.case_id for record in selected if record.backend == backend}
        for backend in ("generic", "kleidiai")
    }
    paired_cases = by_backend["generic"] & by_backend["kleidiai"]
    scheduled_cases = by_backend["generic"] | by_backend["kleidiai"]
    expected_rows = len(scheduled_cases) * 2 * repetitions
    return {
        "quality_repetitions_per_case": repetitions,
        "paired_case_count": len(paired_cases),
        "measured_rows": len(selected),
        "expected_rows": expected_rows,
        "failed_or_missing_rows": max(0, expected_rows - len(selected)),
        "complete": len(selected) == expected_rows,
    }


def render_report(
    *,
    project_root: Path | str = ".",
    artifacts_dir: Path | str = "artifacts",
    templates_dir: Path | str = "templates",
) -> dict[str, Path]:
    root = Path(project_root)
    artifacts = root / artifacts_dir
    artifacts.mkdir(parents=True, exist_ok=True)
    system_payload = _load_json(artifacts / "system-info.json")
    if system_payload is None:
        raise ValueError("system-info.json is required before report rendering")
    try:
        system = SystemInfoSchema.model_validate(system_payload).model_dump(mode="json")
    except Exception as exc:
        raise ValueError(
            f"system-info.json failed schema validation: {type(exc).__name__}"
        ) from exc
    store = ArtifactStore(artifacts / "raw")
    records = list(store.records())
    measured = [record for record in records if record.evidence_kind == "measured"]
    if measured:
        measured, evidence_errors = validate_evidence_bundle(artifacts, require_records=True)
        if evidence_errors:
            raise ValueError("evidence integrity failed: " + "; ".join(evidence_errors))
        # Diagnostic smoke and calibration rows remain in raw/ but never enter
        # formal public performance summaries or claims.
        measured = [record for record in measured if record.split == "test"]
    claims = generate_claims(measured, split_path=root / "demo/split.json")
    claim_errors = verify_claim_sources(claims, measured)
    claim_errors.extend(
        verify_claim_held_out_coverage(
            claims,
            measured,
            split_path=root / "demo/split.json",
        )
    )
    if claim_errors:
        raise ValueError("claim integrity failed: " + "; ".join(claim_errors))
    summaries = _summaries(measured)
    build = _load_json(artifacts / "build-manifest.json")
    models = _load_json(artifacts / "model-manifest.json")
    cascade_status = _load_json(artifacts / "cascade-status.json")
    cascade_quality = _load_json(artifacts / "quality-results.json")
    workload_provenance = _workload_provenance(root)
    probe_path = artifacts / "performance-probes.json"
    performance_probes = None
    probe_provenance = None
    if probe_path.is_file():
        probe_evidence = load_performance_probes(
            probe_path,
            project_root=root,
            require_current_files=True,
        )
        performance_probes = summarize_performance_probes(probe_evidence)
        build_variants = (build or {}).get("variants", []) if isinstance(build, dict) else []
        model_rows = (models or {}).get("models", []) if isinstance(models, dict) else []
        used_model_hashes = {run.model_file_sha256 for run in probe_evidence.micro_runs} | {
            run.model_file_sha256 for run in probe_evidence.service_runs
        }
        probe_provenance = {
            "source_url": (build or {}).get("source_url") if isinstance(build, dict) else None,
            "source_commit": probe_evidence.build_source_commit,
            "build_variants": [
                {
                    "backend": row.get("backend"),
                    "cpu_only_configured": row.get("cpu_only_configured"),
                    "kleidiai_configured": row.get("kleidiai_configured"),
                    "runtime_marker_verified": row.get("runtime_marker_verified"),
                    "llama_bench_sha256": row.get("binary_sha256", {}).get("llama-bench"),
                    "llama_server_sha256": row.get("binary_sha256", {}).get("llama-server"),
                }
                for row in build_variants
                if isinstance(row, dict)
            ],
            "models": [
                {
                    "filename": row.get("filename"),
                    "quantization": row.get("quantization"),
                    "bytes": row.get("bytes"),
                    "sha256": row.get("sha256"),
                    "tensor_inventory_sha256": row.get("tensor_inventory_sha256"),
                }
                for row in model_rows
                if isinstance(row, dict) and row.get("sha256") in used_model_hashes
            ],
        }
    formal_repetitions_per_case = _formal_repetitions_per_case(measured)
    formal_sample_disclosure = _formal_sample_disclosure(measured)
    target_provenance_limitations = (
        system.get("provenance_limitations", []) if isinstance(system, dict) else []
    )

    figures = artifacts / "figures"
    ablation_path = render_ablation(measured, figures / "ablation.png")
    pareto_path = render_pareto(measured, figures / "pareto.png")
    write_json(artifacts / "claims.json", [claim.model_dump(mode="json") for claim in claims])
    write_json(
        artifacts / "benchmark-results.json",
        [record.model_dump(mode="json") for record in measured],
    )
    with (artifacts / "benchmark-results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(BenchmarkRecord.model_fields)
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in measured:
            writer.writerow(record.model_dump(mode="json"))
    with (artifacts / "ablation-results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(summaries[0]) if summaries else ["candidate_id", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        if summaries:
            writer.writerows(summaries)
        else:
            writer.writerow({"candidate_id": "none", "status": "Arm64 measurement pending"})

    data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "evidence_status": "measured" if measured else "measurement-pending",
        "measurement_count": len(measured),
        "fixture_count": len(records) - len(measured),
        "system": system,
        "build": build,
        "models": models,
        "cascade_status": cascade_status,
        "cascade_quality": cascade_quality,
        "performance_probes": performance_probes,
        "probe_provenance": probe_provenance,
        "formal_repetitions_per_case": formal_repetitions_per_case,
        "formal_sample_disclosure": formal_sample_disclosure,
        **workload_provenance,
        "primary_claim_id": PRIMARY_CLAIM_ID,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "summaries": summaries,
        "limitations": [
            "Results apply only to the recorded target, model files, runtime commit, and workload.",
            "The synthetic incident suite is not a general LLM capability benchmark.",
            "No energy or cloud-cost claim is made without a credible counter or supplied price.",
            "Fixture responses are excluded from every performance claim.",
            (
                "A4 calibration replays measured weak/strong outputs for quality and routing; "
                "it is not live-cascade latency, throughput, or combined-RSS evidence."
            ),
            *[
                (
                    f"Target provenance limitation {item.get('code', 'unknown')} "
                    f"({item.get('field', 'unknown')}): {item.get('reason', 'no reason recorded')}"
                )
                for item in target_provenance_limitations
                if isinstance(item, dict)
            ],
        ],
    }
    write_json(artifacts / "report-data.json", data)
    environment = Environment(
        loader=FileSystemLoader(root / templates_dir),
        undefined=StrictUndefined,
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    report_html = environment.get_template("report.html.j2").render(**data)
    report_md = environment.get_template("report.md.j2").render(**data)
    (artifacts / "report.html").write_text(report_html, encoding="utf-8")
    (artifacts / "report.md").write_text(report_md, encoding="utf-8")
    write_json(
        artifacts / "report-integrity.json",
        {
            "report.html": sha256_file(artifacts / "report.html"),
            "report.md": sha256_file(artifacts / "report.md"),
            "claims.json": sha256_file(artifacts / "claims.json"),
            "benchmark-results.json": sha256_file(artifacts / "benchmark-results.json"),
            "report-data.json": sha256_file(artifacts / "report-data.json"),
            **(
                {"performance-probes.json": sha256_file(probe_path)} if probe_path.is_file() else {}
            ),
        },
    )
    return {
        "html": artifacts / "report.html",
        "markdown": artifacts / "report.md",
        "claims": artifacts / "claims.json",
        "ablation": ablation_path,
        "pareto": pareto_path,
    }
