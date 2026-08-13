"""Portable, failure-aware CPU-affinity helpers."""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from .topology import format_cpu_list


class AffinityError(RuntimeError):
    """Raised when a requested CPU set is invalid or cannot be applied."""


@dataclass(frozen=True, slots=True)
class AffinityResult:
    requested_cpus: tuple[int, ...]
    applied: bool
    method: str | None
    limitation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_cpus": list(self.requested_cpus),
            "applied": self.applied,
            "method": self.method,
            "limitation": self.limitation,
        }


def validate_affinity(
    cpus: Iterable[int], *, allowed_cpus: Iterable[int] | None = None
) -> tuple[int, ...]:
    normalized = tuple(sorted(set(cpus)))
    if not normalized:
        raise AffinityError("affinity CPU set must not be empty")
    if any(cpu < 0 for cpu in normalized):
        raise AffinityError("affinity CPU ids must be non-negative")
    if allowed_cpus is not None:
        allowed = set(allowed_cpus)
        outside = sorted(set(normalized).difference(allowed))
        if outside:
            raise AffinityError(f"CPU ids are outside the allowed set: {outside}")
    return normalized


def current_affinity(pid: int = 0) -> tuple[int, ...] | None:
    if not hasattr(os, "sched_getaffinity"):
        return None
    try:
        return tuple(sorted(os.sched_getaffinity(pid)))
    except OSError:
        return None


def apply_affinity(
    cpus: Iterable[int],
    *,
    pid: int = 0,
    strict: bool = False,
) -> AffinityResult:
    """Apply affinity on Linux, or record the OS limitation.

    ``strict=False`` is intentional for the tuner: container policy can deny
    affinity even on Linux, and that candidate should be recorded and skipped
    rather than crashing unrelated diagnostics.
    """

    allowed = current_affinity(pid)
    requested = validate_affinity(cpus, allowed_cpus=allowed)
    if platform.system().lower() != "linux" or not hasattr(os, "sched_setaffinity"):
        message = "CPU affinity is not supported by this operating system"
        if strict:
            raise AffinityError(message)
        return AffinityResult(requested, False, None, message)
    try:
        os.sched_setaffinity(pid, set(requested))
    except OSError as exc:
        message = f"operating system rejected CPU affinity: {exc.strerror or exc}"
        if strict:
            raise AffinityError(message) from exc
        return AffinityResult(requested, False, "sched_setaffinity", message)
    return AffinityResult(requested, True, "sched_setaffinity")


def taskset_prefix(cpus: Sequence[int]) -> tuple[str, ...] | None:
    """Return a safe argv prefix for a child process when taskset is present."""

    normalized = validate_affinity(cpus)
    executable = shutil.which("taskset")
    if platform.system().lower() != "linux" or executable is None:
        return None
    return (executable, "--cpu-list", format_cpu_list(normalized))


@contextmanager
def temporary_affinity(cpus: Sequence[int], *, strict: bool = False) -> Iterator[AffinityResult]:
    """Temporarily constrain the current process and restore its prior set."""

    previous = current_affinity()
    result = apply_affinity(cpus, strict=strict)
    try:
        yield result
    finally:
        if result.applied and previous is not None:
            try:
                os.sched_setaffinity(0, set(previous))
            except OSError as exc:
                if strict:
                    raise AffinityError("failed to restore original CPU affinity") from exc
