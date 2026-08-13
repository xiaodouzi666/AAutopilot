"""Best-effort CPU topology discovery and benchmark affinity candidates."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CoreInfo:
    """Topology facts for one logical CPU."""

    cpu_id: int
    core_id: int | None = None
    package_id: int | None = None
    max_frequency_khz: int | None = None
    capacity: int | None = None
    numa_node: int | None = None

    def physical_key(self) -> tuple[int, int]:
        return (
            self.package_id if self.package_id is not None else 0,
            self.core_id if self.core_id is not None else self.cpu_id,
        )

    def to_dict(self) -> dict[str, int | None]:
        return {
            "cpu_id": self.cpu_id,
            "core_id": self.core_id,
            "package_id": self.package_id,
            "max_frequency_khz": self.max_frequency_khz,
            "capacity": self.capacity,
            "numa_node": self.numa_node,
        }


@dataclass(frozen=True, slots=True)
class AffinityCandidate:
    """A named CPU set derived from measured host topology."""

    name: str
    cpus: tuple[int, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "cpus": list(self.cpus),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class Topology:
    """Normalized topology plus safe candidates for bounded tuning."""

    logical_cpus: int
    physical_cores: int
    allowed_cpus: tuple[int, ...]
    cores: tuple[CoreInfo, ...]
    affinity_candidates: tuple[AffinityCandidate, ...]
    sources: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_cpus": self.logical_cpus,
            "physical_cores": self.physical_cores,
            "allowed_cpus": list(self.allowed_cpus),
            "cores": [core.to_dict() for core in self.cores],
            "affinity_candidates": [item.to_dict() for item in self.affinity_candidates],
            "sources": list(self.sources),
            "limitations": list(self.limitations),
        }


def parse_cpu_list(value: str) -> tuple[int, ...]:
    """Parse Linux CPU-list syntax (for example ``0-3,8,10-11``)."""

    cpus: set[int] = set()
    for fragment in value.strip().split(","):
        fragment = fragment.strip()
        if not fragment:
            continue
        if "-" in fragment:
            start_text, separator, end_text = fragment.partition("-")
            if not separator or not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"invalid CPU range: {fragment!r}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"descending CPU range: {fragment!r}")
            cpus.update(range(start, end + 1))
        elif fragment.isdigit():
            cpus.add(int(fragment))
        else:
            raise ValueError(f"invalid CPU id: {fragment!r}")
    return tuple(sorted(cpus))


def format_cpu_list(cpus: Iterable[int]) -> str:
    """Format a CPU set compactly using Linux CPU-list syntax."""

    ordered = sorted(set(cpus))
    if not ordered:
        return ""
    ranges: list[str] = []
    start = previous = ordered[0]
    for cpu in ordered[1:]:
        if cpu == previous + 1:
            previous = cpu
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = cpu
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip(), 0)
    except (OSError, ValueError):
        return None


def _allowed_cpus() -> tuple[int, ...]:
    if hasattr(os, "sched_getaffinity"):
        try:
            allowed = tuple(sorted(os.sched_getaffinity(0)))
            if allowed:
                return allowed
        except OSError:
            pass
    return tuple(range(os.cpu_count() or 1))


def _numa_nodes(sysfs_root: Path) -> dict[int, int]:
    cpu_to_node: dict[int, int] = {}
    node_root = sysfs_root.parent / "node"
    for node_path in sorted(node_root.glob("node[0-9]*")):
        match = re.fullmatch(r"node(\d+)", node_path.name)
        if match is None:
            continue
        try:
            cpus = parse_cpu_list((node_path / "cpulist").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for cpu in cpus:
            cpu_to_node[cpu] = int(match.group(1))
    return cpu_to_node


def parse_linux_sysfs(
    *,
    sysfs_root: Path = Path("/sys/devices/system/cpu"),
    allowed_cpus: Sequence[int] | None = None,
) -> tuple[CoreInfo, ...]:
    """Read per-CPU topology facts from a Linux sysfs tree."""

    allowed = set(allowed_cpus) if allowed_cpus is not None else None
    cpu_to_node = _numa_nodes(sysfs_root)
    cores: list[CoreInfo] = []
    cpu_paths = [path for path in sysfs_root.glob("cpu[0-9]*") if path.name[3:].isdigit()]
    for cpu_path in sorted(cpu_paths, key=lambda path: int(path.name[3:])):
        suffix = cpu_path.name[3:]
        cpu_id = int(suffix)
        if allowed is not None and cpu_id not in allowed:
            continue
        max_frequency = _read_int(cpu_path / "cpufreq" / "cpuinfo_max_freq")
        if max_frequency is None:
            max_frequency = _read_int(cpu_path / "cpufreq" / "scaling_max_freq")
        cores.append(
            CoreInfo(
                cpu_id=cpu_id,
                core_id=_read_int(cpu_path / "topology" / "core_id"),
                package_id=_read_int(cpu_path / "topology" / "physical_package_id"),
                max_frequency_khz=max_frequency,
                capacity=_read_int(cpu_path / "cpu_capacity"),
                numa_node=cpu_to_node.get(cpu_id),
            )
        )
    return tuple(cores)


def candidate_affinity_sets(
    cores: Sequence[CoreInfo], allowed_cpus: Sequence[int]
) -> tuple[AffinityCandidate, ...]:
    """Derive bounded, nonempty affinity candidates from measured facts."""

    allowed = tuple(sorted(set(allowed_cpus)))
    if not allowed:
        return ()
    allowed_set = set(allowed)
    usable = tuple(core for core in cores if core.cpu_id in allowed_set)
    candidates: list[AffinityCandidate] = [
        AffinityCandidate("all_allowed", allowed, ("OS allowed CPU set",))
    ]

    physical: dict[tuple[int, int], int] = {}
    for core in usable:
        physical.setdefault(core.physical_key(), core.cpu_id)
    physical_cpus = tuple(sorted(physical.values()))
    if physical_cpus and physical_cpus != allowed:
        candidates.append(
            AffinityCandidate(
                "one_thread_per_physical_core",
                physical_cpus,
                ("sysfs package/core identifiers",),
            )
        )

    capacities = [core.capacity for core in usable if core.capacity is not None]
    frequencies = [core.max_frequency_khz for core in usable if core.max_frequency_khz is not None]
    if capacities and len(set(capacities)) > 1:
        maximum = max(capacities)
        performance = tuple(sorted(core.cpu_id for core in usable if core.capacity == maximum))
        evidence = (f"sysfs cpu_capacity maximum={maximum}",)
    elif frequencies and len(set(frequencies)) > 1:
        maximum = max(frequencies)
        performance = tuple(
            sorted(core.cpu_id for core in usable if core.max_frequency_khz == maximum)
        )
        evidence = (f"sysfs cpuinfo_max_freq maximum={maximum} kHz",)
    else:
        performance = ()
        evidence = ()
    if performance and performance != allowed:
        candidates.append(AffinityCandidate("performance_cluster", performance, evidence))

    nodes = sorted({core.numa_node for core in usable if core.numa_node is not None})
    for node in nodes:
        node_cpus = tuple(sorted(core.cpu_id for core in usable if core.numa_node == node))
        if node_cpus and node_cpus != allowed:
            candidates.append(
                AffinityCandidate(
                    f"numa_node_{node}",
                    node_cpus,
                    (f"sysfs node{node}/cpulist",),
                )
            )

    # A heterogeneous host can yield duplicate CPU sets.  Preserve the first,
    # most generally useful name and avoid redundant tuner candidates.
    unique: list[AffinityCandidate] = []
    seen: set[tuple[int, ...]] = set()
    for candidate in candidates:
        if candidate.cpus not in seen:
            unique.append(candidate)
            seen.add(candidate.cpus)
    return tuple(unique)


def _sysctl_int(name: str) -> int | None:
    try:
        completed = subprocess.run(
            ["sysctl", "-n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def detect_topology(
    *,
    system: str | None = None,
    sysfs_root: Path = Path("/sys/devices/system/cpu"),
    allowed_cpus: Sequence[int] | None = None,
) -> Topology:
    """Discover host topology without assuming affinity is enforceable."""

    system_name = (system or platform.system()).lower()
    allowed = tuple(sorted(set(allowed_cpus or _allowed_cpus())))
    sources: list[str] = []
    limitations: list[str] = []

    if system_name == "linux":
        cores = parse_linux_sysfs(sysfs_root=sysfs_root, allowed_cpus=allowed)
        if cores:
            sources.append("Linux sysfs CPU topology")
        else:
            cores = tuple(CoreInfo(cpu_id=cpu) for cpu in allowed)
            limitations.append("Linux sysfs CPU topology unavailable")
    else:
        cores = tuple(CoreInfo(cpu_id=cpu) for cpu in allowed)
        if system_name == "darwin":
            sources.append("sysctl hw.logicalcpu/hw.physicalcpu")
            limitations.append("macOS does not expose Linux-style CPU affinity")
        else:
            limitations.append("per-core topology unavailable on this operating system")

    physical_keys = {core.physical_key() for core in cores}
    physical_count = len(physical_keys) if physical_keys else len(allowed)
    logical_count = len(allowed)
    if system_name == "darwin":
        logical_count = _sysctl_int("hw.logicalcpu") or logical_count
        physical_count = _sysctl_int("hw.physicalcpu") or physical_count

    return Topology(
        logical_cpus=logical_count,
        physical_cores=physical_count,
        allowed_cpus=allowed,
        cores=cores,
        affinity_candidates=candidate_affinity_sets(cores, allowed),
        sources=tuple(sources),
        limitations=tuple(limitations),
    )
