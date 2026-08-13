"""Evidence parsers for KleidiAI and strict CPU-only execution."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from a64pilot.models.gguf import GgufTensor, ModelInventoryProof
from a64pilot.models.registry import get_model

from .cmake import BuildPlan, BuildVariant, fairness_differences

_KLEIDIAI_REJECTION_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bkleidiai\b.*\bno\s+kernel\s+for\s+tensor\s+type\b", re.IGNORECASE),
    re.compile(r"\bkleidiai\b.*\bnot\s+accelerated\b", re.IGNORECASE),
    re.compile(
        r"\bkleidiai\b.*\b(?:unsupported|not\s+supported)\s+"
        r"(?:tensor|quantization|kernel)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bkleidiai\b.*\b(?:cannot|unable\s+to)\s+accelerate\b", re.IGNORECASE),
)

_KLEIDIAI_QUANT_KERNELS = {
    "Q4_0": re.compile(r"\bkleidiai:\s+primary\s+q4\s+kernel\s+feature\b", re.IGNORECASE),
    "Q8_0": re.compile(r"\bkleidiai:\s+primary\s+q8\s+kernel\s+feature\b", re.IGNORECASE),
}

_KLEIDIAI_MODEL_BUFFER_MARKER = re.compile(
    r"\bCPU_KLEIDIAI\s+model\s+buffer\s+size\b", re.IGNORECASE
)

_REVIEWED_Q6_K_FALLBACK = re.compile(
    r"\bkleidiai:\s+no\s+kernel\s+for\s+tensor\s+type\s+Q6_K,\s+"
    r"not\s+accelerated\s+by\s+KleidiAI\s+"
    r"\(kernels\s+available\s+for\s+Q4_0\s+and\s+Q8_0\)\s*$",
    re.IGNORECASE,
)

_GPU_ACTIVE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\busing\s+(?:cann|cuda|metal|vulkan|rocm|hip|musa|opencl|sycl|webgpu|virtgpu)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bloaded\s+(?:cann|cuda|metal|vulkan|rocm|hip|musa|opencl|sycl|webgpu|virtgpu)\s+backend\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bggml_(?:cann|cuda|metal|musa|vulkan|sycl|webgpu)_init\b", re.IGNORECASE),
    re.compile(r"\boffload(?:ing|ed)?\s+\d+\s+layers?\s+to\s+gpu\b", re.IGNORECASE),
)

_GPU_CACHE_KEYS = frozenset(
    {
        "GGML_CUDA",
        "GGML_CANN",
        "GGML_ET",
        "GGML_HEXAGON",
        "GGML_HIP",
        "GGML_METAL",
        "GGML_MUSA",
        "GGML_OPENCL",
        "GGML_OPENVINO",
        "GGML_RPC",
        "GGML_SYCL",
        "GGML_VIRTGPU",
        "GGML_VIRTGPU_BACKEND",
        "GGML_VULKAN",
        "GGML_WEBGPU",
        "GGML_ZDNN",
        "GGML_ZENDNN",
    }
)


@dataclass(frozen=True, slots=True)
class BackendVerification:
    backend: str
    verified: bool
    marker_found: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "verified": self.verified,
            "marker_found": self.marker_found,
            "evidence": list(self.evidence),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class CpuOnlyVerification:
    verified: bool
    gpu_layers_zero: bool
    device_none: bool
    gpu_builds_disabled: bool
    gpu_runtime_marker_found: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "verified": self.verified,
            "gpu_layers_zero": self.gpu_layers_zero,
            "device_none": self.device_none,
            "gpu_builds_disabled": self.gpu_builds_disabled,
            "gpu_runtime_marker_found": self.gpu_runtime_marker_found,
            "evidence": list(self.evidence),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class BuildPairVerification:
    verified: bool
    same_source_commit: bool
    fair_definitions: bool
    backend_flags_configured: bool
    generic_backend: BackendVerification
    optimized_backend: BackendVerification
    generic_cpu_only: CpuOnlyVerification
    optimized_cpu_only: CpuOnlyVerification
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "verified": self.verified,
            "same_source_commit": self.same_source_commit,
            "fair_definitions": self.fair_definitions,
            "backend_flags_configured": self.backend_flags_configured,
            "generic_backend": self.generic_backend.to_dict(),
            "optimized_backend": self.optimized_backend.to_dict(),
            "generic_cpu_only": self.generic_cpu_only.to_dict(),
            "optimized_cpu_only": self.optimized_cpu_only.to_dict(),
            "errors": list(self.errors),
        }


def _matching_lines(text: str, patterns: Sequence[re.Pattern[str]]) -> tuple[str, ...]:
    lines: list[str] = []
    for line in text.splitlines():
        if any(pattern.search(line) for pattern in patterns):
            lines.append(line.strip()[:500])
    return tuple(lines)


def verify_backend_log(
    log_text: str,
    expected: BuildVariant | str,
    *,
    quantization: str | None = None,
    reviewed_model: ModelInventoryProof | None = None,
) -> BackendVerification:
    """Require usable KleidiAI evidence, not merely a compiled-in marker.

    The pinned llama.cpp KleidiAI backend accelerates quantized weights only for
    Q4_0 and Q8_0.  A fallback warning invalidates a run except for the single
    disclosed Q6_K ``output.weight`` tensor in the exact pinned strong Q4_0
    GGUF, whose full header inventory must have been independently replayed.
    """

    variant = BuildVariant(expected)
    kernel_matching = _matching_lines(log_text, tuple(_KLEIDIAI_QUANT_KERNELS.values()))
    buffer_matching = _matching_lines(log_text, (_KLEIDIAI_MODEL_BUFFER_MARKER,))
    matching = kernel_matching + buffer_matching
    marker_found = bool(kernel_matching) and bool(buffer_matching)
    rejected = _matching_lines(log_text, _KLEIDIAI_REJECTION_MARKERS)
    if variant is BuildVariant.KLEIDIAI:
        errors: list[str] = []
        if not kernel_matching:
            errors.append("runtime log has no KleidiAI primary quant kernel-selection marker")
        if not buffer_matching:
            errors.append("runtime log has no CPU_KLEIDIAI model buffer marker")
        if rejected and not _is_reviewed_strong_q4_fallback(
            rejected,
            quantization=quantization,
            reviewed_model=reviewed_model,
        ):
            errors.append("runtime log reports an unknown tensor fallback from KleidiAI")
        if quantization is not None:
            normalized = quantization.upper()
            expected_kernel = _KLEIDIAI_QUANT_KERNELS.get(normalized)
            if expected_kernel is None:
                errors.append(
                    f"quantization {quantization} is not supported by the pinned KleidiAI backend"
                )
            elif not expected_kernel.search(log_text):
                errors.append(f"runtime log has no KleidiAI {normalized} kernel-selection marker")
        evidence = matching + rejected
    else:
        errors = []
        if matching or rejected:
            errors.append("generic runtime unexpectedly reported KleidiAI activity")
        evidence = (
            ("no KleidiAI runtime marker in generic log",)
            if not matching and not rejected
            else matching + rejected
        )
    return BackendVerification(
        backend=variant.value,
        verified=not errors,
        marker_found=marker_found,
        evidence=evidence,
        errors=tuple(errors),
    )


def _is_reviewed_strong_q4_fallback(
    rejected_lines: tuple[str, ...],
    *,
    quantization: str | None,
    reviewed_model: ModelInventoryProof | None,
) -> bool:
    """Bind the only warning exception to the exact reviewed file inventory."""

    if quantization is None or quantization.upper() != "Q4_0":
        return False
    if len(rejected_lines) != 1 or not _REVIEWED_Q6_K_FALLBACK.search(rejected_lines[0]):
        return False
    if reviewed_model is None or not reviewed_model.verified:
        return False
    spec = get_model("strong-q4-0")
    expected_fallbacks = tuple(
        GgufTensor(item.name, item.tensor_type, item.dimensions)
        for item in spec.reviewed_kleidiai_fallbacks
    )
    return (
        reviewed_model.model_id == spec.model_id
        and reviewed_model.model_sha256 == spec.expected_sha256
        and reviewed_model.inventory_sha256 == spec.expected_tensor_inventory_sha256
        and reviewed_model.tensor_histogram == spec.expected_tensor_histogram
        and reviewed_model.reviewed_fallback_tensors == expected_fallbacks
    )


def parse_cmake_cache(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        key = key_and_type.split(":", 1)[0]
        if key:
            values[key] = value.strip()
    return values


def _argv(command: Sequence[str] | str) -> tuple[str, ...]:
    return tuple(shlex.split(command) if isinstance(command, str) else command)


def _zero_option(arguments: Sequence[str], names: frozenset[str]) -> bool:
    for index, token in enumerate(arguments):
        key, separator, inline_value = token.partition("=")
        if key not in names:
            continue
        if separator:
            return inline_value == "0"
        return index + 1 < len(arguments) and arguments[index + 1] == "0"
    return False


def _none_option(arguments: Sequence[str], name: str) -> bool:
    for index, token in enumerate(arguments):
        key, separator, inline_value = token.partition("=")
        if key != name:
            continue
        if separator:
            return inline_value.lower() == "none"
        return index + 1 < len(arguments) and arguments[index + 1].lower() == "none"
    return False


def verify_cpu_only(
    command: Sequence[str] | str,
    *,
    cmake_cache: str | Mapping[str, str],
    runtime_log: str = "",
    require_device_none: bool = True,
) -> CpuOnlyVerification:
    """Verify command, build cache, and runtime evidence agree on CPU-only use."""

    arguments = _argv(command)
    gpu_layers_zero = _zero_option(arguments, frozenset({"--n-gpu-layers", "-ngl"}))
    device_none = _none_option(arguments, "--device")
    cache = parse_cmake_cache(cmake_cache) if isinstance(cmake_cache, str) else dict(cmake_cache)
    missing_cache_keys = sorted(_GPU_CACHE_KEYS.difference(cache))
    enabled_cache_keys = sorted(
        key
        for key in _GPU_CACHE_KEYS
        if cache.get(key, "").strip().upper() not in {"OFF", "FALSE", "0", "NO"}
    )
    gpu_builds_disabled = not missing_cache_keys and not enabled_cache_keys
    gpu_lines = _matching_lines(runtime_log, _GPU_ACTIVE_MARKERS)
    errors: list[str] = []
    evidence: list[str] = []
    if gpu_layers_zero:
        evidence.append("runtime command sets n-gpu-layers=0")
    else:
        errors.append("runtime command does not set n-gpu-layers=0")
    if device_none:
        evidence.append("runtime command sets device=none")
    elif require_device_none:
        errors.append("runtime command does not set device=none")
    if gpu_builds_disabled:
        evidence.append("CMake cache disables all reviewed GPU backends")
    else:
        if missing_cache_keys:
            errors.append("CMake cache lacks GPU backend keys: " + ", ".join(missing_cache_keys))
        if enabled_cache_keys:
            errors.append("GPU backends enabled in CMake cache: " + ", ".join(enabled_cache_keys))
    if gpu_lines:
        errors.append("runtime log reports an active GPU backend")
        evidence.extend(gpu_lines)
    else:
        evidence.append("runtime log has no active GPU backend marker")
    return CpuOnlyVerification(
        verified=not errors,
        gpu_layers_zero=gpu_layers_zero,
        device_none=device_none,
        gpu_builds_disabled=gpu_builds_disabled,
        gpu_runtime_marker_found=bool(gpu_lines),
        evidence=tuple(evidence),
        errors=tuple(errors),
    )


def verify_build_pair(
    generic_plan: BuildPlan,
    optimized_plan: BuildPlan,
    *,
    generic_log: str,
    optimized_log: str,
    generic_command: Sequence[str] | str,
    optimized_command: Sequence[str] | str,
    generic_cache: str | Mapping[str, str],
    optimized_cache: str | Mapping[str, str],
) -> BuildPairVerification:
    """Verify the full same-commit, fair-flags, CPU-only backend ablation."""

    same_commit = generic_plan.source_commit == optimized_plan.source_commit
    fair = not fairness_differences(generic_plan, optimized_plan)
    generic_cache_values = (
        parse_cmake_cache(generic_cache) if isinstance(generic_cache, str) else dict(generic_cache)
    )
    optimized_cache_values = (
        parse_cmake_cache(optimized_cache)
        if isinstance(optimized_cache, str)
        else dict(optimized_cache)
    )
    backend_flags_configured = (
        generic_cache_values.get("GGML_CPU_KLEIDIAI", "").upper() == "OFF"
        and optimized_cache_values.get("GGML_CPU_KLEIDIAI", "").upper() == "ON"
    )
    generic_backend = verify_backend_log(generic_log, BuildVariant.GENERIC)
    optimized_backend = verify_backend_log(optimized_log, BuildVariant.KLEIDIAI)
    generic_cpu = verify_cpu_only(
        generic_command, cmake_cache=generic_cache, runtime_log=generic_log
    )
    optimized_cpu = verify_cpu_only(
        optimized_command, cmake_cache=optimized_cache, runtime_log=optimized_log
    )
    errors: list[str] = []
    if not same_commit:
        errors.append("builds use different llama.cpp commits")
    if not fair:
        errors.append("build definitions differ beyond GGML_CPU_KLEIDIAI")
    if not backend_flags_configured:
        errors.append("CMake caches do not prove generic=OFF and KleidiAI=ON")
    for label, verification in (
        ("generic backend", generic_backend),
        ("optimized backend", optimized_backend),
        ("generic CPU-only", generic_cpu),
        ("optimized CPU-only", optimized_cpu),
    ):
        errors.extend(f"{label}: {error}" for error in verification.errors)
    return BuildPairVerification(
        verified=not errors,
        same_source_commit=same_commit,
        fair_definitions=fair,
        backend_flags_configured=backend_flags_configured,
        generic_backend=generic_backend,
        optimized_backend=optimized_backend,
        generic_cpu_only=generic_cpu,
        optimized_cpu_only=optimized_cpu,
        errors=tuple(errors),
    )


def read_cache(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read CMake cache: {path}") from exc
