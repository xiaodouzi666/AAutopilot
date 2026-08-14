"""Real Arm64 Linux service benchmark orchestration.

This module has no simulation path. Fixture smoke lives in the API layer and its output is
explicitly ineligible for benchmark claims.
"""

from __future__ import annotations

import asyncio
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import (
    IncidentCase,
    triage_json_schema,
    triage_openai_response_format,
)
from a64pilot.benchmark.quality import (
    aggregate_scores,
    load_cases,
    load_split,
    score_case,
    validate_dataset,
)
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.build.cmake import BuildVariant
from a64pilot.build.verify_backend import (
    BackendVerification,
    verify_backend_log,
    verify_cpu_only,
)
from a64pilot.hardware.detect import assert_arm64_benchmark
from a64pilot.models.checksum import sha256_file
from a64pilot.models.gguf import ModelInventoryProof, verify_model_inventory
from a64pilot.models.registry import default_registry
from a64pilot.provenance import write_json
from a64pilot.runtime.llama_command import LlamaServerConfig, inspect_llama_server_capabilities
from a64pilot.runtime.openai_client import OpenAIClient
from a64pilot.runtime.process_manager import LlamaServerProcess, find_available_port
from a64pilot.schemas import BenchmarkRecord, BuildManifest
from a64pilot.settings import BENCHMARK_MAX_OUTPUT_TOKENS


class BenchmarkEnvironmentError(RuntimeError):
    pass


REAL_BENCHMARK_MAX_TOKENS: Final[int] = BENCHMARK_MAX_OUTPUT_TOKENS


def _wait_for_kleidiai_load_proof(
    manager: LlamaServerProcess,
    *,
    quantization: str,
    reviewed_model: ModelInventoryProof,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> tuple[str, BackendVerification]:
    """Wait briefly for the async logger to flush both required load markers."""

    if timeout_s < 0 or interval_s <= 0:
        raise ValueError("load-proof timeout must be non-negative and interval positive")
    deadline = time.monotonic() + timeout_s
    while True:
        log_text = manager.log_text()
        proof = verify_backend_log(
            log_text,
            BuildVariant.KLEIDIAI,
            quantization=quantization,
            reviewed_model=reviewed_model,
        )
        if proof.verified or time.monotonic() >= deadline:
            return log_text, proof
        time.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    candidate_id: str
    stage: str
    backend: str
    binary: Path
    cmake_cache: Path
    model: Path
    model_role: str
    quantization: str
    threads: int
    batch: int = 256
    ubatch: int = 128
    parallel: int = 1
    context: int = 2048
    affinity: tuple[int, ...] | None = None


def _validate_environment(candidate: RuntimeCandidate) -> None:
    assert_arm64_benchmark()
    if platform.system().lower() != "linux":
        raise BenchmarkEnvironmentError("final service benchmarks require Arm64 Linux")
    for path in (candidate.binary, candidate.cmake_cache, candidate.model):
        if not path.is_file():
            raise BenchmarkEnvironmentError(f"missing benchmark input: {path}")


def _stage_value(stage: str) -> str:
    aliases = {
        "a0": "reference",
        "a1": "baseline",
        "a2": "kleidiai",
        "a3": "tuned",
        "a4": "cascade",
    }
    value = aliases.get(stage.lower(), stage.lower())
    allowed = {"reference", "baseline", "quant", "kleidiai", "tuned", "cascade"}
    if value not in allowed:
        raise ValueError(f"unknown benchmark stage: {stage}")
    return value


class RealServiceBenchmark:
    def __init__(
        self,
        *,
        artifacts_dir: Path | str = "artifacts",
        build_manifest_path: Path | str = "artifacts/build-manifest.json",
        cases_path: Path | str = "demo/cases.jsonl",
        split_path: Path | str = "demo/split.json",
        seed: int = 20260813,
        max_tokens: int = REAL_BENCHMARK_MAX_TOKENS,
        startup_timeout_s: float = 240.0,
    ) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.build_manifest_path = Path(build_manifest_path)
        self.store = ArtifactStore(self.artifacts_dir / "raw")
        self.cases_path = Path(cases_path)
        self.split_path = Path(split_path)
        self.cases_sha256 = sha256_file(self.cases_path)
        self.split_sha256 = sha256_file(self.split_path)
        self.cases = load_cases(self.cases_path)
        self.split = load_split(self.split_path)
        validate_dataset(self.cases, self.split)
        self.seed = seed
        self.max_tokens = max_tokens
        self.startup_timeout_s = startup_timeout_s

    def selected_cases(self, split: str, limit: int | None = None) -> tuple[IncidentCase, ...]:
        ids = self.split.calibration if split == "calibration" else self.split.test
        # The frozen manifest order is part of the benchmark protocol.  In particular,
        # bounded calibration intentionally takes a prefix of that manifest; filtering the
        # dataset in file order would silently evaluate a different prefix after a split
        # amendment.
        cases_by_id = {case.case_id: case for case in self.cases}
        selected = tuple(cases_by_id[case_id] for case_id in ids)
        return selected[:limit] if limit is not None else selected

    async def run_candidate(
        self,
        candidate: RuntimeCandidate,
        *,
        split: str = "test",
        repetitions: int = 1,
        limit: int | None = None,
        warmups: int = 1,
    ) -> list[BenchmarkRecord]:
        _validate_environment(candidate)
        capabilities = inspect_llama_server_capabilities(candidate.binary)
        port = find_available_port(18080 if candidate.backend == "generic" else 18180)
        config = LlamaServerConfig(
            binary=candidate.binary,
            model=candidate.model,
            host="127.0.0.1",
            port=port,
            model_alias=candidate.candidate_id,
            threads=candidate.threads,
            batch_size=candidate.batch,
            ubatch_size=candidate.ubatch,
            context_size=candidate.context,
            parallel=candidate.parallel,
            seed=self.seed,
            cpu_only=True,
            affinity=candidate.affinity,
        )
        manager = LlamaServerProcess(
            config,
            capabilities=capabilities,
            log_dir=self.artifacts_dir / "runtime",
            startup_timeout_s=self.startup_timeout_s,
        )
        model_hash = sha256_file(candidate.model)
        reviewed_model: ModelInventoryProof | None = None
        if candidate.backend == "kleidiai":
            matching_specs = [
                spec
                for spec in default_registry()
                if spec.expected_sha256 == model_hash
                and spec.expected_filename == candidate.model.name
                and spec.quantization == candidate.quantization
            ]
            if len(matching_specs) != 1:
                raise BenchmarkEnvironmentError(
                    "KleidiAI benchmark model is not the exact reviewed registry artifact"
                )
            reviewed_model = verify_model_inventory(
                candidate.model,
                matching_specs[0],
                actual_sha256=model_hash,
            )
            if not reviewed_model.verified:
                raise BenchmarkEnvironmentError("; ".join(reviewed_model.errors))
        cases = self.selected_cases(split, limit)
        if not cases:
            raise ValueError(f"no cases selected for split {split}")
        output: list[BenchmarkRecord] = []
        scores = []
        runtime_marker_recorded = False
        response_format = triage_openai_response_format()
        try:
            manager.start()
            expected = (
                BuildVariant.KLEIDIAI if candidate.backend == "kleidiai" else BuildVariant.GENERIC
            )
            if candidate.backend == "kleidiai":
                assert reviewed_model is not None
                log_text, backend_proof = _wait_for_kleidiai_load_proof(
                    manager,
                    quantization=candidate.quantization,
                    reviewed_model=reviewed_model,
                )
            else:
                log_text = manager.log_text()
                backend_proof = verify_backend_log(log_text, expected)
            cache_text = candidate.cmake_cache.read_text(encoding="utf-8", errors="replace")
            cpu_proof = verify_cpu_only(
                manager.command,
                cmake_cache=cache_text,
                runtime_log=log_text,
                require_device_none=True,
            )
            if not backend_proof.verified:
                raise BenchmarkEnvironmentError("; ".join(backend_proof.errors))
            if not cpu_proof.verified:
                raise BenchmarkEnvironmentError("; ".join(cpu_proof.errors))
            async with OpenAIClient(f"http://127.0.0.1:{port}", timeout_s=240.0) as client:
                for case in cases[:warmups]:
                    await client.chat_completion(
                        messages=build_messages(case.incident),
                        model=candidate.candidate_id,
                        temperature=0.0,
                        top_p=1.0,
                        max_tokens=self.max_tokens,
                        seed=self.seed,
                        stream=True,
                        response_format=response_format,
                    )
                    if candidate.backend == "kleidiai" and not runtime_marker_recorded:
                        post_request_log = manager.log_text()
                        post_request_backend = verify_backend_log(
                            post_request_log,
                            expected,
                            quantization=candidate.quantization,
                            reviewed_model=reviewed_model,
                        )
                        post_request_cpu = verify_cpu_only(
                            manager.command,
                            cmake_cache=cache_text,
                            runtime_log=post_request_log,
                            require_device_none=True,
                        )
                        if not post_request_backend.verified:
                            raise BenchmarkEnvironmentError("; ".join(post_request_backend.errors))
                        if not post_request_cpu.verified:
                            raise BenchmarkEnvironmentError("; ".join(post_request_cpu.errors))
                        _mark_kleidiai_runtime_verified(self.build_manifest_path)
                        runtime_marker_recorded = True
                for repetition in range(repetitions):
                    for case in cases:
                        messages = build_messages(case.incident)
                        completion = await client.chat_completion(
                            messages=messages,
                            model=candidate.candidate_id,
                            temperature=0.0,
                            top_p=1.0,
                            max_tokens=self.max_tokens,
                            seed=self.seed,
                            stream=True,
                            response_format=response_format,
                        )
                        score = score_case(case, completion.text)
                        scores.append(score)
                        timing = completion.timing
                        completion_tokens = completion.completion_tokens or 0
                        generation_rate = completion.generation_tokens_per_second
                        run_id = uuid4().hex
                        run_log_text = manager.log_text()
                        run_backend_proof = verify_backend_log(
                            run_log_text,
                            expected,
                            quantization=(
                                candidate.quantization if candidate.backend == "kleidiai" else None
                            ),
                            reviewed_model=reviewed_model,
                        )
                        run_cpu_proof = verify_cpu_only(
                            manager.command,
                            cmake_cache=cache_text,
                            runtime_log=run_log_text,
                            require_device_none=True,
                        )
                        if not run_backend_proof.verified:
                            raise BenchmarkEnvironmentError("; ".join(run_backend_proof.errors))
                        if not run_cpu_proof.verified:
                            raise BenchmarkEnvironmentError("; ".join(run_cpu_proof.errors))
                        if candidate.backend == "kleidiai" and not runtime_marker_recorded:
                            _mark_kleidiai_runtime_verified(self.build_manifest_path)
                            runtime_marker_recorded = True
                        record = BenchmarkRecord(
                            run_id=run_id,
                            candidate_id=candidate.candidate_id,
                            stage=_stage_value(candidate.stage),
                            case_id=case.case_id,
                            repetition=repetition,
                            split=split,
                            backend=candidate.backend,
                            model_role=candidate.model_role,
                            model_file_sha256=model_hash,
                            quantization=candidate.quantization,
                            threads=candidate.threads,
                            batch=candidate.batch,
                            ubatch=candidate.ubatch,
                            parallel=candidate.parallel,
                            context=candidate.context,
                            affinity=list(candidate.affinity or ()),
                            cpu_only_verified=run_cpu_proof.verified,
                            kleidiai_verified=run_backend_proof.verified
                            if candidate.backend == "kleidiai"
                            else False,
                            start_ns=timing.start_ns,
                            first_token_ns=timing.first_content_token_ns,
                            end_ns=timing.end_ns,
                            ttft_ms=timing.ttft_ms,
                            e2e_ms=timing.e2e_ms,
                            prompt_tokens=int(completion.usage.get("prompt_tokens", 0)),
                            completion_tokens=completion_tokens,
                            generation_tok_s=generation_rate,
                            peak_rss_mb=manager.peak_rss_bytes / (1024 * 1024),
                            route="weak" if candidate.model_role == "weak" else "strong",
                            schema_valid=score.schema_valid,
                            quality_score=score.quality_score,
                            safety_score=score.safety_score,
                            command=list(manager.command),
                            errors=list(score.issues),
                        )
                        self.store.append_record(record)
                        self.store.write_metadata(
                            run_id,
                            "run-config.json",
                            {
                                "candidate": asdict(candidate),
                                "dataset": {
                                    "cases_sha256": self.cases_sha256,
                                    "split_sha256": self.split_sha256,
                                },
                                "prompt_sha256": prompt_fingerprint(),
                                "triage_schema": triage_json_schema(),
                                "backend_proof": run_backend_proof.to_dict(),
                                "cpu_only_proof": run_cpu_proof.to_dict(),
                            },
                        )
                        self.store.write_metadata(
                            run_id,
                            "runtime-proof.txt",
                            run_log_text,
                        )
                        self.store.write_metadata(
                            run_id,
                            "request.json",
                            {
                                "case_id": case.case_id,
                                "repetition": repetition,
                                "messages": messages,
                                "model": candidate.candidate_id,
                                "temperature": 0.0,
                                "top_p": 1.0,
                                "max_tokens": self.max_tokens,
                                "seed": self.seed,
                                "stream": True,
                                "response_format": response_format,
                            },
                        )
                        self.store.write_metadata(
                            run_id,
                            "response.json",
                            {
                                "content": completion.text,
                                "usage": dict(completion.usage),
                                "finish_reason": completion.payload.get("choices", [{}])[0].get(
                                    "finish_reason"
                                ),
                                "timing": {
                                    "start_ns": timing.start_ns,
                                    "first_content_token_ns": timing.first_content_token_ns,
                                    "end_ns": timing.end_ns,
                                    "ttft_ms": timing.ttft_ms,
                                    "e2e_ms": timing.e2e_ms,
                                },
                                "score": score.as_dict(),
                            },
                        )
                        self.store.finalize(run_id)
                        output.append(record)
        finally:
            manager.stop()
        summary = aggregate_scores(scores)
        write_json(
            self.artifacts_dir / f"quality-{candidate.candidate_id}.json",
            {
                "candidate_id": candidate.candidate_id,
                "split": split,
                "summary": summary.as_dict(),
                "prompt_sha256": prompt_fingerprint(),
            },
        )
        return output


def run_candidate_sync(
    benchmark: RealServiceBenchmark,
    candidate: RuntimeCandidate,
    **options: object,
) -> list[BenchmarkRecord]:
    return asyncio.run(benchmark.run_candidate(candidate, **options))


def _mark_kleidiai_runtime_verified(manifest_path: Path) -> None:
    """Record a marker only after the live log verifier has accepted it.

    The strict evidence gate independently replays verification from every raw
    runtime proof.  This manifest bit is therefore a convenient summary, not a
    substitute for the append-only proof files.
    """

    if not manifest_path.is_file():
        raise BenchmarkEnvironmentError(f"missing build manifest: {manifest_path}")
    try:
        manifest = BuildManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkEnvironmentError("build manifest is invalid") from exc
    variants = []
    found = False
    for variant in manifest.variants:
        if variant.backend == "kleidiai":
            found = True
            variants.append(variant.model_copy(update={"runtime_marker_verified": True}))
        else:
            variants.append(variant)
    if not found:
        raise BenchmarkEnvironmentError("build manifest has no KleidiAI variant")
    write_json(manifest_path, manifest.model_copy(update={"variants": variants}))


def discover_binary(build_dir: Path | str, name: str = "llama-server") -> Path:
    root = Path(build_dir)
    for candidate in (root / "bin" / name, root / name):
        if candidate.is_file():
            return candidate
    raise BenchmarkEnvironmentError(f"cannot find {name} under {root}")


def copy_runtime_logs_for_public_review(artifacts_dir: Path | str = "artifacts") -> None:
    """Keep logs local by default; public copies are created only after redaction."""

    root = Path(artifacts_dir)
    destination = root / "proof"
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted((root / "runtime").glob("*.log")):
        text = source.read_text(encoding="utf-8", errors="replace")
        from a64pilot.hardware.detect import redact_text

        (destination / source.name).write_text(redact_text(text), encoding="utf-8")
