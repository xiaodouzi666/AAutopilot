"""Public-safe system discovery for the hardware doctor."""

from __future__ import annotations

import getpass
import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cpu_features import FeatureEvidence, detect_cpu_features, features_to_dict
from .topology import Topology, detect_topology

SCHEMA_VERSION = "1.0"
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
    schema_version: str
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
            "python_version": self.python_version,
            "arm64": self.arm64,
            "real_benchmark_eligible": self.real_benchmark_eligible,
            "cpu_features": features_to_dict(self.cpu_features),
            "topology": self.topology.to_dict(),
            "memory_total_bytes": self.memory_total_bytes,
            "filesystem_free_bytes": self.filesystem_free_bytes,
            "tool_versions": dict(self.tool_versions),
            "sources": list(self.sources),
            "limitations": list(self.limitations),
        }
        return redact_public_data(payload) if public else payload

    def to_schema_payload(self) -> dict[str, object]:
        """Map doctor facts to :mod:`a64pilot.schemas` without losing evidence."""

        topology = self.topology.to_dict()
        payload: dict[str, object] = {
            "schema_version": "1.0.0",
            "collected_at": self.captured_at,
            "architecture": self.architecture,
            "architecture_raw": self.architecture_raw,
            "operating_system": self.operating_system,
            "kernel": self.kernel,
            "cpu_model": self.cpu_model,
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
) -> SystemInfo:
    """Collect a typed doctor report without rejecting unsupported hosts."""

    system_name = system or platform.system()
    raw_machine = machine or platform.machine() or _run_text(("uname", "-m")) or "unknown"
    architecture = normalize_architecture(raw_machine)
    system_lower = system_name.lower()
    sources = ["platform", "uname"]
    limitations: list[str] = []

    features = detect_cpu_features(system=system_name)
    topology = detect_topology(system=system_name)
    sources.extend(topology.sources)
    limitations.extend(topology.limitations)

    if system_lower == "linux":
        memory_total = _linux_memory_bytes()
        cpu_model = _linux_cpu_model()
        if Path("/proc/meminfo").is_file():
            sources.append("/proc/meminfo")
        if Path("/proc/cpuinfo").is_file():
            sources.append("/proc/cpuinfo")
    elif system_lower == "darwin":
        memory_total = _darwin_memory_bytes()
        cpu_model = _darwin_cpu_model()
        if memory_total is not None:
            sources.append("sysctl hw.memsize")
        if cpu_model != "unknown":
            sources.append("sysctl machdep.cpu.brand_string")
    else:
        memory_total = None
        cpu_model = "unknown"
        limitations.append("total memory discovery unavailable")

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
        tool_versions={
            "cmake": _tool_version(("cmake", "--version")),
            "compiler": _tool_version((os.environ.get("CC", "cc"), "--version")),
            "python": sys.version.splitlines()[0],
        },
        cpu_model=cpu_model,
        sources=tuple(dict.fromkeys(sources)),
        limitations=tuple(dict.fromkeys(limitations)),
    )


def render_doctor_markdown(info: SystemInfo) -> str:
    """Render a compact public-safe Markdown companion to the JSON report."""

    supported = [name for name, item in info.cpu_features.items() if item.supported]
    lines = [
        "# AArch64 Autopilot Hardware Doctor",
        "",
        f"- Architecture: `{info.architecture}`",
        f"- Operating system: `{info.operating_system}`",
        f"- Kernel: `{redact_text(info.kernel)}`",
        f"- CPU: `{redact_text(info.cpu_model)}`",
        f"- Logical CPUs available: {info.topology.logical_cpus}",
        f"- Physical cores: {info.topology.physical_cores}",
        f"- Evidence-backed Arm features: {', '.join(supported) if supported else 'none detected'}",
        f"- Real benchmark eligible: {'yes' if info.real_benchmark_eligible else 'no'}",
    ]
    if info.limitations:
        lines.extend(("", "## Limitations", ""))
        lines.extend(f"- {redact_text(item)}" for item in info.limitations)
    return "\n".join(lines) + "\n"
