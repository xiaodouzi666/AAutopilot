"""Public-safe system discovery for the hardware doctor."""

from __future__ import annotations

import getpass
import ipaddress
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from a64pilot.schemas import SYSTEM_INFO_SCHEMA_VERSION

from .cpu_features import FeatureEvidence, detect_cpu_features, features_to_dict
from .topology import CacheInfo, Topology, detect_topology, parse_lscpu_fields

SCHEMA_VERSION = SYSTEM_INFO_SCHEMA_VERSION
_ARM64_NAMES = frozenset({"aarch64", "arm64", "armv8", "armv8l"})


class ArchitectureError(RuntimeError):
    """Raised when a real benchmark is attempted on a non-Arm64 host."""


def normalize_architecture(machine: str) -> str:
    value = machine.strip().lower().replace(" ", "")
    if value in _ARM64_NAMES or value.startswith("aarch64"):
        return "aarch64"
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    return value or "unknown"


def is_arm64(machine: str | None = None) -> bool:
    return normalize_architecture(machine or platform.machine()) == "aarch64"


def assert_arm64_benchmark(machine: str | None = None) -> str:
    """Reject a real (non-dry-run) benchmark unless the host is Arm64.

    Apple reports ``arm64`` while Linux normally reports ``aarch64``; both are
    accepted.  The doctor itself never calls this function, so diagnostics are
    still available on development machines of any architecture.
    """

    raw = machine or platform.machine()
    normalized = normalize_architecture(raw)
    if normalized != "aarch64":
        raise ArchitectureError(
            "real benchmarks require an Arm64 host "
            f"(reported architecture: {raw or 'unknown'}); use --dry-run for planning"
        )
    return normalized


def _run_text(command: Sequence[str], *, timeout_s: float = 3.0) -> str | None:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=environment,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _linux_memory_bytes(path: Path = Path("/proc/meminfo")) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("MemTotal:"):
            continue
        match = re.search(r"(\d+)\s+kB", line, re.IGNORECASE)
        if match:
            return int(match.group(1)) * 1024
    return None


def _darwin_memory_bytes() -> int | None:
    value = _run_text(("sysctl", "-n", "hw.memsize"))
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


_UNKNOWN_FACT_VALUES = frozenset({"", "-", "unknown", "n/a", "not available", "none"})


def _fact_is_known(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _UNKNOWN_FACT_VALUES


def _linux_cpu_model(path: Path = Path("/proc/cpuinfo")) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip():
            values.setdefault(key.strip().lower(), value.strip())
    for key in ("model name", "hardware", "processor"):
        value = values.get(key)
        if value and not value.isdigit():
            return value[:240]
    return "unknown"


def _lscpu_cpu_model(text: str) -> str:
    fields = parse_lscpu_fields(text)
    for key in ("model name", "cpu model name"):
        value = fields.get(key)
        if _fact_is_known(value):
            return str(value)[:240]
    return "unknown"


def _linux_cpu_identifiers(path: Path = Path("/proc/cpuinfo")) -> dict[str, str]:
    """Retain explicit Arm implementer/part IDs when a marketing model is masked."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    wanted = {
        "cpu implementer",
        "cpu architecture",
        "cpu variant",
        "cpu part",
        "cpu revision",
        "hardware",
    }
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        normalized = key.strip().lower()
        if separator and normalized in wanted and value.strip():
            result.setdefault(normalized.replace(" ", "_"), value.strip()[:120])
    return result


@dataclass(frozen=True, slots=True)
class DistributionInfo:
    """Public distribution identity parsed without executing ``os-release``."""

    pretty_name: str
    identifier: str | None = None
    version_id: str | None = None
    source: str = "/etc/os-release"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "pretty_name": self.pretty_name,
            "identifier": self.identifier,
            "version_id": self.version_id,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceLimitation:
    """Stable machine-readable reason that a required target fact is unavailable."""

    code: str
    field: str
    reason: str
    sources_checked: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "field": self.field,
            "reason": self.reason,
            "sources_checked": list(self.sources_checked),
        }


def parse_os_release(text: str, *, source: str = "/etc/os-release") -> DistributionInfo | None:
    """Parse the public subset of the freedesktop ``os-release`` format."""

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if not separator or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
            continue
        try:
            parts = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        if len(parts) == 1:
            values[key] = parts[0]
        elif not parts and raw_value == "":
            values[key] = ""
    pretty_name = values.get("PRETTY_NAME")
    if not _fact_is_known(pretty_name):
        name = values.get("NAME")
        version = values.get("VERSION") or values.get("VERSION_ID")
        pretty_name = " ".join(item for item in (name, version) if _fact_is_known(item))
    if not _fact_is_known(pretty_name):
        return None
    identifier = values.get("ID")
    version_id = values.get("VERSION_ID")
    return DistributionInfo(
        pretty_name=str(pretty_name)[:240],
        identifier=str(identifier)[:120] if _fact_is_known(identifier) else None,
        version_id=str(version_id)[:120] if _fact_is_known(version_id) else None,
        source=source,
    )


def _linux_distribution(path: Path = Path("/etc/os-release")) -> DistributionInfo | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_os_release(text, source=str(path))


def _darwin_cpu_model() -> str:
    return _run_text(("sysctl", "-n", "machdep.cpu.brand_string")) or "unknown"


def _tool_version(command: Sequence[str]) -> str | None:
    output = _run_text(command)
    if not output:
        return None
    return output.splitlines()[0][:240]


_IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6_PATTERN = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F:]*")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_BEARER_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")
_TOKEN_ARGUMENT_PATTERN = re.compile(
    r"(?i)(--?(?:token|api[-_]?key|password|secret)(?:=|\s+))[^\s]+"
)


def redact_text(value: str) -> str:
    """Redact local identity, routable IPs, credentials, and home paths."""

    redacted = value
    home = str(Path.home())
    username = getpass.getuser()
    hostname = socket.gethostname()
    replacements = (
        (home, "<HOME>"),
        (hostname, "<HOST>"),
        (username, "<USER>"),
    )
    for needle, replacement in replacements:
        if needle and len(needle) >= 3:
            redacted = redacted.replace(needle, replacement)
    redacted = _URL_CREDENTIAL_PATTERN.sub(r"\1<REDACTED>@", redacted)
    redacted = _BEARER_PATTERN.sub(r"\1<REDACTED>", redacted)
    redacted = _TOKEN_ARGUMENT_PATTERN.sub(r"\1<REDACTED>", redacted)

    def replace_ip(match: re.Match[str]) -> str:
        address = match.group(0)
        if address.startswith("127.") or address == "0.0.0.0":
            return address
        octets = address.split(".")
        if any(int(octet) > 255 for octet in octets):
            return address
        return "<IP>"

    redacted = _IPV4_PATTERN.sub(replace_ip, redacted)

    def replace_ipv6(match: re.Match[str]) -> str:
        address = match.group(0)
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return address
        return address if parsed.is_loopback or parsed.is_unspecified else "<IP>"

    return _IPV6_PATTERN.sub(replace_ipv6, redacted)


def redact_public_data(value: Any) -> Any:
    """Recursively redact strings while retaining JSON-compatible structure."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(key): redact_public_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [redact_public_data(item) for item in value]
    if isinstance(value, list):
        return [redact_public_data(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SystemInfo:
    schema_version: Literal[SYSTEM_INFO_SCHEMA_VERSION]
    captured_at: str
    architecture: str
    architecture_raw: str
    operating_system: str
    kernel: str
    python_version: str
    arm64: bool
    real_benchmark_eligible: bool
    cpu_features: Mapping[str, FeatureEvidence]
    topology: Topology
    memory_total_bytes: int | None
    filesystem_free_bytes: int | None
    tool_versions: Mapping[str, str | None]
    cpu_model: str = "unknown"
    cpu_identifiers: Mapping[str, str] = field(default_factory=dict)
    distribution: DistributionInfo | None = None
    sockets: int | None = None
    numa_nodes: int | None = None
    cache_layout: tuple[CacheInfo, ...] = field(default_factory=tuple)
    provenance_limitations: tuple[ProvenanceLimitation, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self, *, public: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "architecture": self.architecture,
            "architecture_raw": self.architecture_raw,
            "operating_system": self.operating_system,
            "kernel": self.kernel,
            "cpu_model": self.cpu_model,
            "cpu_identifiers": dict(self.cpu_identifiers),
            "distribution": self.distribution.to_dict() if self.distribution else None,
            "python_version": self.python_version,
            "arm64": self.arm64,
            "real_benchmark_eligible": self.real_benchmark_eligible,
            "cpu_features": features_to_dict(self.cpu_features),
            "topology": self.topology.to_dict(),
            "memory_total_bytes": self.memory_total_bytes,
            "filesystem_free_bytes": self.filesystem_free_bytes,
            "tool_versions": dict(self.tool_versions),
            "sockets": self.sockets,
            "numa_nodes": self.numa_nodes,
            "cache_layout": [cache.to_dict() for cache in self.cache_layout],
            "provenance_limitations": [
                limitation.to_dict() for limitation in self.provenance_limitations
            ],
            "target_provenance_status": ("limited" if self.provenance_limitations else "complete"),
            "sources": list(self.sources),
            "limitations": list(self.limitations),
        }
        return redact_public_data(payload) if public else payload

    def to_schema_payload(self) -> dict[str, object]:
        """Map doctor facts to :mod:`a64pilot.schemas` without losing evidence."""

        topology = self.topology.to_dict()
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "collected_at": self.captured_at,
            "architecture": self.architecture,
            "architecture_raw": self.architecture_raw,
            "operating_system": self.operating_system,
            "kernel": self.kernel,
            "cpu_model": self.cpu_model,
            "cpu_identifiers": dict(self.cpu_identifiers),
            "distribution": self.distribution.to_dict() if self.distribution else None,
            "python_version": self.python_version,
            "arm64": self.arm64,
            "real_benchmark_eligible": self.real_benchmark_eligible,
            "logical_cores": self.topology.logical_cpus,
            "physical_cores": self.topology.physical_cores,
            "memory_bytes": self.memory_total_bytes,
            "filesystem_free_bytes": self.filesystem_free_bytes,
            "tool_versions": dict(self.tool_versions),
            "features": features_to_dict(self.cpu_features),
            "topology": topology,
            "sockets": self.sockets,
            "numa_nodes": self.numa_nodes,
            "cache_layout": [cache.to_dict() for cache in self.cache_layout],
            "provenance_limitations": [
                limitation.to_dict() for limitation in self.provenance_limitations
            ],
            "target_provenance_status": ("limited" if self.provenance_limitations else "complete"),
            "affinity_candidates": {
                candidate.name: list(candidate.cpus)
                for candidate in self.topology.affinity_candidates
            },
            "sources": list(self.sources),
            "limitations": list(self.limitations),
            "public_redacted": True,
        }
        return redact_public_data(payload)

    def to_schema(self) -> Any:
        """Validate and return the repository's shared Pydantic SystemInfo."""

        from a64pilot.schemas import SystemInfo as SystemInfoSchema

        return SystemInfoSchema.model_validate(self.to_schema_payload())


def collect_system_info(
    *,
    filesystem_path: Path | None = None,
    system: str | None = None,
    machine: str | None = None,
    cpuinfo_path: Path = Path("/proc/cpuinfo"),
    os_release_path: Path = Path("/etc/os-release"),
    sysfs_root: Path = Path("/sys/devices/system/cpu"),
) -> SystemInfo:
    """Collect a typed doctor report without rejecting unsupported hosts."""

    system_name = system or platform.system()
    raw_machine = machine or platform.machine() or _run_text(("uname", "-m")) or "unknown"
    architecture = normalize_architecture(raw_machine)
    system_lower = system_name.lower()
    sources = ["platform", "uname"]
    limitations: list[str] = []
    provenance_limitations: list[ProvenanceLimitation] = []

    def record_provenance_limitation(
        code: str, field_name: str, reason: str, checked: Sequence[str]
    ) -> None:
        item = ProvenanceLimitation(code, field_name, reason, tuple(checked))
        provenance_limitations.append(item)
        limitations.append(f"{code}: {reason}")

    lscpu_text = None
    if system_lower == "linux":
        lscpu_text = _run_text(("lscpu", "--json")) or _run_text(("lscpu",))
        if lscpu_text:
            sources.append("lscpu")
    features = detect_cpu_features(system=system_name, cpuinfo_path=cpuinfo_path)
    topology = detect_topology(
        system=system_name,
        sysfs_root=sysfs_root,
        lscpu_text=lscpu_text,
    )
    sources.extend(topology.sources)
    limitations.extend(topology.limitations)

    if system_lower == "linux":
        memory_total = _linux_memory_bytes()
        cpu_model = _lscpu_cpu_model(lscpu_text or "")
        if not _fact_is_known(cpu_model):
            cpu_model = _linux_cpu_model(cpuinfo_path)
        cpu_identifiers = _linux_cpu_identifiers(cpuinfo_path)
        distribution = _linux_distribution(os_release_path)
        if Path("/proc/meminfo").is_file():
            sources.append("/proc/meminfo")
        if cpuinfo_path.is_file():
            sources.append(str(cpuinfo_path))
        if distribution is not None:
            sources.append(distribution.source)
    elif system_lower == "darwin":
        memory_total = _darwin_memory_bytes()
        cpu_model = _darwin_cpu_model()
        cpu_identifiers = {}
        distribution = None
        if memory_total is not None:
            sources.append("sysctl hw.memsize")
        if cpu_model != "unknown":
            sources.append("sysctl machdep.cpu.brand_string")
    else:
        memory_total = None
        cpu_model = "unknown"
        cpu_identifiers = {}
        distribution = None
        limitations.append("total memory discovery unavailable")

    tool_versions = {
        "cmake": _tool_version(("cmake", "--version")),
        "compiler": _tool_version((os.environ.get("CC", "cc"), "--version")),
        "python": sys.version.splitlines()[0],
    }

    if not _fact_is_known(cpu_model):
        runner_context = (
            "The hosted CI runner did not expose a CPU model name"
            if os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
            else "The operating system did not expose a CPU model name"
        )
        record_provenance_limitation(
            "cpu_model_not_exposed",
            "cpu_model",
            f"{runner_context}; no model is inferred from Arm implementer/part IDs.",
            ("lscpu Model name", str(cpuinfo_path)),
        )
        cpu_model = "unknown"
    if distribution is None:
        record_provenance_limitation(
            "distribution_not_exposed",
            "distribution",
            "A Linux distribution identity was not available; the kernel name is not used as a substitute.",
            (str(os_release_path),),
        )
    if topology.physical_cores is None:
        record_provenance_limitation(
            "physical_core_count_not_exposed",
            "physical_cores",
            (
                "Physical core count was unavailable; logical CPUs are not used as a "
                "physical-core substitute."
            ),
            ("Linux sysfs package/core identifiers", "lscpu Core(s) per socket"),
        )
    if memory_total is None:
        record_provenance_limitation(
            "memory_total_not_exposed",
            "memory_bytes",
            "Total system memory was unavailable from the operating-system memory interface.",
            ("/proc/meminfo MemTotal", "sysctl hw.memsize"),
        )
    if not _fact_is_known(tool_versions.get("compiler")):
        record_provenance_limitation(
            "compiler_version_not_exposed",
            "compiler",
            "The configured C/C++ compiler did not expose a version string.",
            (f"{os.environ.get('CC', 'cc')} --version",),
        )
    if not any(item.evidence for item in features.values()):
        record_provenance_limitation(
            "instruction_features_not_exposed",
            "instruction_features",
            (
                "No explicit Arm instruction-feature flags were exposed; unsupported features "
                "are not inferred from the absence of operating-system evidence."
            ),
            (str(cpuinfo_path), "lscpu Flags/Features", "sysctl hw.optional.arm.*"),
        )
    if not topology.cores or any(
        core.capacity is None and core.max_frequency_khz is None for core in topology.cores
    ):
        record_provenance_limitation(
            "heterogeneous_clusters_not_exposed",
            "heterogeneous_clusters",
            (
                "Per-CPU capacity or maximum-frequency facts were incomplete, so homogeneous "
                "or heterogeneous cluster membership is not inferred."
            ),
            ("Linux sysfs cpu_capacity", "Linux sysfs cpuinfo_max_freq"),
        )
    if not topology.affinity_candidates:
        record_provenance_limitation(
            "affinity_candidates_not_exposed",
            "affinity_candidates",
            "No nonempty operating-system CPU affinity candidate could be derived.",
            ("OS allowed CPU set", "Linux sched_getaffinity/sysfs topology"),
        )
    if topology.sockets is None:
        record_provenance_limitation(
            "socket_count_not_exposed",
            "sockets",
            "CPU socket count was unavailable from visible sysfs package IDs and lscpu.",
            ("Linux sysfs physical_package_id", "lscpu Socket(s)"),
        )
    if topology.numa_nodes is None:
        record_provenance_limitation(
            "numa_count_not_exposed",
            "numa_nodes",
            "NUMA node count was unavailable from visible sysfs node mappings and lscpu.",
            ("Linux sysfs node*/cpulist", "lscpu NUMA node(s)"),
        )
    cache_names = {cache.name for cache in topology.cache_layout}
    for cache_name in ("l1d", "l1i", "l2", "l3"):
        if cache_name in cache_names:
            continue
        record_provenance_limitation(
            f"cache_{cache_name}_not_exposed",
            f"cache_{cache_name}",
            (
                f"No {cache_name.upper()} cache entry was exposed by sysfs or lscpu; "
                "physical absence is not inferred."
            ),
            ("Linux sysfs cpu*/cache/index*", f"lscpu {cache_name.upper()} cache"),
        )

    target_path = filesystem_path or Path.cwd()
    try:
        filesystem_free = shutil.disk_usage(target_path).free
        sources.append("filesystem stat")
    except OSError:
        filesystem_free = None
        limitations.append("filesystem capacity unavailable")

    linux_arm64 = architecture == "aarch64" and system_lower == "linux"
    if architecture != "aarch64":
        limitations.append("real benchmark mode requires an Arm64 host")
    elif system_lower != "linux":
        limitations.append("final service benchmark mode requires Linux on Arm64")

    return SystemInfo(
        schema_version=SCHEMA_VERSION,
        captured_at=datetime.now(UTC).isoformat(),
        architecture=architecture,
        architecture_raw=raw_machine,
        operating_system=system_name,
        kernel=platform.release(),
        python_version=platform.python_version(),
        arm64=architecture == "aarch64",
        real_benchmark_eligible=linux_arm64,
        cpu_features=features,
        topology=topology,
        memory_total_bytes=memory_total,
        filesystem_free_bytes=filesystem_free,
        tool_versions=tool_versions,
        cpu_model=cpu_model,
        cpu_identifiers=cpu_identifiers,
        distribution=distribution,
        sockets=topology.sockets,
        numa_nodes=topology.numa_nodes,
        cache_layout=topology.cache_layout,
        provenance_limitations=tuple(provenance_limitations),
        sources=tuple(dict.fromkeys(sources)),
        limitations=tuple(dict.fromkeys(limitations)),
    )


def render_doctor_markdown(info: SystemInfo) -> str:
    """Render a compact public-safe Markdown companion to the JSON report."""

    supported = [name for name, item in info.cpu_features.items() if item.supported]
    distribution = info.distribution.pretty_name if info.distribution else "not exposed"
    cache_by_name = {cache.name: cache for cache in info.cache_layout}

    def cache_summary(name: str) -> str:
        cache = cache_by_name.get(name)
        if cache is None:
            return "not exposed (see provenance limitations)"
        return f"{cache.total_size_bytes} bytes across {cache.instances} instance(s)"

    capacity_groups: dict[int, list[int]] = {}
    frequency_groups: dict[int, list[int]] = {}
    for core in info.topology.cores:
        if core.capacity is not None:
            capacity_groups.setdefault(core.capacity, []).append(core.cpu_id)
        if core.max_frequency_khz is not None:
            frequency_groups.setdefault(core.max_frequency_khz, []).append(core.cpu_id)
    if len(capacity_groups) > 1:
        heterogeneous_summary = "; ".join(
            f"capacity {value}: CPUs {','.join(map(str, cpus))}"
            for value, cpus in sorted(capacity_groups.items())
        )
    elif len(frequency_groups) > 1:
        heterogeneous_summary = "; ".join(
            f"max {value} kHz: CPUs {','.join(map(str, cpus))}"
            for value, cpus in sorted(frequency_groups.items())
        )
    elif capacity_groups or frequency_groups:
        heterogeneous_summary = "not detected in the exposed capacity/frequency data"
    else:
        heterogeneous_summary = "not exposed"

    lines = [
        "# AArch64 Autopilot Hardware Doctor",
        "",
        f"- Architecture: `{info.architecture}`",
        f"- Operating system: `{info.operating_system}`",
        f"- Kernel: `{redact_text(info.kernel)}`",
        f"- Distribution: `{redact_text(distribution)}`",
        f"- CPU: `{redact_text(info.cpu_model)}`",
        f"- Logical CPUs available: {info.topology.logical_cpus}",
        (
            f"- Physical cores: {info.topology.physical_cores}"
            if info.topology.physical_cores is not None
            else "- Physical cores: not exposed"
        ),
        (
            f"- Total memory: {info.memory_total_bytes} bytes"
            if info.memory_total_bytes is not None
            else "- Total memory: not exposed"
        ),
        f"- Compiler: `{redact_text(info.tool_versions.get('compiler') or 'not exposed')}`",
        f"- CPU sockets: {info.sockets if info.sockets is not None else 'not exposed'}",
        f"- NUMA nodes: {info.numa_nodes if info.numa_nodes is not None else 'not exposed'}",
        f"- L1 data cache: {cache_summary('l1d')}",
        f"- L1 instruction cache: {cache_summary('l1i')}",
        f"- L2 cache: {cache_summary('l2')}",
        f"- L3 cache: {cache_summary('l3')}",
        f"- Heterogeneous core groups: {heterogeneous_summary}",
        f"- Evidence-backed Arm features: {', '.join(supported) if supported else 'none detected'}",
        f"- Real benchmark eligible: {'yes' if info.real_benchmark_eligible else 'no'}",
        (
            "- Target provenance status: complete"
            if not info.provenance_limitations
            else f"- Target provenance status: limited ({len(info.provenance_limitations)} declared)"
        ),
    ]
    if info.cpu_identifiers:
        lines.extend(("", "## Explicit CPU identifiers", ""))
        lines.extend(
            f"- `{key}`: `{redact_text(value)}`"
            for key, value in sorted(info.cpu_identifiers.items())
        )
    lines.extend(("", "## Tool versions", ""))
    lines.extend(
        f"- `{name}`: `{redact_text(version or 'not exposed')}`"
        for name, version in sorted(info.tool_versions.items())
    )
    lines.extend(("", "## Affinity candidates", ""))
    if info.topology.affinity_candidates:
        lines.extend(
            (
                f"- `{candidate.name}`: CPUs `{','.join(map(str, candidate.cpus))}`; "
                f"evidence: {', '.join(candidate.evidence) or 'not recorded'}"
            )
            for candidate in info.topology.affinity_candidates
        )
    else:
        lines.append("- No enforceable affinity candidate was exposed.")
    if info.provenance_limitations:
        lines.extend(("", "## Target provenance limitations", ""))
        lines.extend(
            (
                f"- `{item.code}` (`{item.field}`): {redact_text(item.reason)} "
                f"Checked: {', '.join(redact_text(source) for source in item.sources_checked)}."
            )
            for item in info.provenance_limitations
        )
    structured = {f"{item.code}: {item.reason}" for item in info.provenance_limitations}
    other_limitations = [item for item in info.limitations if item not in structured]
    if other_limitations:
        lines.extend(("", "## Other limitations", ""))
        lines.extend(f"- {redact_text(item)}" for item in other_limitations)
    return "\n".join(lines) + "\n"
