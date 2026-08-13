from __future__ import annotations

import pytest

from a64pilot.benchmark.statistics import (
    latency_reduction_pct,
    paired_bootstrap_interval,
    summarize,
    throughput_increase_pct,
)


def test_summary_is_transparent() -> None:
    result = summarize([1.0, 2.0, 3.0, 100.0])
    assert result.count == 4
    assert result.median == 2.5
    assert result.p95 is not None and result.p95 > 3.0


def test_relative_formulas_are_directional() -> None:
    assert latency_reduction_pct(100, 75) == 25
    assert throughput_increase_pct(10, 12) == 20
    with pytest.raises(ValueError):
        latency_reduction_pct(0, 1)


def test_bootstrap_is_deterministic() -> None:
    first = paired_bootstrap_interval([100, 101, 99], [80, 81, 79], samples=200)
    second = paired_bootstrap_interval([100, 101, 99], [80, 81, 79], samples=200)
    assert first == second
    assert first[0] > 0
