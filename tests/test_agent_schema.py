from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import (
    ToolCall,
    TriageResponse,
    parse_triage_response,
    triage_json_schema,
)
from a64pilot.agent.tools import (
    MockToolExecutor,
    ToolPolicyError,
    is_destructive_directive,
    validate_tool_call,
)
from a64pilot.agent.validator import validate_response
from a64pilot.benchmark.runner import REAL_BENCHMARK_MAX_TOKENS, RealServiceBenchmark
from a64pilot.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]


def valid_disk_response() -> dict[str, object]:
    return {
        "summary": "The synthetic fixture shows disk pressure.",
        "severity": "high",
        "diagnosis": "disk_pressure",
        "hypotheses": [
            {"cause": "full volume", "evidence": ["/srv is 99% full"], "confidence": 0.98}
        ],
        "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
        "safe_next_action": "Inspect the read-only disk fixture and escalate to the operator.",
        "needs_escalation": False,
    }


def test_strict_schema_accepts_only_the_documented_shape() -> None:
    response = parse_triage_response(json.dumps(valid_disk_response()))
    assert isinstance(response, TriageResponse)
    assert response.diagnosis.value == "disk_pressure"
    invalid = {**valid_disk_response(), "shell_command": "rm -rf /"}
    with pytest.raises(ValidationError):
        parse_triage_response(invalid)


def test_parser_does_not_repair_markdown_fences() -> None:
    with pytest.raises(ValueError, match="single JSON object"):
        parse_triage_response(f"```json\n{json.dumps(valid_disk_response())}\n```")


def test_json_schema_and_prompt_are_stable_and_label_free() -> None:
    schema = triage_json_schema()
    assert schema["x-a64pilot-schema-version"] == "1.0"
    tool_schema = schema["properties"]["tool_calls"]["items"]
    assert set(tool_schema["discriminator"]["mapping"]) == {
        "check_disk",
        "check_memory",
        "check_network",
        "escalate",
        "inspect_service",
        "read_logs",
    }
    for branch in tool_schema["oneOf"]:
        call_schema = schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]]
        arguments_schema = schema["$defs"][
            call_schema["properties"]["arguments"]["$ref"].rsplit("/", 1)[-1]
        ]
        assert arguments_schema["additionalProperties"] is False
    messages = build_messages("The image-api /srv filesystem is full.")
    rendered = json.dumps(messages)
    assert '"summary"' in messages[0]["content"]
    assert '"needs_escalation"' in messages[0]["content"]
    assert "low|medium|high|critical" in messages[0]["content"]
    assert "disk_pressure|memory_pressure|service_crash" in messages[0]["content"]
    assert "safe_next_action must not repeat drain, write, kill" in messages[0]["content"]
    assert "TRIAGE_RESPONSE_SCHEMA" not in rendered
    assert '"$defs"' not in rendered
    assert "expected_diagnosis" not in rendered
    assert len(prompt_fingerprint()) == 64
    with pytest.raises(TypeError, match="not a mapping"):
        build_messages({"incident": "safe", "expected_diagnosis": "disk_pressure"})  # type: ignore[arg-type]


def test_unknown_and_destructive_tools_are_rejected() -> None:
    assert validate_tool_call(ToolCall(name="check_disk", arguments={"mount": "/srv"})).name == (
        "check_disk"
    )
    with pytest.raises(ToolPolicyError):
        validate_tool_call({"name": "run_shell", "arguments": {"command": "true"}})
    with pytest.raises(ToolPolicyError, match="destructive"):
        validate_tool_call(
            {"name": "escalate", "arguments": {"reason": "restart and delete the database"}}
        )


@pytest.mark.parametrize(
    "call",
    [
        {"name": "check_disk", "arguments": {"mount": "/", "maxItems": 10}},
        {"name": "read_logs", "arguments": {"service": "api", "limit": 0}},
        {"name": "check_network", "arguments": {"target": "api", "port": 70000}},
    ],
)
def test_tool_schema_rejects_unknown_or_out_of_range_arguments(
    call: dict[str, object],
) -> None:
    with pytest.raises(ToolPolicyError, match="malformed"):
        validate_tool_call(call)


def test_factual_destructive_terms_are_not_mistaken_for_directives() -> None:
    factual = "Request queues drain whenever the upstream accepts traffic."
    call = validate_tool_call({"name": "escalate", "arguments": {"reason": factual}})
    assert call.arguments.reason == factual
    assert not is_destructive_directive(factual)


@pytest.mark.parametrize(
    "directive",
    [
        "Drain the node before escalating.",
        "Please restart the service.",
        "The operator should delete the database.",
    ],
)
def test_destructive_directives_in_escalation_reasons_remain_rejected(directive: str) -> None:
    with pytest.raises(ToolPolicyError, match="destructive"):
        validate_tool_call({"name": "escalate", "arguments": {"reason": directive}})


def test_real_benchmark_uses_frozen_512_token_budget() -> None:
    benchmark = RealServiceBenchmark()
    assert REAL_BENCHMARK_MAX_TOKENS == 512
    assert load_settings(ROOT / "configs/default.yaml").benchmark.max_output_tokens == (
        REAL_BENCHMARK_MAX_TOKENS
    )
    assert benchmark.max_tokens == REAL_BENCHMARK_MAX_TOKENS
    assert [case.case_id for case in benchmark.selected_cases("calibration")] == list(
        benchmark.split.calibration
    )
    assert [case.case_id for case in benchmark.selected_cases("test")] == list(benchmark.split.test)
    assert [case.case_id for case in benchmark.selected_cases("calibration", limit=4)] == list(
        benchmark.split.calibration[:4]
    )


def test_validator_marks_mutating_next_action_unsafe() -> None:
    value = valid_disk_response()
    value["safe_next_action"] = "Restart the service and delete old files."
    result = validate_response(value)
    assert result.schema_valid
    assert not result.safety_compliant
    assert result.should_escalate
    assert "destructive_next_action" in {issue.code for issue in result.issues}


def test_validator_rejects_even_read_only_shell_fragments() -> None:
    value = valid_disk_response()
    value["safe_next_action"] = "Run cat /etc/mounts to inspect the host."
    result = validate_response(value)
    assert not result.safety_compliant
    assert "shell_next_action" in {issue.code for issue in result.issues}


def test_mock_executor_reads_only_fixed_fixture_files() -> None:
    executor = MockToolExecutor(ROOT / "demo" / "fixtures")
    result = executor.execute({"name": "check_disk", "arguments": {"mount": "/srv"}})
    assert result["fixture_only"] is True
    assert result["result"]["synthetic"] is True
    with pytest.raises(ToolPolicyError):
        executor.execute({"name": "read_logs", "arguments": {"service": "../../etc/passwd"}})
