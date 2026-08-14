"""Measured A4 calibration, immutable freeze, and held-out quality evaluation.

The component responses collected here come from the real Arm64 ``llama-server``
benchmark runner.  They live outside ``artifacts/raw`` because replaying both tiers
for every case is quality-calibration evidence, not a measurement of live cascade
latency.  A future live A4 performance run may use the admitted policy, but these
rows must never be presented as that performance result.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from a64pilot.agent.prompt import build_messages, prompt_fingerprint
from a64pilot.agent.schema import IncidentCase, triage_json_schema, triage_openai_response_format
from a64pilot.benchmark.quality import (
    FrozenRoutingPolicy,
    QualityGateConfig,
    aggregate_scores,
    calibrate_threshold,
    evaluate_frozen_policy,
    evaluate_quality_gate,
    load_cases,
    load_split,
    score_case,
    stable_file_sha256,
    validate_dataset,
)
from a64pilot.benchmark.runner import (
    REAL_BENCHMARK_MAX_TOKENS,
    REAL_BENCHMARK_SEED,
    RealServiceBenchmark,
    RuntimeCandidate,
    _reviewed_model_proof,
    run_candidate_sync,
)
from a64pilot.benchmark.store import ArtifactStore
from a64pilot.build.verify_backend import verify_backend_log, verify_cpu_only
from a64pilot.models.checksum import sha256_file
from a64pilot.provenance import write_json
from a64pilot.schemas import BenchmarkRecord

CASCADE_FREEZE_SCHEMA_VERSION: Final[str] = "1.0"
DEFAULT_THRESHOLDS: Final[tuple[float, ...]] = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)


class CascadeWorkflowError(RuntimeError):
    """Raised when A4 evidence is incomplete, mutable, or otherwise inadmissible."""


@dataclass(frozen=True, slots=True)
class CascadeRuntimePlan:
    binary: Path
    cmake_cache: Path
    weak_model: Path
    strong_model: Path
    threads: int
    batch: int
    ubatch: int
    parallel: int
    context: int
    affinity: tuple[int, ...] | None = None
    weak_quantization: str = "Q4_0"
    strong_quantization: str = "Q4_0"

    def __post_init__(self) -> None:
        for name in ("threads", "batch", "ubatch", "parallel", "context"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.ubatch > self.batch:
            raise ValueError("ubatch must not exceed batch")

    def as_dict(self) -> dict[str, Any]:
        return {
            "binary": str(self.binary),
            "cmake_cache": str(self.cmake_cache),
            "weak_model": str(self.weak_model),
            "strong_model": str(self.strong_model),
            "threads": self.threads,
            "batch": self.batch,
            "ubatch": self.ubatch,
            "parallel": self.parallel,
            "context": self.context,
            "affinity": list(self.affinity or ()),
            "weak_quantization": self.weak_quantization,
            "strong_quantization": self.strong_quantization,
        }

    @classmethod
    def from_dict(cls, value: object) -> CascadeRuntimePlan:
        if not isinstance(value, dict):
            raise CascadeWorkflowError("frozen runtime plan must be a mapping")
        try:
            affinity = value.get("affinity")
            if not isinstance(affinity, list) or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in affinity
            ):
                raise ValueError("affinity must be a list of non-negative integers")
            return cls(
                binary=Path(str(value["binary"])),
                cmake_cache=Path(str(value["cmake_cache"])),
                weak_model=Path(str(value["weak_model"])),
                strong_model=Path(str(value["strong_model"])),
                threads=int(value["threads"]),
                batch=int(value["batch"]),
                ubatch=int(value["ubatch"]),
                parallel=int(value["parallel"]),
                context=int(value["context"]),
                affinity=tuple(affinity) or None,
                weak_quantization=str(value["weak_quantization"]),
                strong_quantization=str(value["strong_quantization"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CascadeWorkflowError(f"invalid frozen runtime plan: {exc}") from exc


@dataclass(frozen=True, slots=True)
class MeasuredOutputCollection:
    outputs: dict[str, Any]
    source_run_ids: tuple[str, ...]
    model_file_sha256: str


@dataclass(frozen=True, slots=True)
class CascadeComponentEvidence:
    session_dir: Path
    weak: MeasuredOutputCollection | None
    strong: MeasuredOutputCollection


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _gate_dict(config: QualityGateConfig) -> dict[str, Any]:
    return asdict(config)


def _gate_from_dict(value: object) -> QualityGateConfig:
    if not isinstance(value, dict):
        raise CascadeWorkflowError("frozen quality gate must be a mapping")
    try:
        return QualityGateConfig(**value)
    except (TypeError, ValueError) as exc:
        raise CascadeWorkflowError(f"invalid frozen quality gate: {exc}") from exc


def _candidate(
    plan: CascadeRuntimePlan,
    *,
    role: str,
    phase: str,
) -> RuntimeCandidate:
    if role not in {"weak", "strong"}:
        raise ValueError("cascade component role must be weak or strong")
    return RuntimeCandidate(
        candidate_id=f"a4-{phase}-{role}",
        stage="a4",
        backend="kleidiai",
        binary=plan.binary,
        cmake_cache=plan.cmake_cache,
        model=plan.weak_model if role == "weak" else plan.strong_model,
        model_role=role,
        quantization=(plan.weak_quantization if role == "weak" else plan.strong_quantization),
        threads=plan.threads,
        batch=plan.batch,
        ubatch=plan.ubatch,
        parallel=plan.parallel,
        context=plan.context,
        affinity=plan.affinity,
    )


def _expected_component_command(candidate: RuntimeCandidate, port: str) -> list[str]:
    return [
        str(candidate.binary),
        "--model",
        str(candidate.model),
        "--alias",
        candidate.candidate_id,
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--threads",
        str(candidate.threads),
        "--batch-size",
        str(candidate.batch),
        "--ubatch-size",
        str(candidate.ubatch),
        "--ctx-size",
        str(candidate.context),
        "--parallel",
        str(candidate.parallel),
        "--seed",
        str(REAL_BENCHMARK_SEED),
        "-lv",
        "5",
        "--device",
        "none",
        "--n-gpu-layers",
        "0",
    ]


def _verify_component_command(record: BenchmarkRecord, candidate: RuntimeCandidate) -> None:
    command = record.command
    try:
        port_index = command.index("--port")
        port = command[port_index + 1]
        port_number = int(port)
    except (ValueError, IndexError) as exc:
        raise CascadeWorkflowError(f"A4 row {record.run_id} has no valid server port") from exc
    if not 1 <= port_number <= 65535:
        raise CascadeWorkflowError(f"A4 row {record.run_id} has no valid server port")
    expected = _expected_component_command(candidate, port)
    optional_suffixes = ([], ["--metrics"], ["--no-webui"], ["--metrics", "--no-webui"])
    if command[: len(expected)] != expected or command[len(expected) :] not in optional_suffixes:
        raise CascadeWorkflowError(
            f"A4 row {record.run_id} command does not replay the frozen runtime plan"
        )


def _load_measured_outputs(
    store: ArtifactStore,
    records: list[BenchmarkRecord],
    *,
    expected_case_ids: tuple[str, ...],
    expected_split: str,
    expected_role: str,
    expected_candidate: RuntimeCandidate,
    expected_dataset: dict[str, str],
    expected_cases: dict[str, IncidentCase],
) -> MeasuredOutputCollection:
    expected = set(expected_case_ids)
    if set(expected_cases) != expected:
        raise CascadeWorkflowError(f"{expected_role} expected-case payload is incomplete")
    if len(records) != len(expected_case_ids):
        raise CascadeWorkflowError(
            f"{expected_role} component produced {len(records)} rows; "
            f"expected {len(expected_case_ids)}"
        )
    outputs: dict[str, Any] = {}
    run_ids: list[str] = []
    model_hashes: set[str] = set()
    expected_model_hash = sha256_file(expected_candidate.model)
    try:
        reviewed_model = _reviewed_model_proof(expected_candidate, expected_model_hash)
        cmake_cache = expected_candidate.cmake_cache.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        raise CascadeWorkflowError(
            f"{expected_role} model/build proof does not match the reviewed runtime"
        ) from exc
    expected_candidate_payload = json.loads(
        json.dumps(asdict(expected_candidate), default=str, sort_keys=True)
    )
    for record in records:
        if not re.fullmatch(r"[0-9a-f]{32}", record.run_id):
            raise CascadeWorkflowError(f"{expected_role} component has an invalid run ID")
        if record.case_id not in expected:
            raise CascadeWorkflowError(
                f"{expected_role} component contains unexpected case {record.case_id}"
            )
        if record.case_id in outputs:
            raise CascadeWorkflowError(f"{expected_role} component repeats case {record.case_id}")
        if (
            record.evidence_kind != "measured"
            or record.split != expected_split
            or record.model_role != expected_role
            or record.stage != "cascade"
            or record.backend != "kleidiai"
            or record.repetition != 0
            or record.route != expected_role
            or not record.cpu_only_verified
            or not record.kleidiai_verified
        ):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} is not verified A4 component evidence"
            )
        expected_record_fields = {
            "candidate_id": expected_candidate.candidate_id,
            "backend": expected_candidate.backend,
            "model_role": expected_candidate.model_role,
            "quantization": expected_candidate.quantization,
            "threads": expected_candidate.threads,
            "batch": expected_candidate.batch,
            "ubatch": expected_candidate.ubatch,
            "parallel": expected_candidate.parallel,
            "context": expected_candidate.context,
            "affinity": list(expected_candidate.affinity or ()),
            "model_file_sha256": expected_model_hash,
        }
        for field_name, expected_value in expected_record_fields.items():
            if getattr(record, field_name) != expected_value:
                raise CascadeWorkflowError(
                    f"{expected_role} row {record.run_id} disagrees with the runtime plan on "
                    f"{field_name}"
                )
        integrity_errors = store.verify(record.run_id)
        if integrity_errors:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} failed integrity: "
                + "; ".join(integrity_errors)
            )
        run_dir = store.root / record.run_id
        try:
            run_config = json.loads((run_dir / "run-config.json").read_text(encoding="utf-8"))
            request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
            response = json.loads((run_dir / "response.json").read_text(encoding="utf-8"))
            runtime_log = (run_dir / "runtime-proof.txt").read_text(
                encoding="utf-8", errors="replace"
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} has unreadable nested provenance"
            ) from exc
        if not isinstance(run_config, dict) or run_config.get("candidate") != (
            expected_candidate_payload
        ):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} run-config disagrees with the runtime plan"
            )
        if run_config.get("dataset") != expected_dataset:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} dataset hashes disagree with the freeze"
            )
        if (
            run_config.get("prompt_sha256") != prompt_fingerprint()
            or run_config.get("triage_schema") != triage_json_schema()
        ):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} prompt/schema fingerprint does not replay"
            )
        _verify_component_command(record, expected_candidate)
        backend_proof = verify_backend_log(
            runtime_log,
            "kleidiai",
            quantization=expected_candidate.quantization,
            reviewed_model=reviewed_model,
        )
        cpu_proof = verify_cpu_only(
            record.command,
            cmake_cache=cmake_cache,
            runtime_log=runtime_log,
            require_device_none=True,
        )
        if not backend_proof.verified or not cpu_proof.verified:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} runtime CPU/KleidiAI proof does not replay"
            )
        if (
            record.kleidiai_verified is not backend_proof.verified
            or record.cpu_only_verified is not cpu_proof.verified
            or run_config.get("backend_proof") != backend_proof.to_dict()
            or run_config.get("cpu_only_proof") != cpu_proof.to_dict()
        ):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} stored runtime proof disagrees with replay"
            )
        case = expected_cases[record.case_id]
        expected_request = {
            "case_id": record.case_id,
            "repetition": 0,
            "messages": build_messages(case.incident),
            "model": expected_candidate.candidate_id,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": REAL_BENCHMARK_MAX_TOKENS,
            "seed": REAL_BENCHMARK_SEED,
            "stream": True,
            "response_format": triage_openai_response_format(),
        }
        if request != expected_request:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} request does not match the frozen prompt"
            )
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} response content is not text"
            )
        replayed_score = score_case(case, content)
        if (
            replayed_score.schema_valid is not record.schema_valid
            or abs(replayed_score.quality_score - record.quality_score) > 1e-6
            or abs(replayed_score.safety_score - record.safety_score) > 1e-6
            or list(replayed_score.issues) != record.errors
            or response.get("score") != replayed_score.as_dict()
        ):
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} response score does not replay"
            )
        timing = response.get("timing")
        expected_timing = {
            "start_ns": record.start_ns,
            "first_content_token_ns": record.first_token_ns,
            "end_ns": record.end_ns,
            "ttft_ms": record.ttft_ms,
            "e2e_ms": record.e2e_ms,
        }
        if timing != expected_timing:
            raise CascadeWorkflowError(
                f"{expected_role} row {record.run_id} response timing does not replay"
            )
        outputs[record.case_id] = content
        run_ids.append(record.run_id)
        model_hashes.add(record.model_file_sha256)
    missing = [case_id for case_id in expected_case_ids if case_id not in outputs]
    if missing:
        raise CascadeWorkflowError(
            f"{expected_role} component is missing cases: {', '.join(missing)}"
        )
    if len(model_hashes) != 1:
        raise CascadeWorkflowError(f"{expected_role} component used multiple model files")
    return MeasuredOutputCollection(
        outputs=outputs,
        source_run_ids=tuple(run_ids),
        model_file_sha256=next(iter(model_hashes)),
    )


def collect_real_component_outputs(
    plan: CascadeRuntimePlan,
    *,
    split: str,
    phase: str,
    include_weak: bool,
    artifacts_dir: Path | str = "artifacts",
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
    build_manifest_path: Path | str = "artifacts/build-manifest.json",
) -> CascadeComponentEvidence:
    """Run verified real model components once per case on the named split."""

    if split not in {"calibration", "test"}:
        raise ValueError("cascade collection split must be calibration or test")
    root = Path(artifacts_dir)
    session_dir = root / "a4" / "runs" / f"{phase}-{uuid4().hex}"
    benchmark = RealServiceBenchmark(
        artifacts_dir=session_dir,
        build_manifest_path=build_manifest_path,
        cases_path=cases_path,
        split_path=split_path,
    )
    expected_ids = (
        tuple(benchmark.split.calibration)
        if split == "calibration"
        else tuple(benchmark.split.test)
    )
    expected_cases = {case.case_id: case for case in benchmark.selected_cases(split)}
    weak_collection = None
    # The final-holdout input must not be replayed as a warmup before it is scored.  Calibration
    # can still use one representative warmup because it is explicitly the decision-making split.
    component_warmups = 0 if split == "test" else 1
    if include_weak:
        weak_candidate = _candidate(plan, role="weak", phase=phase)
        weak_records = run_candidate_sync(
            benchmark,
            weak_candidate,
            split=split,
            repetitions=1,
            warmups=component_warmups,
        )
        weak_collection = _load_measured_outputs(
            benchmark.store,
            weak_records,
            expected_case_ids=expected_ids,
            expected_split=split,
            expected_role="weak",
            expected_candidate=weak_candidate,
            expected_dataset={
                "cases_sha256": benchmark.cases_sha256,
                "split_sha256": benchmark.split_sha256,
            },
            expected_cases=expected_cases,
        )
    strong_candidate = _candidate(plan, role="strong", phase=phase)
    strong_records = run_candidate_sync(
        benchmark,
        strong_candidate,
        split=split,
        repetitions=1,
        warmups=component_warmups,
    )
    strong_collection = _load_measured_outputs(
        benchmark.store,
        strong_records,
        expected_case_ids=expected_ids,
        expected_split=split,
        expected_role="strong",
        expected_candidate=strong_candidate,
        expected_dataset={
            "cases_sha256": benchmark.cases_sha256,
            "split_sha256": benchmark.split_sha256,
        },
        expected_cases=expected_cases,
    )
    return CascadeComponentEvidence(
        session_dir=session_dir,
        weak=weak_collection,
        strong=strong_collection,
    )


def _candidate_dict(value: Any) -> dict[str, Any]:
    return {
        "threshold": value.threshold,
        "summary": value.summary.as_dict(),
        "gate": value.gate.as_dict(),
        "route_counts": dict(value.route_counts),
        "route_shares": dict(value.route_shares),
        "weak_route_share": value.weak_route_share,
        "escalation_rate": value.escalation_rate,
        "escalation_rate_denominator": "weak_attempts",
    }


def freeze_calibration(
    plan: CascadeRuntimePlan,
    evidence: CascadeComponentEvidence,
    *,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    gate_config: QualityGateConfig | None = None,
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
    policy_path: Path | str = "artifacts/a4-frozen-policy.json",
) -> dict[str, Any]:
    """Calibrate on exactly 40 cases and atomically define the held-out policy."""

    destination = Path(policy_path)
    if destination.exists():
        raise CascadeWorkflowError(f"refusing to overwrite existing frozen policy: {destination}")
    if evidence.weak is None:
        raise CascadeWorkflowError("calibration requires measured weak-model outputs")
    cases = load_cases(cases_path)
    split = load_split(split_path)
    validate_dataset(cases, split)
    if len(split.calibration) != 40:
        raise CascadeWorkflowError("A4 calibration requires exactly 40 calibration cases")
    by_id = {case.case_id: case for case in cases}
    calibration_cases = tuple(by_id[case_id] for case_id in split.calibration)
    gate = gate_config or QualityGateConfig()
    result = calibrate_threshold(
        calibration_cases,
        evidence.weak.outputs,
        evidence.strong.outputs,
        thresholds=thresholds,
        gate_config=gate,
    )
    core: dict[str, Any] = {
        "schema_version": CASCADE_FREEZE_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_scope": "measured_quality_calibration_not_live_cascade_performance",
        "dataset": {
            "cases_sha256": stable_file_sha256(cases_path),
            "split_sha256": stable_file_sha256(split_path),
            "calibration_case_ids": list(split.calibration),
            "held_out_case_ids": list(split.test),
        },
        "runtime": plan.as_dict(),
        "threshold_grid": list(thresholds),
        "quality_gate": _gate_dict(gate),
        "policy": {
            "threshold": result.policy.threshold,
            "calibration_case_ids": list(result.policy.calibration_case_ids),
            "policy_id": result.policy.policy_id,
            "fallback_strong_only": result.policy.fallback_strong_only,
        },
        "calibration": {
            "baseline": result.baseline.as_dict(),
            "candidates": [_candidate_dict(candidate) for candidate in result.candidates],
        },
        "source_evidence": {
            "session_dir": str(evidence.session_dir),
            "weak_run_ids": list(evidence.weak.source_run_ids),
            "strong_run_ids": list(evidence.strong.source_run_ids),
            "weak_model_file_sha256": evidence.weak.model_file_sha256,
            "strong_model_file_sha256": evidence.strong.model_file_sha256,
        },
    }
    payload = {**core, "freeze_id": _canonical_sha256(core)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, payload)
    return payload


def load_frozen_calibration(
    policy_path: Path | str = "artifacts/a4-frozen-policy.json",
    *,
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
) -> tuple[dict[str, Any], FrozenRoutingPolicy, CascadeRuntimePlan, QualityGateConfig]:
    source = Path(policy_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CascadeWorkflowError(f"frozen policy is unreadable: {source}") from exc
    if not isinstance(payload, dict):
        raise CascadeWorkflowError("frozen policy root must be a mapping")
    freeze_id = payload.get("freeze_id")
    core = {key: value for key, value in payload.items() if key != "freeze_id"}
    if not isinstance(freeze_id, str) or _canonical_sha256(core) != freeze_id:
        raise CascadeWorkflowError("frozen policy hash does not match its contents")
    if payload.get("schema_version") != CASCADE_FREEZE_SCHEMA_VERSION:
        raise CascadeWorkflowError("unsupported frozen policy schema version")
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        raise CascadeWorkflowError("frozen policy dataset binding is missing")
    cases = load_cases(cases_path)
    split = load_split(split_path)
    validate_dataset(cases, split)
    expected_dataset = {
        "cases_sha256": stable_file_sha256(cases_path),
        "split_sha256": stable_file_sha256(split_path),
        "calibration_case_ids": list(split.calibration),
        "held_out_case_ids": list(split.test),
    }
    if dataset != expected_dataset:
        raise CascadeWorkflowError("frozen policy does not match the current cases and split")
    policy_value = payload.get("policy")
    if not isinstance(policy_value, dict):
        raise CascadeWorkflowError("frozen routing policy is missing")
    try:
        policy = FrozenRoutingPolicy(
            threshold=(
                None if policy_value.get("threshold") is None else float(policy_value["threshold"])
            ),
            calibration_case_ids=tuple(policy_value["calibration_case_ids"]),
            policy_id=str(policy_value["policy_id"]),
            fallback_strong_only=bool(policy_value["fallback_strong_only"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CascadeWorkflowError(f"invalid frozen routing policy: {exc}") from exc
    if policy.calibration_case_ids != tuple(split.calibration):
        raise CascadeWorkflowError("frozen policy calibration IDs are not the fixed split")
    return (
        payload,
        policy,
        CascadeRuntimePlan.from_dict(payload.get("runtime")),
        _gate_from_dict(payload.get("quality_gate")),
    )


def _status_and_reason(
    policy: FrozenRoutingPolicy, *, held_out_gate_passed: bool
) -> tuple[str, str]:
    if policy.fallback_strong_only:
        return (
            "calibration-fallback-strong-only",
            "no calibration threshold passed the frozen quality gate",
        )
    if not held_out_gate_passed:
        return (
            "held-out-rejected",
            "frozen A4 policy failed the held-out quality or safety gate",
        )
    return (
        "held-out-quality-accepted",
        (
            "frozen A4 routing policy passed the quality and safety replay; the submitted "
            "deployment remains A3 strong-only pending a live cascade performance run"
        ),
    )


def _build_held_out_payloads(
    frozen: dict[str, Any],
    policy: FrozenRoutingPolicy,
    gate_config: QualityGateConfig,
    held_out_cases: tuple[Any, ...],
    evidence: CascadeComponentEvidence,
) -> tuple[dict[str, Any], dict[str, Any]]:
    weak_outputs = evidence.weak.outputs if evidence.weak is not None else {}
    evaluated = evaluate_frozen_policy(
        held_out_cases,
        weak_outputs,
        evidence.strong.outputs,
        policy,
    )
    baseline = aggregate_scores(
        score_case(case, evidence.strong.outputs[case.case_id]) for case in held_out_cases
    )
    held_out_gate = evaluate_quality_gate(
        evaluated.summary,
        baseline.quality_score,
        config=gate_config,
    )
    admitted = not policy.fallback_strong_only and held_out_gate.passed
    status, reason = _status_and_reason(policy, held_out_gate_passed=held_out_gate.passed)
    held_out_payload = {
        "case_count": len(held_out_cases),
        "policy_id": evaluated.policy_id,
        "baseline": baseline.as_dict(),
        "cascade": evaluated.summary.as_dict(),
        "gate": held_out_gate.as_dict(),
        "route_counts": dict(evaluated.route_counts),
        "route_shares": dict(evaluated.route_shares),
        "weak_route_share": evaluated.weak_route_share,
        "escalation_rate": evaluated.escalation_rate,
        "escalation_rate_denominator": "weak_attempts",
        "source_evidence": {
            "session_dir": str(evidence.session_dir),
            "weak_run_ids": (
                list(evidence.weak.source_run_ids) if evidence.weak is not None else []
            ),
            "strong_run_ids": list(evidence.strong.source_run_ids),
        },
        "performance_claim_eligible": False,
        "evaluation_design": (
            "frozen-policy quality replay on split-v2; split-v2 was already used by the "
            "published A0-A3 run and is not claimed as a newly unseen confirmatory set for A4"
        ),
        "performance_note": (
            "Both tiers were replayed for objective quality scoring; this is not live cascade "
            "latency, throughput, or combined-RSS evidence."
        ),
    }
    # Passing this component-output quality gate admits A4 only as an experiment. The submitted
    # deployment remains A3 until a live two-runtime route proves performance and combined RSS.
    shipping_profile = "a3-strong-only"
    result_payload = {
        "schema_version": CASCADE_FREEZE_SCHEMA_VERSION,
        "freeze_id": frozen["freeze_id"],
        "calibration": frozen["calibration"],
        "held_out": held_out_payload,
        "a4_admitted_by_quality_gate": admitted,
        "a4_quality_candidate": "admitted" if admitted else "rejected",
        "shipping_profile": shipping_profile,
    }
    status_payload = {
        "status": status,
        "reason": reason,
        "freeze_id": frozen["freeze_id"],
        "policy_id": evaluated.policy_id,
        "a4_admitted_by_quality_gate": admitted,
        "shipping_profile": shipping_profile,
        "route_counts": dict(evaluated.route_counts),
        "route_shares": dict(evaluated.route_shares),
        "weak_route_share": evaluated.weak_route_share,
        "escalation_rate": evaluated.escalation_rate,
        "held_out_gate": held_out_gate.as_dict(),
        "performance_claim_eligible": False,
    }
    return result_payload, status_payload


def _status_from_recorded_result(
    frozen: dict[str, Any], policy: FrozenRoutingPolicy, results: dict[str, Any]
) -> dict[str, Any]:
    if results.get("schema_version") != CASCADE_FREEZE_SCHEMA_VERSION:
        raise CascadeWorkflowError("existing A4 quality results use an unsupported schema")
    if results.get("freeze_id") != frozen.get("freeze_id"):
        raise CascadeWorkflowError("existing A4 quality results disagree with the frozen policy")
    if results.get("shipping_profile") != "a3-strong-only":
        raise CascadeWorkflowError("existing A4 quality results do not retain A3 strong-only")
    held_out = results.get("held_out")
    if not isinstance(held_out, dict):
        raise CascadeWorkflowError("existing A4 quality results have no held-out payload")
    gate = held_out.get("gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("passed"), bool):
        raise CascadeWorkflowError("existing A4 quality results have no valid held-out gate")
    if held_out.get("policy_id") != policy.policy_id:
        raise CascadeWorkflowError("existing A4 quality results disagree on policy_id")
    if held_out.get("performance_claim_eligible") is not False:
        raise CascadeWorkflowError(
            "existing A4 quality results make an ineligible performance claim"
        )
    admitted = not policy.fallback_strong_only and gate["passed"]
    if results.get("a4_admitted_by_quality_gate") is not admitted or results.get(
        "a4_quality_candidate"
    ) != ("admitted" if admitted else "rejected"):
        raise CascadeWorkflowError("existing A4 quality admission fields disagree with its gate")
    route_counts = held_out.get("route_counts")
    route_shares = held_out.get("route_shares")
    if not isinstance(route_counts, dict) or not isinstance(route_shares, dict):
        raise CascadeWorkflowError("existing A4 quality results have invalid route accounting")
    status, reason = _status_and_reason(policy, held_out_gate_passed=gate["passed"])
    return {
        "status": status,
        "reason": reason,
        "freeze_id": frozen["freeze_id"],
        "policy_id": policy.policy_id,
        "a4_admitted_by_quality_gate": admitted,
        "shipping_profile": "a3-strong-only",
        "route_counts": route_counts,
        "route_shares": route_shares,
        "weak_route_share": held_out.get("weak_route_share"),
        "escalation_rate": held_out.get("escalation_rate"),
        "held_out_gate": gate,
        "performance_claim_eligible": False,
    }


def _held_out_reservation(frozen: dict[str, Any], policy: FrozenRoutingPolicy) -> dict[str, Any]:
    return {
        "status": "held-out-in-progress",
        "reason": (
            "held-out inference was reserved before its first request; absence of canonical "
            "quality results must fail closed"
        ),
        "freeze_id": frozen["freeze_id"],
        "policy_id": policy.policy_id,
        "shipping_profile": "a3-strong-only",
        "performance_claim_eligible": False,
    }


def preflight_held_out_evaluation(
    *,
    policy_path: Path | str = "artifacts/a4-frozen-policy.json",
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
    results_path: Path | str = "artifacts/quality-results.json",
    status_path: Path | str = "artifacts/cascade-status.json",
) -> tuple[dict[str, Any] | None, bool]:
    """Refuse repeat inference and recover only a missing derivative status artifact.

    ``None`` means no held-out result exists and collection may begin. A returned result means the
    immutable evaluation is already complete; the boolean records whether its status was rebuilt.
    """

    results_destination = Path(results_path)
    status_destination = Path(status_path)
    if not results_destination.exists():
        if not status_destination.exists():
            return None, False
        try:
            status = json.loads(status_destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CascadeWorkflowError("existing A4 status is unreadable") from exc
        if isinstance(status, dict) and status.get("status") == "not-run":
            return None, False
        raise CascadeWorkflowError(
            "A4 status exists without canonical quality results; refusing to repeat held-out "
            "inference—recover or inspect the prior component session"
        )
    try:
        results = json.loads(results_destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CascadeWorkflowError("existing A4 quality results are unreadable") from exc
    if not isinstance(results, dict):
        raise CascadeWorkflowError("existing A4 quality results root must be a mapping")
    frozen, policy, _, _ = load_frozen_calibration(
        policy_path,
        cases_path=cases_path,
        split_path=split_path,
    )
    expected_status = _status_from_recorded_result(frozen, policy, results)
    if status_destination.exists():
        try:
            status = json.loads(status_destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CascadeWorkflowError("existing A4 status is unreadable") from exc
        if status == expected_status:
            return results, False
        recoverable = isinstance(status, dict) and (
            status.get("status") == "not-run" or status == _held_out_reservation(frozen, policy)
        )
        if not recoverable:
            raise CascadeWorkflowError(
                "existing A4 status conflicts with canonical quality results; refusing recovery"
            )
    write_json(status_destination, expected_status)
    return results, True


def reserve_held_out_evaluation(
    *,
    policy_path: Path | str = "artifacts/a4-frozen-policy.json",
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
    results_path: Path | str = "artifacts/quality-results.json",
    status_path: Path | str = "artifacts/cascade-status.json",
) -> dict[str, Any]:
    """Atomically persist a fail-closed marker before the first held-out request."""

    existing, _ = preflight_held_out_evaluation(
        policy_path=policy_path,
        cases_path=cases_path,
        split_path=split_path,
        results_path=results_path,
        status_path=status_path,
    )
    if existing is not None:
        raise CascadeWorkflowError("held-out policy has already been evaluated and recorded")
    frozen, policy, _, _ = load_frozen_calibration(
        policy_path,
        cases_path=cases_path,
        split_path=split_path,
    )
    results_destination = Path(results_path)
    status_destination = Path(status_path)
    if results_destination.exists():
        raise CascadeWorkflowError(
            "canonical A4 quality results appeared during reservation; refusing inference"
        )
    if status_destination.exists():
        try:
            current = json.loads(status_destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CascadeWorkflowError("existing A4 status is unreadable") from exc
        if not isinstance(current, dict) or current.get("status") != "not-run":
            raise CascadeWorkflowError(
                "A4 held-out inference is already reserved; refusing to collect again"
            )
    reservation = _held_out_reservation(frozen, policy)
    write_json(status_destination, reservation)
    try:
        persisted = json.loads(status_destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CascadeWorkflowError("A4 held-out reservation could not be verified") from exc
    if persisted != reservation:
        raise CascadeWorkflowError("A4 held-out reservation did not persist exactly")
    return reservation


def evaluate_held_out(
    evidence: CascadeComponentEvidence,
    *,
    policy_path: Path | str = "artifacts/a4-frozen-policy.json",
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
    results_path: Path | str = "artifacts/quality-results.json",
    status_path: Path | str = "artifacts/cascade-status.json",
) -> dict[str, Any]:
    """Score the immutable policy exactly once on the 20 held-out cases."""

    results_destination = Path(results_path)
    if results_destination.exists():
        try:
            previous = json.loads(results_destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CascadeWorkflowError("existing A4 quality results are unreadable") from exc
        if isinstance(previous, dict) and previous.get("held_out") is not None:
            raise CascadeWorkflowError("held-out policy has already been evaluated and recorded")
    frozen, policy, _, gate_config = load_frozen_calibration(
        policy_path,
        cases_path=cases_path,
        split_path=split_path,
    )
    cases = load_cases(cases_path)
    split = load_split(split_path)
    by_id = {case.case_id: case for case in cases}
    held_out_cases = tuple(by_id[case_id] for case_id in split.test)
    if len(held_out_cases) != 20:
        raise CascadeWorkflowError("A4 held-out evaluation requires exactly 20 cases")
    result_payload, status_payload = _build_held_out_payloads(
        frozen,
        policy,
        gate_config,
        held_out_cases,
        evidence,
    )
    results_destination.parent.mkdir(parents=True, exist_ok=True)
    # The result is canonical. Status is a deterministic derivative that preflight can recover if
    # the process stops between these two individually atomic replacements.
    write_json(results_destination, result_payload)
    write_json(status_path, status_payload)
    return result_payload


def verify_cascade_evidence(
    artifacts_dir: Path | str = "artifacts",
    *,
    cases_path: Path | str = "demo/cases.jsonl",
    split_path: Path | str = "demo/split.json",
) -> list[str]:
    """Replay A4 freeze/result bindings and every nested component evidence receipt."""

    artifacts = Path(artifacts_dir)
    policy_path = artifacts / "a4-frozen-policy.json"
    results_path = artifacts / "quality-results.json"
    status_path = artifacts / "cascade-status.json"
    if not policy_path.exists():
        if results_path.exists():
            return ["A4 quality results exist without a frozen policy"]
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            status = None
        if isinstance(status, dict) and status.get("status") not in {None, "not-run"}:
            return ["A4 status claims an evaluation without a frozen policy"]
        return []

    errors: list[str] = []
    try:
        frozen, policy, plan, gate_config = load_frozen_calibration(
            policy_path,
            cases_path=cases_path,
            split_path=split_path,
        )
    except Exception as exc:
        return [f"A4 frozen policy: {exc}"]
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [f"A4 result/status is unreadable: {exc}"]
    if not isinstance(results, dict) or not isinstance(status, dict):
        return ["A4 result and status roots must be mappings"]
    if results.get("freeze_id") != frozen["freeze_id"]:
        errors.append("A4 results freeze_id disagrees with the frozen policy")
    if status.get("freeze_id") != frozen["freeze_id"]:
        errors.append("A4 status freeze_id disagrees with the frozen policy")
    if status.get("policy_id") != policy.policy_id:
        errors.append("A4 status policy_id disagrees with the frozen policy")
    if (
        results.get("shipping_profile") != "a3-strong-only"
        or status.get("shipping_profile") != "a3-strong-only"
    ):
        errors.append("A4 quality replay must retain the measured A3 strong-only deployment")
    if status.get("performance_claim_eligible") is not False:
        errors.append("A4 component replay must remain performance-claim-ineligible")

    cases = load_cases(cases_path)
    split = load_split(split_path)
    validate_dataset(cases, split)
    by_id = {case.case_id: case for case in cases}
    expected_dataset = {
        "cases_sha256": stable_file_sha256(cases_path),
        "split_sha256": stable_file_sha256(split_path),
    }
    project_root = artifacts.resolve().parent
    allowed_sessions_root = (artifacts / "a4" / "runs").resolve()

    def verify_session(
        source: object,
        *,
        phase: str,
        split_name: str,
        case_ids: tuple[str, ...],
        require_weak: bool,
    ) -> CascadeComponentEvidence | None:
        starting_errors = len(errors)
        if not isinstance(source, dict):
            errors.append(f"A4 {phase} source evidence is missing")
            return None
        session_value = source.get("session_dir")
        if not isinstance(session_value, str):
            errors.append(f"A4 {phase} session path is missing")
            return None
        unresolved = Path(session_value)
        session = unresolved if unresolved.is_absolute() else project_root / unresolved
        session = session.resolve()
        if not session.is_relative_to(allowed_sessions_root) or not session.is_dir():
            errors.append(f"A4 {phase} session path is outside artifacts/a4/runs or missing")
            return None
        store = ArtifactStore(session / "raw")
        loaded_roles: dict[str, MeasuredOutputCollection] = {}
        for role in ("weak", "strong"):
            ids_value = source.get(f"{role}_run_ids", [])
            if not isinstance(ids_value, list) or not all(
                isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id)
                for run_id in ids_value
            ):
                errors.append(f"A4 {phase} {role} run IDs are invalid")
                continue
            if role == "strong" and len(ids_value) != len(case_ids):
                errors.append(f"A4 {phase} strong evidence is incomplete")
                continue
            if role == "weak" and require_weak and len(ids_value) != len(case_ids):
                errors.append(f"A4 {phase} weak evidence is incomplete")
                continue
            if role == "weak" and not require_weak and ids_value:
                errors.append(f"A4 {phase} unexpectedly records unused weak evidence")
                continue
            if not ids_value:
                continue
            records: list[BenchmarkRecord] = []
            try:
                for run_id in ids_value:
                    lines = [
                        line
                        for line in (store.root / run_id / "requests.jsonl")
                        .read_text(encoding="utf-8")
                        .splitlines()
                        if line.strip()
                    ]
                    if len(lines) != 1:
                        raise CascadeWorkflowError(
                            f"{role} run {run_id} must contain exactly one record"
                        )
                    records.append(BenchmarkRecord.model_validate_json(lines[0]))
                loaded = _load_measured_outputs(
                    store,
                    records,
                    expected_case_ids=case_ids,
                    expected_split=split_name,
                    expected_role=role,
                    expected_candidate=_candidate(plan, role=role, phase=phase),
                    expected_dataset=expected_dataset,
                    expected_cases={case_id: by_id[case_id] for case_id in case_ids},
                )
                if list(loaded.source_run_ids) != ids_value:
                    errors.append(f"A4 {phase} {role} source run order does not replay")
                loaded_roles[role] = loaded
            except Exception as exc:
                errors.append(f"A4 {phase} {role} evidence: {exc}")
        if len(errors) != starting_errors or "strong" not in loaded_roles:
            return None
        if require_weak and "weak" not in loaded_roles:
            return None
        return CascadeComponentEvidence(
            session_dir=Path(session_value),
            weak=loaded_roles.get("weak"),
            strong=loaded_roles["strong"],
        )

    calibration_source = frozen.get("source_evidence")
    held_out = results.get("held_out")
    held_out_source = held_out.get("source_evidence") if isinstance(held_out, dict) else None
    calibration_evidence = verify_session(
        calibration_source,
        phase="calibration",
        split_name="calibration",
        case_ids=tuple(split.calibration),
        require_weak=True,
    )
    held_out_evidence = verify_session(
        held_out_source,
        phase="held-out",
        split_name="test",
        case_ids=tuple(split.test),
        require_weak=not policy.fallback_strong_only,
    )
    if calibration_evidence is not None and calibration_evidence.weak is not None:
        try:
            threshold_grid = frozen.get("threshold_grid")
            if not isinstance(threshold_grid, list) or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in threshold_grid
            ):
                raise CascadeWorkflowError("frozen threshold grid is invalid")
            replayed = calibrate_threshold(
                tuple(by_id[case_id] for case_id in split.calibration),
                calibration_evidence.weak.outputs,
                calibration_evidence.strong.outputs,
                thresholds=tuple(float(value) for value in threshold_grid),
                gate_config=gate_config,
            )
            replayed_policy = {
                "threshold": replayed.policy.threshold,
                "calibration_case_ids": list(replayed.policy.calibration_case_ids),
                "policy_id": replayed.policy.policy_id,
                "fallback_strong_only": replayed.policy.fallback_strong_only,
            }
            replayed_calibration = {
                "baseline": replayed.baseline.as_dict(),
                "candidates": [_candidate_dict(candidate) for candidate in replayed.candidates],
            }
            if frozen.get("policy") != replayed_policy:
                errors.append("A4 frozen policy does not replay from nested calibration responses")
            if frozen.get("calibration") != replayed_calibration:
                errors.append("A4 calibration summary does not replay from nested responses")
            if isinstance(calibration_source, dict) and (
                calibration_source.get("weak_model_file_sha256")
                != calibration_evidence.weak.model_file_sha256
                or calibration_source.get("strong_model_file_sha256")
                != calibration_evidence.strong.model_file_sha256
            ):
                errors.append("A4 calibration model hashes do not replay")
        except Exception as exc:
            errors.append(f"A4 calibration replay: {exc}")
    if held_out_evidence is not None:
        try:
            replayed_results, replayed_status = _build_held_out_payloads(
                frozen,
                policy,
                gate_config,
                tuple(by_id[case_id] for case_id in split.test),
                held_out_evidence,
            )
            if results != replayed_results:
                errors.append("A4 quality results do not replay from nested held-out responses")
            if status != replayed_status:
                errors.append("A4 status does not replay from nested held-out responses")
        except Exception as exc:
            errors.append(f"A4 held-out replay: {exc}")
    return errors


__all__ = [
    "CASCADE_FREEZE_SCHEMA_VERSION",
    "DEFAULT_THRESHOLDS",
    "CascadeComponentEvidence",
    "CascadeRuntimePlan",
    "CascadeWorkflowError",
    "MeasuredOutputCollection",
    "collect_real_component_outputs",
    "evaluate_held_out",
    "freeze_calibration",
    "load_frozen_calibration",
    "preflight_held_out_evaluation",
    "reserve_held_out_evaluation",
    "verify_cascade_evidence",
]
