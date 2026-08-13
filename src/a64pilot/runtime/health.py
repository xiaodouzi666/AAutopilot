"""HTTP readiness probes based on monotonic time."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ReadinessError(RuntimeError):
    """Base class for startup readiness failures."""


class ReadinessTimeout(ReadinessError):
    """Raised when a service does not become healthy before the deadline."""


class ProcessExitedBeforeReady(ReadinessError):
    """Raised when the managed process exits during readiness probing."""


@dataclass(frozen=True, slots=True)
class HealthResult:
    url: str
    status_code: int
    elapsed_ms: float
    attempts: int
    payload: Mapping[str, object] | None


def get_health(url: str, request_timeout_s: float = 1.0) -> tuple[int, Mapping[str, object] | None]:
    """Fetch one health response without including credentials or custom headers."""

    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(request, timeout=request_timeout_s) as response:  # noqa: S310 - local URL is caller-owned
        raw = response.read(1024 * 1024)
        payload: Mapping[str, object] | None = None
        if raw:
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    payload = decoded
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
        return int(response.status), payload


def wait_for_http_ready(
    url: str,
    *,
    timeout_s: float,
    interval_s: float = 0.1,
    request_timeout_s: float = 1.0,
    process_poll: Callable[[], int | None] | None = None,
    accepted_statuses: frozenset[int] = frozenset({200}),
) -> HealthResult:
    """Wait for a healthy response, failing early if the child exits."""

    if timeout_s <= 0 or interval_s <= 0 or request_timeout_s <= 0:
        raise ValueError("readiness timeouts and interval must be positive")
    started_ns = time.monotonic_ns()
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last_error = "no response"

    while time.monotonic() < deadline:
        if process_poll is not None:
            returncode = process_poll()
            if returncode is not None:
                raise ProcessExitedBeforeReady(
                    f"managed process exited with status {returncode} before {url} became ready"
                )
        attempts += 1
        try:
            status, payload = get_health(url, request_timeout_s=request_timeout_s)
            if status in accepted_statuses:
                return HealthResult(
                    url=url,
                    status_code=status,
                    elapsed_ms=(time.monotonic_ns() - started_ns) / 1_000_000,
                    attempts=attempts,
                    payload=payload,
                )
            last_error = f"HTTP {status}"
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}"
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval_s, remaining))

    elapsed_ms = (time.monotonic_ns() - started_ns) / 1_000_000
    raise ReadinessTimeout(
        f"timed out after {elapsed_ms:.0f} ms waiting for {url}; last probe: {last_error}"
    )
