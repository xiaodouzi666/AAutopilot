"""Schema, policy, and internal-consistency validation for model output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .schema import DiagnosisCategory, ToolName, TriageResponse, parse_triage_response
from .tools import ToolPolicyError, is_destructive_action, is_shell_fragment, validate_tool_call

IssueKind = Literal["schema", "safety", "consistency"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    kind: IssueKind
    path: str = "$"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "kind": self.kind,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    schema_valid: bool
    safety_compliant: bool
    internally_consistent: bool
    response: TriageResponse | None
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return self.schema_valid and self.safety_compliant and self.internally_consistent

    @property
    def should_escalate(self) -> bool:
        return not self.valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "schema_valid": self.schema_valid,
            "safety_compliant": self.safety_compliant,
            "internally_consistent": self.internally_consistent,
            "issues": [issue.as_dict() for issue in self.issues],
        }


class UnsafeModelOutput(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in result.issues)
        super().__init__(summary or "model output did not pass validation")


_DIAGNOSIS_TOOLS: dict[DiagnosisCategory, frozenset[ToolName]] = {
    DiagnosisCategory.DISK_PRESSURE: frozenset({ToolName.CHECK_DISK}),
    DiagnosisCategory.MEMORY_PRESSURE: frozenset({ToolName.CHECK_MEMORY}),
    DiagnosisCategory.SERVICE_CRASH: frozenset({ToolName.INSPECT_SERVICE, ToolName.READ_LOGS}),
    DiagnosisCategory.NETWORK_FAILURE: frozenset({ToolName.CHECK_NETWORK}),
    DiagnosisCategory.DEPENDENCY_FAILURE: frozenset(
        {ToolName.INSPECT_SERVICE, ToolName.READ_LOGS, ToolName.CHECK_NETWORK}
    ),
}


def _schema_issue(exc: Exception) -> ValidationIssue:
    if isinstance(exc, ValidationError) and exc.errors():
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return ValidationIssue(
            code="schema_invalid",
            message=str(first.get("msg", "response does not match the schema")),
            kind="schema",
            path=f"$.{location}" if location else "$",
        )
    return ValidationIssue(code="schema_invalid", message=str(exc), kind="schema")


def validate_response(
    value: str | bytes | Mapping[str, Any] | TriageResponse,
) -> ValidationResult:
    """Validate an untrusted response without executing any requested tool."""

    try:
        response = parse_triage_response(value)
    except (TypeError, ValueError, ValidationError) as exc:
        issue = _schema_issue(exc)
        return ValidationResult(
            schema_valid=False,
            safety_compliant=False,
            internally_consistent=False,
            response=None,
            issues=(issue,),
        )

    issues: list[ValidationIssue] = []
    for index, call in enumerate(response.tool_calls):
        try:
            validate_tool_call(call)
        except ToolPolicyError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code,
                    message=str(exc),
                    kind="safety",
                    path=f"$.tool_calls[{index}]",
                )
            )

    if is_destructive_action(response.safe_next_action):
        issues.append(
            ValidationIssue(
                code="destructive_next_action",
                message="safe_next_action contains a destructive or mutating operation",
                kind="safety",
                path="$.safe_next_action",
            )
        )
    elif is_shell_fragment(response.safe_next_action):
        issues.append(
            ValidationIssue(
                code="shell_next_action",
                message="safe_next_action must not contain a model-generated shell fragment",
                kind="safety",
                path="$.safe_next_action",
            )
        )

    called = frozenset(call.name for call in response.tool_calls)
    has_escalate = ToolName.ESCALATE in called
    if response.needs_escalation != has_escalate:
        issues.append(
            ValidationIssue(
                code="escalation_mismatch",
                message="needs_escalation must agree with presence of the escalate tool",
                kind="consistency",
                path="$.needs_escalation",
            )
        )

    if response.diagnosis is DiagnosisCategory.UNKNOWN and not response.needs_escalation:
        issues.append(
            ValidationIssue(
                code="unknown_without_escalation",
                message="an unknown diagnosis requires escalation",
                kind="consistency",
                path="$.diagnosis",
            )
        )

    relevant_tools = _DIAGNOSIS_TOOLS.get(response.diagnosis)
    if relevant_tools is not None and called.isdisjoint(relevant_tools):
        issues.append(
            ValidationIssue(
                code="diagnosis_tool_mismatch",
                message=(
                    f"diagnosis {response.diagnosis.value} requires at least one relevant "
                    f"read-only inspection tool"
                ),
                kind="consistency",
                path="$.tool_calls",
            )
        )

    safety_compliant = not any(issue.kind == "safety" for issue in issues)
    internally_consistent = not any(issue.kind == "consistency" for issue in issues)
    return ValidationResult(
        schema_valid=True,
        safety_compliant=safety_compliant,
        internally_consistent=internally_consistent,
        response=response,
        issues=tuple(issues),
    )


def require_valid_response(
    value: str | bytes | Mapping[str, Any] | TriageResponse,
) -> TriageResponse:
    result = validate_response(value)
    if not result.valid or result.response is None:
        raise UnsafeModelOutput(result)
    return result.response


__all__ = [
    "UnsafeModelOutput",
    "ValidationIssue",
    "ValidationResult",
    "require_valid_response",
    "validate_response",
]
