from __future__ import annotations

from pathlib import Path

import pytest

from a64pilot.hardware.affinity import AffinityError, validate_affinity
from a64pilot.hardware.cpu_features import (
    FeatureEvidence,
    merge_feature_reports,
    parse_apple_sysctl,
    parse_linux_cpuinfo,
)
from a64pilot.hardware.detect import (
    ArchitectureError,
    SystemInfo,
    assert_arm64_benchmark,
    collect_system_info,
    normalize_architecture,
    redact_text,
)
from a64pilot.hardware.topology import (
    AffinityCandidate,
    CoreInfo,
    Topology,
    candidate_affinity_sets,
    format_cpu_list,
    parse_cpu_list,
    parse_linux_sysfs,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_neoverse_features_are_explicit_and_evidence_backed() -> None:
    report = parse_linux_cpuinfo((FIXTURES / "cpuinfo-neoverse.txt").read_text())
    assert report["dotprod"].supported
    assert report["i8mm"].supported
    assert report["sve"].supported
    assert report["sve2"].supported
    assert report["sme"].supported is False
    assert "/proc/cpuinfo: asimddp" in report["dotprod"].evidence


def test_rk3588_fixture_does_not_infer_features_from_part_name() -> None:
    report = parse_linux_cpuinfo((FIXTURES / "cpuinfo-rk3588.txt").read_text())
    assert report["dotprod"].supported
    assert report["i8mm"].supported is False
    assert report["sve"].supported is False
    assert report["sme"].evidence == ()


def test_apple_sysctl_accepts_only_explicit_true_keys() -> None:
    report = parse_apple_sysctl((FIXTURES / "sysctl-apple-arm64.txt").read_text())
    assert report["dotprod"].supported
    assert report["i8mm"].supported
    assert report["sme2"].supported
    assert not report["sve"].supported
    assert not report["sve2"].supported


def test_feature_sources_merge_without_guessing() -> None:
    linux = parse_linux_cpuinfo("Features: asimddp")
    apple = parse_apple_sysctl("hw.optional.arm.FEAT_I8MM: 1")
    merged = merge_feature_reports(linux, apple)
    assert merged["dotprod"].supported
    assert merged["i8mm"].supported
    assert not merged["sve"].supported


@pytest.mark.parametrize("machine", ["aarch64", "arm64", "AARCH64_be"])
def test_arm64_architectures_are_accepted(machine: str) -> None:
    assert normalize_architecture(machine) == "aarch64"
    assert assert_arm64_benchmark(machine) == "aarch64"


def test_real_benchmark_rejects_non_arm() -> None:
    with pytest.raises(ArchitectureError, match="Arm64"):
        assert_arm64_benchmark("x86_64")


def test_doctor_marks_only_linux_arm64_as_final_benchmark_eligible(tmp_path: Path) -> None:
    mac = collect_system_info(filesystem_path=tmp_path, system="Darwin", machine="arm64")
    linux = collect_system_info(filesystem_path=tmp_path, system="Linux", machine="aarch64")
    assert mac.real_benchmark_eligible is False
    assert "requires Linux" in " ".join(mac.limitations)
    assert linux.real_benchmark_eligible is True


def test_public_redaction_hides_home_and_routable_ip() -> None:
    text = (
        f"artifact={Path.home()}/models host=203.0.113.8 v6=2001:db8::1 local=127.0.0.1 localv6=::1"
    )
    redacted = redact_text(text)
    assert str(Path.home()) not in redacted
    assert "203.0.113.8" not in redacted
    assert "2001:db8::1" not in redacted
    assert "127.0.0.1" in redacted
    assert "::1" in redacted


def test_cpu_list_round_trip() -> None:
    assert parse_cpu_list("0-3,6,8-9") == (0, 1, 2, 3, 6, 8, 9)
    assert format_cpu_list((0, 1, 2, 3, 6, 8, 9)) == "0-3,6,8-9"
    with pytest.raises(ValueError):
        parse_cpu_list("4-2")


def test_topology_candidates_use_capacity_and_physical_cores() -> None:
    cores = (
        CoreInfo(0, core_id=0, package_id=0, capacity=512),
        CoreInfo(1, core_id=1, package_id=0, capacity=512),
        CoreInfo(2, core_id=2, package_id=0, capacity=1024),
        CoreInfo(3, core_id=3, package_id=0, capacity=1024),
    )
    candidates = {item.name: item.cpus for item in candidate_affinity_sets(cores, range(4))}
    assert candidates["all_allowed"] == (0, 1, 2, 3)
    assert candidates["performance_cluster"] == (2, 3)


def test_sysfs_parser_discovers_heterogeneous_cores(tmp_path: Path) -> None:
    cpu_root = tmp_path / "cpu"
    for cpu, capacity, frequency in ((0, 512, 1800000), (1, 1024, 2400000)):
        base = cpu_root / f"cpu{cpu}"
        (base / "topology").mkdir(parents=True)
        (base / "cpufreq").mkdir()
        (base / "topology" / "core_id").write_text(str(cpu))
        (base / "topology" / "physical_package_id").write_text("0")
        (base / "cpufreq" / "cpuinfo_max_freq").write_text(str(frequency))
        (base / "cpu_capacity").write_text(str(capacity))
    cores = parse_linux_sysfs(sysfs_root=cpu_root, allowed_cpus=(0, 1))
    assert [core.capacity for core in cores] == [512, 1024]
    assert [core.max_frequency_khz for core in cores] == [1800000, 2400000]


def test_affinity_validation_rejects_empty_and_disallowed() -> None:
    with pytest.raises(AffinityError):
        validate_affinity(())
    with pytest.raises(AffinityError, match="outside"):
        validate_affinity((0, 2), allowed_cpus=(0, 1))


def test_system_info_maps_to_shared_schema_payload() -> None:
    topology = Topology(
        logical_cpus=2,
        physical_cores=2,
        allowed_cpus=(0, 1),
        cores=(CoreInfo(0), CoreInfo(1)),
        affinity_candidates=(AffinityCandidate("all_allowed", (0, 1)),),
    )
    info = SystemInfo(
        schema_version="1.0",
        captured_at="2026-08-13T00:00:00+00:00",
        architecture="aarch64",
        architecture_raw="arm64",
        operating_system="Darwin",
        kernel="test",
        python_version="3.12",
        arm64=True,
        real_benchmark_eligible=True,
        cpu_features={"dotprod": FeatureEvidence(True, ("sysctl: explicit",))},
        topology=topology,
        memory_total_bytes=1024,
        filesystem_free_bytes=2048,
        tool_versions={},
    )
    payload = info.to_schema_payload()
    assert payload["logical_cores"] == 2
    assert payload["memory_bytes"] == 1024
    assert payload["features"]["dotprod"]["supported"] is True
    assert payload["affinity_candidates"] == {"all_allowed": [0, 1]}
    assert payload["filesystem_free_bytes"] == 2048
    assert payload["real_benchmark_eligible"] is True
    shared = info.to_schema()
    assert shared.architecture == "aarch64"
    assert shared.logical_cores == 2
