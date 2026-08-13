from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import (
    IncidentCase,
    ToolName,
    TriageResponse,
    triage_json_schema,
    triage_openai_response_format,
)
from a64pilot.benchmark.quality import load_cases, load_split, score_case
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.build.cmake import BUILD_TARGETS, COMMON_DEFINITIONS
from a64pilot.build.llama_source import OFFICIAL_LLAMA_REPOSITORY
from a64pilot.build.verify_backend import verify_backend_log, verify_cpu_only
from a64pilot.models.checksum import ChecksumResult
from a64pilot.models.gguf import GgufTensor, ModelInventoryProof
from a64pilot.models.registry import ModelSpec, default_registry, get_model
from a64pilot.provenance import sha256_file, write_json
from a64pilot.report.integrity import SECRET_PATTERN, validate_evidence_bundle
from a64pilot.schemas import BenchmarkRecord, SystemInfo

ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "a94d563ed801d1da1b8c2432946de07d0231bb3d"


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    root: Path
    first_generic_run: str
    first_kleidiai_run: str

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"


def _tool_arguments(name: ToolName) -> dict[str, object]:
    return {
        ToolName.INSPECT_SERVICE: {"service": "fixture-service"},
        ToolName.READ_LOGS: {"service": "fixture-service", "limit": 20},
        ToolName.CHECK_DISK: {"mount": "/"},
        ToolName.CHECK_MEMORY: {"scope": "node"},
        ToolName.CHECK_NETWORK: {"target": "fixture-service", "port": 443},
        ToolName.ESCALATE: {"reason": "The synthetic evidence is intentionally ambiguous."},
    }[name]


def _expected_response(case: IncidentCase) -> str:
    tools = list(case.required_tools)
    if case.expected_escalation and ToolName.ESCALATE not in tools:
        tools.append(ToolName.ESCALATE)
    response = TriageResponse(
        summary="The synthetic incident was triaged from its supplied observations.",
        severity=case.expected_severity,
        diagnosis=case.expected_diagnosis,
        hypotheses=[
            {
                "cause": f"The observations are consistent with {case.expected_diagnosis.value}.",
                "evidence": [case.incident],
                "confidence": 0.9,
            }
        ],
        tool_calls=[{"name": tool, "arguments": _tool_arguments(tool)} for tool in tools],
        safe_next_action="Collect the listed read-only observations for human review.",
        needs_escalation=case.expected_escalation,
    )
    return response.model_dump_json()


def _aarch64_elf_stub(path: Path) -> None:
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    header[18:20] = (183).to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header)
    path.chmod(0o755)


def _cmake_values(backend: str) -> dict[str, str]:
    values = dict(COMMON_DEFINITIONS)
    values["GGML_CPU_KLEIDIAI"] = "ON" if backend == "kleidiai" else "OFF"
    return values


def _write_build_fixture(project: Path) -> None:
    variants: list[dict[str, object]] = []
    for backend in ("generic", "kleidiai"):
        build_root = project / f"build/llama-{backend}"
        binaries: dict[str, str] = {}
        binary_hashes: dict[str, str] = {}
        for name in BUILD_TARGETS:
            relative = Path(f"build/llama-{backend}/bin/{name}")
            binary = project / relative
            _aarch64_elf_stub(binary)
            binaries[name] = str(relative)
            binary_hashes[name] = sha256_file(binary)
        values = _cmake_values(backend)
        cache = "".join(f"{key}:STRING={value}\n" for key, value in sorted(values.items()))
        (build_root / "CMakeCache.txt").write_text(cache, encoding="utf-8")
        variants.append(
            {
                "backend": backend,
                "source_commit": PINNED_COMMIT,
                "build_type": "Release",
                "cmake_flags": [f"-D{key}={value}" for key, value in sorted(values.items())],
                "compiler": "fixture-cc 1.0",
                "binaries": binaries,
                "binary_sha256": binary_hashes,
                "cpu_only_configured": True,
                "kleidiai_configured": backend == "kleidiai",
                "runtime_marker_verified": backend == "kleidiai",
            }
        )
    write_json(
        project / "artifacts/build-manifest.json",
        {"source_url": OFFICIAL_LLAMA_REPOSITORY, "variants": variants},
    )


def _model_row(spec: ModelSpec) -> dict[str, object]:
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
            fallback.to_dict() for fallback in spec.reviewed_kleidiai_fallbacks
        ],
    }


def _write_model_fixture(project: Path) -> None:
    rows = []
    for spec in default_registry():
        rows.append(_model_row(spec))
        model_path = project / "models" / spec.expected_filename
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"GGUFtiny-test-fixture")
    write_json(project / "artifacts/model-manifest.json", {"models": rows})


def _system_info() -> SystemInfo:
    return SystemInfo(
        architecture="aarch64",
        architecture_raw="aarch64",
        operating_system="Linux",
        kernel="fixture-kernel",
        cpu_model="fixture Arm CPU",
        python_version="3.12.0",
        arm64=True,
        real_benchmark_eligible=True,
        logical_cores=4,
        physical_cores=4,
        memory_bytes=16 * 1024**3,
        filesystem_free_bytes=8 * 1024**3,
        public_redacted=True,
    )


def _command(backend: str, model_filename: str) -> list[str]:
    return [
        f"build/llama-{backend}/bin/llama-server",
        "--model",
        f"models/{model_filename}",
        "--alias",
        "a1-generic-q4-0" if backend == "generic" else "a2-kleidiai-q4-0",
        "--host",
        "127.0.0.1",
        "--threads",
        "4",
        "--batch-size",
        "256",
        "--ubatch-size",
        "128",
        "--ctx-size",
        "2048",
        "--parallel",
        "1",
        "--seed",
        "20260813",
        "-lv",
        "4",
        "--n-gpu-layers",
        "0",
        "--device",
        "none",
    ]


def _runtime_log(backend: str) -> str:
    if backend == "kleidiai":
        return (
            "kleidiai: primary q4 kernel feature sve2\n"
            "load_tensors: CPU_KLEIDIAI model buffer size = 934.62 MiB\n"
            "CPU backend ready\n"
        )
    return "generic CPU backend ready\n"


def _write_record(
    project: Path,
    store: ArtifactStore,
    *,
    case: IncidentCase,
    backend: str,
    sequence: int,
) -> str:
    spec = get_model("strong-q4-0")
    candidate_id = "a1-generic-q4-0" if backend == "generic" else "a2-kleidiai-q4-0"
    stage_code = "a1" if backend == "generic" else "a2"
    stage = "baseline" if backend == "generic" else "kleidiai"
    run_id = f"{sequence:032x}"
    command = _command(backend, spec.expected_filename)
    cache = (project / f"build/llama-{backend}/CMakeCache.txt").read_text(encoding="utf-8")
    runtime_log = _runtime_log(backend)
    backend_proof = verify_backend_log(
        runtime_log,
        backend,
        quantization="Q4_0" if backend == "kleidiai" else None,
    )
    cpu_proof = verify_cpu_only(
        command,
        cmake_cache=cache,
        runtime_log=runtime_log,
        require_device_none=True,
    )
    assert backend_proof.verified
    assert cpu_proof.verified

    content = _expected_response(case)
    score = score_case(case, content)
    start_ns = sequence * 1_000_000_000
    e2e_ms = 100.0 if backend == "generic" else 80.0
    end_ns = start_ns + int(e2e_ms * 1_000_000)
    first_token_ns = start_ns + 10_000_000
    record = BenchmarkRecord(
        run_id=run_id,
        candidate_id=candidate_id,
        stage=stage,
        case_id=case.case_id,
        repetition=0,
        split="test",
        backend=backend,
        model_role="strong",
        model_file_sha256=spec.expected_sha256,
        quantization="Q4_0",
        threads=4,
        batch=256,
        ubatch=128,
        parallel=1,
        affinity=[],
        cpu_only_verified=True,
        kleidiai_verified=backend == "kleidiai",
        start_ns=start_ns,
        first_token_ns=first_token_ns,
        end_ns=end_ns,
        ttft_ms=10.0,
        e2e_ms=e2e_ms,
        prompt_tokens=64,
        completion_tokens=32,
        generation_tok_s=20.0,
        peak_rss_mb=256.0,
        route="strong",
        schema_valid=score.schema_valid,
        quality_score=score.quality_score,
        safety_score=score.safety_score,
        command=command,
        errors=list(score.issues),
    )
    candidate = {
        "candidate_id": candidate_id,
        "stage": stage_code,
        "backend": backend,
        "binary": command[0],
        "cmake_cache": f"build/llama-{backend}/CMakeCache.txt",
        "model": f"models/{spec.expected_filename}",
        "model_role": "strong",
        "quantization": "Q4_0",
        "threads": 4,
        "batch": 256,
        "ubatch": 128,
        "parallel": 1,
        "context": 2048,
        "affinity": None,
    }
    store.append_record(record)
    store.write_metadata(
        run_id,
        "run-config.json",
        {
            "candidate": candidate,
            "prompt_sha256": prompt_fingerprint(),
            "triage_schema": triage_json_schema(),
            "backend_proof": backend_proof.to_dict(),
            "cpu_only_proof": cpu_proof.to_dict(),
        },
    )
    store.write_metadata(run_id, "runtime-proof.txt", runtime_log)
    store.write_metadata(
        run_id,
        "request.json",
        {
            "case_id": case.case_id,
            "repetition": 0,
            "messages": build_messages(case.incident),
            "model": candidate_id,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 256,
            "seed": 20260813,
            "stream": True,
            "response_format": triage_openai_response_format(),
        },
    )
    store.write_metadata(
        run_id,
        "response.json",
        {
            "content": content,
            "usage": {"prompt_tokens": 64, "completion_tokens": 32},
            "finish_reason": "stop",
            "timing": {
                "start_ns": start_ns,
                "first_content_token_ns": first_token_ns,
                "end_ns": end_ns,
                "ttft_ms": 10.0,
                "e2e_ms": e2e_ms,
            },
            "score": score.as_dict(),
        },
    )
    store.finalize(run_id)
    return run_id


def _write_valid_bundle(project: Path) -> EvidenceBundle:
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    demo = project / "demo"
    demo.mkdir()
    shutil.copy2(ROOT / "demo/cases.jsonl", demo / "cases.jsonl")
    shutil.copy2(ROOT / "demo/split.json", demo / "split.json")
    source_dir = project / "third_party/llama.cpp"
    source_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "third_party/llama.cpp.lock", project / "third_party/llama.cpp.lock")
    write_json(artifacts / "system-info.json", _system_info())
    _write_build_fixture(project)
    _write_model_fixture(project)

    cases = {case.case_id: case for case in load_cases(demo / "cases.jsonl")}
    split = load_split(demo / "split.json")
    store = ArtifactStore(artifacts / "raw")
    first_generic = ""
    first_kleidiai = ""
    sequence = 1
    for case_id in split.test:
        generic_id = _write_record(
            project,
            store,
            case=cases[case_id],
            backend="generic",
            sequence=sequence,
        )
        sequence += 1
        kleidiai_id = _write_record(
            project,
            store,
            case=cases[case_id],
            backend="kleidiai",
            sequence=sequence,
        )
        sequence += 1
        first_generic = first_generic or generic_id
        first_kleidiai = first_kleidiai or kleidiai_id
    return EvidenceBundle(project, first_generic, first_kleidiai)


def _patch_external_proofs(monkeypatch: pytest.MonkeyPatch, bundle: EvidenceBundle) -> None:
    current_system = _system_info()
    monkeypatch.setattr(
        "a64pilot.hardware.detect.collect_system_info",
        lambda: SimpleNamespace(to_schema=lambda: current_system),
    )
    monkeypatch.setattr(
        "a64pilot.build.llama_source.verify_official_remote", lambda _checkout: None
    )
    monkeypatch.setattr(
        "a64pilot.build.llama_source.current_commit", lambda _checkout: PINNED_COMMIT
    )
    monkeypatch.setattr(
        "a64pilot.runtime.llama_command.inspect_llama_server_capabilities",
        lambda _binary: None,
    )

    def accept_pinned_large_model(
        path: Path,
        expected_sha256: str,
        *,
        expected_bytes: int | None = None,
    ) -> ChecksumResult:
        return ChecksumResult(
            path=str(path),
            expected_sha256=expected_sha256,
            actual_sha256=expected_sha256,
            bytes=expected_bytes,
            valid=True,
        )

    monkeypatch.setattr("a64pilot.models.checksum.verify_file", accept_pinned_large_model)

    def accept_pinned_inventory(
        _path: Path,
        spec: ModelSpec,
        *,
        actual_sha256: str,
    ) -> ModelInventoryProof:
        return ModelInventoryProof(
            model_id=spec.model_id,
            model_sha256=actual_sha256,
            inventory_sha256=spec.expected_tensor_inventory_sha256,
            tensor_histogram=spec.expected_tensor_histogram,
            reviewed_fallback_tensors=tuple(
                GgufTensor(item.name, item.tensor_type, item.dimensions)
                for item in spec.reviewed_kleidiai_fallbacks
            ),
            verified=True,
        )

    monkeypatch.setattr("a64pilot.models.gguf.verify_model_inventory", accept_pinned_inventory)
    assert bundle.root.is_dir()


@pytest.fixture()
def valid_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvidenceBundle:
    bundle = _write_valid_bundle(tmp_path)
    _patch_external_proofs(monkeypatch, bundle)
    return bundle


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _refinalize(bundle: EvidenceBundle, run_id: str) -> None:
    ArtifactStore(bundle.artifacts / "raw").finalize(run_id)


def test_complete_tiny_evidence_bundle_replays_without_errors(
    valid_bundle: EvidenceBundle,
) -> None:
    records, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert len(records) == 40
    assert errors == []


def test_secret_pattern_does_not_match_risk_register_filename() -> None:
    assert SECRET_PATTERN.search("docs/08-risk-register-and-fallbacks.md") is None
    assert SECRET_PATTERN.search("sk-" + "A" * 24) is not None


def test_x86_system_manifest_is_rejected(valid_bundle: EvidenceBundle) -> None:
    path = valid_bundle.artifacts / "system-info.json"
    payload = _load_json(path)
    payload.update(
        {
            "architecture": "x86_64",
            "architecture_raw": "x86_64",
            "arm64": False,
            "real_benchmark_eligible": False,
        }
    )
    write_json(path, payload)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert "system manifest is not an Arm64 Linux target" in errors


def test_model_registry_mismatch_is_rejected(valid_bundle: EvidenceBundle) -> None:
    path = valid_bundle.artifacts / "model-manifest.json"
    payload = _load_json(path)
    models = payload["models"]
    assert isinstance(models, list) and isinstance(models[0], dict)
    models[0]["repository"] = "attacker/unreviewed-model"
    write_json(path, payload)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("repository disagrees with reviewed registry" in error for error in errors)


def test_binary_hash_tampering_is_rejected(valid_bundle: EvidenceBundle) -> None:
    path = valid_bundle.artifacts / "build-manifest.json"
    payload = _load_json(path)
    variants = payload["variants"]
    assert isinstance(variants, list) and isinstance(variants[0], dict)
    binary_hashes = variants[0]["binary_sha256"]
    assert isinstance(binary_hashes, dict)
    binary_hashes["llama-server"] = "0" * 64
    write_json(path, payload)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("SHA-256 mismatch for llama-server" in error for error in errors)


def test_runtime_marker_tampering_is_rejected(valid_bundle: EvidenceBundle) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_kleidiai_run
    (run_dir / "runtime-proof.txt").write_text("generic CPU backend ready\n", encoding="utf-8")
    _refinalize(valid_bundle, valid_bundle.first_kleidiai_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("runtime log has no CPU_KLEIDIAI model buffer marker" in error for error in errors)
    assert any("runtime log has no KleidiAI primary quant" in error for error in errors)


def test_command_fair_settings_tampering_is_rejected(valid_bundle: EvidenceBundle) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_kleidiai_run
    request_rows = (run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    record = BenchmarkRecord.model_validate_json(request_rows[0])
    command = list(record.command)
    command[command.index("--threads") + 1] = "1"
    modified = record.model_copy(update={"command": command})
    (run_dir / "requests.jsonl").write_text(
        modified.model_dump_json() + "\n",
        encoding="utf-8",
    )
    _refinalize(valid_bundle, valid_bundle.first_kleidiai_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("command --threads must appear exactly once as 4" in error for error in errors)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("remove", "command -lv must appear exactly once as 4"),
        ("change", "command -lv must appear exactly once as 4"),
        ("duplicate", "command -lv must appear exactly once as 4"),
    ],
)
def test_command_log_verbosity_tampering_is_rejected(
    valid_bundle: EvidenceBundle, mutation: str, expected: str
) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_kleidiai_run
    request_rows = (run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    record = BenchmarkRecord.model_validate_json(request_rows[0])
    command = list(record.command)
    index = command.index("-lv")
    if mutation == "remove":
        del command[index : index + 2]
    elif mutation == "change":
        command[index + 1] = "3"
    else:
        command.extend(("--verbosity", "4"))
    modified = record.model_copy(update={"command": command})
    (run_dir / "requests.jsonl").write_text(
        modified.model_dump_json() + "\n",
        encoding="utf-8",
    )
    _refinalize(valid_bundle, valid_bundle.first_kleidiai_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any(expected in error for error in errors)


def test_request_sampling_settings_tampering_is_rejected(valid_bundle: EvidenceBundle) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_generic_run
    path = run_dir / "request.json"
    payload = _load_json(path)
    payload["temperature"] = 0.5
    write_json(path, payload)
    _refinalize(valid_bundle, valid_bundle.first_generic_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("request temperature disagrees" in error for error in errors)


def test_request_tampering_is_rejected_after_rehash(valid_bundle: EvidenceBundle) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_generic_run
    path = run_dir / "request.json"
    payload = _load_json(path)
    payload["messages"] = [{"role": "user", "content": "labels leaked into prompt"}]
    write_json(path, payload)
    _refinalize(valid_bundle, valid_bundle.first_generic_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("request prompt does not match the frozen prompt" in error for error in errors)


def test_response_tampering_is_rejected_after_rehash(valid_bundle: EvidenceBundle) -> None:
    run_dir = valid_bundle.artifacts / "raw" / valid_bundle.first_generic_run
    path = run_dir / "response.json"
    payload = _load_json(path)
    payload["content"] = '{"summary":"forged"}'
    write_json(path, payload)
    _refinalize(valid_bundle, valid_bundle.first_generic_run)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert any("schema score disagrees with response replay" in error for error in errors)


def test_duplicate_run_id_is_rejected(valid_bundle: EvidenceBundle) -> None:
    source = valid_bundle.artifacts / "raw" / valid_bundle.first_generic_run
    duplicate = valid_bundle.artifacts / "raw" / ("f" * 32)
    shutil.copytree(source, duplicate)

    _, errors = validate_evidence_bundle(valid_bundle.artifacts)

    assert "raw evidence contains duplicate run_id values" in errors
    assert any("directory name disagrees with record run_id" in error for error in errors)
