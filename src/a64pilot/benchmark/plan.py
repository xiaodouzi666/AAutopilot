"""Bounded benchmark plan construction."""

from __future__ import annotations

import math
from collections.abc import Iterable
from itertools import product
from typing import Any

from pydantic import BaseModel, Field


class BenchmarkCandidate(BaseModel):
    candidate_id: str
    stage: str
    backend: str
    model_role: str
    quantization: str
    threads: int = Field(ge=1)
    batch: int = Field(ge=1)
    ubatch: int = Field(ge=1)
    parallel: int = Field(ge=1)
    context: int = Field(ge=128)
    affinity: list[int] = []


def thread_candidates(allowed_cores: int, physical_cores: int | None = None) -> list[int]:
    """Derive the small thread set prescribed by the benchmark protocol."""

    if allowed_cores < 1:
        raise ValueError("allowed_cores must be positive")
    ceiling = max(1, min(allowed_cores, physical_cores or allowed_cores))
    return sorted({1, math.ceil(ceiling / 4), math.ceil(ceiling / 2), ceiling})


def service_candidates(
    base: BenchmarkCandidate,
    *,
    batches: Iterable[int] = (128, 256, 512),
    ubatches: Iterable[int] = (64, 128, 256),
    parallels: Iterable[int] = (1, 2, 4),
    contexts: Iterable[int] = (2048,),
    limit: int = 36,
) -> list[BenchmarkCandidate]:
    """Generate a deterministically bounded matrix, pruning invalid micro-batches."""

    candidates: list[BenchmarkCandidate] = []
    for batch, ubatch, parallel, context in product(batches, ubatches, parallels, contexts):
        if ubatch > batch:
            continue
        candidate = base.model_copy(
            update={
                "candidate_id": (f"{base.candidate_id}-b{batch}-u{ubatch}-p{parallel}-c{context}"),
                "batch": batch,
                "ubatch": ubatch,
                "parallel": parallel,
                "context": context,
            }
        )
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def candidate_cache_key(candidate: BenchmarkCandidate, provenance: dict[str, Any]) -> str:
    from a64pilot.provenance import sha256_json

    return sha256_json({"candidate": candidate.model_dump(mode="json"), "provenance": provenance})
