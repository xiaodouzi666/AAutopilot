"""Shared, label-free prompt construction for fair model comparisons."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Final

from .schema import RoutingInput, triage_json_schema

SYSTEM_PROMPT: Final[str] = """You are AArch64 Autopilot's safe cloud incident triage assistant.
Analyze only the synthetic incident supplied by the user. Return exactly one JSON object that
matches the provided schema; do not use Markdown or add commentary.

You may request only these deterministic, read-only fixture tools:
- inspect_service: arguments {"service": "name"}
- read_logs: arguments {"service": "name", "limit": 1..200}
- check_disk: arguments {"mount": "/"}
- check_memory: arguments {"scope": "node|service-name"}
- check_network: arguments {"target": "service-name", "port": 1..65535}
- escalate: arguments {"reason": "brief reason"}

Never propose or encode shell commands. Never delete, write, restart, stop, kill, scale,
reboot, patch, or otherwise mutate a system. When evidence is contradictory or insufficient,
set diagnosis to "unknown", set needs_escalation to true, and include the escalate tool.
Keep evidence factual and derived only from the incident text."""


def _incident_text(value: str | RoutingInput) -> str:
    # Refuse mappings rather than accidentally serializing private expected labels.
    if isinstance(value, Mapping):
        raise TypeError("prompt input must be incident text or RoutingInput, not a mapping")
    if isinstance(value, RoutingInput):
        text = value.incident
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError("prompt input must be incident text or RoutingInput")
    text = text.strip()
    if not text:
        raise ValueError("incident text must not be empty")
    return text


def build_user_prompt(incident: str | RoutingInput) -> str:
    text = _incident_text(incident)
    schema = json.dumps(triage_json_schema(), sort_keys=True, separators=(",", ":"))
    return f"TRIAGE_RESPONSE_SCHEMA={schema}\n\nSYNTHETIC_INCIDENT:\n{text}"


def build_messages(incident: str | RoutingInput) -> list[dict[str, str]]:
    """Build identical OpenAI-style messages for every benchmark candidate."""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(incident)},
    ]


def prompt_fingerprint() -> str:
    """Hash the shared prompt and schema so raw records can prove fairness."""

    payload = {
        "system": SYSTEM_PROMPT,
        "schema": triage_json_schema(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["SYSTEM_PROMPT", "build_messages", "build_user_prompt", "prompt_fingerprint"]
