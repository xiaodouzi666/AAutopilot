"""Machine-readable evidence schemas shared across the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"
SYSTEM_INFO_SCHEMA_VERSION = "2.0.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureEvidence(StrictModel):
    supported: bool
    evidence: list[str] = []


class DistributionInfo(StrictModel):
    pretty_name: str = Field(min_length=1, max_length=240)
    identifier: str | None = Field(default=None, max_length=120)
    version_id: str | None = Field(default=None, max_length=120)
    source: str = Field(min_length=1, max_length=240)


class CacheInfo(StrictModel):
    name: str = Field(pattern=r"^l[0-9]+(?:[di]|-[a-z]+)?$")
    level: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=40)
    total_size_bytes: int = Field(gt=0)
    instances: int = Field(gt=0)
    shared_cpu_lists: list[list[int]] = []
    source: str = Field(min_length=1, max_length=240)


class ProvenanceLimitation(StrictModel):
    code: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    field: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    reason: str = Field(min_length=1, max_length=500)
    sources_checked: list[str] = Field(min_length=1)


class SystemInfo(StrictModel):
    # Target provenance gained mandatory completeness/limitation semantics in v2.
    # Keep this version independent from the other evidence schemas so an old
    # system-info payload can never be silently promoted by a default value.
    schema_version: Literal[SYSTEM_INFO_SCHEMA_VERSION]
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    architecture: str
    architecture_raw: str = "unknown"
    operating_system: str
    kernel: str
    cpu_model: str = "unknown"
    cpu_identifiers: dict[str, str] = {}
    distribution: DistributionInfo | None = None
    python_version: str = "unknown"
    arm64: bool = False
    real_benchmark_eligible: bool = False
    logical_cores: int = Field(ge=1)
    physical_cores: int | None = Field(default=None, ge=1)
    memory_bytes: int | None = Field(default=None, gt=0)
    filesystem_free_bytes: int | None = Field(default=None, ge=0)
    sockets: int | None = Field(default=None, ge=1)
    numa_nodes: int | None = Field(default=None, ge=1)
    cache_layout: list[CacheInfo] = []
    tool_versions: dict[str, str | None] = {}
    features: dict[str, FeatureEvidence] = {}
    topology: dict[str, Any] = {}
    affinity_candidates: dict[str, list[int]] = {}
    provenance_limitations: list[ProvenanceLimitation] = []
    target_provenance_status: Literal["complete", "limited"] = "complete"
    sources: list[str] = []
    limitations: list[str] = []
    public_redacted: bool = True

    @model_validator(mode="after")
    def required_target_provenance_is_explicit(self) -> SystemInfo:
        """A required fact must be measured or carry a structured limitation."""

        unknown_values = {"", "-", "unknown", "n/a", "not available", "none"}
        unresolved: set[str] = set()
        if self.cpu_model.strip().lower() in unknown_values:
            unresolved.add("cpu_model")
        if self.distribution is None:
            unresolved.add("distribution")
        if self.physical_cores is None:
            unresolved.add("physical_cores")
        if self.memory_bytes is None:
            unresolved.add("memory_bytes")
        compiler = self.tool_versions.get("compiler")
        if compiler is None or compiler.strip().lower() in unknown_values:
            unresolved.add("compiler")
        if self.sockets is None:
            unresolved.add("sockets")
        if self.numa_nodes is None:
            unresolved.add("numa_nodes")
        if not any(item.evidence for item in self.features.values()):
            unresolved.add("instruction_features")
        topology_cores = self.topology.get("cores")
        if (
            not isinstance(topology_cores, list)
            or not topology_cores
            or any(
                not isinstance(core, dict)
                or (core.get("capacity") is None and core.get("max_frequency_khz") is None)
                for core in topology_cores
            )
        ):
            unresolved.add("heterogeneous_clusters")
        if not self.affinity_candidates:
            unresolved.add("affinity_candidates")
        cache_names = [cache.name for cache in self.cache_layout]
        if len(cache_names) != len(set(cache_names)):
            raise ValueError("cache_layout contains duplicate normalized cache names")
        for name in ("l1d", "l1i", "l2", "l3"):
            if name not in cache_names:
                unresolved.add(f"cache_{name}")

        declared = {item.field for item in self.provenance_limitations}
        undeclared = sorted(unresolved.difference(declared))
        if undeclared:
            raise ValueError(
                "required target provenance is absent without a structured limitation: "
                + ", ".join(undeclared)
            )
        flat_limitations = "\n".join(self.limitations)
        missing_flat = sorted(
            item.code
            for item in self.provenance_limitations
            if f"{item.code}:" not in flat_limitations
        )
        if missing_flat:
            raise ValueError(
                "structured provenance limitations are absent from limitations: "
                + ", ".join(missing_flat)
            )

        expected_status = "limited" if self.provenance_limitations else "complete"
        if self.target_provenance_status != expected_status:
            raise ValueError(
                "target_provenance_status disagrees with structured provenance limitations"
            )

        topology_physical = self.topology.get("physical_cores")
        if topology_physical is not None and topology_physical != self.physical_cores:
            raise ValueError("topology physical core count disagrees with physical_cores")
        topology_sockets = self.topology.get("sockets")
        if topology_sockets is not None and topology_sockets != self.sockets:
            raise ValueError("topology socket count disagrees with top-level sockets")
        topology_numa = self.topology.get("numa_nodes")
        if topology_numa is not None and topology_numa != self.numa_nodes:
            raise ValueError("topology NUMA count disagrees with top-level numa_nodes")
        topology_caches = self.topology.get("cache_layout")
        if topology_caches is not None and topology_caches != [
            cache.model_dump(mode="json") for cache in self.cache_layout
        ]:
            raise ValueError("topology cache layout disagrees with top-level cache_layout")
        return self


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
