"""Machine-readable evidence schemas shared across the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureEvidence(StrictModel):
    supported: bool
    evidence: list[str] = []


class SystemInfo(StrictModel):
    schema_version: str = SCHEMA_VERSION
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    architecture: str
    architecture_raw: str = "unknown"
    operating_system: str
    kernel: str
    cpu_model: str = "unknown"
    python_version: str = "unknown"
    arm64: bool = False
    real_benchmark_eligible: bool = False
    logical_cores: int = Field(ge=1)
    physical_cores: int | None = Field(default=None, ge=1)
    memory_bytes: int | None = Field(default=None, ge=0)
    filesystem_free_bytes: int | None = Field(default=None, ge=0)
    tool_versions: dict[str, str | None] = {}
    features: dict[str, FeatureEvidence] = {}
    topology: dict[str, Any] = {}
    affinity_candidates: dict[str, list[int]] = {}
    sources: list[str] = []
    limitations: list[str] = []
    public_redacted: bool = True


class BuildVariant(StrictModel):
    backend: Literal["generic", "kleidiai"]
    source_commit: str
    build_type: str = "Release"
    cmake_flags: list[str]
    compiler: str
    binaries: dict[str, str] = {}
    binary_sha256: dict[str, str] = {}
    cpu_only_configured: bool
    kleidiai_configured: bool
    runtime_marker_verified: bool = False


class BuildManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_url: str = "https://github.com/ggml-org/llama.cpp.git"
    variants: list[BuildVariant]

    @model_validator(mode="after")
    def fair_pair(self) -> BuildManifest:
        if len(self.variants) >= 2 and len({v.source_commit for v in self.variants}) != 1:
            raise ValueError("all build variants must use the same llama.cpp commit")
        return self


class ModelTensor(StrictModel):
    name: str
    type: str
    dimensions: list[int]


class ModelArtifact(StrictModel):
    role: Literal["weak", "strong"]
    repository: str
    revision: str
    filename: str
    quantization: str
    sha256: str
    bytes: int = Field(ge=0)
    license: str = "Apache-2.0"
    local_path: str
    kleidiai_compatible: bool = False
    tensor_type_histogram: dict[str, int]
    tensor_inventory_sha256: str
    reviewed_kleidiai_fallbacks: list[ModelTensor] = []


class ModelManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    models: list[ModelArtifact]


class BenchmarkRecord(StrictModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    candidate_id: str
    stage: Literal["reference", "baseline", "quant", "kleidiai", "tuned", "cascade"]
    case_id: str
    repetition: int = Field(default=0, ge=0)
    split: Literal["calibration", "test", "micro", "fixture"]
    backend: Literal["generic", "kleidiai", "fixture"]
    model_role: Literal["weak", "strong", "cascade", "fixture"]
    model_file_sha256: str
    quantization: str
    threads: int = Field(ge=1)
    batch: int = Field(ge=1)
    ubatch: int = Field(ge=1)
    parallel: int = Field(ge=1)
    context: int = Field(default=2048, ge=1)
    affinity: list[int] = []
    cpu_only_verified: bool
    kleidiai_verified: bool
    evidence_kind: Literal["measured", "fixture"] = "measured"
    start_ns: int = Field(ge=0)
    first_token_ns: int | None = Field(default=None, ge=0)
    end_ns: int = Field(ge=0)
    ttft_ms: float | None = Field(default=None, ge=0)
    e2e_ms: float = Field(ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    generation_tok_s: float | None = Field(default=None, ge=0)
    peak_rss_mb: float = Field(default=0, ge=0)
    route: Literal["weak", "strong", "weak_then_strong", "fixture"] = "strong"
    schema_valid: bool
    quality_score: float = Field(ge=0, le=100)
    safety_score: float = Field(ge=0, le=100)
    command: list[str]
    errors: list[str] = []

    @model_validator(mode="after")
    def timing_order(self) -> BenchmarkRecord:
        if self.end_ns < self.start_ns:
            raise ValueError("end_ns precedes start_ns")
        if (
            self.first_token_ns is not None
            and not self.start_ns <= self.first_token_ns <= self.end_ns
        ):
            raise ValueError("first_token_ns is outside request interval")
        if self.evidence_kind == "fixture" and self.split != "fixture":
            raise ValueError("fixture evidence must use split=fixture")
        return self


class MetricSummary(StrictModel):
    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None
    p50: float | None = None
    p95: float | None = None
    stddev: float | None = None
    coefficient_of_variation: float | None = None


class CandidateResult(StrictModel):
    candidate_id: str
    stage: str
    backend: str
    model: str
    quality_score: float = Field(ge=0, le=100)
    safety_score: float = Field(ge=0, le=100)
    schema_failures: int = Field(ge=0)
    p95_latency_ms: float = Field(gt=0)
    requests_per_second: float = Field(gt=0)
    peak_rss_mb: float = Field(ge=0)
    measured: bool = True
    source_run_ids: list[str] = []
    config: dict[str, Any] = {}


class Claim(StrictModel):
    claim_id: str
    metric: str
    value: float
    unit: str
    baseline_candidate: str
    optimized_candidate: str
    source_rows: list[str]
    formula: str
    confidence_interval: tuple[float, float] | None = None
    demonstrated: bool = True

    @model_validator(mode="after")
    def has_provenance(self) -> Claim:
        if not self.source_rows:
            raise ValueError("claim must cite at least one measured source row")
        return self
