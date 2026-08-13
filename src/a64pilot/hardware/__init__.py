"""Hardware discovery and affinity helpers for Arm CPU targets."""

from .affinity import AffinityError, AffinityResult, apply_affinity
from .cpu_features import FEATURE_NAMES, FeatureEvidence, detect_cpu_features
from .detect import (
    ArchitectureError,
    SystemInfo,
    assert_arm64_benchmark,
    collect_system_info,
    redact_public_data,
    render_doctor_markdown,
)
from .topology import (
    AffinityCandidate,
    CoreInfo,
    Topology,
    candidate_affinity_sets,
    detect_topology,
)

__all__ = [
    "AffinityCandidate",
    "AffinityError",
    "AffinityResult",
    "ArchitectureError",
    "CoreInfo",
    "FEATURE_NAMES",
    "FeatureEvidence",
    "SystemInfo",
    "Topology",
    "apply_affinity",
    "assert_arm64_benchmark",
    "collect_system_info",
    "candidate_affinity_sets",
    "detect_cpu_features",
    "detect_topology",
    "redact_public_data",
    "render_doctor_markdown",
]
