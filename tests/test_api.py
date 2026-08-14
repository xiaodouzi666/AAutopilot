from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
import pytest

from a64pilot.agent.schema import triage_openai_response_format
from a64pilot.api.app import FIXTURE_MODEL_ID, UpstreamResponder, create_app
from a64pilot.api.openai_types import CompletionResult
from a64pilot.runtime.openai_client import OpenAIClient, OpenAIClientError

VALID_TRIAGE = {
    "summary": "The synthetic volume is full.",
    "severity": "high",
    "diagnosis": "disk_pressure",
    "hypotheses": [
        {
            "cause": "disk utilization reached the fixture threshold",
            "evidence": ["the supplied synthetic incident says the filesystem is full"],
            "confidence": 0.9,
        }
    ],
    "tool_calls": [{"name": "check_disk", "arguments": {"mount": "/srv"}}],
    "safe_next_action": "Inspect the read-only disk fixture and escalate if evidence conflicts.",
    "needs_escalation": False,
}


def request_body(*, stream: bool = False) -> dict[str, object]:
    return {
        "model": FIXTURE_MODEL_ID,
        "messages": [{"role": "user", "content": "The filesystem is full on a synthetic node"}],
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 192,
        "stream": stream,
        "response_format": {"type": "json_object"},
    }


@pytest.fixture
async def fixture_client(tmp_path: Path) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(
        app=create_app(
            fixture_mode=True,
            strict_models=True,
            report_path=tmp_path / "not-generated.html",
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def fake_upstream_client(
    content: str,
    captured: list[dict[str, object]],
    *,
    status_code: int = 200,
) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "ready": True})
        assert request.url.path == "/v1/chat/completions"
        captured.append(json.loads(request.content))
        if status_code != 200:
            return httpx.Response(status_code, text=content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-upstream-test",
                "object": "chat.completion",
                "created": 0,
                "model": "strong-qwen",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def request_through_strong_proxy(
    *,
    upstream_content: str,
    client_response_format: dict[str, object] | None,
    stream: bool = False,
    upstream_status: int = 200,
) -> tuple[httpx.Response, list[dict[str, object]]]:
    captured: list[dict[str, object]] = []
    upstream_http = fake_upstream_client(
        upstream_content,
        captured,
        status_code=upstream_status,
    )
    responder = UpstreamResponder(
        OpenAIClient("http://upstream", client=upstream_http),
        upstream_model="strong-qwen",
        backend="kleidiai",
        profile_id="measured-strong-only",
        cpu_only_verified=True,
    )
    proxy_transport = httpx.ASGITransport(
        app=create_app(
            responder=responder,
            model_ids=["a64pilot"],
            strict_models=True,
        )
    )
    body = request_body(stream=stream)
    body["model"] = "a64pilot"
    if client_response_format is None:
        body.pop("response_format")
    else:
        body["response_format"] = client_response_format
    async with httpx.AsyncClient(transport=proxy_transport, base_url="http://proxy") as client:
        response = await client.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-A64Pilot-Debug": "1"},
        )
    await upstream_http.aclose()
    return response, captured


@pytest.mark.asyncio
async def test_unconfigured_app_is_not_fake_healthy() -> None:
    transport = httpx.ASGITransport(app=create_app(fixture_mode=False))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        completion = await client.post("/v1/chat/completions", json=request_body())
    assert health.status_code == 503
    assert health.json()["ready"] is False
    assert completion.status_code == 503
    assert completion.json()["error"]["code"] == "responder_unconfigured"


@pytest.mark.asyncio
async def test_fixture_api_openai_shape_and_visible_disclaimer(
    fixture_client: httpx.AsyncClient,
) -> None:
    health = await fixture_client.get("/health")
    models = await fixture_client.get("/v1/models")
    response = await fixture_client.post(
        "/v1/chat/completions",
        json=request_body(),
        headers={"X-A64Pilot-Debug": "1"},
    )

    assert health.status_code == 200
    assert health.json() == {
        "status": "fixture",
        "ready": True,
        "mode": "fixture_not_model_inference",
        "message": "Fixture mode is for tests/screenshots and is not performance evidence.",
        "benchmark_evidence": False,
    }
    assert models.json()["data"][0]["id"] == FIXTURE_MODEL_ID
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    incident = json.loads(body["choices"][0]["message"]["content"])
    assert incident["diagnosis"] == "disk_pressure"
    assert incident["tool_calls"] == [{"name": "check_disk", "arguments": {"mount": "/srv"}}]
    assert response.headers["x-a64pilot-route"] == "fixture"
    assert response.headers["x-a64pilot-backend"] == "none"
    assert response.headers["x-a64pilot-cpu-only-verified"] == "false"
    assert response.headers["x-a64pilot-evidence-mode"] == "fixture_not_benchmark_evidence"


@pytest.mark.asyncio
async def test_debug_metadata_is_omitted_without_opt_in(fixture_client: httpx.AsyncClient) -> None:
    response = await fixture_client.post("/v1/chat/completions", json=request_body())
    assert response.status_code == 200
    assert "x-a64pilot-route" not in response.headers
    assert "routing" not in response.json()


@pytest.mark.parametrize(
    ("incident", "tool_name", "arguments", "needs_escalation"),
    [
        ("disk full", "check_disk", {"mount": "/srv"}, False),
        ("memory OOM", "check_memory", {"scope": "node"}, False),
        ("service crash", "inspect_service", {"service": "fixture-service"}, False),
        (
            "network DNS",
            "check_network",
            {"target": "fixture-service", "port": 443},
            False,
        ),
        (
            "upstream dependency",
            "read_logs",
            {"service": "fixture-service", "limit": 100},
            False,
        ),
        ("ambiguous incident", "escalate", {"reason": "insufficient evidence"}, True),
    ],
)
@pytest.mark.asyncio
async def test_fixture_uses_only_validator_accepted_tool_arguments(
    fixture_client: httpx.AsyncClient,
    incident: str,
    tool_name: str,
    arguments: dict[str, str],
    needs_escalation: bool,
) -> None:
    body = request_body()
    body["messages"] = [{"role": "user", "content": incident}]
    response = await fixture_client.post("/v1/chat/completions", json=body)
    structured = json.loads(response.json()["choices"][0]["message"]["content"])
    assert structured["tool_calls"] == [{"name": tool_name, "arguments": arguments}]
    assert structured["needs_escalation"] is needs_escalation


@pytest.mark.asyncio
async def test_stream_is_openai_sse_and_preserves_json(fixture_client: httpx.AsyncClient) -> None:
    async with fixture_client.stream(
        "POST", "/v1/chat/completions", json=request_body(stream=True)
    ) as response:
        lines = [line async for line in response.aiter_lines()]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in lines
    parts: list[str] = []
    for line in lines:
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[5:].strip())
        content = chunk["choices"][0]["delta"].get("content")
        if content:
            parts.append(content)
    assert json.loads("".join(parts))["diagnosis"] == "disk_pressure"


@pytest.mark.asyncio
async def test_unsupported_options_are_rejected_without_echoing_input(
    fixture_client: httpx.AsyncClient,
) -> None:
    payload = request_body()
    payload["tools"] = [{"unsafe_secret": "do-not-echo"}]
    response = await fixture_client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "do-not-echo" not in response.text


@pytest.mark.asyncio
async def test_injected_responder_metadata_only_appears_in_debug_headers() -> None:
    async def responder(_request: object) -> CompletionResult:
        return CompletionResult(
            content=json.dumps(VALID_TRIAGE),
            model="strong-qwen",
            prompt_tokens=11,
            completion_tokens=3,
            metadata={
                "route": "weak_then_strong",
                "selected_model": "strong-qwen",
                "escalated": True,
                "backend": "kleidiai",
                "profile_id": "profile-7",
                "cpu_only_verified": True,
            },
        )

    app = create_app(
        responder=responder,
        fixture_mode=False,
        model_ids=["a64pilot"],
        strict_models=True,
    )
    transport = httpx.ASGITransport(app=app)
    body = request_body()
    body["model"] = "a64pilot"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-A64Pilot-Debug": "1"},
        )
        metrics = await client.get("/metrics")

    assert json.loads(response.json()["choices"][0]["message"]["content"]) == VALID_TRIAGE
    assert response.headers["x-a64pilot-route"] == "weak_then_strong"
    assert response.headers["x-a64pilot-escalated"] == "true"
    assert response.headers["x-a64pilot-backend"] == "kleidiai"
    assert metrics.json()["escalations_total"] == 1
    assert metrics.json()["benchmark_evidence"] is False


@pytest.mark.asyncio
async def test_responder_exception_does_not_leak_sensitive_exception_text() -> None:
    async def responder(_request: object) -> CompletionResult:
        raise RuntimeError("secret-token-and-private-path")

    transport = httpx.ASGITransport(app=create_app(responder=responder, model_ids=["a64pilot"]))
    body = request_body()
    body["model"] = "a64pilot"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "responder_failure"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_unknown_model_rejected_in_strict_mode(fixture_client: httpx.AsyncClient) -> None:
    body = request_body()
    body["model"] = "missing-model"
    response = await fixture_client.post("/v1/chat/completions", json=body)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_cascade_style_aroute_responder_is_supported() -> None:
    class Response:
        def model_dump_json(self) -> str:
            return json.dumps(VALID_TRIAGE)

    class Metadata:
        def as_dict(self) -> dict[str, object]:
            return {
                "final_route": "weak_then_strong",
                "selected_model": "strong",
                "escalated": True,
            }

    class Routed:
        response = Response()
        metadata = Metadata()

    class Router:
        seen_incident = ""

        async def aroute(self, incident: str) -> Routed:
            self.seen_incident = incident
            return Routed()

    router = Router()
    transport = httpx.ASGITransport(
        app=create_app(responder=router, model_ids=["a64pilot"], strict_models=True)
    )
    body = request_body()
    body["model"] = "a64pilot"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-A64Pilot-Debug": "1"},
        )
    assert "filesystem is full" in router.seen_incident
    assert response.json()["model"] == "strong"
    assert response.headers["x-a64pilot-route"] == "weak_then_strong"
    assert response.headers["x-a64pilot-escalated"] == "true"
    assert response.headers["x-a64pilot-output-validation"] == "schema_safety_consistency_passed"


@pytest.mark.asyncio
async def test_metrics_prometheus_and_report_fallback(fixture_client: httpx.AsyncClient) -> None:
    await fixture_client.post("/v1/chat/completions", json=request_body())
    prometheus = await fixture_client.get("/metrics?format=prometheus")
    report = await fixture_client.get("/report")
    assert prometheus.status_code == 200
    assert "a64pilot_requests_total 1" in prometheus.text
    assert report.status_code == 200
    assert "not benchmark evidence" in report.text.lower()


@pytest.mark.asyncio
async def test_report_serves_only_generated_figures_without_path_escape(tmp_path: Path) -> None:
    report_path = tmp_path / "public" / "report.html"
    figures = report_path.parent / "figures"
    figures.mkdir(parents=True)
    report_path.write_text('<img src="figures/ablation.png">', encoding="utf-8")
    png = b"\x89PNG\r\n\x1a\nfixture"
    (figures / "ablation.png").write_bytes(png)
    outside = tmp_path / "private.txt"
    outside.write_text("must-not-be-served", encoding="utf-8")
    (figures / "pareto.png").symlink_to(outside)

    transport = httpx.ASGITransport(app=create_app(fixture_mode=True, report_path=report_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        report = await client.get("/report")
        figure = await client.get("/figures/ablation.png")
        missing = await client.get("/figures/not-generated.png")
        traversal = await client.get("/figures/%2e%2e%2freport.html")
        symlink_escape = await client.get("/figures/pareto.png")

    assert report.status_code == 200
    assert figure.status_code == 200
    assert figure.headers["content-type"] == "image/png"
    assert figure.headers["x-content-type-options"] == "nosniff"
    assert figure.content == png
    assert missing.status_code == 404
    assert traversal.status_code == 404
    assert symlink_escape.status_code == 404
    assert "must-not-be-served" not in symlink_escape.text


@pytest.mark.asyncio
async def test_runtime_openai_client_measures_stream_without_inventing_nonstream_ttft() -> None:
    transport = httpx.ASGITransport(app=create_app(fixture_mode=True))
    async with httpx.AsyncClient(transport=transport) as transport_client:
        client = OpenAIClient("http://fixture", client=transport_client)
        messages = [{"role": "user", "content": "synthetic memory OOM incident"}]
        streamed = await client.chat_completion(
            messages=messages,
            model=FIXTURE_MODEL_ID,
            stream=True,
            stop="END",
        )
        nonstreamed = await client.chat_completion(
            messages=messages,
            model=FIXTURE_MODEL_ID,
            stream=False,
        )
    assert json.loads(streamed.text)["diagnosis"] == "memory_pressure"
    assert streamed.timing.first_content_token_ns is not None
    assert streamed.timing.ttft_ms is not None
    assert nonstreamed.timing.first_content_token_ns is None
    assert nonstreamed.timing.ttft_ms is None


@pytest.mark.asyncio
async def test_runtime_stream_explicitly_requests_and_captures_usage() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        body = (
            'data: {"id":"usage-test","model":"fixture","choices":'
            '[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
            'data: {"id":"usage-test","model":"fixture","choices":[],"usage":'
            '{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIClient("http://fixture", client=http_client)
        completion = await client.chat_completion(
            messages=[{"role": "user", "content": "synthetic incident"}],
            model="fixture",
            stream=True,
            stream_include_usage=True,
        )

    assert captured[0]["stream_options"] == {"include_usage": True}
    assert completion.usage == {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
    }
    assert completion.generation_tokens_per_second is not None
    assert completion.payload["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_runtime_stream_rejects_clean_eof_without_done_terminator() -> None:
    private_body = "private-truncated-response-must-not-leak"

    async def handler(_request: httpx.Request) -> httpx.Response:
        event = {
            "id": "truncated",
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": private_body},
                    "finish_reason": "stop",
                }
            ],
        }
        body = f"data: {json.dumps(event)}\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIClient("http://fixture", client=http_client)
        with pytest.raises(OpenAIClientError, match=r"explicit \[DONE\] terminator") as captured:
            await client.chat_completion(
                messages=[{"role": "user", "content": "synthetic incident"}],
                model="fixture",
                stream=True,
            )

    assert private_body not in str(captured.value)


@pytest.mark.asyncio
async def test_runtime_stream_does_not_invent_finish_reason_before_done() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"id":"no-finish","model":"fixture","choices":'
            '[{"index":0,"delta":{"content":"ok"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIClient("http://fixture", client=http_client)
        completion = await client.chat_completion(
            messages=[{"role": "user", "content": "synthetic incident"}],
            model="fixture",
            stream=True,
        )

    assert completion.text == "ok"
    assert completion.payload["choices"][0]["finish_reason"] is None


@pytest.mark.asyncio
async def test_runtime_openai_client_does_not_reuse_completed_sse_connections() -> None:
    accepted_peers: list[tuple[str, int]] = []
    connection_headers: list[str | None] = []
    event_body = (
        b'data: {"id":"fresh-connection","model":"fixture","choices":'
        b'[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        b"\n\ndata: [DONE]\n\n"
    )

    class RecordingServer(ThreadingHTTPServer):
        def get_request(self) -> tuple[object, tuple[str, int]]:
            request, peer = super().get_request()
            accepted_peers.append(peer)
            return request, peer

    class SSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook
            content_length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(content_length)
            connection_headers.append(self.headers.get("Connection"))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(event_body)))
            self.end_headers()
            self.wfile.write(event_body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = RecordingServer(("127.0.0.1", 0), SSEHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        host, port = server.server_address
        async with OpenAIClient(f"http://{host}:{port}") as client:
            for _ in range(2):
                completion = await client.chat_completion(
                    messages=[{"role": "user", "content": "synthetic incident"}],
                    model="fixture",
                    stream=True,
                )
                assert completion.text == "ok"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert connection_headers == ["close", "close"]
    assert len(accepted_peers) == 2


@pytest.mark.asyncio
async def test_strong_only_upstream_proxy_health_completion_and_debug_metadata() -> None:
    upstream_transport = httpx.ASGITransport(app=create_app(fixture_mode=True))
    async with httpx.AsyncClient(transport=upstream_transport) as upstream_http:
        responder = UpstreamResponder(
            OpenAIClient("http://upstream", client=upstream_http),
            upstream_model=FIXTURE_MODEL_ID,
            backend="generic",
            profile_id="strong-only-test",
            cpu_only_verified=True,
        )
        proxy_transport = httpx.ASGITransport(
            app=create_app(
                responder=responder,
                model_ids=["a64pilot"],
                strict_models=True,
            )
        )
        async with httpx.AsyncClient(transport=proxy_transport, base_url="http://proxy") as client:
            health = await client.get("/health")
            body = request_body()
            body["model"] = "a64pilot"
            response = await client.post(
                "/v1/chat/completions",
                json=body,
                headers={"X-A64Pilot-Debug": "1"},
            )
    assert health.status_code == 200
    assert health.json()["upstream_ready"] is True
    assert (
        json.loads(response.json()["choices"][0]["message"]["content"])["diagnosis"]
        == "disk_pressure"
    )
    assert response.headers["x-a64pilot-route"] == "strong"
    assert response.headers["x-a64pilot-backend"] == "generic"
    assert response.headers["x-a64pilot-cpu-only-verified"] == "true"
    assert response.headers["x-a64pilot-output-validation"] == "schema_safety_consistency_passed"


@pytest.mark.parametrize(
    "client_response_format",
    [
        None,
        {"type": "text"},
        {"type": "json_object"},
        {
            "type": "json_schema",
            "json_schema": {
                "name": "client_weakened_schema",
                "strict": False,
                "schema": {"type": "string"},
            },
        },
    ],
    ids=["omitted", "text", "json-object", "weakened-json-schema"],
)
@pytest.mark.asyncio
async def test_strong_proxy_always_forces_shared_triage_schema(
    client_response_format: dict[str, object] | None,
) -> None:
    response, captured = await request_through_strong_proxy(
        upstream_content=f"\n{json.dumps(VALID_TRIAGE)}\n",
        client_response_format=client_response_format,
    )

    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0]["response_format"] == triage_openai_response_format()
    assert json.loads(response.json()["choices"][0]["message"]["content"]) == VALID_TRIAGE
    assert response.headers["x-a64pilot-route"] == "strong"
    assert response.headers["x-a64pilot-output-validation"] == "schema_safety_consistency_passed"


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream-request"])
@pytest.mark.parametrize(
    "upstream_content",
    [
        "PRIVATE-UPSTREAM-TEXT",
        json.dumps({"summary": "PRIVATE-UPSTREAM-TEXT"}),
        json.dumps(
            {
                **VALID_TRIAGE,
                "safe_next_action": "rm -rf / # PRIVATE-UPSTREAM-TEXT",
            }
        ),
    ],
    ids=["not-json", "schema-invalid", "unsafe-shell"],
)
@pytest.mark.asyncio
async def test_strong_proxy_rejects_invalid_upstream_output_without_leaking_it(
    upstream_content: str,
    stream: bool,
) -> None:
    response, captured = await request_through_strong_proxy(
        upstream_content=upstream_content,
        client_response_format={"type": "json_object"},
        stream=stream,
    )

    assert len(captured) == 1
    assert captured[0]["response_format"] == triage_openai_response_format()
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "upstream_output_rejected"
    assert "PRIVATE-UPSTREAM-TEXT" not in response.text


@pytest.mark.asyncio
async def test_strong_proxy_does_not_reflect_upstream_http_error_body() -> None:
    response, captured = await request_through_strong_proxy(
        upstream_content="PRIVATE-UPSTREAM-ERROR-BODY",
        client_response_format=None,
        upstream_status=500,
    )

    assert len(captured) == 1
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_failure"
    assert "PRIVATE-UPSTREAM-ERROR-BODY" not in response.text
