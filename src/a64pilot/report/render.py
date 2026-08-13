"""Deterministically render public evidence artifacts from raw records."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from a64pilot.benchmark.statistics import summarize
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.provenance import sha256_file, write_json
from a64pilot.report.claims import generate_claims, verify_claim_held_out_coverage
from a64pilot.report.figures import render_ablation, render_pareto
from a64pilot.report.integrity import validate_evidence_bundle, verify_claim_sources
from a64pilot.schemas import BenchmarkRecord


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def render_report(
    *,
    project_root: Path | str = ".",
    artifacts_dir: Path | str = "artifacts",
    templates_dir: Path | str = "templates",
) -> dict[str, Path]:
    root = Path(project_root)
    artifacts = root / artifacts_dir
    artifacts.mkdir(parents=True, exist_ok=True)
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
    system = _load_json(artifacts / "system-info.json")
    build = _load_json(artifacts / "build-manifest.json")
    models = _load_json(artifacts / "model-manifest.json")

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
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "summaries": summaries,
        "limitations": [
            "Results apply only to the recorded target, model files, runtime commit, and workload.",
            "The synthetic incident suite is not a general LLM capability benchmark.",
            "No energy or cloud-cost claim is made without a credible counter or supplied price.",
            "Fixture responses are excluded from every performance claim.",
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
        },
    )
    return {
        "html": artifacts / "report.html",
        "markdown": artifacts / "report.md",
        "claims": artifacts / "claims.json",
        "ablation": ablation_path,
        "pareto": pareto_path,
    }
