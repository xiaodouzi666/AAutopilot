"""Small async OpenAI-compatible client with honest streaming timing."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class OpenAIClientError(RuntimeError):
    """An upstream transport, protocol, or HTTP error."""


@dataclass(frozen=True, slots=True)
class RequestTiming:
    start_ns: int
    first_content_token_ns: int | None
    end_ns: int

    @property
    def e2e_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000

    @property
    def ttft_ms(self) -> float | None:
        if self.first_content_token_ns is None:
            return None
        return (self.first_content_token_ns - self.start_ns) / 1_000_000

    @property
    def decode_time_s(self) -> float | None:
        if self.first_content_token_ns is None:
            return None
        return max((self.end_ns - self.first_content_token_ns) / 1_000_000_000, 0.0)


@dataclass(frozen=True, slots=True)
class ClientCompletion:
    payload: Mapping[str, Any]
    text: str
    timing: RequestTiming
    chunks: tuple[Mapping[str, Any], ...] = ()

    @property
    def usage(self) -> Mapping[str, Any]:
        value = self.payload.get("usage", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def completion_tokens(self) -> int | None:
        value = self.usage.get("completion_tokens")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def generation_tokens_per_second(self) -> float | None:
        tokens = self.completion_tokens
        decode_s = self.timing.decode_time_s
        if tokens is None or decode_s is None or decode_s <= 0:
            return None
        return tokens / decode_s


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("credentials must not be embedded in base_url")
    return base_url.rstrip("/")


def _extract_content(payload: Mapping[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAIClientError("upstream response lacks choices[0].message.content") from exc
    if not isinstance(content, str):
        raise OpenAIClientError("upstream completion content is not a string")
    return content


class OpenAIClient:
    """Client for a local ``llama-server`` or the A64Pilot proxy.

    No authorization value is ever placed in errors or timing records.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18080",
        *,
        timeout_s: float = 180.0,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.base_url = _validate_base_url(base_url)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers=headers,
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> OpenAIClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def health(self) -> Mapping[str, Any]:
        """Return the upstream health object or raise a redacted client error."""

        try:
            response = await self._client.get(f"{self.base_url}/health")
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"upstream health probe failed: {type(exc).__name__}") from exc
        return await self._checked_json(response)

    @staticmethod
    async def _checked_json(response: httpx.Response) -> Mapping[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Upstream error bodies can reflect prompts, credentials, paths,
            # or raw model text. Preserve the status without propagating body
            # content into API errors or public artifacts.
            raise OpenAIClientError(f"upstream returned HTTP {response.status_code}") from exc
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OpenAIClientError("upstream returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise OpenAIClientError("upstream JSON response is not an object")
        return payload

    @staticmethod
    def _request_payload(
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        seed: int | None,
        stream: bool,
        response_format: Mapping[str, Any] | None,
        stop: str | Sequence[str] | None,
        stream_include_usage: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": stream,
        }
        if seed is not None:
            payload["seed"] = seed
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        if stop is not None:
            payload["stop"] = list(stop) if not isinstance(stop, str) else stop
        if stream_include_usage:
            if not stream:
                raise ValueError("stream_include_usage requires stream=True")
            # The pinned llama-server implements the OpenAI streaming usage
            # trailer only when this option is requested.  Benchmark callers
            # opt in explicitly so completion-token and generation-rate fields
            # cannot silently collapse to zero.
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def chat_completion(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 192,
        top_p: float = 1.0,
        seed: int | None = 20260813,
        stream: bool = False,
        response_format: Mapping[str, Any] | None = None,
        stop: str | Sequence[str] | None = None,
        stream_include_usage: bool = False,
    ) -> ClientCompletion:
        if not messages:
            raise ValueError("messages must not be empty")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        payload = self._request_payload(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            seed=seed,
            stream=stream,
            response_format=response_format,
            stop=stop,
            stream_include_usage=stream_include_usage,
        )
        if stream:
            return await self._stream(payload)

        started = time.monotonic_ns()
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"upstream request failed: {type(exc).__name__}") from exc
        data = await self._checked_json(response)
        ended = time.monotonic_ns()
        return ClientCompletion(
            payload=data,
            text=_extract_content(data),
            timing=RequestTiming(started, None, ended),
        )

    async def _stream(self, payload: Mapping[str, Any]) -> ClientCompletion:
        started = time.monotonic_ns()
        first_content_ns: int | None = None
        chunks: list[Mapping[str, Any]] = []
        content_parts: list[str] = []
        usage: Mapping[str, Any] = {}
        response_id = ""
        response_model = str(payload.get("model", ""))
        finish_reason = "stop"
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=dict(payload),
                # llama-server may reap an idle HTTP/1.1 SSE socket before
                # httpx notices. Never return a completed stream connection
                # to the pool for a later measured POST; both backends pay the
                # same fresh-connection cost and no request is retried.
                headers={"Connection": "close"},
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    await response.aread()
                    raise OpenAIClientError(
                        f"upstream returned HTTP {response.status_code}"
                    ) from exc
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise OpenAIClientError(
                            "upstream emitted an invalid SSE JSON chunk"
                        ) from exc
                    if not isinstance(chunk, Mapping):
                        raise OpenAIClientError("upstream emitted a non-object SSE chunk")
                    chunks.append(chunk)
                    response_id = str(chunk.get("id", response_id))
                    response_model = str(chunk.get("model", response_model))
                    candidate_usage = chunk.get("usage")
                    if isinstance(candidate_usage, Mapping):
                        usage = candidate_usage
                    try:
                        choice = chunk["choices"][0]
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if isinstance(choice.get("finish_reason"), str):
                            finish_reason = choice["finish_reason"]
                    except (KeyError, IndexError, TypeError, AttributeError):
                        content = None
                    if isinstance(content, str) and content:
                        if first_content_ns is None:
                            first_content_ns = time.monotonic_ns()
                        content_parts.append(content)
        except httpx.HTTPError as exc:
            raise OpenAIClientError(f"upstream stream failed: {type(exc).__name__}") from exc

        ended = time.monotonic_ns()
        text = "".join(content_parts)
        reconstructed: dict[str, Any] = {
            "id": response_id,
            "object": "chat.completion",
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage:
            reconstructed["usage"] = dict(usage)
        return ClientCompletion(
            payload=reconstructed,
            text=text,
            timing=RequestTiming(started, first_content_ns, ended),
            chunks=tuple(chunks),
        )
