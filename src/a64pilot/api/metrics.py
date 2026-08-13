"""Process-local operational metrics, separate from benchmark evidence."""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestMetric:
    monotonic_ns: int
    latency_ms: float
    route: str
    model: str
    escalated: bool
    success: bool


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


class MetricsRegistry:
    """Bounded, thread-safe counters for the live demo.

    These values describe only requests observed by the current proxy process;
    they are not benchmark claims and are labeled accordingly in snapshots.
    """

    def __init__(self, max_recent: int = 2048) -> None:
        if max_recent < 1:
            raise ValueError("max_recent must be positive")
        self.started_monotonic_ns = time.monotonic_ns()
        self._recent: deque[RequestMetric] = deque(maxlen=max_recent)
        self._routes: Counter[str] = Counter()
        self._models: Counter[str] = Counter()
        self._requests = 0
        self._errors = 0
        self._escalations = 0
        self._lock = threading.Lock()

    def record(
        self,
        *,
        latency_ms: float,
        route: str,
        model: str,
        escalated: bool = False,
        success: bool = True,
    ) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        metric = RequestMetric(
            monotonic_ns=time.monotonic_ns(),
            latency_ms=latency_ms,
            route=route or "unknown",
            model=model or "unknown",
            escalated=escalated,
            success=success,
        )
        with self._lock:
            self._requests += 1
            self._errors += int(not success)
            self._escalations += int(escalated)
            self._routes[metric.route] += 1
            self._models[metric.model] += 1
            self._recent.append(metric)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            recent = tuple(self._recent)
            requests = self._requests
            errors = self._errors
            escalations = self._escalations
            routes = dict(sorted(self._routes.items()))
            models = dict(sorted(self._models.items()))
        latencies = [item.latency_ms for item in recent if item.success]
        uptime_s = (time.monotonic_ns() - self.started_monotonic_ns) / 1_000_000_000
        return {
            "schema_version": "1.0",
            "scope": "live_process_observability",
            "benchmark_evidence": False,
            "uptime_s": round(uptime_s, 6),
            "requests_total": requests,
            "errors_total": errors,
            "escalations_total": escalations,
            "routes": routes,
            "models": models,
            "recent_sample_count": len(recent),
            "recent_latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
            },
        }

    def prometheus_text(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP a64pilot_requests_total Requests handled by this proxy process.",
            "# TYPE a64pilot_requests_total counter",
            f"a64pilot_requests_total {snapshot['requests_total']}",
            "# HELP a64pilot_errors_total Failed requests handled by this proxy process.",
            "# TYPE a64pilot_errors_total counter",
            f"a64pilot_errors_total {snapshot['errors_total']}",
            "# HELP a64pilot_escalations_total Requests escalated by the configured responder.",
            "# TYPE a64pilot_escalations_total counter",
            f"a64pilot_escalations_total {snapshot['escalations_total']}",
        ]
        return "\n".join(lines) + "\n"
