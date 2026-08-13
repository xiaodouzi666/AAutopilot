"""Transparent descriptive statistics and fixed-seed bootstrap intervals."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

from a64pilot.schemas import MetricSummary


def summarize(values: Sequence[float]) -> MetricSummary:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return MetricSummary(count=0)
    mean = float(np.mean(array))
    stddev = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
    return MetricSummary(
        count=int(array.size),
        mean=mean,
        median=float(np.median(array)),
        p50=float(np.percentile(array, 50)),
        p95=float(np.percentile(array, 95)),
        stddev=stddev,
        coefficient_of_variation=(stddev / mean if mean else None),
    )


def latency_reduction_pct(baseline: float, optimized: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline latency must be positive")
    return (baseline - optimized) / baseline * 100.0


def throughput_increase_pct(baseline: float, optimized: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline throughput must be positive")
    return (optimized - baseline) / baseline * 100.0


def paired_bootstrap_interval(
    baseline: Sequence[float],
    optimized: Sequence[float],
    *,
    statistic: Callable[[float, float], float] = latency_reduction_pct,
    confidence: float = 0.95,
    samples: int = 5000,
    seed: int = 20260813,
    reducer: Callable[[np.ndarray], float] | None = None,
) -> tuple[float, float]:
    """Bootstrap a paired relative statistic without deleting any observations."""

    left = np.asarray(baseline, dtype=float)
    right = np.asarray(optimized, dtype=float)
    if left.size != right.size or left.size == 0:
        raise ValueError("paired inputs must be non-empty and have equal length")
    rng = np.random.default_rng(seed)
    reduce_sample = reducer or (lambda values: float(np.median(values)))
    estimates: list[float] = []
    for _ in range(samples):
        indices = rng.integers(0, left.size, size=left.size)
        b = float(reduce_sample(left[indices]))
        o = float(reduce_sample(right[indices]))
        if b > 0 and math.isfinite(b) and math.isfinite(o):
            estimates.append(statistic(b, o))
    if not estimates:
        raise ValueError("no finite bootstrap estimates")
    alpha = (1.0 - confidence) / 2.0
    return (
        float(np.quantile(estimates, alpha)),
        float(np.quantile(estimates, 1.0 - alpha)),
    )


def is_unstable(values: Sequence[float], threshold: float = 0.10) -> bool:
    result = summarize(values)
    return bool(
        result.coefficient_of_variation is not None and result.coefficient_of_variation > threshold
    )
