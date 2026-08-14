from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from a64pilot.report.claims import PRIMARY_CLAIM_ID
from a64pilot.report.integrity import verify_report_integrity
from a64pilot.report.render import render_report

ROOT = Path(__file__).parents[1]


class _NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if tag == "a" and (href := attributes.get("href")):
            self.hrefs.append(href)


def test_offline_report_navigation_targets_exist() -> None:
    environment = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        undefined=StrictUndefined,
        autoescape=False,
    )
    rendered = environment.get_template("report.html.j2").render(
        evidence_status="measurement-pending",
        generated_at="2026-08-14T00:00:00Z",
        measurement_count=0,
        fixture_count=0,
        claims=[],
        summaries=[],
        limitations=["Target-specific."],
        system={
            "architecture": "aarch64",
            "cpu_model": "Neoverse-N2",
            "distribution": {"pretty_name": "Ubuntu 24.04 LTS"},
            "kernel": "fixture-kernel",
            "physical_cores": 4,
            "logical_cores": 4,
            "sockets": 1,
            "numa_nodes": 1,
            "memory_bytes": 16 * 1024**3,
            "tool_versions": {"compiler": "fixture-cc 1.0"},
            "cache_layout": [
                {
                    "name": "l3",
                    "total_size_bytes": 33554432,
                    "instances": 1,
                    "source": "Linux sysfs CPU cache topology",
                }
            ],
            "provenance_limitations": [],
            "target_provenance_status": "complete",
        },
        cases_sha256="a" * 64,
        split_sha256="b" * 64,
        split_schema_version="2.0",
        primary_claim_id=PRIMARY_CLAIM_ID,
    )
    parser = _NavigationParser()
    parser.feed(rendered)
    expected = {
        "overview",
        "target",
        "candidates",
        "probes",
        "figures",
        "evidence",
        "limitations",
    }
    assert expected <= parser.ids
    assert {f"#{target}" for target in expected} <= set(parser.hrefs)
    assert rendered.count("<h1>") == 1
    assert 'aria-label="Report sections"' in rendered
    assert "aaaaaaaaaaaa…" in rendered
    assert "bbbbbbbbbbbb…" in rendered
    assert "Neoverse-N2" in rendered
    assert "Ubuntu 24.04 LTS" in rendered
    assert "Supporting micro/startup/token/p1/p2 evidence is pending" in rendered


def test_report_data_fingerprints_current_cases_and_split(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    cases = demo / "cases.jsonl"
    split = demo / "split.json"
    cases.write_text(
        '{"case_id":"incident-001","category":"simple"}\n',
        encoding="utf-8",
    )
    split.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "seed": 20260813,
                "calibration": [],
                "test": [f"incident-{index:03d}" for index in range(1, 21)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    cache_layout = [
        {
            "name": name,
            "level": level,
            "kind": kind,
            "total_size_bytes": size,
            "instances": 1,
            "shared_cpu_lists": [],
            "source": "fixture",
        }
        for name, level, kind, size in (
            ("l1d", 1, "data", 64 * 1024),
            ("l1i", 1, "instruction", 64 * 1024),
            ("l2", 2, "unified", 1024**2),
            ("l3", 3, "unified", 8 * 1024**2),
        )
    ]
    (artifacts / "system-info.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0.0",
                "collected_at": "2026-08-14T00:00:00Z",
                "architecture": "aarch64",
                "architecture_raw": "aarch64",
                "operating_system": "Linux",
                "kernel": "fixture-kernel",
                "cpu_model": "Fixture Arm CPU",
                "arm64": True,
                "real_benchmark_eligible": True,
                "logical_cores": 4,
                "physical_cores": 4,
                "memory_bytes": 16 * 1024**3,
                "sockets": 1,
                "numa_nodes": 1,
                "distribution": {
                    "pretty_name": "Fixture Linux 1",
                    "identifier": "fixture",
                    "version_id": "1",
                    "source": "/etc/os-release",
                },
                "cache_layout": cache_layout,
                "tool_versions": {"compiler": "fixture-cc 1.0"},
                "features": {"dotprod": {"supported": True, "evidence": ["fixture: asimddp"]}},
                "topology": {
                    "logical_cpus": 4,
                    "physical_cores": 4,
                    "allowed_cpus": [0, 1, 2, 3],
                    "cores": [
                        {
                            "cpu_id": cpu,
                            "core_id": cpu,
                            "package_id": 0,
                            "max_frequency_khz": None,
                            "capacity": 1024,
                            "numa_node": 0,
                        }
                        for cpu in range(4)
                    ],
                    "affinity_candidates": [
                        {
                            "name": "all_allowed",
                            "cpus": [0, 1, 2, 3],
                            "evidence": ["fixture"],
                        }
                    ],
                    "sockets": 1,
                    "numa_nodes": 1,
                    "cache_layout": cache_layout,
                    "sources": ["fixture"],
                    "limitations": [],
                },
                "affinity_candidates": {"all_allowed": [0, 1, 2, 3]},
                "provenance_limitations": [],
                "target_provenance_status": "complete",
                "limitations": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    rendered = render_report(
        project_root=tmp_path,
        templates_dir=ROOT / "templates",
    )
    report_data = json.loads(
        (tmp_path / "artifacts" / "report-data.json").read_text(encoding="utf-8")
    )
    expected_cases = hashlib.sha256(cases.read_bytes()).hexdigest()
    expected_split = hashlib.sha256(split.read_bytes()).hexdigest()
    assert report_data["cases_sha256"] == expected_cases
    assert report_data["split_sha256"] == expected_split
    assert report_data["split_schema_version"] == "2.0"
    assert report_data["primary_claim_id"] == PRIMARY_CLAIM_ID
    assert report_data["performance_probes"] is None
    report_integrity = json.loads(
        (tmp_path / "artifacts" / "report-integrity.json").read_text(encoding="utf-8")
    )
    assert (
        report_integrity["report-data.json"]
        == hashlib.sha256((tmp_path / "artifacts" / "report-data.json").read_bytes()).hexdigest()
    )
    assert verify_report_integrity(tmp_path / "artifacts") == []
    report_md = tmp_path / "artifacts" / "report.md"
    original_markdown = report_md.read_bytes()
    report_md.write_bytes(original_markdown + b"\ntampered\n")
    assert "report integrity digest mismatch: report.md" in verify_report_integrity(
        tmp_path / "artifacts"
    )
    report_md.write_bytes(original_markdown)

    report_data["system"]["cpu_model"] = "tampered CPU"
    report_data_path = tmp_path / "artifacts" / "report-data.json"
    report_data_path.write_text(json.dumps(report_data), encoding="utf-8")
    report_integrity["report-data.json"] = hashlib.sha256(report_data_path.read_bytes()).hexdigest()
    (tmp_path / "artifacts" / "report-integrity.json").write_text(
        json.dumps(report_integrity), encoding="utf-8"
    )
    assert "report-data system manifest disagrees with system-info.json" in verify_report_integrity(
        tmp_path / "artifacts"
    )
    assert expected_cases[:12] in rendered["markdown"].read_text(encoding="utf-8")
    assert expected_split[:12] in rendered["html"].read_text(encoding="utf-8")


def test_report_rejects_invalid_system_info_before_reading_records(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "system-info.json").write_text(
        json.dumps(
            {
                "architecture": "aarch64",
                "operating_system": "Linux",
                "kernel": "fixture",
                "cpu_model": "unknown",
                "logical_cores": 4,
                "limitations": [],
                "provenance_limitations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="system-info.json failed schema validation"):
        render_report(project_root=tmp_path, templates_dir=ROOT / "templates")
