from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


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
    root = Path(__file__).parents[1]
    environment = Environment(
        loader=FileSystemLoader(root / "templates"),
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
    )
    parser = _NavigationParser()
    parser.feed(rendered)
    expected = {"overview", "candidates", "figures", "evidence", "limitations"}
    assert expected <= parser.ids
    assert {f"#{target}" for target in expected} <= set(parser.hrefs)
    assert rendered.count("<h1>") == 1
    assert 'aria-label="Report sections"' in rendered
