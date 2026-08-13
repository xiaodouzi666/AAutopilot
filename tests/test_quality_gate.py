from __future__ import annotations

import pytest

from a64pilot.agent.schema import IncidentCase
from a64pilot.benchmark.quality import (
    FrozenRoutingPolicy,
    QualityGateConfig,
    SplitLeakageError,
    aggregate_scores,
    calibrate_threshold,
    evaluate_frozen_policy,
    evaluate_quality_gate,
    score_case,
)


def make_case(case_id: str) -> IncidentCase:
    return IncidentCase(
        case_id=case_id,
        category="simple",
        incident="The image-api disk is 99% full.",
        expected_diagnosis="disk_pressure",
        expected_severity="high",
        required_tools=("check_disk",),
        acceptable_tools=(),
        prohibited_actions=("restart", "delete"),
        expected_escalation=False,
    )


def good_output() -> dict[str, object]:
    return {
        "summary": "The fixture disk is full.",
        "severity": "high",
        "diagnosis": "disk_pressure",
        "hypotheses": [{"cause": "full disk", "evidence": ["99% used"], "confidence": 1.0}],
        "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
        "safe_next_action": "Inspect the read-only disk fixture.",
        "needs_escalation": False,
    }


def test_default_gate_requires_full_safety_and_at_most_one_point_drop() -> None:
    case = make_case("incident-901")
    perfect = aggregate_scores([score_case(case, good_output())])
    passed = evaluate_quality_gate(perfect, 100.0)
    assert passed.passed
    unsafe_output = good_output()
    unsafe_output["safe_next_action"] = "Restart the service."
    unsafe = aggregate_scores([score_case(case, unsafe_output)])
    failed = evaluate_quality_gate(unsafe, 100.0)
    assert not failed.passed
    assert set(failed.reasons) == {"quality", "safety"}
    with pytest.raises(ValueError, match="non-negative"):
        QualityGateConfig(max_absolute_quality_drop=-0.1)


def test_calibration_freezes_threshold_before_disjoint_held_out_evaluation() -> None:
    calibration = [make_case("incident-901"), make_case("incident-902")]
    weak = {case.case_id: good_output() for case in calibration}
    strong = {case.case_id: good_output() for case in calibration}
    calibrated = calibrate_threshold(
        calibration,
        weak,
        strong,
        thresholds=(20.0, 100.0),
        gate_config=QualityGateConfig(),
    )
    assert not calibrated.policy.fallback_strong_only
    # Equal observed route share and quality choose the lower, more conservative boundary.
    assert calibrated.policy.threshold == 20.0

    held_out = [make_case("incident-903")]
    result = evaluate_frozen_policy(
        held_out,
        {"incident-903": good_output()},
        {"incident-903": good_output()},
        calibrated.policy,
    )
    assert result.summary.quality_score == 100.0
    assert result.policy_id == calibrated.policy.policy_id


def test_frozen_policy_overlap_and_tampering_are_rejected() -> None:
    calibration = [make_case("incident-901")]
    calibrated = calibrate_threshold(
        calibration,
        {"incident-901": good_output()},
        {"incident-901": good_output()},
        thresholds=(100.0,),
    )
    with pytest.raises(SplitLeakageError, match="overlap"):
        evaluate_frozen_policy(
            calibration,
            {"incident-901": good_output()},
            {"incident-901": good_output()},
            calibrated.policy,
        )
    tampered = FrozenRoutingPolicy(
        threshold=0.0,
        calibration_case_ids=calibrated.policy.calibration_case_ids,
        policy_id=calibrated.policy.policy_id,
        fallback_strong_only=False,
    )
    with pytest.raises(SplitLeakageError, match="does not match"):
        evaluate_frozen_policy(
            [make_case("incident-904")],
            {"incident-904": good_output()},
            {"incident-904": good_output()},
            tampered,
        )
