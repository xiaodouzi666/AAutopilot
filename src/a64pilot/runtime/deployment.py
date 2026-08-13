"""Load and serve only measured, CPU-only deployment profiles."""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
import yaml

from a64pilot.api.app import UpstreamResponder, create_app
from a64pilot.build.cmake import BuildVariant
from a64pilot.build.verify_backend import verify_backend_log, verify_cpu_only
from a64pilot.hardware.detect import assert_arm64_benchmark
from a64pilot.models.checksum import sha256_file
from a64pilot.models.gguf import verify_model_inventory
from a64pilot.models.registry import get_model
from a64pilot.runtime.llama_command import (
    LlamaServerConfig,
    inspect_llama_server_capabilities,
)
from a64pilot.runtime.openai_client import OpenAIClient
from a64pilot.runtime.process_manager import LlamaServerProcess, find_available_port


class DeploymentProfileError(ValueError):
    """Raised when a profile is unmeasured, unsafe, or incomplete."""


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    profile_id: str
    backend: str
    binary: Path
    cmake_cache: Path
    model: Path
    model_role: str
    quantization: str
    threads: int
    batch: int
    ubatch: int
    parallel: int
    context: int
    affinity: tuple[int, ...] | None
    source_run_ids: tuple[str, ...]


def _required_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentProfileError(f"{name} must be a mapping")
    return value


def _positive_int(value: object, name: str, default: int | None = None) -> int:
    selected = default if value is None else value
    if not isinstance(selected, int) or isinstance(selected, bool) or selected < 1:
        raise DeploymentProfileError(f"{name} must be a positive integer")
    return selected


def _model_id(role: str, quantization: str) -> str:
    normalized = quantization.lower().replace("_", "-")
    model_id = f"{role}-{normalized}"
    try:
        get_model(model_id)
    except ValueError as exc:
        raise DeploymentProfileError(
            f"profile model is not in the reviewed registry: {role}/{quantization}"
        ) from exc
    return model_id


def _search_plan(root: Path, profile: dict[str, Any]) -> dict[str, Any] | None:
    """Load the exact calibration plan bound into the generated profile."""

    from a64pilot.provenance import sha256_file

    plan_path = root / "artifacts/search-plan.json"
    expected_hash = profile.get("search_plan_sha256")
    calibration_hash = profile.get("calibration_plan_sha256")
    if expected_hash is not None and (
        not isinstance(expected_hash, str) or len(expected_hash) != 64
    ):
        raise DeploymentProfileError("profile search_plan_sha256 is malformed")
    if calibration_hash is not None and calibration_hash != expected_hash:
        raise DeploymentProfileError("profile calibration/search plan hashes disagree")
    if not plan_path.is_file():
        if expected_hash is not None:
            raise DeploymentProfileError("profile cites a missing search plan")
        return None
    if expected_hash is None:
        raise DeploymentProfileError("existing search plan is not hash-bound by the profile")
    if sha256_file(plan_path) != expected_hash:
        raise DeploymentProfileError("profile search plan SHA-256 does not match")
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentProfileError("profile search plan is unreadable") from exc
    return _required_mapping(payload, "search plan")


def _bind_profile_to_evidence(
    profile: dict[str, Any],
    deployment: DeploymentProfile,
    *,
    root: Path,
) -> None:
    """Replay selection and require every served setting to come from formal rows."""

    from a64pilot.optimize.search import candidate_result_from_records, select_frozen_deployment
    from a64pilot.report.integrity import validate_evidence_bundle
    from a64pilot.settings import load_settings

    records, errors = validate_evidence_bundle(root / "artifacts", require_records=True)
    if errors:
        raise DeploymentProfileError("strict evidence validation failed: " + "; ".join(errors))
    formal_rows = [
        row for row in records if row.evidence_kind == "measured" and row.split == "test"
    ]
    selected_rows = [row for row in formal_rows if row.candidate_id == deployment.profile_id]
    if not selected_rows:
        raise DeploymentProfileError("profile has no formal measured test rows")
    if [row.run_id for row in selected_rows] != list(deployment.source_run_ids):
        raise DeploymentProfileError(
            "profile source_run_ids do not exactly match its formal measured test rows"
        )

    expected = {
        "backend": deployment.backend,
        "model_role": deployment.model_role,
        "quantization": deployment.quantization,
        "threads": deployment.threads,
        "batch": deployment.batch,
        "ubatch": deployment.ubatch,
        "parallel": deployment.parallel,
        "context": deployment.context,
        "affinity": list(deployment.affinity or ()),
    }
    for field_name, value in expected.items():
        if any(getattr(row, field_name) != value for row in selected_rows):
            raise DeploymentProfileError(
                f"profile {field_name} disagrees with formal measured test rows"
            )

    grouped: dict[str, list[Any]] = {}
    for row in formal_rows:
        grouped.setdefault(row.candidate_id, []).append(row)
    baseline_groups = [rows for rows in grouped.values() if rows[0].stage == "baseline"]
    if len(baseline_groups) != 1:
        raise DeploymentProfileError("evidence must contain exactly one formal baseline group")
    baseline = candidate_result_from_records(baseline_groups[0])
    candidates = {
        candidate_id: candidate_result_from_records(rows)
        for candidate_id, rows in grouped.items()
        if rows[0].stage != "baseline"
    }
    plan = _search_plan(root, profile)
    try:
        selection = select_frozen_deployment(
            candidates,
            baseline,
            load_settings(root / "configs/default.yaml").quality_gate,
            search_plan=plan,
        )
    except ValueError as exc:
        raise DeploymentProfileError(f"profile selection replay failed: {exc}") from exc
    if selection.selected.candidate_id != deployment.profile_id:
        raise DeploymentProfileError("profile_id disagrees with replayed frozen selection")
    if profile.get("selection_basis") != selection.basis:
        raise DeploymentProfileError("profile selection_basis disagrees with selection replay")
    if profile.get("frozen_candidate_receipt") != selection.frozen_candidate_receipt:
        raise DeploymentProfileError("profile frozen candidate receipt does not replay")
    if list(selection.selected.source_run_ids) != list(deployment.source_run_ids):
        raise DeploymentProfileError("profile source rows disagree with selection replay")


def load_measured_profile(
    path: Path | str = Path("artifacts/optimized-profile.yaml"),
    *,
    project_root: Path | str = Path("."),
) -> DeploymentProfile:
    """Validate a generated profile and resolve its reviewed local inputs."""

    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeploymentProfileError(f"measured profile is missing: {source}") from exc
    profile = _required_mapping(payload, "profile")
    if profile.get("status") != "measured":
        raise DeploymentProfileError("profile status must be measured")
    if profile.get("cpu_only") is not True:
        raise DeploymentProfileError("profile must require CPU-only execution")
    backend = profile.get("backend")
    if backend not in {"generic", "kleidiai"}:
        raise DeploymentProfileError("profile backend must be generic or kleidiai")
    role = profile.get("model_role")
    if role != "strong":
        raise DeploymentProfileError(
            "only a measured strong-only profile can be served; the optional cascade is not deployed"
        )
    config = _required_mapping(profile.get("config"), "profile.config")
    quantization = config.get("quantization")
    if not isinstance(quantization, str) or not quantization:
        raise DeploymentProfileError("profile.config.quantization is required")
    affinity_value = config.get("affinity")
    if affinity_value in (None, []):
        affinity = None
    elif isinstance(affinity_value, list) and all(
        isinstance(cpu, int) and not isinstance(cpu, bool) and cpu >= 0 for cpu in affinity_value
    ):
        affinity = tuple(affinity_value)
    else:
        raise DeploymentProfileError("profile.config.affinity must be a list of CPU indices")
    run_ids = profile.get("source_run_ids")
    if (
        not isinstance(run_ids, list)
        or not run_ids
        or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
    ):
        raise DeploymentProfileError("profile must cite measured source_run_ids")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise DeploymentProfileError("profile_id is required")
    root = Path(project_root)
    model_spec = get_model(_model_id(role, quantization))
    build_dir = root / "build" / f"llama-{backend}"
    deployment = DeploymentProfile(
        profile_id=profile_id,
        backend=backend,
        binary=build_dir / "bin" / "llama-server",
        cmake_cache=build_dir / "CMakeCache.txt",
        model=root / "models" / model_spec.expected_filename,
        model_role=role,
        quantization=quantization,
        threads=_positive_int(config.get("threads"), "profile.config.threads"),
        batch=_positive_int(config.get("batch"), "profile.config.batch", 256),
        ubatch=_positive_int(config.get("ubatch"), "profile.config.ubatch", 128),
        parallel=_positive_int(config.get("parallel"), "profile.config.parallel", 1),
        context=_positive_int(config.get("context"), "profile.config.context", 2048),
        affinity=affinity,
        source_run_ids=tuple(run_ids),
    )
    _bind_profile_to_evidence(profile, deployment, root=root)
    return deployment


def serve_measured_profile(
    profile_path: Path | str = Path("artifacts/optimized-profile.yaml"),
    *,
    host: str = "127.0.0.1",
    port: int = 8088,
) -> None:
    """Start the measured llama-server plus the public localhost proxy."""

    assert_arm64_benchmark()
    if platform.system().lower() != "linux":
        raise DeploymentProfileError("measured deployment requires Arm64 Linux")
    profile = load_measured_profile(profile_path)
    for required in (profile.binary, profile.cmake_cache, profile.model):
        if not required.is_file():
            raise DeploymentProfileError(f"measured deployment input is missing: {required}")
    internal_port = find_available_port(18180 if profile.backend == "kleidiai" else 18080)
    capabilities = inspect_llama_server_capabilities(profile.binary)
    config = LlamaServerConfig(
        binary=profile.binary,
        model=profile.model,
        host="127.0.0.1",
        port=internal_port,
        model_alias=profile.profile_id,
        threads=profile.threads,
        batch_size=profile.batch,
        ubatch_size=profile.ubatch,
        context_size=profile.context,
        parallel=profile.parallel,
        cpu_only=True,
        affinity=profile.affinity,
    )
    manager = LlamaServerProcess(config, capabilities=capabilities)
    try:
        reviewed_model = None
        if profile.backend == "kleidiai":
            model_spec = get_model(_model_id(profile.model_role, profile.quantization))
            model_hash = sha256_file(profile.model)
            reviewed_model = verify_model_inventory(
                profile.model,
                model_spec,
                actual_sha256=model_hash,
            )
            if not reviewed_model.verified:
                raise DeploymentProfileError("; ".join(reviewed_model.errors))
        manager.start()
        log_text = manager.log_tail(3000)
        backend_proof = verify_backend_log(
            log_text,
            BuildVariant(profile.backend),
            quantization=profile.quantization if profile.backend == "kleidiai" else None,
            reviewed_model=reviewed_model,
        )
        cpu_proof = verify_cpu_only(
            manager.command,
            cmake_cache=profile.cmake_cache.read_text(encoding="utf-8", errors="replace"),
            runtime_log=log_text,
            require_device_none=True,
        )
        if not backend_proof.verified:
            raise DeploymentProfileError("; ".join(backend_proof.errors))
        if not cpu_proof.verified:
            raise DeploymentProfileError("; ".join(cpu_proof.errors))
        responder = UpstreamResponder(
            OpenAIClient(f"http://127.0.0.1:{internal_port}"),
            upstream_model=profile.profile_id,
            backend=profile.backend,
            profile_id=profile.profile_id,
            cpu_only_verified=True,
        )
        application = create_app(
            responder=responder,
            model_ids=["a64pilot"],
            report_path=Path("artifacts/report.html"),
            strict_models=True,
        )
        uvicorn.run(application, host=host, port=port, log_level="info")
    finally:
        manager.stop()
