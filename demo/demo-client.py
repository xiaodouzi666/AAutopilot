#!/usr/bin/env python3
"""Call the local AArch64 Autopilot OpenAI-compatible endpoint.

For an API-only demo without GGUF models, explicitly launch the server as:

    A64PILOT_FIXTURE_MODE=1 uvicorn a64pilot.api.app:app --host 127.0.0.1 --port 8088

Fixture mode is visibly marked and must never be used as benchmark evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

DEFAULT_PROMPT = """Synthetic incident inc-demo: checkout-api is restarting.
The fixture log reports a crash loop after a configuration reload.
Return the required safe incident-triage JSON and use read-only tools only."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--model", help="Model ID; defaults to the first advertised model")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--debug", action="store_true", help="Request local routing metadata headers"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Validate health, OpenAI shape, and structured incident JSON",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def _stream_completion(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[str, dict[str, str]]:
    parts: list[str] = []
    debug_headers: dict[str, str] = {}
    with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        debug_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower().startswith("x-a64pilot-")
        }
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            encoded = line[5:].strip()
            if not encoded or encoded == "[DONE]":
                continue
            chunk = json.loads(encoded)
            choices = chunk.get("choices", [])
            if choices:
                content = choices[0].get("delta", {}).get("content")
                if isinstance(content, str):
                    parts.append(content)
    return "".join(parts), debug_headers


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    base_url = args.base_url.rstrip("/")
    headers = {"X-A64Pilot-Debug": "1"} if args.debug else {}

    try:
        with httpx.Client(timeout=args.timeout) as client:
            health_response = client.get(f"{base_url}/health")
            health_response.raise_for_status()
            health = health_response.json()
            models_response = client.get(f"{base_url}/v1/models")
            models_response.raise_for_status()
            advertised = models_response.json().get("data", [])
            if not advertised and not args.model:
                raise RuntimeError("server advertises no models")
            model = args.model or advertised[0]["id"]
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Triage only the supplied synthetic incident using safe read-only tools.",
                    },
                    {"role": "user", "content": args.prompt},
                ],
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 192,
                "seed": 20260813,
                "stream": args.stream,
                "response_format": {"type": "json_object"},
            }
            if args.stream:
                content, debug = _stream_completion(
                    client,
                    f"{base_url}/v1/chat/completions",
                    payload,
                    headers,
                )
                completion: dict[str, Any] = {
                    "object": "chat.completion.reconstructed-from-stream",
                    "model": model,
                    "content": content,
                }
            else:
                response = client.post(
                    f"{base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                completion = response.json()
                debug = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower().startswith("x-a64pilot-")
                }
                content = completion["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError, RuntimeError) as exc:
        print(f"demo client failed: {exc}", file=sys.stderr)
        return 1

    try:
        structured = json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"completion content is not valid JSON: {exc}", file=sys.stderr)
        return 1

    output = {
        "health": health,
        "completion": completion,
        "structured_incident": structured,
        "debug_headers": debug,
    }
    print(json.dumps(output, indent=2, sort_keys=True))

    if args.smoke:
        required = {
            "summary",
            "severity",
            "diagnosis",
            "hypotheses",
            "tool_calls",
            "safe_next_action",
            "needs_escalation",
        }
        missing = (
            sorted(required - structured.keys())
            if isinstance(structured, dict)
            else sorted(required)
        )
        if missing:
            print(f"smoke validation failed; missing fields: {', '.join(missing)}", file=sys.stderr)
            return 1
        print("smoke validation: PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
