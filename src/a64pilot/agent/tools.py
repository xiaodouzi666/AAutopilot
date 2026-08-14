"""Fixture-only tool policy and deterministic mock execution.

Nothing in this module invokes a shell, accesses a production service, or derives a file path
from model output.  Tool arguments are treated as hostile and checked before a fixed fixture is
read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, TypeAdapter

from .schema import ToolCall, ToolCallVariant, ToolName

SAFE_TOOL_ALLOWLIST: Final[frozenset[str]] = frozenset(tool.value for tool in ToolName)
TOOL_ALLOWLIST = SAFE_TOOL_ALLOWLIST

_DESTRUCTIVE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|\s)rm\s+(?:-[a-z]*f[a-z]*\s+|--force\s+)",
        r"\b(?:delete|erase|wipe|destroy|truncate|format|remove|purge)\b",
        r"\bdrop\s+(?:database|schema|table|index)\b",
        r"\b(?:kill|pkill|killall|terminate|stop)\b",
        r"\b(?:restart|reboot|shutdown|poweroff|scale\s+down|drain)\b",
        r"\b(?:write|overwrite|modify|patch)\s+(?:the\s+)?(?:file|config|database|system)\b",
        r"\b(?:chmod|chown|systemctl\s+(?:stop|restart)|kubectl\s+(?:delete|apply|scale))\b",
        r"\bcurl\b[^\n]*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b",
    )
)
_DESTRUCTIVE_DIRECTIVE_ACTION: Final[str] = (
    r"(?:delete|erase|wipe|destroy|truncate|format|remove|purge|"
    r"drop\s+(?:database|schema|table|index)|"
    r"kill|pkill|killall|terminate|stop|restart|reboot|shutdown|poweroff|"
    r"scale\s+down|drain|"
    r"(?:write|overwrite|modify|patch)\s+(?:the\s+)?(?:file|config|database|system)|"
    r"chmod|chown|systemctl\s+(?:stop|restart)|kubectl\s+(?:delete|apply|scale))"
)
_DESTRUCTIVE_DIRECTIVE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        rf"^\s*(?:(?:please|kindly|immediately|now)\s+)*{_DESTRUCTIVE_DIRECTIVE_ACTION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:must|should|shall|need(?:s)?\s+to|have\s+to|"
        rf"recommend(?:ed|s)?\s+(?:that\s+\w+\s+)?|"
        rf"propose(?:d|s)?\s+to|instruct(?:ed|s)?\s+\w+\s+to)"
        rf"\s*{_DESTRUCTIVE_DIRECTIVE_ACTION}\b",
        re.IGNORECASE,
    ),
)
_SHELL_META = re.compile(r"[;&|`$<>\r\n]")
_SHELL_COMMAND = re.compile(
    r"(?:^|\s)(?:sudo|bash|zsh|sh|rm|cat|ls|cp|mv|curl|wget|systemctl|kubectl|docker|"
    r"python(?:3)?|perl|ruby|chmod|chown|tee|sed|awk|xargs|/bin/[a-z0-9_-]+)(?:\s|$)",
    re.IGNORECASE,
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

_ARGUMENT_POLICY: Final[dict[str, frozenset[str]]] = {
    ToolName.INSPECT_SERVICE.value: frozenset({"service"}),
    ToolName.READ_LOGS.value: frozenset({"service", "limit"}),
    ToolName.CHECK_DISK.value: frozenset({"mount"}),
    ToolName.CHECK_MEMORY.value: frozenset({"scope"}),
    ToolName.CHECK_NETWORK.value: frozenset({"target", "port"}),
    ToolName.ESCALATE.value: frozenset({"reason"}),
}
_REQUIRED_ARGUMENTS: Final[dict[str, frozenset[str]]] = {
    ToolName.INSPECT_SERVICE.value: frozenset({"service"}),
    ToolName.READ_LOGS.value: frozenset({"service"}),
    ToolName.CHECK_DISK.value: frozenset(),
    ToolName.CHECK_MEMORY.value: frozenset(),
    ToolName.CHECK_NETWORK.value: frozenset({"target"}),
    ToolName.ESCALATE.value: frozenset({"reason"}),
}
_FIXTURE_FILES: Final[dict[str, str]] = {
    ToolName.INSPECT_SERVICE.value: "services.json",
    ToolName.READ_LOGS.value: "logs.json",
    ToolName.CHECK_DISK.value: "disk.json",
    ToolName.CHECK_MEMORY.value: "memory.json",
    ToolName.CHECK_NETWORK.value: "network.json",
    ToolName.ESCALATE.value: "escalation.json",
}
_TOOL_CALL_ADAPTER: Final[TypeAdapter[ToolCallVariant]] = TypeAdapter(ToolCallVariant)


class ToolPolicyError(ValueError):
    """Raised when an untrusted model tool call violates the fixture policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_destructive_action(text: str) -> bool:
    normalized = " ".join(text.split())
    return any(pattern.search(normalized) for pattern in _DESTRUCTIVE_PATTERNS)


def is_destructive_directive(text: str) -> bool:
    """Reject proposed mutations while allowing factual incident descriptions.

    An escalation reason such as ``request queues drain when upstream recovers`` is evidence,
    not an instruction.  Imperatives and explicit recommendations such as ``drain the node``
    remain forbidden.  The broader :func:`is_destructive_action` is still used for the actual
    ``safe_next_action`` recommendation.
    """

    normalized = " ".join(text.split())
    return any(pattern.search(normalized) for pattern in _DESTRUCTIVE_DIRECTIVE_PATTERNS)


def is_shell_fragment(text: str) -> bool:
    """Identify command-like text; no model-generated shell is accepted, even read-only."""

    return bool(_SHELL_META.search(text) or _SHELL_COMMAND.search(" ".join(text.split())))


def _walk_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        result: list[Any] = []
        for key, nested in value.items():
            result.extend((key, *_walk_values(nested)))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for nested in value:
            result.extend(_walk_values(nested))
        return result
    return [value]


def _validate_argument_value(key: str, value: Any) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if key == "port" and not 1 <= value <= 65535:
            raise ToolPolicyError("invalid_argument", "network port must be in 1..65535")
        if key == "limit" and not 1 <= value <= 200:
            raise ToolPolicyError("invalid_argument", "log limit must be in 1..200")
        return
    if isinstance(value, float):
        raise ToolPolicyError("invalid_argument", "floating-point tool arguments are not supported")
    if not isinstance(value, str):
        raise ToolPolicyError(
            "invalid_argument", "nested or non-scalar tool arguments are not supported"
        )
    if len(value) > 300:
        raise ToolPolicyError("invalid_argument", "tool argument is too long")
    if ".." in value or "\x00" in value or is_shell_fragment(value):
        raise ToolPolicyError("unsafe_argument", "path traversal or shell syntax is forbidden")
    if key == "reason" and is_destructive_directive(value):
        raise ToolPolicyError(
            "destructive_action", "destructive directives in escalation reasons are forbidden"
        )
    if key in {"service", "scope", "target"} and not _SAFE_IDENTIFIER.fullmatch(value):
        raise ToolPolicyError("invalid_argument", f"{key} must be a simple fixture identifier")
    if key == "mount" and value not in {"/", "/var", "/tmp", "/srv"}:
        raise ToolPolicyError("invalid_argument", "mount is not an allowlisted fixture mount")


def tool_arguments(call: ToolCall) -> dict[str, Any]:
    """Return only arguments present in the untrusted call as plain JSON-compatible values."""

    if isinstance(call.arguments, BaseModel):
        return call.arguments.model_dump(mode="python", exclude_unset=True)
    return dict(call.arguments)


def validate_tool_call(value: ToolCall | Mapping[str, Any]) -> ToolCall:
    """Return a typed safe call or raise :class:`ToolPolicyError`."""

    try:
        payload = (
            value.model_dump(mode="python", exclude_unset=True)
            if isinstance(value, ToolCall)
            else value
        )
        call = _TOOL_CALL_ADAPTER.validate_python(payload)
    except Exception as exc:
        raise ToolPolicyError(
            "unknown_or_malformed_tool", "tool call is malformed or not allowlisted"
        ) from exc
    name = call.name.value
    if name not in SAFE_TOOL_ALLOWLIST:
        raise ToolPolicyError("unknown_tool", f"tool is not allowlisted: {name}")
    arguments = tool_arguments(call)
    provided = frozenset(arguments)
    unknown = provided - _ARGUMENT_POLICY[name]
    if unknown:
        raise ToolPolicyError(
            "unknown_argument", f"unsupported arguments for {name}: {sorted(unknown)}"
        )
    missing = _REQUIRED_ARGUMENTS[name] - provided
    if missing:
        raise ToolPolicyError(
            "missing_argument", f"missing arguments for {name}: {sorted(missing)}"
        )
    for key, argument in arguments.items():
        _validate_argument_value(key, argument)
    return call


class MockToolExecutor:
    """Execute only deterministic reads from a fixed fixture directory."""

    def __init__(self, fixture_root: str | Path) -> None:
        self.fixture_root = Path(fixture_root).expanduser().resolve()
        if not self.fixture_root.is_dir():
            raise FileNotFoundError(f"fixture directory does not exist: {self.fixture_root}")

    def _load_fixed_fixture(self, tool_name: str) -> Any:
        path = (self.fixture_root / _FIXTURE_FILES[tool_name]).resolve()
        if path.parent != self.fixture_root:
            raise ToolPolicyError("fixture_escape", "fixture path escaped the fixture directory")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def execute(self, value: ToolCall | Mapping[str, Any]) -> dict[str, Any]:
        call = validate_tool_call(value)
        name = call.name.value
        fixture = self._load_fixed_fixture(name)
        return {
            "tool": name,
            "arguments": tool_arguments(call),
            "fixture_only": True,
            "result": fixture,
        }


def execute_mock_tool(
    value: ToolCall | Mapping[str, Any], fixture_root: str | Path
) -> dict[str, Any]:
    return MockToolExecutor(fixture_root).execute(value)


__all__ = [
    "MockToolExecutor",
    "SAFE_TOOL_ALLOWLIST",
    "TOOL_ALLOWLIST",
    "ToolPolicyError",
    "execute_mock_tool",
    "is_destructive_action",
    "is_destructive_directive",
    "is_shell_fragment",
    "tool_arguments",
    "validate_tool_call",
]
