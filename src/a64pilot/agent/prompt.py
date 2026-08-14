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

The complete response_format schema is enforced separately. Use this compact visible shape and
key order:
{"summary":"...","severity":"low|medium|high|critical","diagnosis":"disk_pressure|memory_pressure|service_crash|network_failure|dependency_failure|unknown","hypotheses":[{"cause":"...","evidence":["..."],"confidence":0.0}],"tool_calls":[{"name":"...","arguments":{}}],"safe_next_action":"...","needs_escalation":false}

You may request only these deterministic, read-only fixture tools:
- inspect_service: arguments {"service": "name"}
- read_logs: arguments {"service": "name", "limit": 1..200}; limit defaults to 100
- check_disk: arguments {"mount": "/|/var|/tmp|/srv"}; mount defaults to "/"
- check_memory: arguments {"scope": "node|service-name"}; scope defaults to "node"
- check_network: arguments {"target": "service-name", "port": 1..65535}; port defaults to 443
- escalate: arguments {"reason": "brief reason"}

Use only the exact argument keys shown for the selected tool; never add guessed fields. Keep
escalate.reason neutral and factual. Do not copy destructive terms such as drain, write, kill,
stop, or restart into tool arguments, and never put an instruction or command in an argument.
Likewise, safe_next_action must not repeat drain, write, kill, stop, restart, delete, or reboot;
use neutral read-only wording such as "Review storage and log evidence."
Never propose or encode shell commands. Never delete, write, restart, stop, kill, scale,
reboot, patch, or otherwise mutate a system. When evidence is contradictory or insufficient,
set diagnosis to "unknown", set needs_escalation to true, and include the escalate tool.
Keep output concise: one-sentence summary and safe_next_action, at most two hypotheses, and one
or two short evidence strings per hypothesis. Derive every evidence string from the incident.

Use this deterministic, label-free decision order and select the first explanation supported by
the incident's causal evidence:
1. Full storage or slow/failed writes caused by storage pressure: disk_pressure + check_disk.
2. Explicit high memory, swapping, or OOM as the cause: memory_pressure + check_memory.
3. A local process crash, exit, exception, or unhealthy service as the cause: service_crash +
   inspect_service and read_logs.
4. Timeouts, packet loss, DNS, TLS, or connection failures without an upstream application
   failure: network_failure + check_network.
5. An upstream dependency returns 5xx/429 or bad data while the local service is healthy:
   dependency_failure + check_network, plus only inspect_service or read_logs if needed.
6. Contradictory or insufficient causal evidence: unknown + escalate.

Set severity by blast radius: all-request or complete outage is critical, multi-service or major
customer impact is high, partial degradation is medium, and limited/insufficient impact is low or
medium. Return exactly one most-likely hypothesis, the minimum necessary tools, concise JSON, and
a read-only safe_next_action."""


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
    # The same frozen schema is already supplied as the constrained response_format.  Repeating
    # it in the chat context wastes scarce 2K model context and increases truncation risk.
    return f"SYNTHETIC_INCIDENT:\n{text}"


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
