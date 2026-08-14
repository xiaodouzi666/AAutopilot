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
class CacheInfo:
    """Normalized aggregate for one cache level/type on the visible host."""

    name: str
    level: int
    kind: str
    total_size_bytes: int
    instances: int
    shared_cpu_lists: tuple[tuple[int, ...], ...] = field(default_factory=tuple)
    source: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "level": self.level,
            "kind": self.kind,
            "total_size_bytes": self.total_size_bytes,
            "instances": self.instances,
            "shared_cpu_lists": [list(cpus) for cpus in self.shared_cpu_lists],
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Topology:
    """Normalized topology plus safe candidates for bounded tuning."""

    logical_cpus: int
    physical_cores: int | None
    allowed_cpus: tuple[int, ...]
    cores: tuple[CoreInfo, ...]
    affinity_candidates: tuple[AffinityCandidate, ...]
    sockets: int | None = None
    numa_nodes: int | None = None
    cache_layout: tuple[CacheInfo, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_cpus": self.logical_cpus,
            "physical_cores": self.physical_cores,
            "allowed_cpus": list(self.allowed_cpus),
            "cores": [core.to_dict() for core in self.cores],
            "affinity_candidates": [item.to_dict() for item in self.affinity_candidates],
            "sockets": self.sockets,
            "numa_nodes": self.numa_nodes,
            "cache_layout": [cache.to_dict() for cache in self.cache_layout],
            "sources": list(self.sources),
            "limitations": list(self.limitations),
        }


_CACHE_NAME_BY_LEVEL_KIND = {
    (1, "data"): "l1d",
    (1, "instruction"): "l1i",
    (1, "unified"): "l1",
    (2, "unified"): "l2",
    (3, "unified"): "l3",
}
_CACHE_SIZE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b|[kmgt])?", re.I)
_CACHE_INSTANCE_PATTERN = re.compile(r"\(([0-9]+)\s+instances?\)", re.I)


def _cache_name(level: int, kind: str) -> str:
    normalized = kind.strip().lower()
    return _CACHE_NAME_BY_LEVEL_KIND.get((level, normalized), f"l{level}-{normalized or 'unknown'}")


def parse_size_bytes(value: str) -> int | None:
    """Parse sysfs/lscpu cache sizes without assuming decimal units."""

    match = _CACHE_SIZE_PATTERN.match(value)
    if match is None:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    exponent = {
        "b": 0,
        "k": 1,
        "kb": 1,
        "kib": 1,
        "m": 2,
        "mb": 2,
        "mib": 2,
        "g": 3,
        "gb": 3,
        "gib": 3,
        "t": 4,
        "tb": 4,
        "tib": 4,
    }.get(unit)
    if exponent is None:
        return None
    return int(number * (1024**exponent))


def parse_lscpu_fields(text: str) -> dict[str, str]:
    """Return normalized fields from either JSON or conventional ``lscpu`` output."""

    import json

    rows: object = None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        rows = payload.get("lscpu")

    fields: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("field", "")).strip().rstrip(":").lower()
            value = str(row.get("data", "")).strip()
            if key and value:
                fields.setdefault(key, value)
        return fields

    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and value.strip():
            fields.setdefault(key.strip().lower(), value.strip())
    return fields


def parse_lscpu_cache_layout(text: str) -> tuple[CacheInfo, ...]:
    """Parse aggregate L1d/L1i/L2/L3 facts reported by ``lscpu``."""

    fields = parse_lscpu_fields(text)
    result: list[CacheInfo] = []
    definitions = (
        ("l1d cache", "l1d", 1, "data"),
        ("l1i cache", "l1i", 1, "instruction"),
        ("l2 cache", "l2", 2, "unified"),
        ("l3 cache", "l3", 3, "unified"),
    )
    for field_name, name, level, kind in definitions:
        raw = fields.get(field_name)
        size = parse_size_bytes(raw or "")
        if size is None or size <= 0:
            continue
        instance_match = _CACHE_INSTANCE_PATTERN.search(raw or "")
        instances = int(instance_match.group(1)) if instance_match else 1
        result.append(
            CacheInfo(
                name=name,
                level=level,
                kind=kind,
                total_size_bytes=size,
                instances=instances,
                source="lscpu",
            )
        )
    return tuple(result)


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


def parse_linux_cache_sysfs(
    *,
    sysfs_root: Path = Path("/sys/devices/system/cpu"),
    allowed_cpus: Sequence[int] | None = None,
) -> tuple[CacheInfo, ...]:
    """Read and deduplicate Linux per-CPU cache instances from sysfs."""

    allowed = set(allowed_cpus) if allowed_cpus is not None else None
    instances: dict[tuple[int, str, tuple[int, ...], str], int] = {}
    cpu_paths = [path for path in sysfs_root.glob("cpu[0-9]*") if path.name[3:].isdigit()]
    for cpu_path in sorted(cpu_paths, key=lambda path: int(path.name[3:])):
        cpu_id = int(cpu_path.name[3:])
        if allowed is not None and cpu_id not in allowed:
            continue
        for index_path in sorted((cpu_path / "cache").glob("index[0-9]*")):
            level = _read_int(index_path / "level")
            try:
                kind = (index_path / "type").read_text(encoding="utf-8").strip().lower()
                size_text = (index_path / "size").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            size = parse_size_bytes(size_text)
            if level is None or level <= 0 or size is None or size <= 0:
                continue
            try:
                shared = parse_cpu_list(
                    (index_path / "shared_cpu_list").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                shared = (cpu_id,)
            try:
                cache_id = (index_path / "id").read_text(encoding="utf-8").strip()
            except OSError:
                cache_id = ""
            # A cache instance is repeated below every CPU that shares it.  The
            # shared CPU list is the primary identity; ``id`` disambiguates the
            # uncommon case where multiple cache records have the same sharing set.
            identity = (level, kind, shared, cache_id)
            instances.setdefault(identity, size)

    grouped: dict[tuple[int, str], list[tuple[tuple[int, ...], int]]] = {}
    for (level, kind, shared, _cache_id), size in instances.items():
        grouped.setdefault((level, kind), []).append((shared, size))

    result: list[CacheInfo] = []
    for (level, kind), rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (row[0], row[1]))
        result.append(
            CacheInfo(
                name=_cache_name(level, kind),
                level=level,
                kind=kind,
                total_size_bytes=sum(size for _, size in ordered),
                instances=len(ordered),
                shared_cpu_lists=tuple(shared for shared, _ in ordered),
                source="Linux sysfs CPU cache topology",
            )
        )
    return tuple(sorted(result, key=lambda item: (item.level, item.name)))


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


def _command_text(command: Sequence[str]) -> str | None:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            env=environment,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _positive_lscpu_int(fields: dict[str, str], key: str) -> int | None:
    value = fields.get(key)
    if value is None:
        return None
    match = re.match(r"\s*([0-9]+)", value)
    if match is None:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def detect_topology(
    *,
    system: str | None = None,
    sysfs_root: Path = Path("/sys/devices/system/cpu"),
    allowed_cpus: Sequence[int] | None = None,
    lscpu_text: str | None = None,
) -> Topology:
    """Discover host topology without assuming affinity is enforceable."""

    system_name = (system or platform.system()).lower()
    allowed = tuple(sorted(set(allowed_cpus or _allowed_cpus())))
    sources: list[str] = []
    limitations: list[str] = []
    sockets: int | None = None
    numa_nodes: int | None = None
    cache_layout: tuple[CacheInfo, ...] = ()
    physical_count: int | None = None

    if system_name == "linux":
        if lscpu_text is None:
            lscpu_text = _command_text(("lscpu", "--json")) or _command_text(("lscpu",))
        lscpu_fields = parse_lscpu_fields(lscpu_text or "")
        cores = parse_linux_sysfs(sysfs_root=sysfs_root, allowed_cpus=allowed)
        if cores:
            sources.append("Linux sysfs CPU topology")
            if all(core.core_id is not None and core.package_id is not None for core in cores):
                physical_count = len({(int(core.package_id), int(core.core_id)) for core in cores})
        else:
            cores = tuple(CoreInfo(cpu_id=cpu) for cpu in allowed)
            limitations.append("Linux sysfs CPU topology unavailable")
        package_ids = {core.package_id for core in cores if core.package_id is not None}
        sockets = (
            len(package_ids) if package_ids else _positive_lscpu_int(lscpu_fields, "socket(s)")
        )
        node_ids = {core.numa_node for core in cores if core.numa_node is not None}
        numa_nodes = (
            len(node_ids) if node_ids else _positive_lscpu_int(lscpu_fields, "numa node(s)")
        )
        cache_layout = parse_linux_cache_sysfs(sysfs_root=sysfs_root, allowed_cpus=allowed)
        if cache_layout:
            sources.append("Linux sysfs CPU cache topology")
        elif lscpu_text:
            cache_layout = parse_lscpu_cache_layout(lscpu_text)
            if cache_layout:
                sources.append("lscpu cache summary")
        if lscpu_text:
            sources.append("lscpu")
        if physical_count is None:
            cores_per_socket = _positive_lscpu_int(lscpu_fields, "core(s) per socket")
            if cores_per_socket is not None and sockets is not None:
                physical_count = cores_per_socket * sockets
        if physical_count is None:
            limitations.append("physical CPU core count unavailable")
        if sockets is None:
            limitations.append("CPU socket count unavailable")
        if numa_nodes is None:
            limitations.append("NUMA node count unavailable")
        if not cache_layout:
            limitations.append("CPU cache layout unavailable")
    else:
        cores = tuple(CoreInfo(cpu_id=cpu) for cpu in allowed)
        if system_name == "darwin":
            sources.append("sysctl hw.logicalcpu/hw.physicalcpu")
            limitations.append("macOS does not expose Linux-style CPU affinity")
        else:
            limitations.append("per-core topology unavailable on this operating system")

    logical_count = len(allowed)
    if system_name == "darwin":
        logical_count = _sysctl_int("hw.logicalcpu") or logical_count
        physical_count = _sysctl_int("hw.physicalcpu")
        if physical_count is None:
            limitations.append("physical CPU core count unavailable")

    return Topology(
        logical_cpus=logical_count,
        physical_cores=physical_count,
        allowed_cpus=allowed,
        cores=cores,
        affinity_candidates=(
            candidate_affinity_sets(cores, allowed) if system_name == "linux" else ()
        ),
        sockets=sockets,
        numa_nodes=numa_nodes,
        cache_layout=cache_layout,
        sources=tuple(sources),
        limitations=tuple(limitations),
    )
