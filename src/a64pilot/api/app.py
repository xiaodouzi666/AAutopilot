"""Localhost-first OpenAI-compatible proxy and evidence dashboard endpoint."""

from __future__ import annotations

import html
import inspect
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from a64pilot.agent.schema import triage_openai_response_format
from a64pilot.agent.validator import UnsafeModelOutput, require_valid_response
from a64pilot.runtime.llama_command import is_loopback_host
from a64pilot.runtime.openai_client import OpenAIClient, OpenAIClientError

from .metrics import MetricsRegistry
from .openai_types import (
    AssistantMessage,
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionResult,
    ModelCard,
    ModelList,
    OpenAIErrorDetail,
    OpenAIErrorResponse,
    Usage,
)

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8088
FIXTURE_MODEL_ID = "a64pilot-fixture-not-for-benchmark"
REPORT_FIGURE_NAMES = frozenset({"ablation.png", "pareto.png"})


class ProxyNotConfigured(RuntimeError):
    """Raised when no real responder or explicit fixture mode is configured."""


def _truthy_environment(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _incident_fixture(user_text: str) -> dict[str, Any]:
    """Return deterministic, safe sample output without running a model.

    This is deliberately a rule fixture, not a simulated LLM and not benchmark
    evidence.  It exists so API compatibility and screenshots can be tested when
    GGUF files or an Arm target are unavailable.
    """

    lowered = user_text.lower()
    if any(term in lowered for term in ("disk", "no space", "filesystem", "inode")):
        diagnosis, severity, tool, arguments = (
            "disk_pressure",
            "high",
            "check_disk",
            {"mount": "/srv"},
        )
    elif any(term in lowered for term in ("memory", "oom", "out of memory")):
        diagnosis, severity, tool, arguments = (
            "memory_pressure",
            "critical",
            "check_memory",
            {"scope": "node"},
        )
    elif any(term in lowered for term in ("crash", "restarting", "restart loop", "segfault")):
        diagnosis, severity, tool, arguments = (
            "service_crash",
            "high",
            "inspect_service",
            {"service": "fixture-service"},
        )
    elif any(term in lowered for term in ("network", "dns", "connection", "packet loss")):
        diagnosis, severity, tool, arguments = (
            "network_failure",
            "high",
            "check_network",
            {"target": "fixture-service"},
        )
    elif any(term in lowered for term in ("dependency", "upstream", "downstream")):
        diagnosis, severity, tool, arguments = (
            "dependency_failure",
            "medium",
            "read_logs",
            {"service": "fixture-service"},
        )
    else:
        diagnosis, severity, tool, arguments = (
            "unknown",
            "medium",
            "escalate",
            {"reason": "insufficient evidence"},
        )

    needs_escalation = diagnosis == "unknown"
    return {
        "summary": "Fixture-only triage result derived from the supplied synthetic incident text.",
        "severity": severity,
        "diagnosis": diagnosis,
        "hypotheses": [
            {
                "cause": diagnosis,
                "evidence": ["keyword match in synthetic request"],
                "confidence": 0.5,
            }
        ],
        "tool_calls": [{"name": tool, "arguments": arguments}],
        "safe_next_action": "Inspect the read-only fixture evidence and escalate if it is inconclusive.",
        "needs_escalation": needs_escalation,
    }


class FixtureResponder:
    """Explicit non-model responder for API tests and screenshot preparation."""

    model_id = FIXTURE_MODEL_ID

    async def complete(self, request: ChatCompletionRequest) -> CompletionResult:
        user_text = "\n".join(
            message.content for message in request.messages if message.role == "user"
        )
        content = json.dumps(_incident_fixture(user_text), sort_keys=True, separators=(",", ":"))
        return CompletionResult(
            content=content,
            model=self.model_id,
            # Zero avoids presenting approximate tokenizer counts as measured data.
            prompt_tokens=0,
            completion_tokens=0,
            metadata={
                "route": "fixture",
                "selected_model": self.model_id,
                "escalated": False,
                "backend": "none",
                "profile_id": "fixture-demo",
                "cpu_only_verified": False,
                "evidence_mode": "fixture_not_benchmark_evidence",
            },
        )


class UpstreamResponder:
    """Strong-only adapter for a real local OpenAI-compatible llama endpoint."""

    def __init__(
        self,
        client: OpenAIClient,
        *,
        upstream_model: str,
        backend: str,
        profile_id: str,
        cpu_only_verified: bool,
    ) -> None:
        self.client = client
        self.upstream_model = upstream_model
        self.backend = backend
        self.profile_id = profile_id
        self.cpu_only_verified = cpu_only_verified

    async def complete(self, request: ChatCompletionRequest) -> CompletionResult:
        # The public endpoint is an incident-triage API, not a generic schema
        # passthrough. Client response_format values cannot weaken the exact
        # constraint shared with the benchmark protocol.
        completion = await self.client.chat_completion(
            messages=[message.model_dump(exclude_none=True) for message in request.messages],
            model=self.upstream_model,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens,
            seed=request.seed,
            response_format=triage_openai_response_format(),
            stop=request.stop,
            stream=False,
        )
        validated = require_valid_response(completion.text)
        usage = completion.usage
        return CompletionResult(
            # Re-serialize the trusted typed object; never forward raw model
            # text, repaired prose, unknown fields, or unsafe tool arguments.
            content=validated.model_dump_json(),
            model=str(completion.payload.get("model", self.upstream_model)),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            metadata={
                "route": "strong",
                "selected_model": self.upstream_model,
                "escalated": False,
                "backend": self.backend,
                "profile_id": self.profile_id,
                "cpu_only_verified": self.cpu_only_verified,
                "evidence_mode": "live_request_not_benchmark_claim",
                "upstream_e2e_ms": completion.timing.e2e_ms,
                "output_validation": "schema_safety_consistency_passed",
            },
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def health(self) -> Mapping[str, Any]:
        return await self.client.health()


ResponderCallable = Callable[
    [ChatCompletionRequest],
    CompletionResult
    | Mapping[str, Any]
    | str
    | Awaitable[CompletionResult | Mapping[str, Any] | str],
]


def _mapping_to_result(value: Mapping[str, Any], requested_model: str) -> CompletionResult:
    if "choices" in value:
        try:
            choice = value["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("responder mapping lacks choices[0].message.content") from exc
        usage_value = value.get("usage", {})
        usage = usage_value if isinstance(usage_value, Mapping) else {}
        metadata_value = value.get("metadata", {})
        metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
        return CompletionResult(
            content=str(content),
            model=str(value.get("model", requested_model)),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason=str(choice.get("finish_reason", "stop")),
            metadata=metadata,
        )
    if "content" not in value:
        raise ValueError("responder mapping must contain content or an OpenAI choices list")
    metadata_value = value.get("metadata", {})
    return CompletionResult(
        content=str(value["content"]),
        model=str(value.get("model", requested_model)),
        prompt_tokens=int(value.get("prompt_tokens", 0)),
        completion_tokens=int(value.get("completion_tokens", 0)),
        finish_reason=str(value.get("finish_reason", "stop")),
        metadata=metadata_value if isinstance(metadata_value, Mapping) else {},
    )


async def _invoke_responder(
    responder: object,
    request: ChatCompletionRequest,
) -> CompletionResult:
    target: Any
    target_argument: Any = request
    if hasattr(responder, "complete"):
        target = responder.complete
    elif hasattr(responder, "respond"):
        target = responder.respond
    elif hasattr(responder, "aroute"):
        # Compatibility with a64pilot.agent.router.CascadeRouter without
        # importing the agent package into the reusable API layer.
        target = responder.aroute
        target_argument = "\n".join(
            message.content for message in request.messages if message.role == "user"
        )
    elif callable(responder):
        target = responder
    else:
        raise TypeError("responder must be callable or expose complete()/respond()/aroute()")
    value = target(target_argument)
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, CompletionResult):
        return value
    if isinstance(value, str):
        return CompletionResult(content=value, model=request.model)
    if isinstance(value, Mapping):
        return _mapping_to_result(value, request.model)
    if hasattr(value, "response") and hasattr(value, "metadata"):
        routed_response = value.response
        routed_metadata = value.metadata
        if hasattr(routed_response, "model_dump_json"):
            content = routed_response.model_dump_json()
        elif isinstance(routed_response, Mapping):
            content = json.dumps(routed_response, sort_keys=True, separators=(",", ":"))
        else:
            raise TypeError("routed responder response is not serializable")
        metadata = (
            routed_metadata.as_dict()
            if hasattr(routed_metadata, "as_dict")
            else dict(routed_metadata)
            if isinstance(routed_metadata, Mapping)
            else {}
        )
        selected_model = str(metadata.get("selected_model", request.model))
        return CompletionResult(
            content=content,
            model=selected_model,
            metadata={
                **metadata,
                "route": metadata.get("final_route", metadata.get("route", "unknown")),
                "selected_model": selected_model,
            },
        )
    raise TypeError("responder returned an unsupported result type")


def _validated_result(result: CompletionResult) -> CompletionResult:
    """Canonicalize and validate every responder result at the API boundary."""

    validated = require_valid_response(result.content)
    return CompletionResult(
        content=validated.model_dump_json(),
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        finish_reason=result.finish_reason,
        metadata={
            **dict(result.metadata),
            "output_validation": "schema_safety_consistency_passed",
        },
    )


def _debug_headers(result: CompletionResult) -> dict[str, str]:
    metadata = result.metadata

    def safe(name: str, default: object) -> str:
        value = str(metadata.get(name, default))
        return "".join(ch for ch in value if 32 <= ord(ch) < 127)[:200]

    return {
        "X-A64Pilot-Selected-Model": safe("selected_model", result.model),
        "X-A64Pilot-Route": safe("route", "unknown"),
        "X-A64Pilot-Escalated": safe("escalated", False).lower(),
        "X-A64Pilot-Backend": safe("backend", "unknown"),
        "X-A64Pilot-Profile": safe("profile_id", "unknown"),
        "X-A64Pilot-CPU-Only-Verified": safe("cpu_only_verified", False).lower(),
        "X-A64Pilot-Evidence-Mode": safe("evidence_mode", "live_request_not_benchmark_claim"),
        "X-A64Pilot-Output-Validation": safe("output_validation", "not_reported"),
    }


def _openai_error(
    message: str,
    *,
    status_code: int,
    error_type: str,
    code: str | None = None,
) -> JSONResponse:
    payload = OpenAIErrorResponse(
        error=OpenAIErrorDetail(message=message, type=error_type, code=code)
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _stream_chunks(
    *,
    completion_id: str,
    created: int,
    result: CompletionResult,
) -> AsyncIterator[str]:
    async def generate() -> AsyncIterator[str]:
        role_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": result.model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(role_chunk, separators=(',', ':'))}\n\n"
        # Bounded chunks preserve exact content while avoiding one giant SSE frame.
        for offset in range(0, len(result.content), 96):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": result.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": result.content[offset : offset + 96]},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": result.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": result.finish_reason}],
        }
        yield f"data: {json.dumps(final, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"

    return generate()


def create_app(
    *,
    responder: object | None = None,
    fixture_mode: bool | None = None,
    model_ids: Sequence[str] | None = None,
    report_path: Path | str = Path("artifacts/report.html"),
    metrics: MetricsRegistry | None = None,
    strict_models: bool = False,
) -> FastAPI:
    """Create the proxy without starting any subprocess or model generation."""

    fixture_enabled = (
        _truthy_environment("A64PILOT_FIXTURE_MODE") if fixture_mode is None else fixture_mode
    )
    if responder is not None and fixture_enabled:
        raise ValueError("configure either a real responder or fixture_mode, not both")
    configured_responder = FixtureResponder() if fixture_enabled else responder
    if model_ids is None:
        if fixture_enabled:
            advertised_models = (FIXTURE_MODEL_ID,)
        elif responder is None:
            advertised_models = ()
        else:
            advertised_models = ("a64pilot",)
    else:
        advertised_models = tuple(dict.fromkeys(model_ids))
    if any(not model.strip() for model in advertised_models):
        raise ValueError("model IDs must not be empty")

    report_file = Path(report_path)
    registry = metrics or MetricsRegistry()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if configured_responder is not None and hasattr(configured_responder, "aclose"):
            close_result = configured_responder.aclose()
            if inspect.isawaitable(close_result):
                await close_result

    application = FastAPI(
        title="AArch64 Autopilot",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.responder = configured_responder
    application.state.fixture_mode = fixture_enabled
    application.state.metrics = registry
    application.state.report_path = report_file

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo input values: they may contain incident text or credentials.
        problems = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error.get("loc", ()))
            problems.append(f"{location}: {error.get('msg', 'invalid value')}")
        return _openai_error(
            "; ".join(problems),
            status_code=422,
            error_type="invalid_request_error",
            code="validation_error",
        )

    @application.get("/health")
    async def health() -> JSONResponse:
        if configured_responder is None:
            payload = {
                "status": "unconfigured",
                "ready": False,
                "mode": "no_responder",
                "message": "Configure a real responder or explicitly enable fixture mode.",
                "benchmark_evidence": False,
            }
            return JSONResponse(status_code=503, content=payload)
        if fixture_enabled:
            payload = {
                "status": "fixture",
                "ready": True,
                "mode": "fixture_not_model_inference",
                "message": "Fixture mode is for tests/screenshots and is not performance evidence.",
                "benchmark_evidence": False,
            }
        else:
            upstream: Mapping[str, Any] | None = None
            if hasattr(configured_responder, "health"):
                try:
                    health_value = configured_responder.health()
                    if inspect.isawaitable(health_value):
                        health_value = await health_value
                    if isinstance(health_value, Mapping):
                        upstream = health_value
                except Exception:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "status": "upstream_unavailable",
                            "ready": False,
                            "mode": "configured_responder",
                            "benchmark_evidence": False,
                        },
                    )
            upstream_ready = bool(upstream.get("ready", True)) if upstream is not None else True
            payload = {
                "status": "ok",
                "ready": upstream_ready,
                "mode": "configured_responder",
                "benchmark_evidence": False,
            }
            if upstream is not None:
                payload["upstream_ready"] = upstream_ready
        return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)

    @application.get("/v1/models", response_model=ModelList)
    async def models() -> ModelList:
        return ModelList(data=[ModelCard(id=model) for model in advertised_models])

    @application.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: ChatCompletionRequest,
        x_a64pilot_debug: str | None = Header(default=None, alias="X-A64Pilot-Debug"),
    ) -> JSONResponse | StreamingResponse:
        if configured_responder is None:
            return _openai_error(
                "No inference responder is configured. Explicit fixture mode is available for tests only.",
                status_code=503,
                error_type="service_unavailable",
                code="responder_unconfigured",
            )
        if strict_models and request.model not in advertised_models:
            return _openai_error(
                f"Unknown model {request.model!r}",
                status_code=404,
                error_type="invalid_request_error",
                code="model_not_found",
            )

        started_ns = time.monotonic_ns()
        try:
            # Keep this final validation at the public boundary even when an
            # adapter (such as UpstreamResponder) validates earlier. Injected
            # or future routing responders cannot bypass the same fail-closed
            # schema, tool-policy, safety, and consistency gate.
            result = _validated_result(await _invoke_responder(configured_responder, request))
        except UnsafeModelOutput:
            registry.record(
                latency_ms=(time.monotonic_ns() - started_ns) / 1_000_000,
                route="upstream_output_rejected",
                model=request.model,
                success=False,
            )
            return _openai_error(
                "Upstream model output failed the required incident schema, safety, or consistency validation.",
                status_code=502,
                error_type="upstream_error",
                code="upstream_output_rejected",
            )
        except (ValueError, TypeError):
            registry.record(
                latency_ms=(time.monotonic_ns() - started_ns) / 1_000_000,
                route="error",
                model=request.model,
                success=False,
            )
            return _openai_error(
                "The configured responder returned unsupported data.",
                status_code=502,
                error_type="upstream_error",
                code="responder_error",
            )
        except OpenAIClientError:
            registry.record(
                latency_ms=(time.monotonic_ns() - started_ns) / 1_000_000,
                route="upstream_error",
                model=request.model,
                success=False,
            )
            return _openai_error(
                "The configured upstream model service failed.",
                status_code=502,
                error_type="upstream_error",
                code="upstream_failure",
            )
        except Exception:
            # Do not reflect exception text: model adapters can include prompt,
            # path, host, or token data in their messages.
            registry.record(
                latency_ms=(time.monotonic_ns() - started_ns) / 1_000_000,
                route="responder_error",
                model=request.model,
                success=False,
            )
            return _openai_error(
                "The configured inference responder failed.",
                status_code=502,
                error_type="upstream_error",
                code="responder_failure",
            )

        latency_ms = (time.monotonic_ns() - started_ns) / 1_000_000
        route = str(result.metadata.get("route", "unknown"))
        escalated = bool(result.metadata.get("escalated", False))
        registry.record(
            latency_ms=latency_ms,
            route=route,
            model=result.model,
            escalated=escalated,
            success=True,
        )
        headers = _debug_headers(result) if x_a64pilot_debug == "1" else {}
        completion_id = f"chatcmpl-{uuid4().hex}"
        created = int(time.time())

        if request.stream:
            return StreamingResponse(
                _stream_chunks(completion_id=completion_id, created=created, result=result),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", **headers},
            )

        finish_reason = result.finish_reason
        if finish_reason not in {"stop", "length", "content_filter", "tool_calls"}:
            finish_reason = "stop"
        response = ChatCompletionResponse(
            id=completion_id,
            created=created,
            model=result.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=AssistantMessage(content=result.content),
                    finish_reason=finish_reason,  # type: ignore[arg-type]
                )
            ],
            usage=Usage(
                prompt_tokens=max(0, result.prompt_tokens),
                completion_tokens=max(0, result.completion_tokens),
                total_tokens=max(0, result.prompt_tokens) + max(0, result.completion_tokens),
            ),
        )
        return JSONResponse(content=response.model_dump(exclude_none=True), headers=headers)

    @application.get("/metrics", response_model=None)
    async def metrics_endpoint(
        format: str = Query(default="json", pattern="^(json|prometheus)$"),  # noqa: A002
    ) -> JSONResponse | PlainTextResponse:
        if format == "prometheus":
            return PlainTextResponse(
                registry.prometheus_text(), media_type="text/plain; version=0.0.4"
            )
        return JSONResponse(content=registry.snapshot())

    @application.get("/report", response_model=None)
    async def report() -> FileResponse | HTMLResponse:
        if report_file.is_file():
            return FileResponse(
                report_file,
                media_type="text/html",
                headers={
                    "X-A64Pilot-API-Mode": (
                        "fixture_not_model_inference" if fixture_enabled else "configured_responder"
                    )
                },
            )
        mode = (
            "Fixture mode — not benchmark evidence"
            if fixture_enabled
            else "Report not generated yet"
        )
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>AArch64 Autopilot</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:16px system-ui;max-width:760px;margin:10vh auto;padding:2rem;background:#10151d;color:#eef4ff}}code{{color:#8de1ff}}</style>
</head><body><h1>AArch64 Autopilot</h1><h2>{html.escape(mode)}</h2>
<p>The evidence dashboard is generated only from validated raw artifacts.</p>
<p>Expected fixed path: <code>{html.escape(str(report_file))}</code></p></body></html>"""
        return HTMLResponse(
            content=body,
            status_code=200,
            headers={
                "X-A64Pilot-API-Mode": (
                    "fixture_not_model_inference" if fixture_enabled else "unconfigured"
                )
            },
        )

    @application.get("/figures/{name}", response_model=None)
    async def report_figure(name: str) -> FileResponse | JSONResponse:
        # The report generator emits exactly these two public images. Keep the
        # asset surface fixed and also resolve symlinks before serving so an
        # allowed filename cannot escape the configured figures directory.
        if name not in REPORT_FIGURE_NAMES or Path(name).name != name:
            return JSONResponse(status_code=404, content={"detail": "figure not found"})
        figures_root = (report_file.parent / "figures").resolve()
        figure = (figures_root / name).resolve()
        if figure.parent != figures_root or not figure.is_file():
            return JSONResponse(status_code=404, content={"detail": "figure not found"})
        return FileResponse(
            figure,
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return application


def app_from_environment() -> FastAPI:
    """Build the Uvicorn entry point from a deliberately small env surface."""

    fixture_mode = _truthy_environment("A64PILOT_FIXTURE_MODE")
    upstream_url = os.getenv("A64PILOT_UPSTREAM_URL", "").strip()
    if fixture_mode and upstream_url:
        raise RuntimeError("A64PILOT_FIXTURE_MODE and A64PILOT_UPSTREAM_URL are mutually exclusive")
    if not upstream_url:
        return create_app(
            fixture_mode=fixture_mode,
            report_path=os.getenv("A64PILOT_REPORT_PATH", "artifacts/report.html"),
        )

    from urllib.parse import urlparse

    parsed = urlparse(upstream_url)
    if not parsed.hostname:
        raise RuntimeError("A64PILOT_UPSTREAM_URL must be an absolute HTTP(S) URL")
    if not is_loopback_host(parsed.hostname) and not _truthy_environment(
        "A64PILOT_ALLOW_REMOTE_UPSTREAM"
    ):
        raise RuntimeError(
            "A64PILOT_UPSTREAM_URL must use loopback unless A64PILOT_ALLOW_REMOTE_UPSTREAM=1"
        )
    upstream_model = os.getenv("A64PILOT_UPSTREAM_MODEL", "a64pilot-strong")
    public_model = os.getenv("A64PILOT_MODEL_ID", "a64pilot")
    responder = UpstreamResponder(
        OpenAIClient(
            upstream_url,
            timeout_s=float(os.getenv("A64PILOT_REQUEST_TIMEOUT_S", "180")),
        ),
        upstream_model=upstream_model,
        backend=os.getenv("A64PILOT_BACKEND", "unknown"),
        profile_id=os.getenv("A64PILOT_PROFILE_ID", "unidentified"),
        cpu_only_verified=_truthy_environment("A64PILOT_CPU_ONLY_VERIFIED"),
    )
    return create_app(
        responder=responder,
        fixture_mode=False,
        model_ids=[public_model],
        report_path=os.getenv("A64PILOT_REPORT_PATH", "artifacts/report.html"),
        strict_models=True,
    )


# ``uvicorn a64pilot.api.app:app`` remains safe by default: without an injected
# responder it reports unconfigured and performs no inference. Fixture mode is
# explicit; a real upstream is configured through A64PILOT_UPSTREAM_URL.
app = app_from_environment()
