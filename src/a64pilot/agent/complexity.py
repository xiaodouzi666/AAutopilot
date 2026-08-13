"""Transparent request-only features for weak/strong routing."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Final

DEFAULT_COMPLEXITY_THRESHOLD: Final[float] = 30.0

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[^\w\s]", re.UNICODE)
_BACKTICK_SERVICE_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9_.:-]{1,80})`")
_NAMED_SERVICE_RE = re.compile(
    r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:-api|-service|-worker|-gateway|-db|-cache))\b",
    re.IGNORECASE,
)
_LOG_LINE_RE = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|TRACE\b|DEBUG\b|INFO\b|WARN(?:ING)?\b|ERROR\b|FATAL\b)",
    re.IGNORECASE,
)

_SYMPTOM_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "disk": re.compile(r"\b(?:disk|filesystem|volume|inode|no space|read-only fs)\b", re.I),
    "memory": re.compile(r"\b(?:memory|oom|out of memory|swap|rss|heap)\b", re.I),
    "service": re.compile(
        r"\b(?:crash|crashloop|exited|unhealthy|5\d\d|timeout|not responding)\b", re.I
    ),
    "network": re.compile(
        r"\b(?:network|dns|connection refused|packet loss|unreachable|tls|latency)\b", re.I
    ),
    "dependency": re.compile(
        r"\b(?:dependency|upstream|downstream|database|queue|cache|third[- ]party)\b", re.I
    ),
}
_CONTRADICTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bbut\b",
        r"\bhowever\b",
        r"\balthough\b",
        r"\bdespite\b",
        r"\byet\b",
        r"\bcontradict(?:s|ory|ion)?\b",
        r"\bon the other hand\b",
    )
)
_AMBIGUITY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bunclear\b",
        r"\bunknown\b",
        r"\binsufficient (?:data|evidence|information)\b",
        r"\bintermittent\b",
        r"\b(?:maybe|possibly|might|could be)\b",
        r"\bno logs?\b",
        r"\bno metrics?\b",
        r"\bcannot reproduce\b",
    )
)
_TOOL_REQUEST_RE = re.compile(
    r"\b(?:inspect_service|read_logs|check_disk|check_memory|check_network|escalate)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ComplexityFeatures:
    character_count: int
    approximate_token_count: int
    log_line_count: int
    named_service_count: int
    symptom_category_count: int
    contradiction_count: int
    ambiguity_marker_count: int
    requested_tool_count: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ComplexityReport:
    features: ComplexityFeatures
    contributions: dict[str, float]
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "features": self.features.as_dict(),
            "contributions": dict(self.contributions),
            "score": self.score,
        }


def extract_complexity_features(incident: str) -> ComplexityFeatures:
    """Extract only features visible in the user's incident text."""

    if not isinstance(incident, str):
        raise TypeError("complexity extraction accepts incident text only")
    text = incident.strip()
    if not text:
        raise ValueError("incident text must not be empty")

    lines = [line for line in text.splitlines() if line.strip()]
    explicit_log_lines = sum(bool(_LOG_LINE_RE.search(line)) for line in lines)
    log_lines = explicit_log_lines or max(0, len(lines) - 1)

    services = {match.casefold() for match in _BACKTICK_SERVICE_RE.findall(text)}
    services.update(match.casefold() for match in _NAMED_SERVICE_RE.findall(text))
    symptoms = sum(bool(pattern.search(text)) for pattern in _SYMPTOM_PATTERNS.values())
    contradictions = sum(len(pattern.findall(text)) for pattern in _CONTRADICTION_PATTERNS)
    ambiguities = sum(len(pattern.findall(text)) for pattern in _AMBIGUITY_PATTERNS)
    requested_tools = len(set(match.casefold() for match in _TOOL_REQUEST_RE.findall(text)))

    return ComplexityFeatures(
        character_count=len(text),
        approximate_token_count=len(_TOKEN_RE.findall(text)),
        log_line_count=log_lines,
        named_service_count=len(services),
        symptom_category_count=symptoms,
        contradiction_count=contradictions,
        ambiguity_marker_count=ambiguities,
        requested_tool_count=requested_tools,
    )


def score_complexity(incident: str) -> ComplexityReport:
    features = extract_complexity_features(incident)
    contributions = {
        "length": min(15.0, max(0.0, (features.character_count - 160) / 24.0)),
        "log_lines": min(15.0, features.log_line_count * 3.0),
        "multiple_services": min(15.0, max(0, features.named_service_count - 1) * 5.0),
        "multiple_symptoms": min(20.0, max(0, features.symptom_category_count - 1) * 10.0),
        "contradictions": min(25.0, features.contradiction_count * 25.0),
        "ambiguity": min(30.0, features.ambiguity_marker_count * 30.0),
        "requested_tools": min(10.0, max(0, features.requested_tool_count - 1) * 5.0),
    }
    score = round(min(100.0, sum(contributions.values())), 3)
    return ComplexityReport(features=features, contributions=contributions, score=score)


def complexity_score(incident: str) -> float:
    return score_complexity(incident).score


__all__ = [
    "ComplexityFeatures",
    "ComplexityReport",
    "DEFAULT_COMPLEXITY_THRESHOLD",
    "complexity_score",
    "extract_complexity_features",
    "score_complexity",
]
