from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from a64pilot.report.claims import PRIMARY_CLAIM_ID
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
        cases_sha256="a" * 64,
        split_sha256="b" * 64,
        split_schema_version="2.0",
        primary_claim_id=PRIMARY_CLAIM_ID,
    )
    parser = _NavigationParser()
    parser.feed(rendered)
    expected = {"overview", "candidates", "figures", "evidence", "limitations"}
    assert expected <= parser.ids
    assert {f"#{target}" for target in expected} <= set(parser.hrefs)
    assert rendered.count("<h1>") == 1
    assert 'aria-label="Report sections"' in rendered
    assert "aaaaaaaaaaaa…" in rendered
    assert "bbbbbbbbbbbb…" in rendered


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
    assert expected_cases[:12] in rendered["markdown"].read_text(encoding="utf-8")
    assert expected_split[:12] in rendered["html"].read_text(encoding="utf-8")
