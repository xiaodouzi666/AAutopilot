"""Fair dual-build command construction for llama.cpp."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from a64pilot.models.checksum import sha256_file


class BuildError(RuntimeError):
    """Raised when a configured native build fails validation or execution."""


class BuildVariant(StrEnum):
    GENERIC = "generic"
    KLEIDIAI = "kleidiai"


BUILD_TARGETS: tuple[str, ...] = ("llama-server", "llama-cli", "llama-bench")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_ALLOWED_EXTRA_DEFINITIONS = frozenset({"CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER"})
_NON_CPU_BACKEND_KEYS = frozenset(
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

# Every accelerator that llama.cpp may discover is explicitly disabled.  BLAS
# and Accelerate are also disabled so the only intended implementation delta
# is GGML_CPU_KLEIDIAI.
COMMON_DEFINITIONS: Mapping[str, str] = {
    "CMAKE_BUILD_TYPE": "Release",
    "BUILD_SHARED_LIBS": "OFF",
    "GGML_ACCELERATE": "OFF",
    "GGML_BLAS": "OFF",
    "GGML_CUDA": "OFF",
    "GGML_CANN": "OFF",
    "GGML_ET": "OFF",
    "GGML_HEXAGON": "OFF",
    "GGML_HIP": "OFF",
    "GGML_METAL": "OFF",
    "GGML_MUSA": "OFF",
    "GGML_OPENCL": "OFF",
    "GGML_OPENVINO": "OFF",
    "GGML_RPC": "OFF",
    "GGML_SYCL": "OFF",
    "GGML_VIRTGPU": "OFF",
    "GGML_VIRTGPU_BACKEND": "OFF",
    "GGML_VULKAN": "OFF",
    "GGML_WEBGPU": "OFF",
    "GGML_ZDNN": "OFF",
    "GGML_ZENDNN": "OFF",
    "LLAMA_BUILD_EXAMPLES": "ON",
    "LLAMA_BUILD_SERVER": "ON",
    "LLAMA_BUILD_TESTS": "OFF",
}


@dataclass(frozen=True, slots=True)
class BuildPlan:
    variant: BuildVariant
    source_commit: str
    source_dir: Path
    build_dir: Path
    definitions: Mapping[str, str]
    configure_command: tuple[str, ...]
    build_command: tuple[str, ...]
    targets: tuple[str, ...] = BUILD_TARGETS

    def to_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant.value,
            "source_commit": self.source_commit,
            "source_dir": str(self.source_dir),
            "build_dir": str(self.build_dir),
            "definitions": dict(sorted(self.definitions.items())),
            "configure_command": list(self.configure_command),
            "build_command": list(self.build_command),
            "targets": list(self.targets),
        }


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    plan: BuildPlan
    binaries: Mapping[str, str]
    binary_sha256: Mapping[str, str]
    cmake_version: str | None
    compiler_version: str | None
    cache_path: str | None

    def to_dict(self) -> dict[str, object]:
        payload = self.plan.to_dict()
        payload.update(
            {
                "binaries": dict(sorted(self.binaries.items())),
                "binary_sha256": dict(sorted(self.binary_sha256.items())),
                "cmake_version": self.cmake_version,
                "compiler_version": self.compiler_version,
                "cache_path": self.cache_path,
            }
        )
        return payload

    def to_schema_payload(self) -> dict[str, object]:
        """Map captured facts to the repository's strict build schema."""

        from a64pilot.hardware.detect import redact_text

        return {
            "backend": self.plan.variant.value,
            "source_commit": self.plan.source_commit,
            "build_type": self.plan.definitions.get("CMAKE_BUILD_TYPE", "Release"),
            "cmake_flags": [
                redact_text(f"-D{key}={value}")
                for key, value in sorted(self.plan.definitions.items())
            ],
            "compiler": redact_text(self.compiler_version or "unknown"),
            "binaries": {name: redact_text(path) for name, path in sorted(self.binaries.items())},
            "binary_sha256": dict(sorted(self.binary_sha256.items())),
            "cpu_only_configured": all(
                self.plan.definitions.get(key) == "OFF" for key in _NON_CPU_BACKEND_KEYS
            ),
            "kleidiai_configured": self.plan.definitions.get("GGML_CPU_KLEIDIAI") == "ON",
            "runtime_marker_verified": False,
        }


def build_definitions(
    variant: BuildVariant | str, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    selected = BuildVariant(variant)
    definitions = dict(COMMON_DEFINITIONS)
    definitions["GGML_CPU_KLEIDIAI"] = "ON" if selected is BuildVariant.KLEIDIAI else "OFF"
    if extra:
        forbidden = set(extra).difference(_ALLOWED_EXTRA_DEFINITIONS)
        if forbidden:
            raise BuildError(
                "unreviewed CMake definitions would invalidate build fairness: "
                + ", ".join(sorted(forbidden))
            )
        definitions.update({str(key): str(value) for key, value in extra.items()})
    return definitions


def cmake_configure_command(
    variant: BuildVariant | str,
    *,
    source_dir: Path = Path("third_party/llama.cpp"),
    build_dir: Path | None = None,
    generator: str = "Ninja",
    extra_definitions: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    selected = BuildVariant(variant)
    destination = build_dir or Path(f"build/llama-{selected.value}")
    definitions = build_definitions(selected, extra=extra_definitions)
    return (
        "cmake",
        "-S",
        str(source_dir),
        "-B",
        str(destination),
        "-G",
        generator,
        *(f"-D{key}={definitions[key]}" for key in sorted(definitions)),
    )


def build_command(
    variant: BuildVariant | str,
    *,
    build_dir: Path | None = None,
    jobs: int | None = None,
    targets: Sequence[str] = BUILD_TARGETS,
) -> tuple[str, ...]:
    selected = BuildVariant(variant)
    destination = build_dir or Path(f"build/llama-{selected.value}")
    safe_jobs = max(1, jobs or 1)
    if not targets or any(target not in BUILD_TARGETS for target in targets):
        raise BuildError("build targets must be the reviewed llama-server/cli/bench set")
    return (
        "cmake",
        "--build",
        str(destination),
        "--config",
        "Release",
        "--parallel",
        str(safe_jobs),
        "--target",
        *targets,
    )


def create_build_plan(
    variant: BuildVariant | str,
    *,
    source_commit: str,
    source_dir: Path = Path("third_party/llama.cpp"),
    build_dir: Path | None = None,
    jobs: int | None = None,
    extra_definitions: Mapping[str, str] | None = None,
) -> BuildPlan:
    if not _COMMIT_PATTERN.fullmatch(source_commit):
        raise BuildError("source_commit must be a full 40-character Git SHA")
    selected = BuildVariant(variant)
    destination = build_dir or Path(f"build/llama-{selected.value}")
    definitions = build_definitions(selected, extra=extra_definitions)
    return BuildPlan(
        variant=selected,
        source_commit=source_commit,
        source_dir=source_dir,
        build_dir=destination,
        definitions=definitions,
        configure_command=cmake_configure_command(
            selected,
            source_dir=source_dir,
            build_dir=destination,
            extra_definitions=extra_definitions,
        ),
        build_command=build_command(
            selected, build_dir=destination, jobs=jobs, targets=BUILD_TARGETS
        ),
    )


def fairness_differences(
    generic: BuildPlan, optimized: BuildPlan
) -> dict[str, tuple[object, object]]:
    """Return all differences other than the intended KleidiAI switch/path."""

    differences: dict[str, tuple[object, object]] = {}
    if generic.source_commit != optimized.source_commit:
        differences["source_commit"] = (generic.source_commit, optimized.source_commit)
    if generic.source_dir != optimized.source_dir:
        differences["source_dir"] = (str(generic.source_dir), str(optimized.source_dir))
    keys = set(generic.definitions).union(optimized.definitions)
    for key in sorted(keys):
        if key == "GGML_CPU_KLEIDIAI":
            continue
        if generic.definitions.get(key) != optimized.definitions.get(key):
            differences[f"definition:{key}"] = (
                generic.definitions.get(key),
                optimized.definitions.get(key),
            )
    if generic.targets != optimized.targets:
        differences["targets"] = (generic.targets, optimized.targets)
    return differences


def assert_fair_build_pair(generic: BuildPlan, optimized: BuildPlan) -> None:
    if generic.variant is not BuildVariant.GENERIC:
        raise BuildError("first build plan must be generic")
    if optimized.variant is not BuildVariant.KLEIDIAI:
        raise BuildError("second build plan must be KleidiAI")
    if generic.definitions.get("GGML_CPU_KLEIDIAI") != "OFF":
        raise BuildError("generic plan must disable KleidiAI")
    if optimized.definitions.get("GGML_CPU_KLEIDIAI") != "ON":
        raise BuildError("optimized plan must enable KleidiAI")
    differences = fairness_differences(generic, optimized)
    if differences:
        raise BuildError(f"build plans are not fair: {differences}")


def _run(command: Sequence[str], *, timeout_s: float = 3600.0) -> str:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"failed to run build command: {' '.join(command)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        summary = detail[-1][:400] if detail else f"exit code {completed.returncode}"
        raise BuildError(f"build command failed ({' '.join(command)}): {summary}")
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part)


def execute_build(plan: BuildPlan, *, dry_run: bool = False) -> None:
    """Execute one reviewed plan.  Dry-run never creates build output."""

    if dry_run:
        return
    _run(plan.configure_command)
    _run(plan.build_command)


def _first_line(command: Sequence[str]) -> str | None:
    try:
        return _run(command, timeout_s=15).splitlines()[0]
    except BuildError:
        return None


def find_binary(build_dir: Path, name: str) -> Path | None:
    for candidate in (build_dir / "bin" / name, build_dir / name):
        if candidate.is_file():
            return candidate
    return None


def collect_build_artifact(plan: BuildPlan) -> BuildArtifact:
    hashes: dict[str, str] = {}
    binaries: dict[str, str] = {}
    for name in plan.targets:
        path = find_binary(plan.build_dir, name)
        if path is None:
            raise BuildError(f"missing expected binary for {plan.variant.value}: {name}")
        hashes[name] = sha256_file(path)
        binaries[name] = str(path)
    cache = plan.build_dir / "CMakeCache.txt"
    return BuildArtifact(
        plan=plan,
        binaries=binaries,
        binary_sha256=hashes,
        cmake_version=_first_line(("cmake", "--version")),
        compiler_version=_first_line(("cc", "--version")),
        cache_path=str(cache) if cache.is_file() else None,
    )


def write_build_manifest(
    artifacts: Sequence[BuildArtifact], path: Path = Path("artifacts/build-manifest.json")
) -> None:
    """Write the strict manifest plus redacted CMake/toolchain evidence files."""

    from a64pilot.hardware.detect import redact_text
    from a64pilot.schemas import BuildManifest

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = BuildManifest.model_validate(
        {"variants": [artifact.to_schema_payload() for artifact in artifacts]}
    ).model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    toolchains: dict[str, dict[str, str | None]] = {}
    for artifact in artifacts:
        variant = artifact.plan.variant.value
        flags_path = path.parent / f"cmake-{variant}-flags.txt"
        flags_path.write_text(
            "\n".join(
                redact_text(f"-D{key}={value}")
                for key, value in sorted(artifact.plan.definitions.items())
            )
            + "\n",
            encoding="utf-8",
        )
        if artifact.cache_path is not None:
            cache_source = Path(artifact.cache_path)
            if cache_source.is_file():
                cache_text = cache_source.read_text(encoding="utf-8", errors="replace")
                (path.parent / f"cmake-{variant}-cache.txt").write_text(
                    redact_text(cache_text), encoding="utf-8"
                )
        toolchains[variant] = {
            "cmake": artifact.cmake_version,
            "compiler": artifact.compiler_version,
            "architecture": platform.machine(),
        }
    (path.parent / "build-toolchain.json").write_text(
        json.dumps(toolchains, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
