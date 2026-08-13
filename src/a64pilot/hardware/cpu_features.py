"""Evidence-backed Arm CPU feature discovery.

Only explicit kernel or operating-system feature flags count as support.  In
particular, this module deliberately does not map CPU marketing names to an
assumed instruction set.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

FEATURE_NAMES: tuple[str, ...] = (
    "dotprod",
    "i8mm",
    "sve",
    "sve2",
    "sme",
    "sme2",
    "bf16",
    "fp16",
)

# Linux feature names are defined by the arm64 kernel ABI.  Aliases included
# here have also appeared in lscpu output.  Exact-token matching avoids false
# positives such as treating ``sve2p1`` as proof of base ``sve``.
_LINUX_ALIASES: Mapping[str, frozenset[str]] = {
    "dotprod": frozenset({"asimddp", "dotprod"}),
    "i8mm": frozenset({"i8mm", "asimdi8mm"}),
    "sve": frozenset({"sve"}),
    "sve2": frozenset({"sve2"}),
    "sme": frozenset({"sme"}),
    "sme2": frozenset({"sme2"}),
    "bf16": frozenset({"bf16", "asimdbf16", "svebf16"}),
    "fp16": frozenset({"fphp", "asimdhp", "fp16"}),
}

_APPLE_KEYS: Mapping[str, frozenset[str]] = {
    "dotprod": frozenset(
        {
            "hw.optional.arm.feat_dotprod",
            "hw.optional.armv8_2_dotprod",
        }
    ),
    "i8mm": frozenset({"hw.optional.arm.feat_i8mm"}),
    "sve": frozenset({"hw.optional.arm.feat_sve"}),
    "sve2": frozenset({"hw.optional.arm.feat_sve2"}),
    "sme": frozenset({"hw.optional.arm.feat_sme"}),
    "sme2": frozenset({"hw.optional.arm.feat_sme2"}),
    "bf16": frozenset({"hw.optional.arm.feat_bf16"}),
    "fp16": frozenset(
        {
            "hw.optional.arm.feat_fp16",
            "hw.optional.armv8_2_fhm",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    """A normalized feature result and the OS facts supporting it."""

    supported: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {"supported": self.supported, "evidence": list(self.evidence)}


def _empty_feature_sets() -> dict[str, set[str]]:
    return {name: set() for name in FEATURE_NAMES}


def _add_linux_tokens(result: dict[str, set[str]], tokens: Iterable[str], source: str) -> None:
    normalized = {token.strip().lower() for token in tokens if token.strip()}
    for feature, aliases in _LINUX_ALIASES.items():
        for alias in sorted(normalized.intersection(aliases)):
            result[feature].add(f"{source}: {alias}")


def _finalize(evidence: Mapping[str, set[str]]) -> dict[str, FeatureEvidence]:
    return {
        name: FeatureEvidence(bool(evidence[name]), tuple(sorted(evidence[name])))
        for name in FEATURE_NAMES
    }


def parse_linux_cpuinfo(text: str, *, source: str = "/proc/cpuinfo") -> dict[str, FeatureEvidence]:
    """Parse explicit ``Features``/``flags`` lines from Linux cpuinfo text."""

    evidence = _empty_feature_sets()
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip().lower() not in {"features", "flags"}:
            continue
        _add_linux_tokens(evidence, value.split(), source)
    return _finalize(evidence)


def parse_lscpu_output(text: str, *, source: str = "lscpu") -> dict[str, FeatureEvidence]:
    """Parse either ``lscpu --json`` or the conventional text format."""

    evidence = _empty_feature_sets()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, dict):
        rows = payload.get("lscpu", [])
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                field_name = str(row.get("field", "")).strip().rstrip(":").lower()
                if field_name not in {"flags", "features"}:
                    continue
                _add_linux_tokens(evidence, str(row.get("data", "")).split(), source)
    else:
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() in {"flags", "features"}:
                _add_linux_tokens(evidence, value.split(), source)
    return _finalize(evidence)


def parse_apple_sysctl(text: str, *, source: str = "sysctl") -> dict[str, FeatureEvidence]:
    """Parse explicit Apple ``hw.optional.arm.*`` feature booleans."""

    evidence = _empty_feature_sets()
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            key, separator, raw_value = line.partition("=")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        value = raw_value.strip().lower()
        if value not in {"1", "true", "yes", "supported"}:
            continue
        for feature, keys in _APPLE_KEYS.items():
            if normalized_key in keys:
                evidence[feature].add(f"{source}: {key.strip()}={raw_value.strip()}")
    return _finalize(evidence)


def merge_feature_reports(*reports: Mapping[str, FeatureEvidence]) -> dict[str, FeatureEvidence]:
    """Union evidence from independent operating-system sources."""

    merged = _empty_feature_sets()
    for report in reports:
        for name in FEATURE_NAMES:
            item = report.get(name)
            if item is not None and item.supported:
                merged[name].update(item.evidence)
    return _finalize(merged)


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
    return completed.stdout


def detect_cpu_features(
    *,
    system: str | None = None,
    cpuinfo_path: Path = Path("/proc/cpuinfo"),
) -> dict[str, FeatureEvidence]:
    """Discover Arm instruction features using available explicit OS sources."""

    system_name = (system or platform.system()).lower()
    reports: list[Mapping[str, FeatureEvidence]] = []

    if system_name == "linux":
        try:
            cpuinfo = cpuinfo_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            cpuinfo = ""
        if cpuinfo:
            reports.append(parse_linux_cpuinfo(cpuinfo))
        lscpu = _run_text(("lscpu", "--json"))
        if lscpu:
            reports.append(parse_lscpu_output(lscpu))
    elif system_name == "darwin":
        sysctl = _run_text(("sysctl", "-a"))
        if sysctl:
            reports.append(parse_apple_sysctl(sysctl))

    return merge_feature_reports(*reports) if reports else _finalize(_empty_feature_sets())


def features_to_dict(features: Mapping[str, FeatureEvidence]) -> dict[str, dict[str, object]]:
    """Return a stable JSON-serializable feature mapping."""

    return {name: features.get(name, FeatureEvidence(False)).to_dict() for name in FEATURE_NAMES}


def feature_tokens(text: str) -> frozenset[str]:
    """Expose normalized flag tokens for diagnostics and focused tests."""

    return frozenset(re.findall(r"[a-zA-Z0-9_]+", text.lower()))
