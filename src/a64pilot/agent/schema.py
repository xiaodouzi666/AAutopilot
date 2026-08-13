"""Typed schemas for the synthetic incident-triage workload.

The models in this module deliberately reject unknown fields.  Model output is
untrusted input, so accepting a nearly-correct shape would make both the safety
gate and the benchmark score ambiguous.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DiagnosisCategory(StrEnum):
    DISK_PRESSURE = "disk_pressure"
    MEMORY_PRESSURE = "memory_pressure"
    SERVICE_CRASH = "service_crash"
    NETWORK_FAILURE = "network_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    UNKNOWN = "unknown"


class ToolName(StrEnum):
    INSPECT_SERVICE = "inspect_service"
    READ_LOGS = "read_logs"
    CHECK_DISK = "check_disk"
    CHECK_MEMORY = "check_memory"
    CHECK_NETWORK = "check_network"
    ESCALATE = "escalate"


class CaseCategory(StrEnum):
    SIMPLE = "simple"
    MULTI = "multi"
    NOISY = "noisy"
    AMBIGUOUS = "ambiguous"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Hypothesis(StrictModel):
    cause: str = Field(min_length=1, max_length=240)
    evidence: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_meaningful(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("hypothesis evidence entries must not be empty")
        return values


class ToolCall(StrictModel):
    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def arguments_must_be_json_compatible(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool arguments must be finite JSON values") from exc
        return value


class TriageResponse(StrictModel):
    """The only response shape accepted from either model tier."""

    summary: str = Field(min_length=1, max_length=500)
    severity: Severity
    diagnosis: DiagnosisCategory
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=6)
    tool_calls: list[ToolCall] = Field(min_length=1, max_length=8)
    safe_next_action: str = Field(min_length=1, max_length=500)
    needs_escalation: bool


class IncidentCase(StrictModel):
    """A synthetic benchmark item and its private objective labels."""

    case_id: str = Field(pattern=r"^incident-[0-9]{3}$")
    category: CaseCategory
    incident: str = Field(min_length=12, max_length=3000)
    expected_diagnosis: DiagnosisCategory
    acceptable_diagnoses: tuple[DiagnosisCategory, ...] = ()
    expected_severity: Severity
    acceptable_severities: tuple[Severity, ...] = ()
    required_tools: tuple[ToolName, ...] = ()
    acceptable_tools: tuple[ToolName, ...] = ()
    prohibited_actions: tuple[str, ...] = ()
    expected_escalation: bool = False

    @field_validator(
        "acceptable_diagnoses",
        "acceptable_severities",
        "required_tools",
        "acceptable_tools",
        "prohibited_actions",
    )
    @classmethod
    def tuple_values_must_be_unique(cls, values: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(values) != len(set(values)):
            raise ValueError("case label lists must not contain duplicates")
        return values

    @model_validator(mode="after")
    def ambiguous_cases_must_escalate(self) -> IncidentCase:
        if self.category is CaseCategory.AMBIGUOUS and not self.expected_escalation:
            raise ValueError("ambiguous cases must expect escalation")
        return self


class RoutingInput(StrictModel):
    """The intentionally label-free view supplied to the router and prompt."""

    case_id: str = Field(pattern=r"^incident-[0-9]{3}$")
    incident: str = Field(min_length=1)


class SplitManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    seed: int
    calibration: tuple[str, ...]
    test: tuple[str, ...]

    @model_validator(mode="after")
    def ids_must_be_unique_and_disjoint(self) -> SplitManifest:
        calibration = set(self.calibration)
        test = set(self.test)
        if len(calibration) != len(self.calibration):
            raise ValueError("calibration split contains duplicate IDs")
        if len(test) != len(self.test):
            raise ValueError("test split contains duplicate IDs")
        overlap = calibration & test
        if overlap:
            raise ValueError(f"calibration and test splits overlap: {sorted(overlap)}")
        return self


def parse_triage_response(
    value: str | bytes | Mapping[str, Any] | TriageResponse,
) -> TriageResponse:
    """Parse a response strictly, without repairing prose or Markdown fences."""

    if isinstance(value, TriageResponse):
        return value
    if isinstance(value, (str, bytes)):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("response must be a single JSON object") from exc
        if not isinstance(decoded, dict):
            raise ValueError("response JSON root must be an object")
        return TriageResponse.model_validate(decoded)
    if isinstance(value, Mapping):
        return TriageResponse.model_validate(dict(value))
    raise TypeError(f"unsupported response type: {type(value).__name__}")


def parse_incident_case(value: str | bytes | Mapping[str, Any] | IncidentCase) -> IncidentCase:
    if isinstance(value, IncidentCase):
        return value
    if isinstance(value, (str, bytes)):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("incident case JSON root must be an object")
        return IncidentCase.model_validate(decoded)
    if isinstance(value, Mapping):
        return IncidentCase.model_validate(dict(value))
    raise TypeError(f"unsupported incident case type: {type(value).__name__}")


def triage_json_schema() -> dict[str, Any]:
    """Return a stable JSON-schema document suitable for constrained output."""

    schema = TriageResponse.model_json_schema()
    schema["$id"] = "https://a64pilot.local/schema/triage-response-v1.json"
    schema["x-a64pilot-schema-version"] = SCHEMA_VERSION
    return schema


def triage_openai_response_format() -> dict[str, Any]:
    """Return the one constrained-output format used by benchmark and deployment."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "a64pilot_triage_response",
            "strict": True,
            "schema": triage_json_schema(),
        },
    }


# Friendly aliases retained for callers that use the product terminology.
AgentResponse = TriageResponse
IncidentTriageResponse = TriageResponse
Diagnosis = DiagnosisCategory


__all__ = [
    "AgentResponse",
    "CaseCategory",
    "Diagnosis",
    "DiagnosisCategory",
    "Hypothesis",
    "IncidentCase",
    "IncidentTriageResponse",
    "RoutingInput",
    "SCHEMA_VERSION",
    "Severity",
    "SplitManifest",
    "ToolCall",
    "ToolName",
    "TriageResponse",
    "parse_incident_case",
    "parse_triage_response",
    "triage_json_schema",
    "triage_openai_response_format",
]
