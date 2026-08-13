"""OpenAI-compatible streaming timing primitives used by the service benchmark."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ServiceTiming:
    start_ns: int
    first_token_ns: int | None
    end_ns: int
    prompt_tokens: int
    completion_tokens: int
    content: str

    @property
    def ttft_ms(self) -> float | None:
        if self.first_token_ns is None:
            return None
        return (self.first_token_ns - self.start_ns) / 1_000_000

    @property
    def e2e_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000


async def time_chat_completion(
    base_url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 180.0,
) -> ServiceTiming:
    request = dict(payload)
    request["stream"] = True
    start = time.monotonic_ns()
    first: int | None = None
    pieces: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    async with (
        httpx.AsyncClient(timeout=timeout_s) as client,
        client.stream(
            "POST",
            f"{base_url.rstrip('/')}/v1/chat/completions",
            json=request,
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                if first is None:
                    first = time.monotonic_ns()
                pieces.append(delta)
            if chunk.get("usage"):
                usage = chunk["usage"]
    end = time.monotonic_ns()
    return ServiceTiming(
        start_ns=start,
        first_token_ns=first,
        end_ns=end,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        content="".join(pieces),
    )
