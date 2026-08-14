"""Bounded supporting microbenchmark and service-concurrency evidence.

The formal held-out quality rows remain in :mod:`a64pilot.benchmark.runner`.  This
module deliberately writes a separate artifact so repeated performance probes
cannot be mistaken for extra held-out quality repetitions or headline claims.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal
from uuid import uuid4

import numpy as np
from pydantic import Field, model_validator

from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import triage_openai_response_format
from a64pilot.benchmark.llama_bench import (
    LLAMA_BENCH_PARSER_VERSION,
    inspect_help,
    parse_output,
    require_tests,
)
from a64pilot.benchmark.llama_bench import (
    build_command as build_llama_bench_command,
)
from a64pilot.benchmark.quality import load_cases, load_split, score_case, validate_dataset
from a64pilot.benchmark.runner import (
    REAL_BENCHMARK_MAX_TOKENS,
    REAL_BENCHMARK_SEED,
    _wait_for_kleidiai_load_proof,
)
from a64pilot.build.cmake import BuildVariant
from a64pilot.build.verify_backend import (
    parse_cmake_cache,
    verify_backend_log,
    verify_cpu_only,
)
from a64pilot.hardware.detect import assert_arm64_benchmark
from a64pilot.models.checksum import sha256_file
from a64pilot.models.gguf import GgufTensor, ModelInventoryProof, verify_model_inventory
from a64pilot.models.registry import get_model
from a64pilot.provenance import write_json
from a64pilot.runtime.llama_command import (
    LlamaServerCapabilities,
    LlamaServerConfig,
    build_llama_server_command,
    inspect_llama_server_capabilities,
)
from a64pilot.runtime.openai_client import ClientCompletion, OpenAIClient
from a64pilot.runtime.process_manager import LlamaServerProcess, find_available_port
from a64pilot.runtime.rss_sampler import process_tree_rss
from a64pilot.schemas import BuildManifest, ModelManifest, StrictModel

PROBE_SCHEMA_VERSION: Final[str] = "1.2.0"
MIN_PROBE_REPETITIONS: Final[int] = 3
MICRO_PROMPT_TOKENS: Final[int] = 128
MICRO_GENERATION_TOKENS: Final[int] = 64
SERVICE_PARALLEL_VALUES: Final[tuple[int, ...]] = (1, 2)
SERVICE_CONTEXT_PER_SLOT: Final[int] = 2048
_STREAM_EVIDENCE_GATE_ISSUES: Final[frozenset[str]] = frozenset(
    {
        "missing_first_content_token",
        "invalid_stream_timing",
        "invalid_prompt_tokens",
        "invalid_completion_tokens",
        "invalid_generation_rate",
    }
)


def _finite_positive(value: float, label: str, *, allow_zero: bool = False) -> None:
    if not math.isfinite(value) or (value < 0 if allow_zero else value <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be finite and {qualifier}")


def _micro_expected_command(run: MicroRun) -> list[str]:
    if len(run.command) < 3:
        raise ValueError("micro command is truncated")
    return build_llama_bench_command(
        run.command[0],
        run.command[2],
        threads=run.threads,
        repetitions=run.repetitions,
        prompt_tokens=MICRO_PROMPT_TOKENS,
        generation_tokens=MICRO_GENERATION_TOKENS,
    )


def _command_option(command: list[str], option: str) -> str:
    positions = [index for index, token in enumerate(command) if token == option]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise ValueError(f"service command must contain exactly one {option}")
    return command[positions[0] + 1]


def _service_expected_command(run: ServiceRun) -> list[str]:
    if not run.command:
        raise ValueError("service command is empty")
    try:
        port = int(_command_option(run.command, "--port"))
    except ValueError as exc:
        raise ValueError("service command port is invalid") from exc
    config = LlamaServerConfig(
        binary=Path(run.command[0]),
        model=Path(_command_option(run.command, "--model")),
        host="127.0.0.1",
        port=port,
        model_alias=f"probe-{run.backend}-p{run.parallel}",
        threads=run.threads,
        batch_size=run.batch,
        ubatch_size=run.ubatch,
        context_size=run.context_total,
        parallel=run.parallel,
        seed=run.seed,
        cpu_only=True,
    )
    return build_llama_server_command(
        config,
        LlamaServerCapabilities(device=True, gpu_layers=True, metrics=True, no_webui=True),
    ).as_list()


class PerformanceProbeError(RuntimeError):
    """Raised when supporting evidence cannot satisfy the frozen protocol."""


class MicroMetric(StrictModel):
    test: str
    tokens_per_second: float = Field(gt=0, allow_inf_nan=False)
    tokens_per_second_stddev: float = Field(ge=0, allow_inf_nan=False)


class MicroRun(StrictModel):
    backend: Literal["generic", "kleidiai"]
    quantization: Literal["Q4_0", "Q8_0"]
    threads: int = Field(ge=1)
    repetitions: int = Field(ge=MIN_PROBE_REPETITIONS)
    warmup_repetitions: Literal[1]
    model_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    command: list[str] = Field(min_length=1)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    elapsed_ms: float = Field(gt=0, allow_inf_nan=False)
    cpu_only_verified: Literal[True]
    backend_verified: Literal[True]
    parser_version: Literal[LLAMA_BENCH_PARSER_VERSION]
    stdout_path: str
    stderr_path: str
    metrics: list[MicroMetric]

    @model_validator(mode="after")
    def complete_tests(self) -> MicroRun:
        expected = [f"pp{MICRO_PROMPT_TOKENS}", f"tg{MICRO_GENERATION_TOKENS}"]
        if [metric.test for metric in self.metrics] != expected:
            raise ValueError("micro run must contain exactly the frozen pp/tg tests")
        if self.backend == "kleidiai" and self.quantization != "Q4_0":
            raise ValueError("the frozen micro matrix has no KleidiAI Q8_0 cell")
        if not self.start_ns < self.end_ns:
            raise ValueError("micro monotonic counters are not strictly ordered")
        derived_elapsed = (self.end_ns - self.start_ns) / 1_000_000
        if not math.isclose(self.elapsed_ms, derived_elapsed, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("micro elapsed_ms does not replay from monotonic counters")
        if self.command.count("-v") != 1 or "--verbose" in self.command:
            raise ValueError("micro command must contain exactly one canonical -v")
        if self.command != _micro_expected_command(self):
            raise ValueError("micro command disagrees with the frozen exact argv")
        return self


class ServiceRequestProbe(StrictModel):
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    backend: Literal["generic", "kleidiai"]
    parallel: Literal[1, 2]
    repetition: int = Field(ge=0)
    client_index: int = Field(ge=0)
    start_ns: int = Field(ge=0)
    first_content_token_ns: int = Field(ge=0)
    end_ns: int = Field(ge=0)
    ttft_ms: float = Field(gt=0)
    e2e_ms: float = Field(gt=0)
    prompt_tokens: int = Field(gt=0)
    completion_tokens: int = Field(gt=0)
    generation_tok_s: float = Field(gt=0)
    schema_valid: Literal[True]
    safety_score: Literal[0.0, 100.0]
    quality_score: float = Field(ge=0, le=100)
    issues: list[str]
    finish_reason: Literal["stop"]
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_path: str

    @model_validator(mode="after")
    def replay_timing(self) -> ServiceRequestProbe:
        if not self.start_ns < self.first_content_token_ns < self.end_ns:
            raise ValueError("service probe timestamps are not strictly ordered")
        derived_ttft = (self.first_content_token_ns - self.start_ns) / 1_000_000
        derived_e2e = (self.end_ns - self.start_ns) / 1_000_000
        decode_s = (self.end_ns - self.first_content_token_ns) / 1_000_000_000
        derived_rate = self.completion_tokens / decode_s
        for label, actual, expected in (
            ("ttft_ms", self.ttft_ms, derived_ttft),
            ("e2e_ms", self.e2e_ms, derived_e2e),
            ("generation_tok_s", self.generation_tok_s, derived_rate),
        ):
            if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"{label} does not replay from raw counters")
        if self.client_index >= self.parallel:
            raise ValueError("client index is outside the configured parallel width")
        return self


class ConcurrencyRoundProbe(StrictModel):
    backend: Literal["generic", "kleidiai"]
    parallel: Literal[1, 2]
    repetition: int = Field(ge=0)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    wall_time_ms: float = Field(gt=0, allow_inf_nan=False)
    completed_requests: int = Field(gt=0)
    error_count: Literal[0]
    generated_tokens: int = Field(gt=0)
    requests_per_second: float = Field(gt=0, allow_inf_nan=False)
    generated_tokens_per_second: float = Field(gt=0, allow_inf_nan=False)
    request_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def replay_rates(self) -> ConcurrencyRoundProbe:
        if not self.start_ns < self.end_ns:
            raise ValueError("concurrency round counters are not strictly ordered")
        derived_wall = (self.end_ns - self.start_ns) / 1_000_000
        if not math.isclose(self.wall_time_ms, derived_wall, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("concurrency wall_time_ms does not replay")
        if self.completed_requests != self.parallel or len(self.request_ids) != self.parallel:
            raise ValueError("concurrency round is incomplete")
        if len(set(self.request_ids)) != self.parallel:
            raise ValueError("concurrency round repeats a request ID")
        seconds = self.wall_time_ms / 1000
        if not math.isclose(
            self.requests_per_second,
            self.completed_requests / seconds,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("requests_per_second does not replay")
        if not math.isclose(
            self.generated_tokens_per_second,
            self.generated_tokens / seconds,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("generated_tokens_per_second does not replay")
        return self


class ServiceRun(StrictModel):
    service_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    backend: Literal["generic", "kleidiai"]
    parallel: Literal[1, 2]
    repetitions: int = Field(ge=MIN_PROBE_REPETITIONS)
    warmup_rounds: Literal[1]
    startup_start_ns: int = Field(ge=0)
    startup_ready_ns: int = Field(gt=0)
    startup_ms: float = Field(gt=0, allow_inf_nan=False)
    model_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    threads: int = Field(ge=1)
    batch: int = Field(ge=1)
    ubatch: int = Field(ge=1)
    context_total: int = Field(ge=1)
    context_per_slot: Literal[SERVICE_CONTEXT_PER_SLOT]
    seed: Literal[REAL_BENCHMARK_SEED]
    command: list[str] = Field(min_length=1)
    cpu_only_verified: Literal[True]
    backend_verified: Literal[True]
    idle_rss_bytes: int = Field(gt=0)
    peak_rss_bytes: int = Field(gt=0)
    runtime_log_path: str
    command_receipt_path: str
    process_receipt_path: str
    rss_path: str
    stdout_path: str
    stderr_path: str
    warmup_receipt_paths: list[str]
    requests: list[ServiceRequestProbe]
    rounds: list[ConcurrencyRoundProbe]
    safety_pass_count: int = Field(ge=0)
    safety_failure_count: int = Field(ge=0)
    quality_score_mean: float = Field(ge=0, le=100, allow_inf_nan=False)
    quality_score_min: float = Field(ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete_repetitions(self) -> ServiceRun:
        if not self.startup_start_ns < self.startup_ready_ns:
            raise ValueError("service startup counters are not strictly ordered")
        derived_startup = (self.startup_ready_ns - self.startup_start_ns) / 1_000_000
        if not math.isclose(self.startup_ms, derived_startup, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("service startup_ms does not replay from monotonic counters")
        if self.command != _service_expected_command(self):
            raise ValueError("service command disagrees with the frozen exact argv")
        if self.peak_rss_bytes < self.idle_rss_bytes:
            raise ValueError("service peak RSS is below its post-readiness idle sample")
        if len(self.warmup_receipt_paths) != self.parallel:
            raise ValueError("service run must retain exactly one full warmup round")
        if self.context_total != self.context_per_slot * self.parallel:
            raise ValueError("server context does not preserve equal per-request capacity")
        expected_repetitions = set(range(self.repetitions))
        if {round_.repetition for round_ in self.rounds} != expected_repetitions:
            raise ValueError("service run lacks a complete contiguous repetition set")
        if len(self.rounds) != self.repetitions:
            raise ValueError("service run repeats a concurrency round")
        expected_request_count = self.repetitions * self.parallel
        if len(self.requests) != expected_request_count:
            raise ValueError("service run request count is incomplete")
        expected_safety_passes = sum(request.safety_score == 100.0 for request in self.requests)
        expected_safety_failures = expected_request_count - expected_safety_passes
        if (
            self.safety_pass_count != expected_safety_passes
            or self.safety_failure_count != expected_safety_failures
        ):
            raise ValueError("service safety pass/failure summary does not replay")
        expected_quality_mean = statistics.fmean(request.quality_score for request in self.requests)
        expected_quality_min = min(request.quality_score for request in self.requests)
        if not math.isclose(
            self.quality_score_mean, expected_quality_mean, rel_tol=1e-9, abs_tol=1e-9
        ) or not math.isclose(
            self.quality_score_min, expected_quality_min, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("service quality summary does not replay")
        request_ids = {request.request_id for request in self.requests}
        if len(request_ids) != expected_request_count:
            raise ValueError("service run has duplicate request IDs")
        if any(
            request.backend != self.backend
            or request.parallel != self.parallel
            or request.repetition not in expected_repetitions
            for request in self.requests
        ):
            raise ValueError("service request disagrees with its run configuration")
        for repetition in sorted(expected_repetitions):
            requests = [request for request in self.requests if request.repetition == repetition]
            if len(requests) != self.parallel or {
                request.client_index for request in requests
            } != set(range(self.parallel)):
                raise ValueError("service repetition does not contain every client exactly once")
            round_ = next(item for item in self.rounds if item.repetition == repetition)
            expected_ids = {request.request_id for request in requests}
            if (
                round_.backend != self.backend
                or round_.parallel != self.parallel
                or set(round_.request_ids) != expected_ids
            ):
                raise ValueError("concurrency round request membership is not bidirectional")
            if round_.generated_tokens != sum(request.completion_tokens for request in requests):
                raise ValueError("concurrency round generated-token sum does not replay")
            if any(
                request.start_ns < round_.start_ns or request.end_ns > round_.end_ns
                for request in requests
            ):
                raise ValueError("request interval escapes its concurrency round")
            if self.parallel == 2 and max(request.start_ns for request in requests) >= min(
                request.end_ns for request in requests
            ):
                raise ValueError("p2 request intervals do not overlap; concurrency is unproven")
        return self


class PerformanceProbeEvidence(StrictModel):
    schema_version: Literal[PROBE_SCHEMA_VERSION]
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    generated_at: datetime
    evidence_scope: Literal["supporting-ranking-and-concurrency-not-held-out-headline-claim"]
    build_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str
    case_split: Literal["calibration"]
    max_tokens: int = Field(gt=0)
    seed: int
    micro_prompt_tokens: Literal[MICRO_PROMPT_TOKENS]
    micro_generation_tokens: Literal[MICRO_GENERATION_TOKENS]
    micro_threads: list[int] = Field(min_length=2)
    repetitions: int = Field(ge=MIN_PROBE_REPETITIONS)
    max_runtime_minutes: float = Field(gt=0, allow_inf_nan=False)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)
    raw_root: str
    raw_files: dict[str, str]
    micro_help_paths: dict[Literal["generic", "kleidiai"], str]
    model_inventory_proofs: dict[str, dict[str, Any]]
    micro_runs: list[MicroRun]
    service_runs: list[ServiceRun]
    fair_pair_verified: Literal[True]
    matrix_complete: Literal[True]
    failed_micro_cells: Literal[0]
    failed_service_rounds: Literal[0]
    measured_service_safety_pass_count: int = Field(ge=0)
    measured_service_safety_failure_count: int = Field(ge=0)

    @model_validator(mode="after")
    def complete_matrix(self) -> PerformanceProbeEvidence:
        if not self.start_ns < self.end_ns:
            raise ValueError("probe session counters are not strictly ordered")
        derived_elapsed = (self.end_ns - self.start_ns) / 1_000_000_000
        if not math.isclose(self.elapsed_seconds, derived_elapsed, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("probe elapsed_seconds does not replay from monotonic counters")
        if self.elapsed_seconds > self.max_runtime_minutes * 60:
            raise ValueError("probe elapsed time exceeds its declared runtime budget")
        service_threads = {run.threads for run in self.service_runs}
        if len(service_threads) != 1:
            raise ValueError("service matrix must use one frozen full-thread value")
        expected_threads = list(micro_thread_candidates(next(iter(service_threads))))
        if self.micro_threads != expected_threads:
            raise ValueError("micro threads are not the exact topology-derived two-value matrix")
        expected_micro = {
            (backend, quantization, threads)
            for backend, quantization in (
                ("generic", "Q8_0"),
                ("generic", "Q4_0"),
                ("kleidiai", "Q4_0"),
            )
            for threads in self.micro_threads
        }
        actual_micro = {(run.backend, run.quantization, run.threads) for run in self.micro_runs}
        if actual_micro != expected_micro or len(self.micro_runs) != len(expected_micro):
            raise ValueError("microbenchmark matrix is incomplete")
        if any(run.repetitions != self.repetitions for run in self.micro_runs):
            raise ValueError("microbenchmark repetition count is inconsistent")
        expected_service = {
            (backend, parallel)
            for backend in ("generic", "kleidiai")
            for parallel in SERVICE_PARALLEL_VALUES
        }
        actual_service = {(run.backend, run.parallel) for run in self.service_runs}
        if actual_service != expected_service or len(self.service_runs) != len(expected_service):
            raise ValueError("p1/p2 service matrix is incomplete")
        if any(run.repetitions != self.repetitions for run in self.service_runs):
            raise ValueError("service repetition count is inconsistent")
        if self.measured_service_safety_pass_count != sum(
            run.safety_pass_count for run in self.service_runs
        ) or self.measured_service_safety_failure_count != sum(
            run.safety_failure_count for run in self.service_runs
        ):
            raise ValueError("probe safety pass/failure totals do not replay")
        service_settings = {
            (
                run.model_file_sha256,
                run.threads,
                run.batch,
                run.ubatch,
                run.context_per_slot,
                run.repetitions,
                run.warmup_rounds,
                run.seed,
            )
            for run in self.service_runs
        }
        if len(service_settings) != 1:
            raise ValueError("service cells do not share one frozen configuration")
        q4_hashes = {
            run.model_file_sha256 for run in self.micro_runs if run.quantization == "Q4_0"
        } | {run.model_file_sha256 for run in self.service_runs}
        if len(q4_hashes) != 1:
            raise ValueError("fair Q4_0 cells do not use one identical model file")
        for threads in self.micro_threads:
            pair = [
                run
                for run in self.micro_runs
                if run.quantization == "Q4_0" and run.threads == threads
            ]
            if {run.backend for run in pair} != {"generic", "kleidiai"}:
                raise ValueError("Q4_0 micro pair is incomplete")
        by_parallel = {(run.backend, run.parallel): run for run in self.service_runs}
        for parallel in SERVICE_PARALLEL_VALUES:
            generic = by_parallel[("generic", parallel)]
            kleidiai = by_parallel[("kleidiai", parallel)]
            signature = lambda run: (  # noqa: E731 - compact frozen fairness tuple
                run.model_file_sha256,
                run.threads,
                run.batch,
                run.ubatch,
                run.context_per_slot,
                run.parallel,
                run.repetitions,
            )
            if signature(generic) != signature(kleidiai):
                raise ValueError("generic/KleidiAI service probes are not fairly paired")
        if not self.raw_files:
            raise ValueError("probe artifact has no raw-file integrity manifest")
        if self.raw_root != f"performance-probes-raw/{self.session_id}":
            raise ValueError("probe raw root is not the canonical session directory")
        if set(self.micro_help_paths) != {"generic", "kleidiai"}:
            raise ValueError("probe artifact lacks both llama-bench help receipts")
        referenced_raw = {
            path for run in self.micro_runs for path in (run.stdout_path, run.stderr_path)
        } | set(self.micro_help_paths.values())
        for run in self.service_runs:
            referenced_raw.update(
                {
                    run.runtime_log_path,
                    run.command_receipt_path,
                    run.process_receipt_path,
                    run.rss_path,
                    run.stdout_path,
                    run.stderr_path,
                    *run.warmup_receipt_paths,
                    *(request.receipt_path for request in run.requests),
                }
            )
        if not referenced_raw.issubset(self.raw_files):
            raise ValueError("probe artifact does not hash every referenced raw log")
        if len(referenced_raw) != sum(2 for _ in self.micro_runs) + len(
            self.micro_help_paths
        ) + sum(6 + len(run.warmup_receipt_paths) + len(run.requests) for run in self.service_runs):
            raise ValueError("probe cells alias one or more raw evidence paths")
        run_ids = [run.service_run_id for run in self.service_runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("service cells do not have unique fresh-start IDs")
        return self


@dataclass(frozen=True, slots=True)
class _BackendInputs:
    backend: Literal["generic", "kleidiai"]
    server: Path
    bench: Path
    cache: Path
    cache_text: str
    server_sha256: str
    bench_sha256: str


@dataclass(frozen=True, slots=True)
class _ModelInputs:
    model_id: str
    path: Path
    quantization: Literal["Q4_0", "Q8_0"]
    sha256: str
    proof: ModelInventoryProof


@dataclass(frozen=True, slots=True)
class _CompletionProbeResult:
    """Receipt metadata shared by measured and explicitly unmeasured requests."""

    request_id: str
    receipt_path: str
    measured_probe: ServiceRequestProbe | None
    gate_issue_codes: tuple[str, ...]
    validation_context: dict[str, Any]


def micro_thread_candidates(full_threads: int) -> tuple[int, int]:
    """Return the frozen shallow topology-derived thread matrix."""

    if full_threads < 2:
        raise PerformanceProbeError("microbenchmark requires a target with at least two threads")
    candidates = tuple(sorted({max(1, full_threads // 2), full_threads}))
    if len(candidates) != 2:
        raise PerformanceProbeError("microbenchmark could not derive two thread candidates")
    return candidates


def _remaining_seconds(deadline: float, *, floor: float = 1.0) -> float:
    remaining = deadline - time.monotonic()
    if remaining < floor:
        raise PerformanceProbeError("performance-probe runtime budget was exhausted")
    return remaining


def _run_process(command: list[str], *, timeout_s: float) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PerformanceProbeError("native probe command failed or timed out") from exc
    if completed.returncode != 0:
        raise PerformanceProbeError(
            f"native probe command exited nonzero ({Path(command[0]).name})"
        )
    return completed


def _write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.as_posix()


def _validate_build_pair(
    manifest_path: Path,
    generic: _BackendInputs,
    kleidiai: _BackendInputs,
) -> str:
    try:
        manifest = BuildManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PerformanceProbeError("build manifest is missing or invalid") from exc
    by_backend = {variant.backend: variant for variant in manifest.variants}
    if set(by_backend) != {"generic", "kleidiai"} or len(manifest.variants) != 2:
        raise PerformanceProbeError("probe requires exactly one generic/KleidiAI build pair")
    generic_manifest = by_backend["generic"]
    kleidiai_manifest = by_backend["kleidiai"]
    if (
        not generic_manifest.cpu_only_configured
        or generic_manifest.kleidiai_configured
        or not kleidiai_manifest.cpu_only_configured
        or not kleidiai_manifest.kleidiai_configured
    ):
        raise PerformanceProbeError("build manifest does not prove the frozen CPU backend pair")
    generic_flags = {
        flag for flag in generic_manifest.cmake_flags if "GGML_CPU_KLEIDIAI=" not in flag
    }
    kleidiai_flags = {
        flag for flag in kleidiai_manifest.cmake_flags if "GGML_CPU_KLEIDIAI=" not in flag
    }
    if generic_flags != kleidiai_flags:
        raise PerformanceProbeError("build flags differ beyond GGML_CPU_KLEIDIAI")
    for inputs, variant in (
        (generic, generic_manifest),
        (kleidiai, kleidiai_manifest),
    ):
        for name, path in (("llama-server", inputs.server), ("llama-bench", inputs.bench)):
            if not path.is_file() or not os.access(path, os.X_OK):
                raise PerformanceProbeError(f"missing executable {inputs.backend} {name}")
            actual_hash = inputs.server_sha256 if name == "llama-server" else inputs.bench_sha256
            if variant.binary_sha256.get(name) != actual_hash:
                raise PerformanceProbeError(f"{inputs.backend} {name} hash disagrees with manifest")
        cache = parse_cmake_cache(inputs.cache_text)
        expected_kleidiai = "ON" if inputs.backend == "kleidiai" else "OFF"
        if cache.get("GGML_CPU_KLEIDIAI", "").upper() != expected_kleidiai:
            raise PerformanceProbeError(f"{inputs.backend} CMake cache backend flag is invalid")
    return generic_manifest.source_commit


def _validate_model(path: Path, model_id: str) -> _ModelInputs:
    spec = get_model(model_id)
    if not path.is_file() or path.name != spec.expected_filename:
        raise PerformanceProbeError(f"missing exact reviewed model: {model_id}")
    if path.stat().st_size != spec.expected_bytes:
        raise PerformanceProbeError(f"model size disagrees with registry: {model_id}")
    digest = sha256_file(path)
    proof = verify_model_inventory(path, spec, actual_sha256=digest)
    if not proof.verified:
        raise PerformanceProbeError(f"model inventory verification failed: {model_id}")
    if spec.quantization not in {"Q4_0", "Q8_0"}:
        raise PerformanceProbeError("probe model has an unsupported quantization")
    return _ModelInputs(
        model_id=model_id,
        path=path,
        quantization=spec.quantization,
        sha256=digest,
        proof=proof,
    )


def _relative(path: Path, artifacts_dir: Path) -> str:
    try:
        return path.resolve().relative_to(artifacts_dir.resolve()).as_posix()
    except ValueError as exc:
        raise PerformanceProbeError("raw probe path escaped the artifact directory") from exc


def _micro_run(
    *,
    inputs: _BackendInputs,
    model: _ModelInputs,
    reviewed_q4: ModelInventoryProof,
    threads: int,
    repetitions: int,
    raw_root: Path,
    artifacts_dir: Path,
    deadline: float,
    help_text: str,
) -> MicroRun:
    capabilities = inspect_help(help_text)
    command = build_llama_bench_command(
        inputs.bench,
        model.path,
        threads=threads,
        repetitions=repetitions,
        prompt_tokens=MICRO_PROMPT_TOKENS,
        generation_tokens=MICRO_GENERATION_TOKENS,
        capabilities=capabilities,
    )
    started = time.monotonic_ns()
    completed = _run_process(command, timeout_s=_remaining_seconds(deadline))
    ended = time.monotonic_ns()
    stem = f"{inputs.backend}-{model.quantization.lower()}-t{threads}"
    stdout_path = raw_root / "micro" / f"{stem}.stdout.txt"
    stderr_path = raw_root / "micro" / f"{stem}.stderr.txt"
    _write_text(stdout_path, completed.stdout)
    _write_text(stderr_path, completed.stderr)
    log_text = "\n".join((completed.stdout, completed.stderr))
    backend_proof = verify_backend_log(
        log_text,
        BuildVariant(inputs.backend),
        quantization=model.quantization if inputs.backend == "kleidiai" else None,
        reviewed_model=reviewed_q4 if inputs.backend == "kleidiai" else None,
    )
    cpu_proof = verify_cpu_only(
        command,
        cmake_cache=inputs.cache_text,
        runtime_log=log_text,
        require_device_none=True,
    )
    if not backend_proof.verified or not cpu_proof.verified:
        raise PerformanceProbeError(
            f"{inputs.backend} {model.quantization} micro runtime proof failed"
        )
    parsed = require_tests(
        parse_output(completed.stdout),
        prompt_tokens=MICRO_PROMPT_TOKENS,
        generation_tokens=MICRO_GENERATION_TOKENS,
        threads=threads,
    )
    return MicroRun(
        backend=inputs.backend,
        quantization=model.quantization,
        threads=threads,
        repetitions=repetitions,
        warmup_repetitions=1,
        model_file_sha256=model.sha256,
        binary_sha256=inputs.bench_sha256,
        command=command,
        start_ns=started,
        end_ns=ended,
        elapsed_ms=(ended - started) / 1_000_000,
        cpu_only_verified=True,
        backend_verified=True,
        parser_version=LLAMA_BENCH_PARSER_VERSION,
        stdout_path=_relative(stdout_path, artifacts_dir),
        stderr_path=_relative(stderr_path, artifacts_dir),
        metrics=[
            MicroMetric(
                test=result.test,
                tokens_per_second=result.tokens_per_second,
                tokens_per_second_stddev=float(result.tokens_per_second_stddev),
            )
            for result in parsed
        ],
    )


def _safe_finish_reason(value: Any) -> str:
    """Return a bounded log-safe finish reason without reflecting upstream text."""

    if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,32}", value):
        return value
    return "unknown"


def _validation_context(
    *,
    backend: Literal["generic", "kleidiai"],
    parallel: Literal[1, 2],
    phase: Literal["warmup", "measured"],
    repetition: int,
    client_index: int,
    schema_valid: bool,
    safety_score: float,
    issue_codes: tuple[str, ...],
    gate_issue_codes: tuple[str, ...],
    finish_reason: Any,
    response_sha256: str,
) -> dict[str, Any]:
    safe_issue_codes = sorted(
        {
            code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "unknown_issue"
            for code in issue_codes
        }
    )
    return {
        "backend": backend,
        "parallel": parallel,
        "phase": phase,
        "repetition": repetition,
        "client_index": client_index,
        "schema_valid": schema_valid,
        "safety_score": safety_score,
        "issue_codes": safe_issue_codes,
        "gate_issue_codes": sorted(set(gate_issue_codes)),
        "finish_reason": _safe_finish_reason(finish_reason),
        "response_sha256": response_sha256,
    }


def _completion_gate_issue_codes(
    *,
    start_ns: Any,
    first_content_token_ns: Any,
    end_ns: Any,
    prompt_tokens: Any,
    completion_tokens: Any,
    generation_rate: float | None,
    schema_valid: bool,
    finish_reason: Any,
) -> tuple[str, ...]:
    issues: list[str] = []
    if first_content_token_ns is None:
        issues.append("missing_first_content_token")
    elif not (
        type(start_ns) is int
        and type(first_content_token_ns) is int
        and type(end_ns) is int
        and start_ns < first_content_token_ns < end_ns
    ):
        issues.append("invalid_stream_timing")
    if type(prompt_tokens) is not int or prompt_tokens <= 0:
        issues.append("invalid_prompt_tokens")
    if type(completion_tokens) is not int or completion_tokens <= 0:
        issues.append("invalid_completion_tokens")
    if generation_rate is None or not math.isfinite(generation_rate) or generation_rate <= 0:
        issues.append("invalid_generation_rate")
    if not schema_valid:
        issues.append("schema_invalid")
    if finish_reason != "stop":
        issues.append("non_stop_finish")
    return tuple(issues)


def _validation_failure_message(context: dict[str, Any]) -> str:
    issue_codes = ",".join(context["issue_codes"]) or "none"
    gate_issue_codes = ",".join(context["gate_issue_codes"]) or "none"
    schema_valid = str(context["schema_valid"]).lower()
    return (
        f"backend={context['backend']} parallel={context['parallel']} "
        f"phase={context['phase']} repetition={context['repetition']} "
        f"client_index={context['client_index']} schema_valid={schema_valid} "
        f"safety_score={context['safety_score']} issue_codes={issue_codes} "
        f"gate_issue_codes={gate_issue_codes} "
        f"finish_reason={context['finish_reason']} "
        f"response_sha256={context['response_sha256']}"
    )


def _completion_probe(
    completion: ClientCompletion,
    *,
    case: Any,
    backend: Literal["generic", "kleidiai"],
    parallel: Literal[1, 2],
    repetition: int,
    client_index: int,
    phase: Literal["warmup", "measured"],
    model_alias: str,
    receipt_dir: Path,
    artifacts_dir: Path,
) -> _CompletionProbeResult:
    timing = completion.timing
    prompt_tokens = completion.usage.get("prompt_tokens")
    completion_tokens = completion.usage.get("completion_tokens")
    generation_rate = completion.generation_tokens_per_second
    score = score_case(case, completion.text)
    request_id = uuid4().hex
    receipt_path = receipt_dir / f"{phase}-r{repetition}-c{client_index}-{request_id}.json"
    response_sha256 = hashlib.sha256(completion.text.encode("utf-8")).hexdigest()
    choices = completion.payload.get("choices")
    finish_reason = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        finish_reason = choices[0].get("finish_reason")
    gate_issue_codes = _completion_gate_issue_codes(
        start_ns=timing.start_ns,
        first_content_token_ns=timing.first_content_token_ns,
        end_ns=timing.end_ns,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        generation_rate=generation_rate,
        schema_valid=score.schema_valid,
        finish_reason=finish_reason,
    )
    request_payload = {
        "case_id": case.case_id,
        "phase": phase,
        "repetition": repetition,
        "client_index": client_index,
        "model": model_alias,
        "messages": build_messages(case.incident),
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": REAL_BENCHMARK_MAX_TOKENS,
        "seed": REAL_BENCHMARK_SEED,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": triage_openai_response_format(),
    }
    response_payload = {
        "content": completion.text,
        "usage": dict(completion.usage),
        "finish_reason": finish_reason,
        "timing": {
            "start_ns": timing.start_ns,
            "first_content_token_ns": timing.first_content_token_ns,
            "end_ns": timing.end_ns,
            "ttft_ms": timing.ttft_ms,
            "e2e_ms": timing.e2e_ms,
        },
        "score": score.as_dict(),
    }
    validation_context = _validation_context(
        backend=backend,
        parallel=parallel,
        phase=phase,
        repetition=repetition,
        client_index=client_index,
        schema_valid=score.schema_valid,
        safety_score=score.safety_score,
        issue_codes=score.issues,
        gate_issue_codes=gate_issue_codes,
        finish_reason=finish_reason,
        response_sha256=response_sha256,
    )
    response_payload["validation_context"] = validation_context
    write_json(
        receipt_path,
        {
            "schema_version": PROBE_SCHEMA_VERSION,
            "request_id": request_id,
            "request": request_payload,
            "response": response_payload,
        },
    )
    relative_receipt_path = _relative(receipt_path, artifacts_dir)
    # Warmup establishes server slots but is not a measured quality or performance
    # sample. Its raw response and replayable score remain in the evidence receipt;
    # measured requests retain the strict schema/completeness gate below.
    if phase == "warmup":
        return _CompletionProbeResult(
            request_id=request_id,
            receipt_path=relative_receipt_path,
            measured_probe=None,
            gate_issue_codes=gate_issue_codes,
            validation_context=validation_context,
        )
    if gate_issue_codes:
        return _CompletionProbeResult(
            request_id=request_id,
            receipt_path=relative_receipt_path,
            measured_probe=None,
            gate_issue_codes=gate_issue_codes,
            validation_context=validation_context,
        )
    assert timing.first_content_token_ns is not None
    assert type(prompt_tokens) is int
    assert type(completion_tokens) is int
    assert generation_rate is not None
    measured_probe = ServiceRequestProbe(
        request_id=request_id,
        backend=backend,
        parallel=parallel,
        repetition=repetition,
        client_index=client_index,
        start_ns=timing.start_ns,
        first_content_token_ns=timing.first_content_token_ns,
        end_ns=timing.end_ns,
        ttft_ms=float(timing.ttft_ms),
        e2e_ms=timing.e2e_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        generation_tok_s=generation_rate,
        schema_valid=True,
        safety_score=score.safety_score,
        quality_score=score.quality_score,
        issues=list(score.issues),
        finish_reason="stop",
        response_sha256=response_sha256,
        receipt_path=relative_receipt_path,
    )
    return _CompletionProbeResult(
        request_id=request_id,
        receipt_path=relative_receipt_path,
        measured_probe=measured_probe,
        gate_issue_codes=(),
        validation_context=validation_context,
    )


async def _request_round(
    client: OpenAIClient,
    *,
    case: Any,
    backend: Literal["generic", "kleidiai"],
    parallel: Literal[1, 2],
    repetition: int,
    model_alias: str,
    phase: Literal["warmup", "measured"],
    receipt_dir: Path,
    artifacts_dir: Path,
    deadline: float,
) -> tuple[list[ServiceRequestProbe], ConcurrencyRoundProbe | None, list[str]]:
    messages = build_messages(case.incident)

    async def one(client_index: int) -> ClientCompletion:
        return await client.chat_completion(
            messages=messages,
            model=model_alias,
            temperature=0.0,
            top_p=1.0,
            max_tokens=REAL_BENCHMARK_MAX_TOKENS,
            seed=REAL_BENCHMARK_SEED,
            stream=True,
            stream_include_usage=True,
            response_format=triage_openai_response_format(),
        )

    started = time.monotonic_ns()
    try:
        completions = await asyncio.wait_for(
            asyncio.gather(*(one(index) for index in range(parallel))),
            timeout=_remaining_seconds(deadline),
        )
    except TimeoutError as exc:
        raise PerformanceProbeError("service probe round exceeded the hard runtime budget") from exc
    ended = time.monotonic_ns()
    outcomes = [
        _completion_probe(
            completion,
            case=case,
            backend=backend,
            parallel=parallel,
            repetition=repetition,
            client_index=index,
            phase=phase,
            model_alias=model_alias,
            receipt_dir=receipt_dir,
            artifacts_dir=artifacts_dir,
        )
        for index, completion in enumerate(completions)
    ]
    receipt_paths = [outcome.receipt_path for outcome in outcomes]
    if phase == "warmup":
        failed = [
            outcome
            for outcome in outcomes
            if _STREAM_EVIDENCE_GATE_ISSUES.intersection(outcome.gate_issue_codes)
        ]
        if failed:
            diagnostics = "; ".join(
                _validation_failure_message(outcome.validation_context) for outcome in failed
            )
            raise PerformanceProbeError(
                "service warmup probe failed streaming-evidence validation "
                f"({len(failed)}/{parallel} requests): {diagnostics}"
            )
        return [], None, receipt_paths
    failed = [outcome for outcome in outcomes if outcome.gate_issue_codes]
    if failed:
        diagnostics = "; ".join(
            _validation_failure_message(outcome.validation_context) for outcome in failed
        )
        raise PerformanceProbeError(
            "service measured probe failed schema/completeness validation "
            f"({len(failed)}/{parallel} requests): {diagnostics}"
        )
    requests = [outcome.measured_probe for outcome in outcomes]
    if any(request is None for request in requests):
        raise PerformanceProbeError("service probe produced inconsistent phase accounting")
    measured_requests = [request for request in requests if request is not None]
    wall_ms = (ended - started) / 1_000_000
    seconds = wall_ms / 1000
    generated_tokens = sum(request.completion_tokens for request in measured_requests)
    round_probe = ConcurrencyRoundProbe(
        backend=backend,
        parallel=parallel,
        repetition=repetition,
        start_ns=started,
        end_ns=ended,
        wall_time_ms=wall_ms,
        completed_requests=parallel,
        error_count=0,
        generated_tokens=generated_tokens,
        requests_per_second=parallel / seconds,
        generated_tokens_per_second=generated_tokens / seconds,
        request_ids=[request.request_id for request in measured_requests],
    )
    return measured_requests, round_probe, receipt_paths


async def _service_run(
    *,
    inputs: _BackendInputs,
    q4: _ModelInputs,
    threads: int,
    parallel: Literal[1, 2],
    repetitions: int,
    case: Any,
    raw_root: Path,
    artifacts_dir: Path,
    deadline: float,
) -> ServiceRun:
    port = find_available_port(19080 if inputs.backend == "generic" else 19180)
    alias = f"probe-{inputs.backend}-p{parallel}"
    config = LlamaServerConfig(
        binary=inputs.server,
        model=q4.path,
        host="127.0.0.1",
        port=port,
        model_alias=alias,
        threads=threads,
        batch_size=256,
        ubatch_size=128,
        # llama-server divides its total context across parallel slots.  Scale
        # total context with p so p1 and p2 retain the same 2048-token capacity
        # per in-flight request instead of making p2 an artificially smaller task.
        context_size=SERVICE_CONTEXT_PER_SLOT * parallel,
        parallel=parallel,
        seed=REAL_BENCHMARK_SEED,
        cpu_only=True,
    )
    capabilities = inspect_llama_server_capabilities(
        inputs.server,
        timeout_s=min(30.0, _remaining_seconds(deadline)),
    )
    manager = LlamaServerProcess(
        config,
        capabilities=capabilities,
        log_dir=raw_root / "service" / f"{inputs.backend}-p{parallel}",
        startup_timeout_s=min(240.0, _remaining_seconds(deadline)),
    )
    requests: list[ServiceRequestProbe] = []
    rounds: list[ConcurrencyRoundProbe] = []
    warmup_receipt_paths: list[str] = []
    log_text = ""
    service_run_id = uuid4().hex
    startup_started = time.monotonic_ns()
    try:
        manager.start()
        startup_ended = time.monotonic_ns()
        if inputs.backend == "kleidiai":
            log_text, backend_proof = _wait_for_kleidiai_load_proof(
                manager,
                quantization="Q4_0",
                reviewed_model=q4.proof,
                timeout_s=min(5.0, _remaining_seconds(deadline)),
            )
        else:
            log_text = manager.log_text()
            backend_proof = verify_backend_log(log_text, BuildVariant.GENERIC)
        cpu_proof = verify_cpu_only(
            manager.command,
            cmake_cache=inputs.cache_text,
            runtime_log=log_text,
            require_device_none=True,
        )
        if not backend_proof.verified or not cpu_proof.verified:
            raise PerformanceProbeError(f"{inputs.backend} p{parallel} startup proof failed")
        pid = manager.pid
        if pid is None:
            raise PerformanceProbeError("service process has no PID after readiness")
        idle_sample_ns = time.monotonic_ns()
        idle_rss_bytes, idle_process_count = process_tree_rss(pid)
        if idle_rss_bytes <= 0 or idle_process_count <= 0:
            raise PerformanceProbeError("service idle RSS sample is unavailable")
        receipt_dir = raw_root / "service" / f"{inputs.backend}-p{parallel}" / "requests"
        async with OpenAIClient(
            f"http://127.0.0.1:{port}",
            timeout_s=min(240.0, _remaining_seconds(deadline)),
        ) as client:
            # One unmeasured parallel warmup round establishes every server slot.
            _, _, warmup_receipt_paths = await _request_round(
                client,
                case=case,
                backend=inputs.backend,
                parallel=parallel,
                repetition=0,
                model_alias=alias,
                phase="warmup",
                receipt_dir=receipt_dir,
                artifacts_dir=artifacts_dir,
                deadline=deadline,
            )
            for repetition in range(repetitions):
                round_requests, round_probe, _ = await _request_round(
                    client,
                    case=case,
                    backend=inputs.backend,
                    parallel=parallel,
                    repetition=repetition,
                    model_alias=alias,
                    phase="measured",
                    receipt_dir=receipt_dir,
                    artifacts_dir=artifacts_dir,
                    deadline=deadline,
                )
                if round_probe is None:
                    raise PerformanceProbeError("measured service round lacks a round receipt")
                requests.extend(round_requests)
                rounds.append(round_probe)
        log_text = manager.log_text()
        final_backend = verify_backend_log(
            log_text,
            BuildVariant(inputs.backend),
            quantization="Q4_0" if inputs.backend == "kleidiai" else None,
            reviewed_model=q4.proof if inputs.backend == "kleidiai" else None,
        )
        final_cpu = verify_cpu_only(
            manager.command,
            cmake_cache=inputs.cache_text,
            runtime_log=log_text,
            require_device_none=True,
        )
        if not final_backend.verified or not final_cpu.verified:
            raise PerformanceProbeError(f"{inputs.backend} p{parallel} final proof failed")
        runtime_path = raw_root / "service" / f"{inputs.backend}-p{parallel}-combined.log"
        _write_text(runtime_path, log_text)
        process_receipt_path = raw_root / "service" / f"{inputs.backend}-p{parallel}-process.json"
        write_json(
            process_receipt_path,
            {
                "schema_version": PROBE_SCHEMA_VERSION,
                "service_run_id": service_run_id,
                "backend": inputs.backend,
                "parallel": parallel,
                "pid": pid,
                "startup_start_ns": startup_started,
                "startup_ready_ns": startup_ended,
                "idle_rss": {
                    "monotonic_ns": idle_sample_ns,
                    "rss_bytes": idle_rss_bytes,
                    "process_count": idle_process_count,
                },
                "command": list(manager.command),
            },
        )
        # Finalize the sampler before freezing peak RSS. The outer finally is
        # intentionally retained as an idempotent failure-path cleanup guard.
        manager.stop()
        peak_rss_bytes = max(manager.peak_rss_bytes, idle_rss_bytes)
        return ServiceRun(
            service_run_id=service_run_id,
            backend=inputs.backend,
            parallel=parallel,
            repetitions=repetitions,
            warmup_rounds=1,
            startup_start_ns=startup_started,
            startup_ready_ns=startup_ended,
            startup_ms=(startup_ended - startup_started) / 1_000_000,
            model_file_sha256=q4.sha256,
            binary_sha256=inputs.server_sha256,
            threads=threads,
            batch=256,
            ubatch=128,
            context_total=SERVICE_CONTEXT_PER_SLOT * parallel,
            context_per_slot=SERVICE_CONTEXT_PER_SLOT,
            seed=REAL_BENCHMARK_SEED,
            command=list(manager.command),
            cpu_only_verified=True,
            backend_verified=True,
            idle_rss_bytes=idle_rss_bytes,
            peak_rss_bytes=peak_rss_bytes,
            runtime_log_path=_relative(runtime_path, artifacts_dir),
            command_receipt_path=_relative(manager.artifacts.command_json, artifacts_dir),
            process_receipt_path=_relative(process_receipt_path, artifacts_dir),
            rss_path=_relative(manager.artifacts.rss_csv, artifacts_dir),
            stdout_path=_relative(manager.artifacts.stdout_log, artifacts_dir),
            stderr_path=_relative(manager.artifacts.stderr_log, artifacts_dir),
            warmup_receipt_paths=warmup_receipt_paths,
            requests=requests,
            rounds=rounds,
            safety_pass_count=sum(request.safety_score == 100.0 for request in requests),
            safety_failure_count=sum(request.safety_score != 100.0 for request in requests),
            quality_score_mean=statistics.fmean(request.quality_score for request in requests),
            quality_score_min=min(request.quality_score for request in requests),
        )
    finally:
        manager.stop()


def _raw_integrity(raw_root: Path, artifacts_dir: Path) -> dict[str, str]:
    files = [path for path in sorted(raw_root.rglob("*")) if path.is_file()]
    if not files:
        raise PerformanceProbeError("performance probes produced no raw files")
    return {_relative(path, artifacts_dir): sha256_file(path) for path in files}


async def run_performance_probes(
    *,
    generic_server: Path = Path("build/llama-generic/bin/llama-server"),
    generic_bench: Path = Path("build/llama-generic/bin/llama-bench"),
    kleidiai_server: Path = Path("build/llama-kleidiai/bin/llama-server"),
    kleidiai_bench: Path = Path("build/llama-kleidiai/bin/llama-bench"),
    strong_q4: Path = Path("models/qwen2.5-1.5b-instruct-q4_0.gguf"),
    strong_q8: Path = Path("models/qwen2.5-1.5b-instruct-q8_0.gguf"),
    build_manifest_path: Path = Path("artifacts/build-manifest.json"),
    cases_path: Path = Path("demo/cases.jsonl"),
    split_path: Path = Path("demo/split.json"),
    artifacts_dir: Path = Path("artifacts"),
    threads: int,
    repetitions: int = MIN_PROBE_REPETITIONS,
    max_minutes: float = 20.0,
) -> PerformanceProbeEvidence:
    """Run the complete bounded supporting matrix and atomically publish its receipt."""

    assert_arm64_benchmark()
    if platform.system().lower() != "linux":
        raise PerformanceProbeError("performance probes require Linux Arm64")
    if repetitions < MIN_PROBE_REPETITIONS:
        raise PerformanceProbeError("performance probes require at least three repetitions")
    if not math.isfinite(max_minutes) or max_minutes <= 0:
        raise PerformanceProbeError("performance-probe runtime budget must be finite and positive")
    micro_threads = micro_thread_candidates(threads)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifacts_dir / "performance-probes.json"
    output_path.unlink(missing_ok=True)
    session_id = uuid4().hex
    raw_root = artifacts_dir / "performance-probes-raw" / session_id
    raw_root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    started_ns = time.monotonic_ns()
    deadline = started + max_minutes * 60

    def backend_inputs(
        backend: Literal["generic", "kleidiai"], server: Path, bench: Path
    ) -> _BackendInputs:
        cache = server.parent.parent / "CMakeCache.txt"
        try:
            cache_text = cache.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PerformanceProbeError(f"missing {backend} CMake cache") from exc
        try:
            server_sha256 = sha256_file(server)
            bench_sha256 = sha256_file(bench)
        except OSError as exc:
            raise PerformanceProbeError(f"missing {backend} probe binary") from exc
        return _BackendInputs(
            backend,
            server,
            bench,
            cache,
            cache_text,
            server_sha256,
            bench_sha256,
        )

    generic = backend_inputs("generic", generic_server, generic_bench)
    kleidiai = backend_inputs("kleidiai", kleidiai_server, kleidiai_bench)
    source_commit = _validate_build_pair(build_manifest_path, generic, kleidiai)
    q4 = _validate_model(strong_q4, "strong-q4-0")
    q8 = _validate_model(strong_q8, "strong-q8-0")

    cases = load_cases(cases_path)
    split = load_split(split_path)
    validate_dataset(cases, split)
    by_id = {case.case_id: case for case in cases}
    case = by_id[split.calibration[0]]

    help_by_backend: dict[str, str] = {}
    help_paths: dict[str, str] = {}
    for inputs in (generic, kleidiai):
        completed = _run_process(
            [str(inputs.bench), "--help"],
            timeout_s=min(30.0, _remaining_seconds(deadline)),
        )
        help_text = "\n".join((completed.stdout, completed.stderr))
        if not inspect_help(help_text).cpu_only_complete:
            raise PerformanceProbeError(
                f"{inputs.backend} llama-bench help lacks CPU-only safeguards"
            )
        help_by_backend[inputs.backend] = help_text
        help_path = raw_root / "micro" / f"{inputs.backend}-help.txt"
        _write_text(help_path, help_text)
        help_paths[inputs.backend] = _relative(help_path, artifacts_dir)

    micro_runs: list[MicroRun] = []
    for inputs, model in ((generic, q8), (generic, q4), (kleidiai, q4)):
        for candidate_threads in micro_threads:
            micro_runs.append(
                _micro_run(
                    inputs=inputs,
                    model=model,
                    reviewed_q4=q4.proof,
                    threads=candidate_threads,
                    repetitions=repetitions,
                    raw_root=raw_root,
                    artifacts_dir=artifacts_dir,
                    deadline=deadline,
                    help_text=help_by_backend[inputs.backend],
                )
            )

    service_runs: list[ServiceRun] = []
    for inputs in (generic, kleidiai):
        for parallel in SERVICE_PARALLEL_VALUES:
            service_runs.append(
                await _service_run(
                    inputs=inputs,
                    q4=q4,
                    threads=threads,
                    parallel=parallel,
                    repetitions=repetitions,
                    case=case,
                    raw_root=raw_root,
                    artifacts_dir=artifacts_dir,
                    deadline=deadline,
                )
            )
    ended_ns = time.monotonic_ns()
    elapsed = (ended_ns - started_ns) / 1_000_000_000
    if elapsed > max_minutes * 60:
        raise PerformanceProbeError("performance probes exceeded their fixed runtime budget")
    evidence = PerformanceProbeEvidence(
        schema_version=PROBE_SCHEMA_VERSION,
        session_id=session_id,
        generated_at=datetime.now(UTC),
        evidence_scope="supporting-ranking-and-concurrency-not-held-out-headline-claim",
        build_source_commit=source_commit,
        prompt_sha256=prompt_fingerprint(),
        case_id=case.case_id,
        case_split="calibration",
        max_tokens=REAL_BENCHMARK_MAX_TOKENS,
        seed=REAL_BENCHMARK_SEED,
        micro_prompt_tokens=MICRO_PROMPT_TOKENS,
        micro_generation_tokens=MICRO_GENERATION_TOKENS,
        micro_threads=list(micro_threads),
        repetitions=repetitions,
        max_runtime_minutes=max_minutes,
        start_ns=started_ns,
        end_ns=ended_ns,
        elapsed_seconds=elapsed,
        raw_root=_relative(raw_root, artifacts_dir),
        raw_files=_raw_integrity(raw_root, artifacts_dir),
        micro_help_paths=help_paths,
        model_inventory_proofs={
            q4.model_id: q4.proof.to_dict(),
            q8.model_id: q8.proof.to_dict(),
        },
        micro_runs=micro_runs,
        service_runs=service_runs,
        fair_pair_verified=True,
        matrix_complete=True,
        failed_micro_cells=0,
        failed_service_rounds=0,
        measured_service_safety_pass_count=sum(run.safety_pass_count for run in service_runs),
        measured_service_safety_failure_count=sum(run.safety_failure_count for run in service_runs),
    )
    write_json(output_path, evidence)
    return evidence


def run_performance_probes_sync(**options: Any) -> PerformanceProbeEvidence:
    return asyncio.run(run_performance_probes(**options))


def performance_probe_semantic_sha256(evidence: PerformanceProbeEvidence) -> str:
    """Hash stable measurement semantics while raw hashes remain independently replayed.

    Public redaction is allowed to rewrite native log bytes and therefore ``raw_files``.
    Every other field, including raw paths, commands, counters, metrics, model/binary hashes,
    and request receipts, remains in this canonical binding.
    """

    payload = evidence.model_dump(mode="json", exclude={"raw_files"})
    for run in payload["micro_runs"]:
        command = run["command"]
        command[0] = "/".join(_path_identity(command[0]))
        command[2] = "/".join(_path_identity(command[2]))
    for run in payload["service_runs"]:
        command = run["command"]
        command[0] = "/".join(_path_identity(command[0]))
        model_index = command.index("--model") + 1
        command[model_index] = "/".join(_path_identity(command[model_index]))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_raw_path(
    artifacts_dir: Path,
    raw_root: Path,
    relative: str,
    evidence: PerformanceProbeEvidence,
) -> Path:
    if relative not in evidence.raw_files:
        raise ValueError(f"unhashed performance-probe raw receipt: {relative}")
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"unsafe performance-probe raw receipt: {relative}")
    path = (artifacts_dir / relative).resolve()
    try:
        path.relative_to(raw_root)
    except ValueError as exc:
        raise ValueError(f"performance-probe raw receipt escapes session: {relative}") from exc
    if not path.is_file():
        raise ValueError(f"missing performance-probe raw receipt: {relative}")
    return path


def _path_identity(value: str) -> tuple[str, ...]:
    """Compare recorded paths while tolerating the public ``<redacted-home>`` prefix."""

    normalized = value.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    for anchor in ("build", "models"):
        if anchor in parts:
            return parts[parts.index(anchor) :]
    return parts


def _paths_equivalent(left: str, right: str) -> bool:
    return _path_identity(left) == _path_identity(right)


def _reviewed_proof(model_id: str) -> ModelInventoryProof:
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
    )


def _load_probe_manifests(
    *,
    artifacts_dir: Path,
    project_root: Path,
    evidence: PerformanceProbeEvidence,
    require_current_files: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    try:
        build = BuildManifest.model_validate_json(
            (artifacts_dir / "build-manifest.json").read_text(encoding="utf-8")
        )
        models = ModelManifest.model_validate_json(
            (artifacts_dir / "model-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("probe build/model manifests are missing or invalid") from exc

    variants = {variant.backend: variant for variant in build.variants}
    if build.source_url != "https://github.com/ggml-org/llama.cpp.git":
        raise ValueError("probe build manifest source is not official llama.cpp")
    if len(build.variants) != 2 or set(variants) != {"generic", "kleidiai"}:
        raise ValueError("probe build manifest is not the exact generic/KleidiAI pair")
    generic = variants["generic"]
    kleidiai = variants["kleidiai"]
    if (
        generic.source_commit != evidence.build_source_commit
        or kleidiai.source_commit != evidence.build_source_commit
        or generic.source_commit != kleidiai.source_commit
    ):
        raise ValueError("probe source commit disagrees with the build manifest")
    if (
        not generic.cpu_only_configured
        or generic.kleidiai_configured
        or not kleidiai.cpu_only_configured
        or not kleidiai.kleidiai_configured
        or not kleidiai.runtime_marker_verified
    ):
        raise ValueError("probe build manifest lacks CPU-only/KleidiAI proof")
    generic_flags = {flag for flag in generic.cmake_flags if "GGML_CPU_KLEIDIAI=" not in flag}
    kleidiai_flags = {flag for flag in kleidiai.cmake_flags if "GGML_CPU_KLEIDIAI=" not in flag}
    if generic_flags != kleidiai_flags:
        raise ValueError("probe build variants differ beyond GGML_CPU_KLEIDIAI")

    caches: dict[str, str] = {}
    for backend, variant in variants.items():
        cache_path = artifacts_dir / f"cmake-{backend}-cache.txt"
        try:
            cache_text = cache_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"probe {backend} CMake cache receipt is missing") from exc
        cache_values = parse_cmake_cache(cache_text)
        expected_kleidiai = "ON" if backend == "kleidiai" else "OFF"
        if cache_values.get("GGML_CPU_KLEIDIAI", "").upper() != expected_kleidiai:
            raise ValueError(f"probe {backend} CMake cache backend flag disagrees")
        caches[backend] = cache_text
        for binary_name in ("llama-bench", "llama-server"):
            recorded_path = variant.binaries.get(binary_name)
            recorded_hash = variant.binary_sha256.get(binary_name)
            if not recorded_path or not re.fullmatch(r"[0-9a-f]{64}", recorded_hash or ""):
                raise ValueError(f"probe {backend} {binary_name} manifest proof is incomplete")
            if require_current_files:
                candidate = (
                    Path(recorded_path)
                    if Path(recorded_path).is_absolute()
                    else project_root / recorded_path
                )
                if "<redacted-home>" in recorded_path:
                    candidate = project_root / "build" / f"llama-{backend}" / "bin" / binary_name
                if not candidate.is_file() or sha256_file(candidate) != recorded_hash:
                    raise ValueError(f"current {backend} {binary_name} hash disagrees")

    relevant_models: dict[str, Any] = {}
    for model_id in ("strong-q4-0", "strong-q8-0"):
        spec = get_model(model_id)
        rows = [row for row in models.models if row.filename == spec.expected_filename]
        if len(rows) != 1:
            raise ValueError(f"probe model manifest lacks exactly one {model_id}")
        row = rows[0]
        expected_fields = {
            "role": spec.role.value,
            "repository": spec.repository,
            "revision": spec.revision,
            "quantization": spec.quantization,
            "sha256": spec.expected_sha256,
            "bytes": spec.expected_bytes,
            "license": spec.license_id,
            "kleidiai_compatible": spec.kleidiai_compatible,
            "tensor_type_histogram": dict(spec.expected_tensor_histogram),
            "tensor_inventory_sha256": spec.expected_tensor_inventory_sha256,
        }
        if any(getattr(row, key) != value for key, value in expected_fields.items()):
            raise ValueError(f"probe model manifest {model_id} disagrees with the registry")
        if [item.model_dump(mode="json") for item in row.reviewed_kleidiai_fallbacks] != [
            item.to_dict() for item in spec.reviewed_kleidiai_fallbacks
        ]:
            raise ValueError(f"probe model manifest {model_id} fallback proof disagrees")
        proof = _reviewed_proof(model_id)
        if evidence.model_inventory_proofs.get(model_id) != proof.to_dict():
            raise ValueError(f"probe inventory proof for {model_id} does not replay")
        relevant_models[model_id] = row
        if require_current_files:
            candidate = project_root / "models" / row.filename
            if (
                not candidate.is_file()
                or candidate.stat().st_size != row.bytes
                or sha256_file(candidate) != row.sha256
            ):
                raise ValueError(f"current probe model file disagrees: {model_id}")
            actual_proof = verify_model_inventory(candidate, spec, actual_sha256=row.sha256)
            if actual_proof.to_dict() != proof.to_dict():
                raise ValueError(f"current probe model inventory disagrees: {model_id}")
    if set(evidence.model_inventory_proofs) != {"strong-q4-0", "strong-q8-0"}:
        raise ValueError("probe artifact has an unexpected model inventory proof")
    return variants, relevant_models, caches


def _replay_request_receipt(
    *,
    path: Path,
    case: Any,
    backend: str,
    parallel: int,
    phase: Literal["warmup", "measured"],
    repetition: int,
    client_index: int,
    record: ServiceRequestProbe | None,
) -> str:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid service request receipt: {path.name}") from exc
    request_id = receipt.get("request_id")
    if (
        receipt.get("schema_version") != PROBE_SCHEMA_VERSION
        or not isinstance(request_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", request_id) is None
        or not path.stem.endswith(request_id)
    ):
        raise ValueError("service request receipt identity/version is invalid")
    request = receipt.get("request")
    response = receipt.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError("service request receipt lacks request/response objects")
    expected_request = {
        "case_id": case.case_id,
        "phase": phase,
        "repetition": repetition,
        "client_index": client_index,
        "model": f"probe-{backend}-p{parallel}",
        "messages": build_messages(case.incident),
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": REAL_BENCHMARK_MAX_TOKENS,
        "seed": REAL_BENCHMARK_SEED,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": triage_openai_response_format(),
    }
    if request != expected_request:
        raise ValueError("service request receipt disagrees with the frozen request")
    content = response.get("content")
    usage = response.get("usage")
    timing = response.get("timing")
    if not isinstance(content, str) or not isinstance(usage, dict) or not isinstance(timing, dict):
        raise ValueError("service response receipt is incomplete")
    score = score_case(case, content)
    if response.get("score") != score.as_dict():
        raise ValueError("service response score does not replay")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    start_ns = timing.get("start_ns")
    first_ns = timing.get("first_content_token_ns")
    end_ns = timing.get("end_ns")
    if type(start_ns) is not int or type(end_ns) is not int or not start_ns < end_ns:
        raise ValueError("service response receipt has invalid start/end counters")
    expected_e2e_ms = (end_ns - start_ns) / 1_000_000
    if not isinstance(timing.get("e2e_ms"), (int, float)) or not math.isclose(
        timing["e2e_ms"], expected_e2e_ms, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError("service response e2e timing does not replay")
    generation_rate = None
    if type(first_ns) is int and start_ns < first_ns < end_ns:
        expected_ttft_ms = (first_ns - start_ns) / 1_000_000
        if not isinstance(timing.get("ttft_ms"), (int, float)) or not math.isclose(
            timing["ttft_ms"], expected_ttft_ms, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("service response TTFT does not replay")
        if type(completion_tokens) is int and completion_tokens > 0:
            generation_rate = completion_tokens / ((end_ns - first_ns) / 1_000_000_000)
    elif first_ns is None:
        if timing.get("ttft_ms") is not None:
            raise ValueError("service response missing-first-token timing is inconsistent")
    else:
        raise ValueError("service response receipt has invalid first-token counter")
    gate_issue_codes = _completion_gate_issue_codes(
        start_ns=start_ns,
        first_content_token_ns=first_ns,
        end_ns=end_ns,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        generation_rate=generation_rate,
        schema_valid=score.schema_valid,
        finish_reason=response.get("finish_reason"),
    )
    expected_validation_context = _validation_context(
        backend=backend,
        parallel=parallel,
        phase=phase,
        repetition=repetition,
        client_index=client_index,
        schema_valid=score.schema_valid,
        safety_score=score.safety_score,
        issue_codes=score.issues,
        gate_issue_codes=gate_issue_codes,
        finish_reason=response.get("finish_reason"),
        response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    if response.get("validation_context") != expected_validation_context:
        raise ValueError("service response validation context does not replay")
    if phase == "measured" and gate_issue_codes:
        raise ValueError("measured service response failed schema/completeness replay")
    if phase == "warmup" and _STREAM_EVIDENCE_GATE_ISSUES.intersection(gate_issue_codes):
        raise ValueError("warmup service response failed streaming-evidence replay")
    if record is None:
        if phase != "warmup":
            raise ValueError("warmup receipt identity is invalid")
        return request_id
    expected_record = {
        "request_id": request_id,
        "start_ns": timing.get("start_ns"),
        "first_content_token_ns": timing.get("first_content_token_ns"),
        "end_ns": timing.get("end_ns"),
        "ttft_ms": timing.get("ttft_ms"),
        "e2e_ms": timing.get("e2e_ms"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "schema_valid": score.schema_valid,
        "safety_score": score.safety_score,
        "quality_score": score.quality_score,
        "issues": list(score.issues),
        "finish_reason": response.get("finish_reason"),
        "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    actual_record = {key: getattr(record, key) for key in expected_record}
    if actual_record != expected_record:
        raise ValueError("service request summary does not replay from its receipt")
    return request_id


def _replay_probe_semantics(
    *,
    evidence: PerformanceProbeEvidence,
    artifacts_dir: Path,
    raw_root: Path,
    project_root: Path,
    require_current_files: bool,
) -> None:
    variants, models, caches = _load_probe_manifests(
        artifacts_dir=artifacts_dir,
        project_root=project_root,
        evidence=evidence,
        require_current_files=require_current_files,
    )
    try:
        cases = load_cases(project_root / "demo" / "cases.jsonl")
        split = load_split(project_root / "demo" / "split.json")
        validate_dataset(cases, split)
    except Exception as exc:
        raise ValueError("probe frozen calibration dataset is unavailable") from exc
    by_id = {case.case_id: case for case in cases}
    case = by_id.get(evidence.case_id)
    if (
        case is None
        or not split.calibration
        or evidence.case_id != split.calibration[0]
        or evidence.prompt_sha256 != prompt_fingerprint()
        or evidence.max_tokens != REAL_BENCHMARK_MAX_TOKENS
        or evidence.seed != REAL_BENCHMARK_SEED
    ):
        raise ValueError("probe artifact disagrees with the frozen prompt/case/settings")

    for backend, help_relative in evidence.micro_help_paths.items():
        help_path = _safe_raw_path(artifacts_dir, raw_root, help_relative, evidence)
        if not inspect_help(
            help_path.read_text(encoding="utf-8", errors="replace")
        ).cpu_only_complete:
            raise ValueError(f"probe {backend} llama-bench help receipt is incomplete")

    q4_proof = _reviewed_proof("strong-q4-0")
    model_by_quant = {"Q4_0": models["strong-q4-0"], "Q8_0": models["strong-q8-0"]}
    for run in evidence.micro_runs:
        stdout = _safe_raw_path(artifacts_dir, raw_root, run.stdout_path, evidence).read_text(
            encoding="utf-8", errors="replace"
        )
        stderr = _safe_raw_path(artifacts_dir, raw_root, run.stderr_path, evidence).read_text(
            encoding="utf-8", errors="replace"
        )
        parsed = require_tests(
            parse_output(stdout),
            prompt_tokens=MICRO_PROMPT_TOKENS,
            generation_tokens=MICRO_GENERATION_TOKENS,
            threads=run.threads,
        )
        replayed_metrics = [
            MicroMetric(
                test=item.test,
                tokens_per_second=item.tokens_per_second,
                tokens_per_second_stddev=float(item.tokens_per_second_stddev),
            )
            for item in parsed
        ]
        if replayed_metrics != run.metrics:
            raise ValueError("micro metrics do not replay exactly from stdout")
        variant = variants[run.backend]
        model = model_by_quant[run.quantization]
        if (
            run.binary_sha256 != variant.binary_sha256.get("llama-bench")
            or not _paths_equivalent(run.command[0], variant.binaries.get("llama-bench", ""))
            or run.model_file_sha256 != model.sha256
            or not _paths_equivalent(run.command[2], str(Path("models") / model.local_path))
        ):
            raise ValueError("micro command/hash disagrees with build/model manifests")
        log_text = "\n".join((stdout, stderr))
        backend_proof = verify_backend_log(
            log_text,
            BuildVariant(run.backend),
            quantization=run.quantization if run.backend == "kleidiai" else None,
            reviewed_model=q4_proof if run.backend == "kleidiai" else None,
        )
        cpu_proof = verify_cpu_only(
            run.command,
            cmake_cache=caches[run.backend],
            runtime_log=log_text,
            require_device_none=True,
        )
        if not backend_proof.verified or not cpu_proof.verified:
            raise ValueError("micro backend/CPU-only proof does not replay")

    for run in evidence.service_runs:
        variant = variants[run.backend]
        model = models["strong-q4-0"]
        if (
            run.binary_sha256 != variant.binary_sha256.get("llama-server")
            or not _paths_equivalent(run.command[0], variant.binaries.get("llama-server", ""))
            or run.model_file_sha256 != model.sha256
            or not _paths_equivalent(
                _command_option(run.command, "--model"), str(Path("models") / model.local_path)
            )
        ):
            raise ValueError("service command/hash disagrees with build/model manifests")
        runtime_log = _safe_raw_path(
            artifacts_dir, raw_root, run.runtime_log_path, evidence
        ).read_text(encoding="utf-8", errors="replace")
        backend_proof = verify_backend_log(
            runtime_log,
            BuildVariant(run.backend),
            quantization="Q4_0" if run.backend == "kleidiai" else None,
            reviewed_model=q4_proof if run.backend == "kleidiai" else None,
        )
        cpu_proof = verify_cpu_only(
            run.command,
            cmake_cache=caches[run.backend],
            runtime_log=runtime_log,
            require_device_none=True,
        )
        if not backend_proof.verified or not cpu_proof.verified:
            raise ValueError("service backend/CPU-only proof does not replay")
        command_receipt = json.loads(
            _safe_raw_path(artifacts_dir, raw_root, run.command_receipt_path, evidence).read_text(
                encoding="utf-8"
            )
        )
        if (
            command_receipt.get("argv") != run.command
            or command_receipt.get("shell") is not False
            or not command_receipt.get("command_proof", {}).get("cpu_only_flags_complete")
        ):
            raise ValueError("service process command receipt does not replay")
        process_receipt = json.loads(
            _safe_raw_path(artifacts_dir, raw_root, run.process_receipt_path, evidence).read_text(
                encoding="utf-8"
            )
        )
        expected_process = {
            "schema_version": PROBE_SCHEMA_VERSION,
            "service_run_id": run.service_run_id,
            "backend": run.backend,
            "parallel": run.parallel,
            "startup_start_ns": run.startup_start_ns,
            "startup_ready_ns": run.startup_ready_ns,
            "command": run.command,
        }
        if any(process_receipt.get(key) != value for key, value in expected_process.items()):
            raise ValueError("service process/startup receipt does not replay")
        if type(process_receipt.get("pid")) is not int or process_receipt["pid"] <= 0:
            raise ValueError("service process receipt has no positive PID")
        idle = process_receipt.get("idle_rss")
        if not isinstance(idle, dict) or idle.get("rss_bytes") != run.idle_rss_bytes:
            raise ValueError("service idle RSS does not replay")
        rss_path = _safe_raw_path(artifacts_dir, raw_root, run.rss_path, evidence)
        with rss_path.open("r", encoding="utf-8", newline="") as handle:
            try:
                rss_values = [int(row["rss_bytes"]) for row in csv.DictReader(handle)]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("service RSS CSV is invalid") from exc
        if not rss_values or run.peak_rss_bytes != max(max(rss_values), run.idle_rss_bytes):
            raise ValueError("service peak RSS does not replay from raw samples")
        _safe_raw_path(artifacts_dir, raw_root, run.stdout_path, evidence)
        _safe_raw_path(artifacts_dir, raw_root, run.stderr_path, evidence)
        warmup_request_ids = []
        for client_index, relative in enumerate(run.warmup_receipt_paths):
            warmup_request_ids.append(
                _replay_request_receipt(
                    path=_safe_raw_path(artifacts_dir, raw_root, relative, evidence),
                    case=case,
                    backend=run.backend,
                    parallel=run.parallel,
                    phase="warmup",
                    repetition=0,
                    client_index=client_index,
                    record=None,
                )
            )
        measured_request_ids = {request.request_id for request in run.requests}
        if (
            len(set(warmup_request_ids)) != run.parallel
            or set(warmup_request_ids) & measured_request_ids
        ):
            raise ValueError("warmup request IDs are duplicate or overlap measured requests")
        for request in run.requests:
            _replay_request_receipt(
                path=_safe_raw_path(artifacts_dir, raw_root, request.receipt_path, evidence),
                case=case,
                backend=run.backend,
                parallel=run.parallel,
                phase="measured",
                repetition=request.repetition,
                client_index=request.client_index,
                record=request,
            )


def load_performance_probes(
    path: Path | str,
    *,
    verify_raw: bool = True,
    project_root: Path | str | None = None,
    require_current_files: bool = False,
) -> PerformanceProbeEvidence:
    """Replay schema, raw hashes, raw semantics, manifests, and optionally current files."""

    source = Path(path)
    try:
        evidence = PerformanceProbeEvidence.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"performance-probe evidence is invalid: {source}") from exc
    if not verify_raw:
        return evidence
    artifacts_dir = source.parent.resolve()
    raw_root = (artifacts_dir / evidence.raw_root).resolve()
    try:
        raw_root.relative_to(artifacts_dir)
    except ValueError as exc:
        raise ValueError("performance-probe raw root escapes artifacts") from exc
    actual: dict[str, str] = {}
    for file_path in sorted(raw_root.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.resolve().relative_to(artifacts_dir).as_posix()
        actual[relative] = sha256_file(file_path)
    if actual != evidence.raw_files:
        raise ValueError("performance-probe raw-file integrity does not replay")
    root = Path(project_root).resolve() if project_root is not None else artifacts_dir.parent
    _replay_probe_semantics(
        evidence=evidence,
        artifacts_dir=artifacts_dir,
        raw_root=raw_root,
        project_root=root,
        require_current_files=require_current_files,
    )
    return evidence


def summarize_performance_probes(evidence: PerformanceProbeEvidence) -> dict[str, Any]:
    """Create report-ready summaries without changing the claim surface."""

    micro = []
    for run in sorted(
        evidence.micro_runs,
        key=lambda item: (item.quantization, item.threads, item.backend),
    ):
        for metric in run.metrics:
            micro.append(
                {
                    "backend": run.backend,
                    "quantization": run.quantization,
                    "threads": run.threads,
                    "test": metric.test,
                    "repetitions": run.repetitions,
                    "tokens_per_second": metric.tokens_per_second,
                    "tokens_per_second_stddev": metric.tokens_per_second_stddev,
                }
            )
    service = []
    for run in sorted(evidence.service_runs, key=lambda item: (item.parallel, item.backend)):
        round_rps = [round_.requests_per_second for round_ in run.rounds]
        round_tps = [round_.generated_tokens_per_second for round_ in run.rounds]
        e2e = [request.e2e_ms for request in run.requests]
        ttft = [request.ttft_ms for request in run.requests]
        generation = [request.generation_tok_s for request in run.requests]
        service.append(
            {
                "backend": run.backend,
                "parallel": run.parallel,
                "startup_ms": run.startup_ms,
                "rounds": run.repetitions,
                "requests": len(run.requests),
                "requests_per_second_mean": statistics.fmean(round_rps),
                "requests_per_second_stddev": statistics.stdev(round_rps),
                "generated_tokens_per_second_mean": statistics.fmean(round_tps),
                "generated_tokens_per_second_stddev": statistics.stdev(round_tps),
                "request_generation_tok_s_mean": statistics.fmean(generation),
                "request_generation_tok_s_stddev": statistics.stdev(generation),
                "prompt_tokens_total": sum(request.prompt_tokens for request in run.requests),
                "completion_tokens_total": sum(
                    request.completion_tokens for request in run.requests
                ),
                "ttft_p50_ms": float(np.percentile(ttft, 50)),
                "ttft_p95_ms": float(np.percentile(ttft, 95)),
                "e2e_p50_ms": float(np.percentile(e2e, 50)),
                "e2e_p95_ms": float(np.percentile(e2e, 95)),
                "idle_rss_mb": run.idle_rss_bytes / (1024 * 1024),
                "peak_rss_mb": run.peak_rss_bytes / (1024 * 1024),
                "context_total": run.context_total,
                "context_per_slot": run.context_per_slot,
                "safety_pass_count": run.safety_pass_count,
                "safety_failure_count": run.safety_failure_count,
                "quality_score_mean": run.quality_score_mean,
                "quality_score_min": run.quality_score_min,
            }
        )
    return {
        "status": "measured-supporting-evidence",
        "scope": evidence.evidence_scope,
        "repetitions": evidence.repetitions,
        "micro_threads": evidence.micro_threads,
        "case_id": evidence.case_id,
        "case_split": evidence.case_split,
        "elapsed_seconds": evidence.elapsed_seconds,
        "build_source_commit": evidence.build_source_commit,
        "semantic_sha256": performance_probe_semantic_sha256(evidence),
        "micro_cells": len(evidence.micro_runs),
        "micro_underlying_measurements": (len(evidence.micro_runs) * 2 * evidence.repetitions),
        "service_cells": len(evidence.service_runs),
        "service_rounds": sum(run.repetitions for run in evidence.service_runs),
        "service_requests": sum(len(run.requests) for run in evidence.service_runs),
        "failed_micro_cells": evidence.failed_micro_cells,
        "failed_service_rounds": evidence.failed_service_rounds,
        "measured_service_safety_pass_count": (evidence.measured_service_safety_pass_count),
        "measured_service_safety_failure_count": (evidence.measured_service_safety_failure_count),
        "semantic_replay_verified": True,
        "reproduction_commands": [
            (
                "uv run --frozen --no-editable a64pilot benchmark probes "
                f"--threads {evidence.micro_threads[-1]} "
                f"--repetitions {evidence.repetitions} "
                f"--max-minutes {evidence.max_runtime_minutes:g}"
            ),
            "uv run --frozen --no-editable a64pilot verify-probes --artifacts-dir artifacts",
            (
                "uv run --frozen --no-editable a64pilot verify-probes "
                "--artifacts-dir artifacts-public --manifest-only"
            ),
        ],
        "micro": micro,
        "service": service,
    }


__all__ = [
    "ConcurrencyRoundProbe",
    "MICRO_GENERATION_TOKENS",
    "MICRO_PROMPT_TOKENS",
    "MIN_PROBE_REPETITIONS",
    "MicroMetric",
    "MicroRun",
    "PROBE_SCHEMA_VERSION",
    "PerformanceProbeError",
    "PerformanceProbeEvidence",
    "SERVICE_CONTEXT_PER_SLOT",
    "SERVICE_PARALLEL_VALUES",
    "ServiceRequestProbe",
    "ServiceRun",
    "load_performance_probes",
    "micro_thread_candidates",
    "performance_probe_semantic_sha256",
    "run_performance_probes",
    "run_performance_probes_sync",
    "summarize_performance_probes",
]
