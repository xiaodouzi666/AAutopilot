"""Typed project configuration with YAML and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

BENCHMARK_MAX_OUTPUT_TOKENS = 512


class ProjectConfig(BaseModel):
    name: str = "aarch64-autopilot"
    artifacts_dir: Path = Path("artifacts")


class RuntimeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8088, ge=1024, le=65535)
    generic_base_port: int = Field(default=18080, ge=1024, le=65535)
    optimized_base_port: int = Field(default=18180, ge=1024, le=65535)
    startup_timeout_s: float = Field(default=180.0, gt=0)
    request_timeout_s: float = Field(default=180.0, gt=0)
    cpu_only: bool = True


class ModelRoleConfig(BaseModel):
    repo: str
    candidates: list[str]


class ModelsConfig(BaseModel):
    weak: ModelRoleConfig = ModelRoleConfig(
        repo="Qwen/Qwen2.5-0.5B-Instruct-GGUF", candidates=["Q4_0"]
    )
    strong: ModelRoleConfig = ModelRoleConfig(
        repo="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        candidates=["Q4_0", "Q8_0"],
    )


class BenchmarkConfig(BaseModel):
    warmup_requests: int = Field(default=2, ge=0)
    repetitions: int = Field(default=3, ge=1)
    max_search_minutes: int = Field(default=120, ge=1)
    random_seed: int = 20260813
    max_output_tokens: int = Field(default=BENCHMARK_MAX_OUTPUT_TOKENS, ge=1)
    temperature: float = Field(default=0.0, ge=0)


class QualityGateConfig(BaseModel):
    max_absolute_quality_drop: float = Field(default=1.0, ge=0)
    minimum_safety_score: float = Field(default=100.0, ge=0, le=100)
    maximum_schema_failures: int = Field(default=0, ge=0)
    p95_latency_ms: float | None = Field(default=None, gt=0)
    peak_rss_mb: float | None = Field(default=None, gt=0)


class SelectionConfig(BaseModel):
    policy: str = "pareto_knee"
    minimize: list[str] = ["p95_latency_ms", "peak_rss_mb"]
    maximize: list[str] = ["requests_per_second", "quality_score"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectConfig = ProjectConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    models: ModelsConfig = ModelsConfig()
    benchmark: BenchmarkConfig = BenchmarkConfig()
    quality_gate: QualityGateConfig = QualityGateConfig()
    selection: SelectionConfig = SelectionConfig()


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(path: Path | str = "configs/default.yaml") -> Settings:
    """Load settings and apply the small documented environment override surface."""

    config_path = Path(path)
    payload: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"configuration root must be a mapping: {config_path}")
        payload = loaded

    overrides: dict[str, Any] = {}
    if value := os.getenv("A64PILOT_ARTIFACTS_DIR"):
        overrides = _deep_merge(overrides, {"project": {"artifacts_dir": value}})
    if value := os.getenv("A64PILOT_RUNTIME_HOST"):
        overrides = _deep_merge(overrides, {"runtime": {"host": value}})
    if value := os.getenv("A64PILOT_RUNTIME_PORT"):
        overrides = _deep_merge(overrides, {"runtime": {"port": int(value)}})
    return Settings.model_validate(_deep_merge(payload, overrides))
