from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import runpy
from contextlib import suppress
from pathlib import Path

import pytest
from pydantic import ValidationError

import a64pilot.benchmark.probes as probes_module
from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import ToolName, TriageResponse, triage_openai_response_format
from a64pilot.benchmark.llama_bench import (
    LLAMA_BENCH_PARSER_VERSION,
    build_command,
    inspect_help,
    parse_output,
    require_tests,
)
from a64pilot.benchmark.probes import (
    PROBE_SCHEMA_VERSION,
    ConcurrencyRoundProbe,
    MicroMetric,
    MicroRun,
    PerformanceProbeError,
    PerformanceProbeEvidence,
    ServiceRequestProbe,
    ServiceRun,
    _request_round,
    load_performance_probes,
    micro_thread_candidates,
    performance_probe_semantic_sha256,
    summarize_performance_probes,
)
from a64pilot.benchmark.quality import load_cases, score_case
from a64pilot.build.cmake import COMMON_DEFINITIONS
from a64pilot.models.gguf import GgufTensor, ModelInventoryProof
from a64pilot.models.registry import get_model
from a64pilot.provenance import sha256_file, write_json
from a64pilot.runtime.llama_command import LlamaServerConfig, build_llama_server_command

ROOT = Path(__file__).parents[1]
SESSION_ID = "a" * 32
SOURCE_COMMIT = "b" * 40


def _tool_arguments(name: ToolName) -> dict[str, object]:
    return {
        ToolName.INSPECT_SERVICE: {"service": "fixture-service"},
        ToolName.READ_LOGS: {"service": "fixture-service", "limit": 20},
        ToolName.CHECK_DISK: {"mount": "/"},
        ToolName.CHECK_MEMORY: {"scope": "node"},
        ToolName.CHECK_NETWORK: {"target": "fixture-service", "port": 443},
        ToolName.ESCALATE: {"reason": "The synthetic evidence is ambiguous."},
    }[name]


def _response_text(case: object) -> str:
    tools = list(case.required_tools)
    if case.expected_escalation and ToolName.ESCALATE not in tools:
        tools.append(ToolName.ESCALATE)
    response = TriageResponse(
        summary="The synthetic incident was triaged from its supplied observations.",
        severity=case.expected_severity,
        diagnosis=case.expected_diagnosis,
        hypotheses=[
            {
                "cause": f"The observations match {case.expected_diagnosis.value}.",
                "evidence": [case.incident],
                "confidence": 0.9,
            }
        ],
        tool_calls=[{"name": tool, "arguments": _tool_arguments(tool)} for tool in tools],
        safe_next_action="Collect the listed read-only observations for human review.",
        needs_escalation=case.expected_escalation,
    )
    return response.model_dump_json()


def _reviewed_proof(model_id: str) -> dict[str, object]:
    spec = get_model(model_id)
    return ModelInventoryProof(
        model_id=spec.model_id,
        model_sha256=spec.expected_sha256,
        inventory_sha256=spec.expected_tensor_inventory_sha256,
        tensor_histogram=spec.expected_tensor_histogram,
        reviewed_fallback_tensors=tuple(
            GgufTensor(item.name, item.tensor_type, item.dimensions)
            for item in spec.reviewed_kleidiai_fallbacks
        ),
        verified=True,
        errors=(),
    ).to_dict()


def _model_row(model_id: str) -> dict[str, object]:
    spec = get_model(model_id)
    return {
        "role": spec.role.value,
        "repository": spec.repository,
        "revision": spec.revision,
        "filename": spec.expected_filename,
        "quantization": spec.quantization,
        "sha256": spec.expected_sha256,
        "bytes": spec.expected_bytes,
        "license": spec.license_id,
        "local_path": spec.expected_filename,
        "kleidiai_compatible": spec.kleidiai_compatible,
        "tensor_type_histogram": dict(spec.expected_tensor_histogram),
        "tensor_inventory_sha256": spec.expected_tensor_inventory_sha256,
        "reviewed_kleidiai_fallbacks": [
            item.to_dict() for item in spec.reviewed_kleidiai_fallbacks
        ],
    }


def _write_manifests(artifacts: Path) -> dict[str, dict[str, str]]:
    hashes = {
        "generic": {"llama-bench": "1" * 64, "llama-server": "2" * 64},
        "kleidiai": {"llama-bench": "3" * 64, "llama-server": "4" * 64},
    }
    variants = []
    for backend in ("generic", "kleidiai"):
        definitions = dict(COMMON_DEFINITIONS)
        definitions["GGML_CPU_KLEIDIAI"] = "ON" if backend == "kleidiai" else "OFF"
        variants.append(
            {
                "backend": backend,
                "source_commit": SOURCE_COMMIT,
                "cmake_flags": [f"-D{key}={value}" for key, value in sorted(definitions.items())],
                "compiler": "fixture-cc",
                "binaries": {
                    name: f"build/llama-{backend}/bin/{name}"
                    for name in ("llama-bench", "llama-server")
                },
                "binary_sha256": hashes[backend],
                "cpu_only_configured": True,
                "kleidiai_configured": backend == "kleidiai",
                "runtime_marker_verified": backend == "kleidiai",
            }
        )
        (artifacts / f"cmake-{backend}-cache.txt").write_text(
            "".join(f"{key}:BOOL={value}\n" for key, value in sorted(definitions.items())),
            encoding="utf-8",
        )
    write_json(artifacts / "build-manifest.json", {"variants": variants})
    write_json(
        artifacts / "model-manifest.json",
        {"models": [_model_row("strong-q4-0"), _model_row("strong-q8-0")]},
    )
    return hashes


def _request_receipt(
    path: Path,
    *,
    case: object,
    backend: str,
    parallel: int,
    phase: str,
    repetition: int,
    client_index: int,
    request_id: str,
    start_ns: int,
) -> tuple[dict[str, object], str]:
    content = _response_text(case)
    score = score_case(case, content)
    timing = {
        "start_ns": start_ns,
        "first_content_token_ns": start_ns + 10_000_000,
        "end_ns": start_ns + 110_000_000,
        "ttft_ms": 10.0,
        "e2e_ms": 110.0,
    }
    receipt = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "request_id": request_id,
        "request": {
            "case_id": case.case_id,
            "phase": phase,
            "repetition": repetition,
            "client_index": client_index,
            "model": f"probe-{backend}-p{parallel}",
            "messages": build_messages(case.incident),
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 512,
            "seed": 20260813,
            "stream": True,
            "stream_options": {"include_usage": True},
            "response_format": triage_openai_response_format(),
        },
        "response": {
            "content": content,
            "usage": {"prompt_tokens": 64, "completion_tokens": 10, "total_tokens": 74},
            "finish_reason": "stop",
            "timing": timing,
            "score": score.as_dict(),
        },
    }
    write_json(path, receipt)
    return receipt, content


def _build_fixture(tmp_path: Path) -> tuple[Path, PerformanceProbeEvidence]:
    artifacts = tmp_path / "artifacts"
    raw = artifacts / "performance-probes-raw" / SESSION_ID
    raw.mkdir(parents=True)
    hashes = _write_manifests(artifacts)
    case = {case.case_id: case for case in load_cases(ROOT / "demo/cases.jsonl")}["incident-001"]
    q4 = get_model("strong-q4-0")
    q8 = get_model("strong-q8-0")

    help_paths: dict[str, str] = {}
    for backend in ("generic", "kleidiai"):
        path = raw / "micro" / f"{backend}-help.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("--device DEV\n-ngl N\n-v, --verbose\n", encoding="utf-8")
        help_paths[backend] = path.relative_to(artifacts).as_posix()

    micro_runs = []
    counter = 1
    for backend, quantization, spec in (
        ("generic", "Q8_0", q8),
        ("generic", "Q4_0", q4),
        ("kleidiai", "Q4_0", q4),
    ):
        for threads in (2, 4):
            stem = f"{backend}-{quantization.lower()}-t{threads}"
            stdout = raw / "micro" / f"{stem}.stdout.txt"
            stderr = raw / "micro" / f"{stem}.stderr.txt"
            stdout.write_text(
                "| model | size | backend | threads | test | t/s |\n"
                "| --- | ---: | --- | ---: | --- | ---: |\n"
                f"| qwen | 100 MiB | CPU | {threads} | pp128 | 100.50 ± 2.25 |\n"
                f"| qwen | 100 MiB | CPU | {threads} | tg64 | 20.25 ± 0.50 |\n",
                encoding="utf-8",
            )
            stderr.write_text(
                (
                    "kleidiai: primary q4 kernel feature DOTPROD\n"
                    "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB\n"
                )
                if backend == "kleidiai"
                else "",
                encoding="utf-8",
            )
            start_ns = counter * 1_000_000_000
            counter += 1
            micro_runs.append(
                MicroRun(
                    backend=backend,
                    quantization=quantization,
                    threads=threads,
                    repetitions=3,
                    warmup_repetitions=1,
                    model_file_sha256=spec.expected_sha256,
                    binary_sha256=hashes[backend]["llama-bench"],
                    command=build_command(
                        f"build/llama-{backend}/bin/llama-bench",
                        f"models/{spec.expected_filename}",
                        threads=threads,
                    ),
                    start_ns=start_ns,
                    end_ns=start_ns + 1_000_000_000,
                    elapsed_ms=1000.0,
                    cpu_only_verified=True,
                    backend_verified=True,
                    parser_version=LLAMA_BENCH_PARSER_VERSION,
                    stdout_path=stdout.relative_to(artifacts).as_posix(),
                    stderr_path=stderr.relative_to(artifacts).as_posix(),
                    metrics=[
                        MicroMetric(
                            test="pp128", tokens_per_second=100.5, tokens_per_second_stddev=2.25
                        ),
                        MicroMetric(
                            test="tg64", tokens_per_second=20.25, tokens_per_second_stddev=0.5
                        ),
                    ],
                )
            )

    service_runs = []
    request_counter = 1
    for backend in ("generic", "kleidiai"):
        for parallel in (1, 2):
            service_root = raw / "service" / f"{backend}-p{parallel}"
            receipts = service_root / "requests"
            receipts.mkdir(parents=True)
            command = build_llama_server_command(
                LlamaServerConfig(
                    binary=Path(f"build/llama-{backend}/bin/llama-server"),
                    model=Path(f"models/{q4.expected_filename}"),
                    port=19080 + parallel + (100 if backend == "kleidiai" else 0),
                    model_alias=f"probe-{backend}-p{parallel}",
                    threads=4,
                    context_size=2048 * parallel,
                    parallel=parallel,
                )
            ).as_list()
            command_path = service_root / "command.json"
            write_json(
                command_path,
                {
                    "argv": command,
                    "shell": False,
                    "command_proof": {"cpu_only_flags_complete": True},
                },
            )
            stdout_path = service_root / "server.stdout.log"
            stderr_path = service_root / "server.stderr.log"
            runtime_path = raw / "service" / f"{backend}-p{parallel}-combined.log"
            log_text = (
                "kleidiai: primary q4 kernel feature DOTPROD\n"
                "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB\n"
                if backend == "kleidiai"
                else ""
            )
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(log_text, encoding="utf-8")
            runtime_path.write_text(log_text, encoding="utf-8")
            rss_path = service_root / "rss.csv"
            rss_path.write_text(
                "monotonic_ns,rss_bytes,rss_mb,process_count\n1,943718400,900,1\n2,1048576000,1000,1\n",
                encoding="utf-8",
            )
            startup_start = counter * 2_000_000_000
            startup_ready = startup_start + 250_000_000
            service_run_id = f"{counter:032x}"
            counter += 1
            process_path = raw / "service" / f"{backend}-p{parallel}-process.json"
            write_json(
                process_path,
                {
                    "schema_version": PROBE_SCHEMA_VERSION,
                    "service_run_id": service_run_id,
                    "backend": backend,
                    "parallel": parallel,
                    "pid": 1000 + counter,
                    "startup_start_ns": startup_start,
                    "startup_ready_ns": startup_ready,
                    "idle_rss": {
                        "monotonic_ns": startup_ready + 1,
                        "rss_bytes": 943718400,
                        "process_count": 1,
                    },
                    "command": command,
                },
            )
            warmup_paths = []
            for client_index in range(parallel):
                request_id = f"{request_counter:032x}"
                request_counter += 1
                path = receipts / f"warmup-r0-c{client_index}-{request_id}.json"
                _request_receipt(
                    path,
                    case=case,
                    backend=backend,
                    parallel=parallel,
                    phase="warmup",
                    repetition=0,
                    client_index=client_index,
                    request_id=request_id,
                    start_ns=startup_ready + 1_000_000 + client_index,
                )
                warmup_paths.append(path.relative_to(artifacts).as_posix())
            requests = []
            rounds = []
            for repetition in range(3):
                round_start = startup_ready + (repetition + 1) * 1_000_000_000
                round_requests = []
                for client_index in range(parallel):
                    request_id = f"{request_counter:032x}"
                    request_counter += 1
                    start_ns = round_start + 1_000_000 + client_index
                    path = receipts / f"measured-r{repetition}-c{client_index}-{request_id}.json"
                    _, content = _request_receipt(
                        path,
                        case=case,
                        backend=backend,
                        parallel=parallel,
                        phase="measured",
                        repetition=repetition,
                        client_index=client_index,
                        request_id=request_id,
                        start_ns=start_ns,
                    )
                    score = score_case(case, content)
                    item = ServiceRequestProbe(
                        request_id=request_id,
                        backend=backend,
                        parallel=parallel,
                        repetition=repetition,
                        client_index=client_index,
                        start_ns=start_ns,
                        first_content_token_ns=start_ns + 10_000_000,
                        end_ns=start_ns + 110_000_000,
                        ttft_ms=10.0,
                        e2e_ms=110.0,
                        prompt_tokens=64,
                        completion_tokens=10,
                        generation_tok_s=100.0,
                        schema_valid=True,
                        safety_score=100.0,
                        quality_score=score.quality_score,
                        response_sha256=hashlib.sha256(content.encode()).hexdigest(),
                        receipt_path=path.relative_to(artifacts).as_posix(),
                    )
                    requests.append(item)
                    round_requests.append(item)
                round_end = round_start + 200_000_000
                rounds.append(
                    ConcurrencyRoundProbe(
                        backend=backend,
                        parallel=parallel,
                        repetition=repetition,
                        start_ns=round_start,
                        end_ns=round_end,
                        wall_time_ms=200.0,
                        completed_requests=parallel,
                        error_count=0,
                        generated_tokens=10 * parallel,
                        requests_per_second=5.0 * parallel,
                        generated_tokens_per_second=50.0 * parallel,
                        request_ids=[item.request_id for item in round_requests],
                    )
                )
            service_runs.append(
                ServiceRun(
                    service_run_id=service_run_id,
                    backend=backend,
                    parallel=parallel,
                    repetitions=3,
                    warmup_rounds=1,
                    startup_start_ns=startup_start,
                    startup_ready_ns=startup_ready,
                    startup_ms=250.0,
                    model_file_sha256=q4.expected_sha256,
                    binary_sha256=hashes[backend]["llama-server"],
                    threads=4,
                    batch=256,
                    ubatch=128,
                    context_total=2048 * parallel,
                    context_per_slot=2048,
                    seed=20260813,
                    command=command,
                    cpu_only_verified=True,
                    backend_verified=True,
                    idle_rss_bytes=943718400,
                    peak_rss_bytes=1048576000,
                    runtime_log_path=runtime_path.relative_to(artifacts).as_posix(),
                    command_receipt_path=command_path.relative_to(artifacts).as_posix(),
                    process_receipt_path=process_path.relative_to(artifacts).as_posix(),
                    rss_path=rss_path.relative_to(artifacts).as_posix(),
                    stdout_path=stdout_path.relative_to(artifacts).as_posix(),
                    stderr_path=stderr_path.relative_to(artifacts).as_posix(),
                    warmup_receipt_paths=warmup_paths,
                    requests=requests,
                    rounds=rounds,
                )
            )

    raw_files = {
        path.relative_to(artifacts).as_posix(): sha256_file(path)
        for path in sorted(raw.rglob("*"))
        if path.is_file()
    }
    evidence = PerformanceProbeEvidence(
        schema_version=PROBE_SCHEMA_VERSION,
        session_id=SESSION_ID,
        generated_at="2026-08-14T00:00:00Z",
        evidence_scope="supporting-ranking-and-concurrency-not-held-out-headline-claim",
        build_source_commit=SOURCE_COMMIT,
        prompt_sha256=prompt_fingerprint(),
        case_id=case.case_id,
        case_split="calibration",
        max_tokens=512,
        seed=20260813,
        micro_prompt_tokens=128,
        micro_generation_tokens=64,
        micro_threads=[2, 4],
        repetitions=3,
        max_runtime_minutes=20,
        start_ns=1_000_000_000,
        end_ns=121_000_000_000,
        elapsed_seconds=120.0,
        raw_root=f"performance-probes-raw/{SESSION_ID}",
        raw_files=raw_files,
        micro_help_paths=help_paths,
        model_inventory_proofs={
            "strong-q4-0": _reviewed_proof("strong-q4-0"),
            "strong-q8-0": _reviewed_proof("strong-q8-0"),
        },
        micro_runs=micro_runs,
        service_runs=service_runs,
        fair_pair_verified=True,
        matrix_complete=True,
        failed_micro_cells=0,
        failed_service_rounds=0,
    )
    path = artifacts / "performance-probes.json"
    write_json(path, evidence)
    return path, evidence


def test_versioned_llama_bench_parser_and_exact_cpu_command() -> None:
    capabilities = inspect_help("--device DEVICES\n-ngl N\n-v, --verbose\n")
    assert capabilities.cpu_only_complete
    command = build_command("llama-bench", "model.gguf", threads=4, capabilities=capabilities)
    assert command.count("-v") == 1
    assert command[-5:] == ["--device", "none", "-ngl", "0", "-v"]
    output = """
| model | size | backend | threads | test | t/s |
| --- | ---: | --- | ---: | --- | ---: |
| qwen | 1016.8 MiB | CPU | 4 | pp128 | 100.50 ± 2.25 |
| qwen | 1016.8 MiB | CPU | 4 | tg64 | 20.25 ± 0.50 |
"""
    pp, tg = require_tests(parse_output(output), prompt_tokens=128, generation_tokens=64, threads=4)
    assert pp.tokens_per_second == 100.5
    assert tg.tokens_per_second_stddev == 0.5
    with pytest.raises(ValueError, match="at least three"):
        build_command("bench", "model", threads=4, repetitions=2)


def test_semantic_loader_replays_micro_stdout_and_exact_command(tmp_path: Path) -> None:
    path, evidence = _build_fixture(tmp_path)
    loaded = load_performance_probes(path, project_root=ROOT)
    summary = summarize_performance_probes(loaded)
    assert summary["semantic_replay_verified"] is True
    assert len(summary["micro"]) == 12
    assert all(row["idle_rss_mb"] > 0 for row in summary["service"])

    payload = evidence.model_dump(mode="json")
    payload["micro_runs"][0]["metrics"][0]["tokens_per_second"] = 999.0
    write_json(path, payload)
    with pytest.raises(ValueError, match="micro metrics"):
        load_performance_probes(path, project_root=ROOT)

    payload = evidence.model_dump(mode="json")
    payload["micro_runs"][0]["command"].append("-v")
    with pytest.raises(ValidationError, match="exactly one canonical -v"):
        PerformanceProbeEvidence.model_validate(payload)
    payload = evidence.model_dump(mode="json")
    payload["micro_runs"][0]["command"].remove("-v")
    with pytest.raises(ValidationError, match="exactly one canonical -v"):
        PerformanceProbeEvidence.model_validate(payload)


def test_service_round_membership_tokens_wall_and_receipt_replay(tmp_path: Path) -> None:
    path, evidence = _build_fixture(tmp_path)
    payload = evidence.model_dump(mode="json")
    run = payload["service_runs"][1]
    run["rounds"][1]["request_ids"] = list(run["rounds"][0]["request_ids"])
    with pytest.raises(ValidationError, match="membership"):
        PerformanceProbeEvidence.model_validate(payload)

    payload = evidence.model_dump(mode="json")
    run = payload["service_runs"][1]
    run["rounds"][0]["generated_tokens"] += 1
    run["rounds"][0]["generated_tokens_per_second"] += 5
    with pytest.raises(ValidationError, match="generated-token sum"):
        PerformanceProbeEvidence.model_validate(payload)

    payload = evidence.model_dump(mode="json")
    run = next(item for item in payload["service_runs"] if item["parallel"] == 2)
    first, second = [item for item in run["requests"] if item["repetition"] == 0]
    second["start_ns"] = first["end_ns"] + 1
    second["first_content_token_ns"] = second["start_ns"] + 10_000_000
    second["end_ns"] = second["start_ns"] + 50_000_000
    second["ttft_ms"] = 10.0
    second["e2e_ms"] = 50.0
    second["generation_tok_s"] = 250.0
    with pytest.raises(ValidationError, match="concurrency is unproven"):
        PerformanceProbeEvidence.model_validate(payload)

    payload = evidence.model_dump(mode="json")
    request = payload["service_runs"][0]["requests"][0]
    receipt_path = path.parent / request["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["response"]["usage"]["completion_tokens"] = 11
    write_json(receipt_path, receipt)
    payload["raw_files"][request["receipt_path"]] = sha256_file(receipt_path)
    write_json(path, payload)
    with pytest.raises(ValueError, match="summary does not replay"):
        load_performance_probes(path, project_root=ROOT)


def test_probe_binds_build_model_inventory_and_rss_raw(tmp_path: Path) -> None:
    path, evidence = _build_fixture(tmp_path)
    payload = evidence.model_dump(mode="json")
    rss_relative = payload["service_runs"][0]["rss_path"]
    rss_path = path.parent / rss_relative
    rss_path.write_text(
        "monotonic_ns,rss_bytes,rss_mb,process_count\n1,943718400,900,1\n",
        encoding="utf-8",
    )
    payload["raw_files"][rss_relative] = sha256_file(rss_path)
    write_json(path, payload)
    with pytest.raises(ValueError, match="peak RSS"):
        load_performance_probes(path, project_root=ROOT)

    path, evidence = _build_fixture(tmp_path / "second")
    manifest = json.loads((path.parent / "build-manifest.json").read_text(encoding="utf-8"))
    manifest["variants"][0]["binary_sha256"]["llama-bench"] = "f" * 64
    write_json(path.parent / "build-manifest.json", manifest)
    with pytest.raises(ValueError, match="command/hash"):
        load_performance_probes(path, project_root=ROOT)


def test_probe_matrix_context_and_semantic_fingerprint_are_strict(tmp_path: Path) -> None:
    path, evidence = _build_fixture(tmp_path)
    assert micro_thread_candidates(8) == (4, 8)
    with pytest.raises(Exception, match="at least two threads"):
        micro_thread_candidates(1)
    payload = evidence.model_dump(mode="json")
    payload["service_runs"][0]["context_per_slot"] = 1024
    payload["service_runs"][0]["context_total"] = 1024
    with pytest.raises(ValidationError):
        PerformanceProbeEvidence.model_validate(payload)

    private_payload = copy.deepcopy(evidence.model_dump(mode="json"))
    for run in [*private_payload["micro_runs"], *private_payload["service_runs"]]:
        run["command"][0] = str(Path.home() / "repo" / run["command"][0])
        model_index = 2 if "-m" in run["command"] else run["command"].index("--model") + 1
        run["command"][model_index] = str(Path.home() / "repo" / run["command"][model_index])
    private = PerformanceProbeEvidence.model_validate(private_payload)
    private_hash = performance_probe_semantic_sha256(private)
    public_payload = copy.deepcopy(private_payload)
    for run in [*public_payload["micro_runs"], *public_payload["service_runs"]]:
        run["command"] = [
            token.replace(str(Path.home()), "<redacted-home>") for token in run["command"]
        ]
    public = PerformanceProbeEvidence.model_validate(public_payload)
    assert performance_probe_semantic_sha256(public) == private_hash

    destination = tmp_path / "artifacts-public"
    redactor = runpy.run_path(str(ROOT / "scripts/redact-artifacts.py"))
    redactor["sanitized_copy"](path.parent, destination)
    public_replayed = load_performance_probes(
        destination / "performance-probes.json",
        project_root=ROOT,
    )
    assert performance_probe_semantic_sha256(public_replayed) == (
        performance_probe_semantic_sha256(evidence)
    )


def test_service_round_timeout_fails_before_artifact_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = {case.case_id: case for case in load_cases(ROOT / "demo/cases.jsonl")}["incident-001"]

    class HangingClient:
        async def chat_completion(self, **options: object) -> object:
            await asyncio.Event().wait()
            raise AssertionError(options)

    async def immediate_timeout(awaitable: object, *, timeout: float) -> object:
        assert timeout > 0
        awaitable.cancel()
        with suppress(asyncio.CancelledError):
            await awaitable
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
    with pytest.raises(PerformanceProbeError, match="hard runtime budget"):
        asyncio.run(
            _request_round(
                HangingClient(),
                case=case,
                backend="generic",
                parallel=1,
                repetition=0,
                model_alias="probe-generic-p1",
                phase="measured",
                receipt_dir=tmp_path / "raw",
                artifacts_dir=tmp_path,
                deadline=10**12,
            )
        )
    assert not (tmp_path / "performance-probes.json").exists()


def test_service_capability_inspection_and_startup_share_hard_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remaining = iter((12.0, 7.0))
    observed: dict[str, object] = {}
    capabilities = object()

    def fake_remaining(_deadline: float, *, floor: float = 1.0) -> float:
        assert floor == 1.0
        return next(remaining)

    def fake_inspect(binary: Path, timeout_s: float = 30.0) -> object:
        observed["binary"] = binary
        observed["inspection_timeout_s"] = timeout_s
        return capabilities

    class StopAfterManagerConstruction(RuntimeError):
        pass

    def fake_manager(
        config: object,
        *,
        capabilities: object,
        log_dir: Path,
        startup_timeout_s: float,
    ) -> object:
        observed["config"] = config
        observed["capabilities"] = capabilities
        observed["log_dir"] = log_dir
        observed["startup_timeout_s"] = startup_timeout_s
        raise StopAfterManagerConstruction

    monkeypatch.setattr(probes_module, "_remaining_seconds", fake_remaining)
    monkeypatch.setattr(probes_module, "inspect_llama_server_capabilities", fake_inspect)
    monkeypatch.setattr(probes_module, "LlamaServerProcess", fake_manager)
    inputs = probes_module._BackendInputs(
        backend="generic",
        server=tmp_path / "llama-server",
        bench=tmp_path / "llama-bench",
        cache=tmp_path / "CMakeCache.txt",
        cache_text="GGML_CUDA:BOOL=OFF",
        server_sha256="a" * 64,
        bench_sha256="b" * 64,
    )
    model = probes_module._ModelInputs(
        model_id="strong-q4-0",
        path=tmp_path / "model.gguf",
        quantization="Q4_0",
        sha256="c" * 64,
        proof=ModelInventoryProof(
            model_id="strong-q4-0",
            model_sha256="c" * 64,
            inventory_sha256=None,
        ),
    )

    with pytest.raises(StopAfterManagerConstruction):
        asyncio.run(
            probes_module._service_run(
                inputs=inputs,
                q4=model,
                threads=4,
                parallel=1,
                repetitions=3,
                case=object(),
                raw_root=tmp_path / "raw",
                artifacts_dir=tmp_path,
                deadline=999.0,
            )
        )

    assert observed["binary"] == inputs.server
    assert observed["inspection_timeout_s"] == 12.0
    assert observed["capabilities"] is capabilities
    assert observed["startup_timeout_s"] == 7.0
