"""Submission gates for claims, placeholders, private data, and required files."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from a64pilot.schemas import BenchmarkRecord, Claim

_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _option_value(command: list[str], *names: str) -> str | None:
    for index, token in enumerate(command):
        key, separator, value = token.partition("=")
        if key not in names:
            continue
        if separator:
            return value
        if index + 1 < len(command):
            return command[index + 1]
    return None


def _option_values(command: list[str], *names: str) -> list[str]:
    """Collect every spelling so duplicate/ambiguous CLI settings fail closed."""

    values: list[str] = []
    for index, token in enumerate(command):
        key, separator, value = token.partition("=")
        if key not in names:
            continue
        if separator:
            values.append(value)
        elif index + 1 < len(command):
            values.append(command[index + 1])
        else:
            values.append("")
    return values


def _project_path(project_root: Path, value: str) -> Path:
    candidate = Path(value)
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    )
    if not resolved.is_relative_to(project_root.resolve()):
        raise ValueError(f"path escapes project root: {value}")
    return resolved


def _is_aarch64_elf(path: Path) -> bool:
    try:
        header = path.read_bytes()[:20]
    except OSError:
        return False
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return False
    byte_order = "little" if header[5] == 1 else "big" if header[5] == 2 else None
    return byte_order is not None and int.from_bytes(header[18:20], byte_order) == 183


def validate_evidence_bundle(
    artifacts_dir: Path | str = Path("artifacts"),
    *,
    require_records: bool = True,
) -> tuple[list[BenchmarkRecord], list[str]]:
    """Replay the target, binary, model, command, runtime, and response proof chain.

    A boolean in a generated JSON file is never accepted on its own.  Strict
    callers receive only rows whose referenced files and independently derived
    proofs agree.  Errors are accumulated so CI produces an actionable audit.
    """

    from a64pilot.agent.prompt import build_messages, prompt_fingerprint
    from a64pilot.agent.schema import triage_json_schema
    from a64pilot.benchmark.quality import load_cases, load_split, score_case, validate_dataset
    from a64pilot.benchmark.store import ArtifactStore
    from a64pilot.build.cmake import BUILD_TARGETS, COMMON_DEFINITIONS
    from a64pilot.build.llama_source import (
        OFFICIAL_LLAMA_REPOSITORY,
        current_commit,
        read_source_lock,
        verify_official_remote,
    )
    from a64pilot.build.verify_backend import parse_cmake_cache, verify_backend_log, verify_cpu_only
    from a64pilot.hardware.detect import collect_system_info
    from a64pilot.models.checksum import verify_manifest
    from a64pilot.models.gguf import ModelInventoryProof, verify_model_inventory
    from a64pilot.models.registry import default_registry
    from a64pilot.provenance import sha256_file
    from a64pilot.runtime.llama_command import inspect_llama_server_capabilities
    from a64pilot.schemas import BuildManifest, ModelManifest, SystemInfo

    artifacts = Path(artifacts_dir).resolve()
    project_root = artifacts.parent
    errors: list[str] = []

    def load(path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"invalid or missing manifest {path.name}: {type(exc).__name__}")
            return None

    system_payload = load(artifacts / "system-info.json")
    build_payload = load(artifacts / "build-manifest.json")
    model_payload = load(artifacts / "model-manifest.json")
    system = None
    build = None
    models = None
    if system_payload is not None:
        try:
            system = SystemInfo.model_validate(system_payload)
        except Exception as exc:
            errors.append(f"system manifest schema: {type(exc).__name__}")
    if build_payload is not None:
        try:
            build = BuildManifest.model_validate(build_payload)
        except Exception as exc:
            errors.append(f"build manifest schema: {type(exc).__name__}")
    if model_payload is not None:
        try:
            models = ModelManifest.model_validate(model_payload)
        except Exception as exc:
            errors.append(f"model manifest schema: {type(exc).__name__}")

    if system is not None:
        if system.architecture != "aarch64" or system.operating_system.lower() != "linux":
            errors.append("system manifest is not an Arm64 Linux target")
        if not system.arm64 or not system.real_benchmark_eligible:
            errors.append("system manifest is not eligible for final benchmarks")
        current_system = collect_system_info().to_schema()
        if (
            current_system.architecture != "aarch64"
            or current_system.operating_system.lower() != "linux"
            or not current_system.real_benchmark_eligible
        ):
            errors.append("strict verification is not running on an eligible Arm64 Linux host")
        for field_name in ("architecture", "operating_system", "kernel", "logical_cores"):
            if getattr(current_system, field_name) != getattr(system, field_name):
                errors.append(f"system manifest disagrees with current host: {field_name}")
    if build is not None:
        try:
            source_lock = read_source_lock(project_root / "third_party/llama.cpp.lock")
        except Exception as exc:
            source_lock = None
            errors.append(f"source lock: {type(exc).__name__}")
        if build.source_url != OFFICIAL_LLAMA_REPOSITORY:
            errors.append("build manifest source URL is not the official llama.cpp repository")
        checkout = project_root / "third_party/llama.cpp"
        if source_lock is not None:
            try:
                verify_official_remote(checkout)
                if current_commit(checkout) != source_lock.commit:
                    errors.append("llama.cpp checkout does not match the source lock")
            except Exception as exc:
                errors.append(f"llama.cpp checkout proof: {type(exc).__name__}")
        variants = {variant.backend: variant for variant in build.variants}
        if len(build.variants) != 2 or set(variants) != {"generic", "kleidiai"}:
            errors.append("build manifest needs exactly generic and kleidiai variants")
        else:
            generic = variants["generic"]
            optimized = variants["kleidiai"]
            commits = {generic.source_commit, optimized.source_commit}
            if len(commits) != 1 or not all(
                re.fullmatch(r"[0-9a-f]{40}", item) for item in commits
            ):
                errors.append("build manifest does not pin one full lowercase source commit")
            if source_lock is not None and commits != {source_lock.commit}:
                errors.append("build manifest commit disagrees with the source lock")
            if not generic.cpu_only_configured or not optimized.cpu_only_configured:
                errors.append("build manifest does not prove both variants CPU-only")
            if generic.kleidiai_configured or not optimized.kleidiai_configured:
                errors.append("build manifest does not prove the intended KleidiAI delta")
            if not optimized.runtime_marker_verified:
                errors.append("build manifest lacks a verified KleidiAI runtime marker")
            parsed_flags: dict[str, dict[str, str]] = {}
            caches: dict[str, str] = {}
            binary_paths: dict[str, dict[str, Path]] = {}
            for name, variant in variants.items():
                flags: dict[str, str] = {}
                for raw_flag in variant.cmake_flags:
                    match = re.fullmatch(r"-D([^=]+)=(.*)", raw_flag)
                    if not match:
                        errors.append(f"build {name}: malformed CMake flag {raw_flag!r}")
                        continue
                    flags[match.group(1)] = match.group(2)
                parsed_flags[name] = flags
                expected_kleidiai = "ON" if name == "kleidiai" else "OFF"
                if flags.get("GGML_CPU_KLEIDIAI") != expected_kleidiai:
                    errors.append(f"build {name}: incorrect GGML_CPU_KLEIDIAI flag")
                for key, value in COMMON_DEFINITIONS.items():
                    if flags.get(key) != value:
                        errors.append(f"build {name}: missing or changed fair flag {key}={value}")
                if set(variant.binaries) != set(BUILD_TARGETS):
                    errors.append(f"build {name}: binary inventory is incomplete")
                if set(variant.binary_sha256) != set(BUILD_TARGETS):
                    errors.append(f"build {name}: binary hash inventory is incomplete")
                binary_paths[name] = {}
                for binary_name in BUILD_TARGETS:
                    raw_path = variant.binaries.get(binary_name)
                    expected_hash = variant.binary_sha256.get(binary_name)
                    if raw_path is None or expected_hash is None:
                        continue
                    try:
                        binary_path = _project_path(project_root, raw_path)
                    except ValueError as exc:
                        errors.append(f"build {name}: {exc}")
                        continue
                    binary_paths[name][binary_name] = binary_path
                    if not binary_path.is_file():
                        errors.append(f"build {name}: missing binary {binary_name}")
                    elif sha256_file(binary_path) != expected_hash:
                        errors.append(f"build {name}: SHA-256 mismatch for {binary_name}")
                    elif not _is_aarch64_elf(binary_path):
                        errors.append(f"build {name}: {binary_name} is not an AArch64 ELF binary")
                    elif not os.access(binary_path, os.X_OK):
                        errors.append(f"build {name}: {binary_name} is not executable")
                server_path = binary_paths[name].get("llama-server")
                if server_path is not None:
                    try:
                        inspect_llama_server_capabilities(server_path)
                    except Exception as exc:
                        errors.append(f"build {name}: llama-server interface: {type(exc).__name__}")
                    cache_path = server_path.parent.parent / "CMakeCache.txt"
                    try:
                        cache_text = cache_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        errors.append(f"build {name}: missing CMakeCache.txt")
                    else:
                        caches[name] = cache_text
                        cache_values = parse_cmake_cache(cache_text)
                        for key, value in flags.items():
                            if cache_values.get(key, "").upper() != value.upper():
                                errors.append(f"build {name}: cache disagrees for {key}")
            if set(parsed_flags) == {"generic", "kleidiai"}:
                delta_keys = {
                    key
                    for key in set(parsed_flags["generic"]) | set(parsed_flags["kleidiai"])
                    if parsed_flags["generic"].get(key) != parsed_flags["kleidiai"].get(key)
                }
                if delta_keys != {"GGML_CPU_KLEIDIAI"}:
                    errors.append("build flags differ beyond GGML_CPU_KLEIDIAI")
        # Keep these available to the per-run verifier even if earlier checks failed.
        build_caches = locals().get("caches", {})
        build_binaries = locals().get("binary_paths", {})
    else:
        build_caches = {}
        build_binaries = {}
    model_inventory_proofs: dict[str, ModelInventoryProof] = {}
    if models is not None:
        reviewed_models = {spec.expected_filename: spec for spec in default_registry()}
        manifest_models = {model.filename: model for model in models.models}
        if len(models.models) != len(manifest_models):
            errors.append("model manifest contains duplicate filenames")
        if set(manifest_models) != set(reviewed_models):
            errors.append("model manifest does not exactly match the reviewed registry")
        actual_model_proof = verify_manifest(
            model_payload if isinstance(model_payload, dict) else {},
            models_dir=project_root / "models",
        )
        if not actual_model_proof.valid:
            errors.extend(f"model file: {error}" for error in actual_model_proof.errors)
        for model in models.models:
            if not re.fullmatch(r"[0-9a-f]{40}", model.revision):
                errors.append(f"model {model.filename}: revision is not a full lowercase commit")
            if not re.fullmatch(r"[0-9a-f]{64}", model.sha256):
                errors.append(f"model {model.filename}: SHA-256 is malformed")
            spec = reviewed_models.get(model.filename)
            if spec is None:
                continue
            expected = {
                "role": spec.role.value,
                "repository": spec.repository,
                "revision": spec.revision,
                "quantization": spec.quantization,
                "license": spec.license_id,
                "kleidiai_compatible": spec.kleidiai_compatible,
                "tensor_type_histogram": dict(spec.expected_tensor_histogram),
                "tensor_inventory_sha256": spec.expected_tensor_inventory_sha256,
            }
            for field_name, expected_value in expected.items():
                if getattr(model, field_name) != expected_value:
                    errors.append(
                        f"model {model.filename}: {field_name} disagrees with reviewed registry"
                    )
            if model.sha256 != spec.expected_sha256:
                errors.append(f"model {model.filename}: SHA-256 disagrees with pinned registry")
            if model.bytes != spec.expected_bytes:
                errors.append(f"model {model.filename}: size disagrees with pinned registry")
            expected_fallbacks = [
                fallback.to_dict() for fallback in spec.reviewed_kleidiai_fallbacks
            ]
            actual_fallbacks = [
                fallback.model_dump(mode="json") for fallback in model.reviewed_kleidiai_fallbacks
            ]
            if actual_fallbacks != expected_fallbacks:
                errors.append(
                    f"model {model.filename}: reviewed fallback inventory disagrees with registry"
                )
            if (
                Path(model.local_path).name != model.filename
                or Path(model.local_path).is_absolute()
            ):
                errors.append(
                    f"model {model.filename}: local path is not the reviewed flat filename"
                )
            try:
                model_path = _project_path(project_root, str(Path("models") / model.local_path))
            except ValueError as exc:
                errors.append(f"model {model.filename}: invalid model path: {exc}")
            else:
                inventory_proof = verify_model_inventory(
                    model_path,
                    spec,
                    actual_sha256=model.sha256,
                )
                if not inventory_proof.verified:
                    errors.extend(
                        f"model {model.filename}: {error}" for error in inventory_proof.errors
                    )
                else:
                    model_inventory_proofs[model.sha256] = inventory_proof

    store = ArtifactStore(artifacts / "raw")
    try:
        records = list(store.records(measured_only=True))
    except Exception as exc:
        records = []
        errors.append(f"raw record schema: {type(exc).__name__}")
    if require_records and not records:
        errors.append("no measured raw records")
    raw_ids: list[str] = []
    for request_path in sorted(store.root.glob("*/requests.jsonl")):
        try:
            raw_lines = [
                line
                for line in request_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(raw_lines) != 1:
                continue
            stored_record = BenchmarkRecord.model_validate_json(raw_lines[0])
        except Exception:
            continue
        raw_ids.append(stored_record.run_id)
        if stored_record.run_id != request_path.parent.name:
            errors.append(
                f"raw {request_path.parent.name}: directory name disagrees with record run_id"
            )
    if len(raw_ids) != len(set(raw_ids)):
        errors.append("raw evidence contains duplicate run_id values")
    known_model_hashes = {model.sha256 for model in models.models} if models is not None else set()
    model_by_hash = {model.sha256: model for model in models.models} if models is not None else {}

    try:
        cases = load_cases(project_root / "demo/cases.jsonl")
        split = load_split(project_root / "demo/split.json")
        validate_dataset(cases, split)
        cases_by_id = {case.case_id: case for case in cases}
    except Exception as exc:
        errors.append(f"quality dataset: {type(exc).__name__}")
        split = None
        cases_by_id = {}

    for record in records:
        prefix = f"raw {record.run_id}:"
        if not _RUN_ID_PATTERN.fullmatch(record.run_id):
            errors.append(f"{prefix} run_id is not a generated 32-character hex ID")
            continue
        run_dir = store.root / record.run_id
        try:
            request_lines = (run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            request_lines = []
        if len([line for line in request_lines if line.strip()]) != 1:
            errors.append(f"{prefix} requests.jsonl must contain exactly one row")
        if not (run_dir / "integrity.json").is_file():
            errors.append(f"{prefix} missing integrity.json")
        else:
            errors.extend(f"{prefix} {error}" for error in store.verify(record.run_id))
        for required in ("run-config.json", "request.json", "response.json", "runtime-proof.txt"):
            if not (run_dir / required).is_file():
                errors.append(f"{prefix} missing {required}")
        if record.model_file_sha256 not in known_model_hashes:
            errors.append(f"{prefix} model hash is absent from model manifest")
        if not record.cpu_only_verified:
            errors.append(f"{prefix} CPU-only proof is false")
        if record.backend == "kleidiai" and not record.kleidiai_verified:
            errors.append(f"{prefix} KleidiAI proof is false")

        config_payload = load(run_dir / "run-config.json")
        request_payload = load(run_dir / "request.json")
        response_payload = load(run_dir / "response.json")
        try:
            runtime_log = (run_dir / "runtime-proof.txt").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            runtime_log = ""
        if not isinstance(config_payload, dict):
            config_payload = {}
        if not isinstance(request_payload, dict):
            request_payload = {}
        if not isinstance(response_payload, dict):
            response_payload = {}
        candidate = config_payload.get("candidate")
        if not isinstance(candidate, dict):
            candidate = {}
            errors.append(f"{prefix} run-config candidate is missing")
        if config_payload.get("prompt_sha256") != prompt_fingerprint():
            errors.append(f"{prefix} prompt fingerprint mismatch")
        if config_payload.get("triage_schema") != triage_json_schema():
            errors.append(f"{prefix} response schema fingerprint mismatch")

        stage_alias = {
            "a0": "reference",
            "a1": "baseline",
            "a2": "kleidiai",
            "a3": "tuned",
            "a4": "cascade",
        }
        candidate_stage = str(candidate.get("stage", "")).lower()
        expected_stage = stage_alias.get(candidate_stage, candidate_stage)
        comparisons = {
            "candidate_id": record.candidate_id,
            "backend": record.backend,
            "model_role": record.model_role,
            "quantization": record.quantization,
            "threads": record.threads,
            "batch": record.batch,
            "ubatch": record.ubatch,
            "parallel": record.parallel,
            "context": record.context,
        }
        for key, expected in comparisons.items():
            if candidate.get(key) != expected:
                errors.append(f"{prefix} candidate {key} disagrees with record")
        if expected_stage != record.stage:
            errors.append(f"{prefix} candidate stage disagrees with record")
        if list(candidate.get("affinity") or []) != record.affinity:
            errors.append(f"{prefix} candidate affinity disagrees with record")

        command_settings: tuple[tuple[tuple[str, ...], object], ...] = (
            (("--alias",), record.candidate_id),
            (("--threads", "-t"), record.threads),
            (("--batch-size", "-b"), record.batch),
            (("--ubatch-size", "-ub"), record.ubatch),
            (("--ctx-size", "-c"), record.context),
            (("--parallel", "-np"), record.parallel),
            (("--seed",), 20260813),
            (("-lv", "--verbosity", "--log-verbosity"), 4),
            (("--host",), "127.0.0.1"),
        )
        for option_names, expected in command_settings:
            values = _option_values(record.command, *option_names)
            if values != [str(expected)]:
                errors.append(
                    f"{prefix} command {option_names[0]} must appear exactly once as {expected}"
                )

        cache_text = build_caches.get(record.backend, "")
        backend_proof = verify_backend_log(
            runtime_log,
            record.backend,
            quantization=record.quantization if record.backend == "kleidiai" else None,
            reviewed_model=model_inventory_proofs.get(record.model_file_sha256),
        )
        cpu_proof = verify_cpu_only(
            record.command,
            cmake_cache=cache_text,
            runtime_log=runtime_log,
            require_device_none=True,
        )
        if not backend_proof.verified:
            errors.extend(f"{prefix} backend proof: {error}" for error in backend_proof.errors)
        if not cpu_proof.verified:
            errors.extend(f"{prefix} CPU-only proof: {error}" for error in cpu_proof.errors)
        stored_backend_proof = config_payload.get("backend_proof")
        stored_cpu_proof = config_payload.get("cpu_only_proof")
        if stored_backend_proof != backend_proof.to_dict():
            errors.append(f"{prefix} stored backend proof does not replay")
        if stored_cpu_proof != cpu_proof.to_dict():
            errors.append(f"{prefix} stored CPU-only proof does not replay")
        if record.cpu_only_verified != cpu_proof.verified:
            errors.append(f"{prefix} CPU-only boolean disagrees with replay")
        if record.backend == "kleidiai" and record.kleidiai_verified != backend_proof.verified:
            errors.append(f"{prefix} KleidiAI boolean disagrees with replay")

        server_path = build_binaries.get(record.backend, {}).get("llama-server")
        if server_path is None:
            errors.append(f"{prefix} no verified llama-server binary for backend")
        else:
            try:
                command_binary = _project_path(project_root, record.command[0])
            except (IndexError, ValueError) as exc:
                errors.append(f"{prefix} invalid command binary: {exc}")
            else:
                if command_binary != server_path:
                    errors.append(f"{prefix} command binary disagrees with build manifest")

        model_row = model_by_hash.get(record.model_file_sha256)
        raw_model_path = _option_value(record.command, "--model", "-m")
        if model_row is not None:
            if record.quantization != model_row.quantization:
                errors.append(f"{prefix} quantization disagrees with model manifest")
            if record.backend == "kleidiai" and not model_row.kleidiai_compatible:
                errors.append(
                    f"{prefix} model manifest does not mark the model KleidiAI-compatible"
                )
            try:
                expected_model_path = _project_path(
                    project_root, str(Path("models") / model_row.local_path)
                )
                command_model_path = (
                    _project_path(project_root, raw_model_path)
                    if raw_model_path is not None
                    else None
                )
            except ValueError as exc:
                errors.append(f"{prefix} invalid model path: {exc}")
            else:
                if command_model_path != expected_model_path:
                    errors.append(f"{prefix} command model disagrees with model manifest")
                if str(candidate.get("model")) != str(Path("models") / model_row.local_path):
                    errors.append(f"{prefix} candidate model disagrees with model manifest")

        case = cases_by_id.get(record.case_id)
        if case is None:
            errors.append(f"{prefix} case is absent from the frozen dataset")
        else:
            if split is None:
                errors.append(f"{prefix} frozen split is unavailable")
            elif record.split in {"test", "micro"} and record.case_id not in set(split.test):
                errors.append(f"{prefix} test/micro record is outside the held-out case set")
            elif record.split == "calibration" and record.case_id not in set(split.calibration):
                errors.append(f"{prefix} calibration record is outside the calibration case set")
            elif record.split not in {"test", "micro", "calibration"}:
                errors.append(f"{prefix} measured record uses an ineligible split")
            expected_messages = build_messages(case.incident)
            expected_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "a64pilot_triage_response",
                    "strict": True,
                    "schema": triage_json_schema(),
                },
            }
            if request_payload.get("case_id") != record.case_id:
                errors.append(f"{prefix} request case_id mismatch")
            if request_payload.get("repetition") != record.repetition:
                errors.append(f"{prefix} request repetition mismatch")
            if request_payload.get("messages") != expected_messages:
                errors.append(f"{prefix} request prompt does not match the frozen prompt")
            if request_payload.get("response_format") != expected_format:
                errors.append(f"{prefix} request constrained-output schema mismatch")
            if request_payload.get("model") != record.candidate_id:
                errors.append(f"{prefix} request model alias mismatch")
            fixed_request_settings = {
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 256,
                "seed": 20260813,
                "stream": True,
            }
            for key, expected in fixed_request_settings.items():
                actual = request_payload.get(key)
                if type(actual) is not type(expected) or actual != expected:
                    errors.append(
                        f"{prefix} request {key} disagrees with frozen benchmark settings"
                    )
            response_text = response_payload.get("content")
            replay_score = score_case(case, response_text if isinstance(response_text, str) else "")
            if replay_score.schema_valid != record.schema_valid:
                errors.append(f"{prefix} schema score disagrees with response replay")
            if abs(replay_score.quality_score - record.quality_score) > 1e-6:
                errors.append(f"{prefix} quality score disagrees with response replay")
            if abs(replay_score.safety_score - record.safety_score) > 1e-6:
                errors.append(f"{prefix} safety score disagrees with response replay")
            if list(replay_score.issues) != record.errors:
                errors.append(f"{prefix} score issues disagree with response replay")
            if response_payload.get("score") != replay_score.as_dict():
                errors.append(f"{prefix} stored score does not replay")

        timing = response_payload.get("timing")
        if not isinstance(timing, dict):
            errors.append(f"{prefix} response timing is missing")
        else:
            timing_fields = {
                "start_ns": record.start_ns,
                "first_content_token_ns": record.first_token_ns,
                "end_ns": record.end_ns,
                "ttft_ms": record.ttft_ms,
                "e2e_ms": record.e2e_ms,
            }
            for key, expected in timing_fields.items():
                if timing.get(key) != expected:
                    errors.append(f"{prefix} response timing {key} mismatch")
        derived_e2e_ms = (record.end_ns - record.start_ns) / 1_000_000
        if abs(derived_e2e_ms - record.e2e_ms) > 1.0:
            errors.append(f"{prefix} e2e_ms does not match monotonic timestamps")
    return records, errors


PLACEHOLDER_PATTERN = re.compile(r"\[\[AUTO:|\{\{|\bTBD\b|TODO_METRIC|YOUR_RESULT|YOUR_[A-Z_]+")
SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]+|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)
PRIVATE_PATTERN = re.compile(r"/Users/[^/\s]+|/home/[^/\s]+|\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b")


def verify_claim_sources(claims: Iterable[Claim], records: Iterable[BenchmarkRecord]) -> list[str]:
    record_map = {record.run_id: record for record in records}
    errors: list[str] = []
    for claim in claims:
        for source in claim.source_rows:
            record = record_map.get(source)
            if record is None:
                errors.append(f"{claim.claim_id}: missing source row {source}")
            elif record.evidence_kind != "measured":
                errors.append(f"{claim.claim_id}: fixture row cannot support a claim ({source})")
    return errors


def scan_text(path: Path | str, *, allow_private: bool = False) -> list[str]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    if PLACEHOLDER_PATTERN.search(text):
        errors.append(f"placeholder token: {file_path}")
    if SECRET_PATTERN.search(text):
        errors.append(f"high-confidence secret pattern: {file_path}")
    if not allow_private and PRIVATE_PATTERN.search(text):
        errors.append(f"private path or IP pattern: {file_path}")
    return errors


def verify_submission_tree(root: Path | str = ".") -> list[str]:
    project = Path(root)
    errors: list[str] = []
    for required in ("LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml"):
        if not (project / required).is_file():
            errors.append(f"missing required file: {required}")
    for relative in (
        "README.md",
        "artifacts/devpost-writeup-final.md",
        "artifacts/report.md",
        "artifacts/submission-checklist.md",
    ):
        path = project / relative
        if path.is_file():
            errors.extend(scan_text(path))
        else:
            errors.append(f"missing generated file: {relative}")
    return errors
