from __future__ import annotations

import json
from pathlib import Path

import pytest

from a64pilot.benchmark.runner import _wait_for_kleidiai_load_proof
from a64pilot.build.cmake import (
    BuildArtifact,
    BuildError,
    BuildVariant,
    assert_fair_build_pair,
    build_definitions,
    cmake_configure_command,
    create_build_plan,
    fairness_differences,
    write_build_manifest,
)
from a64pilot.build.llama_source import (
    OFFICIAL_LLAMA_REPOSITORY,
    SourceLock,
    ensure_source,
    read_source_lock,
    write_source_lock,
)
from a64pilot.build.verify_backend import (
    verify_backend_log,
    verify_build_pair,
    verify_cpu_only,
)
from a64pilot.models.gguf import GgufTensor, ModelInventoryProof
from a64pilot.models.registry import get_model

FIXTURES = Path(__file__).parent / "fixtures"
COMMIT = "a" * 40


def test_dual_configure_commands_are_cpu_only_and_fair() -> None:
    generic = create_build_plan(BuildVariant.GENERIC, source_commit=COMMIT, jobs=4)
    optimized = create_build_plan(BuildVariant.KLEIDIAI, source_commit=COMMIT, jobs=4)
    assert_fair_build_pair(generic, optimized)
    assert fairness_differences(generic, optimized) == {}
    for key in ("GGML_CUDA", "GGML_HIP", "GGML_METAL", "GGML_VULKAN", "GGML_OPENCL"):
        assert generic.definitions[key] == "OFF"
        assert optimized.definitions[key] == "OFF"
    assert generic.definitions["GGML_CPU_KLEIDIAI"] == "OFF"
    assert optimized.definitions["GGML_CPU_KLEIDIAI"] == "ON"


def test_configure_is_an_argv_not_a_shell_string() -> None:
    command = cmake_configure_command("generic", source_dir=Path("source with spaces"))
    assert isinstance(command, tuple)
    assert command[command.index("-S") + 1] == "source with spaces"
    assert not any("shell=True" in token for token in command)


def test_unreviewed_cmake_definition_is_rejected() -> None:
    with pytest.raises(BuildError, match="fairness"):
        build_definitions("generic", extra={"SOMETHING_UNREVIEWED": "ON"})
    with pytest.raises(BuildError, match="fairness"):
        build_definitions("generic", extra={"GGML_CUDA": "ON"})


def test_build_plan_requires_immutable_commit() -> None:
    with pytest.raises(BuildError, match="40-character"):
        create_build_plan("generic", source_commit="main")


def test_source_lock_round_trip_and_offline_dry_run(tmp_path: Path) -> None:
    lock_path = tmp_path / "llama.cpp.lock"
    source_path = tmp_path / "llama.cpp"
    lock = SourceLock(OFFICIAL_LLAMA_REPOSITORY, COMMIT)
    write_source_lock(lock, lock_path)
    assert read_source_lock(lock_path) == lock
    checkout = ensure_source(lock_path=lock_path, source_dir=source_path, dry_run=True)
    assert checkout.commit == COMMIT
    assert checkout.dry_run
    assert not source_path.exists()
    assert checkout.commands[0][0:2] == ("git", "clone")


def test_runtime_marker_must_come_from_log() -> None:
    generic_log = (FIXTURES / "llama-generic.log").read_text()
    optimized_log = (FIXTURES / "llama-kleidiai.log").read_text()
    assert verify_backend_log(generic_log, "generic").verified
    result = verify_backend_log(optimized_log, "kleidiai")
    assert result.verified and result.marker_found
    assert not verify_backend_log(generic_log, "kleidiai").verified
    assert not verify_backend_log(optimized_log, "generic").verified
    assert not verify_backend_log("KleidiAI backend disabled", "kleidiai").verified
    assert verify_backend_log(
        "kleidiai: primary q4 kernel feature dotprod\n"
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB",
        "kleidiai",
    ).verified
    assert not verify_backend_log(
        "kleidiai: primary q4 kernel feature dotprod", "kleidiai"
    ).verified
    assert not verify_backend_log(
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB", "kleidiai"
    ).verified
    assert not verify_backend_log("kleidiai: primary q4 kernel feature dotprod", "generic").verified
    assert not verify_backend_log(
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB", "generic"
    ).verified
    assert not verify_backend_log(
        "kleidiai: no compatible q4 kernels found for CPU features mask 0", "kleidiai"
    ).verified


def test_backend_typed_evidence_strips_ambiguous_llama_elapsed_prefix() -> None:
    result = verify_backend_log(
        "0.10.168.200 D kleidiai: primary q4 kernel feature DOTPROD\n"
        "0.10.168.201 I load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB",
        "kleidiai",
        quantization="Q4_0",
    )
    assert result.verified
    assert result.evidence == (
        "kleidiai: primary q4 kernel feature DOTPROD",
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB",
    )


def test_kleidiai_verifier_rejects_unaccelerated_tensor_warning() -> None:
    log = "\n".join(
        (
            "kleidiai: primary q4 kernel feature DOTPROD",
            "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB",
            "kleidiai: no kernel for tensor type q4_K, not accelerated by KleidiAI "
            "(kernels available for Q4_0 and Q8_0)",
        )
    )
    result = verify_backend_log(log, "kleidiai", quantization="Q4_0")
    assert not result.verified
    assert any("unknown tensor fallback" in error for error in result.errors)


def _strong_q4_inventory_proof() -> ModelInventoryProof:
    spec = get_model("strong-q4-0")
    return ModelInventoryProof(
        model_id=spec.model_id,
        model_sha256=spec.expected_sha256,
        inventory_sha256=spec.expected_tensor_inventory_sha256,
        tensor_histogram=spec.expected_tensor_histogram,
        reviewed_fallback_tensors=(GgufTensor("output.weight", "Q6_K", (1536, 151936)),),
        verified=True,
    )


def test_kleidiai_verifier_allows_only_exact_reviewed_strong_q4_fallback() -> None:
    warning = (
        "kleidiai: no kernel for tensor type Q6_K, not accelerated by KleidiAI "
        "(kernels available for Q4_0 and Q8_0)"
    )
    log = (
        "kleidiai: primary q4 kernel feature DOTPROD\n"
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB\n"
        f"{warning}"
    )
    proof = _strong_q4_inventory_proof()
    warning_only = verify_backend_log(
        warning,
        "kleidiai",
        quantization="Q4_0",
        reviewed_model=proof,
    )
    assert not warning_only.verified
    assert not warning_only.marker_found
    assert any("no KleidiAI primary quant" in error for error in warning_only.errors)
    assert any("no CPU_KLEIDIAI model buffer" in error for error in warning_only.errors)
    assert any("no KleidiAI Q4_0 kernel-selection marker" in error for error in warning_only.errors)
    assert verify_backend_log(
        log,
        "kleidiai",
        quantization="Q4_0",
        reviewed_model=proof,
    ).verified

    wrong_inventory = proof.__class__(
        model_id=proof.model_id,
        model_sha256=proof.model_sha256,
        inventory_sha256="0" * 64,
        tensor_histogram=proof.tensor_histogram,
        reviewed_fallback_tensors=proof.reviewed_fallback_tensors,
        verified=True,
    )
    assert not verify_backend_log(
        log,
        "kleidiai",
        quantization="Q4_0",
        reviewed_model=wrong_inventory,
    ).verified
    assert not verify_backend_log(
        f"{log}\nkleidiai: unsupported tensor type Q4_K",
        "kleidiai",
        quantization="Q4_0",
        reviewed_model=proof,
    ).verified


@pytest.mark.parametrize(
    "warning",
    [
        "kleidiai: unsupported tensor type q4_K",
        "kleidiai: unable to accelerate tensor type q4_K",
        "kleidiai: cannot accelerate this quantized tensor",
    ],
)
def test_kleidiai_verifier_rejects_other_acceleration_fallbacks(warning: str) -> None:
    log = (
        "kleidiai: primary q4 kernel feature DOTPROD\n"
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB\n"
        f"{warning}"
    )
    assert not verify_backend_log(log, "kleidiai", quantization="Q4_0").verified


def test_kleidiai_verifier_requires_quantization_specific_kernel() -> None:
    q4_log = (
        "kleidiai: primary q4 kernel feature DOTPROD\n"
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB"
    )
    assert verify_backend_log(q4_log, "kleidiai", quantization="Q4_0").verified
    assert not verify_backend_log(q4_log, "kleidiai", quantization="Q8_0").verified
    assert not verify_backend_log(q4_log, "kleidiai", quantization="Q4_K_M").verified


def test_kleidiai_load_proof_poll_waits_for_both_async_markers() -> None:
    complete = (
        "kleidiai: primary q4 kernel feature DOTPROD\n"
        "load_tensors: CPU_KLEIDIAI model buffer size = 100 MiB"
    )

    class LogSequence:
        def __init__(self) -> None:
            self.calls = 0

        def log_text(self) -> str:
            self.calls += 1
            return "kleidiai: primary q4 kernel feature DOTPROD" if self.calls == 1 else complete

    manager = LogSequence()
    log_text, proof = _wait_for_kleidiai_load_proof(  # type: ignore[arg-type]
        manager,
        quantization="Q4_0",
        reviewed_model=_strong_q4_inventory_proof(),
        timeout_s=0.1,
        interval_s=0.001,
    )
    assert manager.calls == 2
    assert log_text == complete
    assert proof.verified


def test_kleidiai_load_proof_poll_never_accepts_warning_only() -> None:
    warning = (
        "kleidiai: no kernel for tensor type Q6_K, not accelerated by KleidiAI "
        "(kernels available for Q4_0 and Q8_0)"
    )

    class WarningOnly:
        def log_text(self) -> str:
            return warning

    _, proof = _wait_for_kleidiai_load_proof(  # type: ignore[arg-type]
        WarningOnly(),
        quantization="Q4_0",
        reviewed_model=_strong_q4_inventory_proof(),
        timeout_s=0,
    )
    assert not proof.verified
    assert not proof.marker_found


def test_cpu_only_verifier_requires_command_cache_and_no_gpu_marker() -> None:
    cache = (FIXTURES / "cmake-cpu-only.txt").read_text()
    log = (FIXTURES / "llama-kleidiai.log").read_text()
    valid = verify_cpu_only(
        ("llama-server", "--device", "none", "--n-gpu-layers", "0"),
        cmake_cache=cache,
        runtime_log=log,
    )
    assert valid.verified
    invalid = verify_cpu_only(
        ("llama-server", "--n-gpu-layers", "0"),
        cmake_cache=cache,
        runtime_log="using CUDA backend",
    )
    assert not invalid.verified
    assert invalid.gpu_runtime_marker_found


def test_pair_verifier_rejects_different_commits() -> None:
    generic = create_build_plan("generic", source_commit="a" * 40)
    optimized = create_build_plan("kleidiai", source_commit="b" * 40)
    cache = (FIXTURES / "cmake-cpu-only.txt").read_text()
    command = ("llama-server", "--device", "none", "--n-gpu-layers", "0")
    result = verify_build_pair(
        generic,
        optimized,
        generic_log=(FIXTURES / "llama-generic.log").read_text(),
        optimized_log=(FIXTURES / "llama-kleidiai.log").read_text(),
        generic_command=command,
        optimized_command=command,
        generic_cache=cache + "GGML_CPU_KLEIDIAI:BOOL=OFF\n",
        optimized_cache=cache + "GGML_CPU_KLEIDIAI:BOOL=ON\n",
    )
    assert not result.verified
    assert not result.same_source_commit


def test_pair_verifier_requires_backend_flags_from_actual_caches() -> None:
    generic = create_build_plan("generic", source_commit=COMMIT)
    optimized = create_build_plan("kleidiai", source_commit=COMMIT)
    cache = (FIXTURES / "cmake-cpu-only.txt").read_text()
    command = ("llama-server", "--device", "none", "--n-gpu-layers", "0")
    common = {
        "generic_plan": generic,
        "optimized_plan": optimized,
        "generic_log": (FIXTURES / "llama-generic.log").read_text(),
        "optimized_log": (FIXTURES / "llama-kleidiai.log").read_text(),
        "generic_command": command,
        "optimized_command": command,
    }
    invalid = verify_build_pair(
        **common,
        generic_cache=cache,
        optimized_cache=cache,
    )
    assert not invalid.verified
    assert not invalid.backend_flags_configured
    valid = verify_build_pair(
        **common,
        generic_cache=cache + "GGML_CPU_KLEIDIAI:BOOL=OFF\n",
        optimized_cache=cache + "GGML_CPU_KLEIDIAI:BOOL=ON\n",
    )
    assert valid.verified
    assert valid.backend_flags_configured


def test_build_manifest_validates_and_captures_redacted_evidence(tmp_path: Path) -> None:
    plan = create_build_plan("generic", source_commit=COMMIT, build_dir=tmp_path / "build")
    cache = tmp_path / "CMakeCache.txt"
    cache.write_text(f"CMAKE_HOME_DIRECTORY:INTERNAL={Path.home()}/llama.cpp\n")
    artifact = BuildArtifact(
        plan=plan,
        binaries={name: str(tmp_path / name) for name in plan.targets},
        binary_sha256={name: "f" * 64 for name in plan.targets},
        cmake_version="cmake version 4.0",
        compiler_version="clang version 18",
        cache_path=str(cache),
    )
    manifest_path = tmp_path / "artifacts" / "build-manifest.json"
    write_build_manifest((artifact,), manifest_path)
    payload = json.loads(manifest_path.read_text())
    assert payload["variants"][0]["backend"] == "generic"
    assert payload["variants"][0]["cpu_only_configured"] is True
    assert payload["variants"][0]["kleidiai_configured"] is False
    assert (manifest_path.parent / "cmake-generic-flags.txt").is_file()
    cache_copy = (manifest_path.parent / "cmake-generic-cache.txt").read_text()
    assert str(Path.home()) not in cache_copy
    assert "<HOME>" in cache_copy
