from __future__ import annotations

import pytest

from a64pilot.agent.complexity import extract_complexity_features, score_complexity
from a64pilot.agent.router import CascadeRouter, ComplexityRouter, InvalidStrongModelOutput


def valid_disk_response() -> dict[str, object]:
    return {
        "summary": "The fixture volume is full.",
        "severity": "high",
        "diagnosis": "disk_pressure",
        "hypotheses": [{"cause": "disk full", "evidence": ["99% used"], "confidence": 0.99}],
        "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
        "safe_next_action": "Inspect the read-only fixture and notify the operator.",
        "needs_escalation": False,
    }


def test_complexity_features_are_request_only_and_transparent() -> None:
    simple = "The image-api filesystem is 99% full."
    complex_text = (
        "It might be disk or memory, but evidence is contradictory.\n"
        "ERROR `checkout-api` timed out calling `cart-db`.\n"
        "WARN packet loss and OOM were both reported."
    )
    simple_report = score_complexity(simple)
    complex_report = score_complexity(complex_text)
    assert complex_report.score > simple_report.score
    assert complex_report.contributions["contradictions"] > 0
    assert complex_report.contributions["ambiguity"] > 0
    assert extract_complexity_features(complex_text).named_service_count == 2


def test_default_router_sends_ambiguity_directly_to_strong() -> None:
    assert (
        ComplexityRouter().decide("An intermittent error has unknown cause and no logs.").route
        == "strong"
    )
    assert ComplexityRouter().decide("The image-api disk is full.").route == "weak"


def test_valid_weak_output_is_returned_without_strong_call() -> None:
    calls = {"weak": 0, "strong": 0}

    def weak(_: str) -> dict[str, object]:
        calls["weak"] += 1
        return valid_disk_response()

    def strong(_: str) -> dict[str, object]:
        calls["strong"] += 1
        return valid_disk_response()

    result = CascadeRouter(weak, strong).route("The image-api disk is full.")
    assert result.metadata.final_route == "weak"
    assert calls == {"weak": 1, "strong": 0}


def test_unsafe_weak_output_automatically_escalates() -> None:
    weak_output = valid_disk_response()
    weak_output["safe_next_action"] = "Restart the service and delete old files."
    result = CascadeRouter(lambda _: weak_output, lambda _: valid_disk_response()).route(
        "The image-api disk is full."
    )
    assert result.metadata.final_route == "weak_then_strong"
    assert result.metadata.escalated
    assert result.metadata.selected_model == "strong"
    assert result.metadata.escalation_reason is not None


def test_invalid_strong_output_fails_closed() -> None:
    router = CascadeRouter(lambda _: valid_disk_response(), lambda _: {"summary": "invalid"})
    with pytest.raises(InvalidStrongModelOutput):
        router.route("The cause is unknown and evidence is intermittent.")


@pytest.mark.asyncio
async def test_async_router_supports_async_fake_models() -> None:
    async def weak(_: str) -> dict[str, object]:
        return valid_disk_response()

    async def strong(_: str) -> dict[str, object]:
        return valid_disk_response()

    result = await CascadeRouter(weak, strong).aroute("The image-api disk is full.")
    assert result.metadata.final_route == "weak"
