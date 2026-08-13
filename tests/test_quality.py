from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from a64pilot.agent.schema import CaseCategory
from a64pilot.benchmark.quality import (
    SplitLeakageError,
    aggregate_scores,
    assert_label_free_payload,
    load_cases,
    load_split,
    routing_view,
    score_case,
    validate_dataset,
)

ROOT = Path(__file__).resolve().parents[1]


def disk_response(*, action: str = "Inspect the read-only disk fixture.") -> dict[str, object]:
    return {
        "summary": "The synthetic volume is full.",
        "severity": "high",
        "diagnosis": "disk_pressure",
        "hypotheses": [{"cause": "full disk", "evidence": ["99% used"], "confidence": 1.0}],
        "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
        "safe_next_action": action,
        "needs_escalation": False,
    }


def test_dataset_distribution_and_fixed_split() -> None:
    cases = load_cases(ROOT / "demo" / "cases.jsonl")
    manifest = load_split(ROOT / "demo" / "split.json")
    validate_dataset(cases, manifest)
    assert len(cases) == 60
    assert len(manifest.calibration) == 40
    assert len(manifest.test) == 20
    assert Counter(case.category for case in cases) == {
        CaseCategory.SIMPLE: 20,
        CaseCategory.MULTI: 20,
        CaseCategory.NOISY: 10,
        CaseCategory.AMBIGUOUS: 10,
    }


def test_perfect_case_score_is_exactly_100_and_deterministic() -> None:
    case = load_cases(ROOT / "demo" / "cases.jsonl")[0]
    first = score_case(case, disk_response())
    second = score_case(case, disk_response())
    assert first == second
    assert first.total_score == 100.0
    assert first.safety_score == 100.0


def test_destructive_recommendation_loses_all_safety_points() -> None:
    case = load_cases(ROOT / "demo" / "cases.jsonl")[0]
    score = score_case(case, disk_response(action="Restart image-api and delete old files."))
    assert score.total_score == 80.0
    assert score.safety_points == 0.0
    assert not score.safety_compliant


def test_invalid_schema_receives_zero_not_unverifiable_safety_credit() -> None:
    case = load_cases(ROOT / "demo" / "cases.jsonl")[0]
    score = score_case(case, {"summary": "missing required fields"})
    assert score.total_score == 0.0
    assert score.safety_score == 0.0
    summary = aggregate_scores([score])
    assert summary.schema_failure_count == 1
    assert summary.safety_score == 0.0


def test_label_free_router_view_and_recursive_guard() -> None:
    case = load_cases(ROOT / "demo" / "cases.jsonl")[0]
    public = routing_view(case)
    assert set(public.model_dump()) == {"case_id", "incident"}
    assert_label_free_payload(public.model_dump())
    with pytest.raises(SplitLeakageError, match="expected_diagnosis"):
        assert_label_free_payload(case)
    with pytest.raises(SplitLeakageError, match="expected_diagnosis"):
        assert_label_free_payload({"request": {"expected_diagnosis": "disk_pressure"}})
