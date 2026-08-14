from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import a64pilot.hardware.detect as detect_module
import a64pilot.hardware.topology as topology_module
from a64pilot.hardware.affinity import AffinityError, validate_affinity
from a64pilot.hardware.cpu_features import (
    FeatureEvidence,
    merge_feature_reports,
    parse_apple_sysctl,
    parse_linux_cpuinfo,
)
from a64pilot.hardware.detect import (
    ArchitectureError,
    DistributionInfo,
    SystemInfo,
    assert_arm64_benchmark,
    collect_system_info,
    normalize_architecture,
    parse_os_release,
    redact_text,
    render_doctor_markdown,
)
from a64pilot.hardware.topology import (
    AffinityCandidate,
    CacheInfo,
    CoreInfo,
    Topology,
    candidate_affinity_sets,
    format_cpu_list,
    parse_cpu_list,
    parse_linux_cache_sysfs,
    parse_linux_sysfs,
    parse_lscpu_cache_layout,
    parse_lscpu_fields,
)
from a64pilot.schemas import SYSTEM_INFO_SCHEMA_VERSION
from a64pilot.schemas import SystemInfo as SystemInfoSchema

FIXTURES = Path(__file__).parent / "fixtures"

LSCPU_JSON = """{
  "lscpu": [
    {"field": "Architecture:", "data": "aarch64"},
    {"field": "Model name:", "data": "Neoverse-N2"},
    {"field": "Socket(s):", "data": "1"},
    {"field": "Core(s) per socket:", "data": "4"},
    {"field": "NUMA node(s):", "data": "1"},
    {"field": "L1d cache:", "data": "256 KiB (4 instances)"},
    {"field": "L1i cache:", "data": "256 KiB (4 instances)"},
    {"field": "L2 cache:", "data": "4 MiB (4 instances)"},
    {"field": "L3 cache:", "data": "32 MiB (1 instance)"},
    {"field": "Flags:", "data": "asimddp i8mm sve"}
  ]
}"""


def _complete_cache_layout() -> tuple[CacheInfo, ...]:
    return (
        CacheInfo("l1d", 1, "data", 128 * 1024, 2, source="fixture"),
        CacheInfo("l1i", 1, "instruction", 128 * 1024, 2, source="fixture"),
        CacheInfo("l2", 2, "unified", 2 * 1024**2, 2, source="fixture"),
        CacheInfo("l3", 3, "unified", 16 * 1024**2, 1, source="fixture"),
    )


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


def test_lscpu_json_parses_model_counts_and_required_cache_levels() -> None:
    fields = parse_lscpu_fields(LSCPU_JSON)
    caches = {item.name: item for item in parse_lscpu_cache_layout(LSCPU_JSON)}

    assert fields["model name"] == "Neoverse-N2"
    assert fields["socket(s)"] == "1"
    assert set(caches) == {"l1d", "l1i", "l2", "l3"}
    assert caches["l1d"].total_size_bytes == 256 * 1024
    assert caches["l1d"].instances == 4
    assert caches["l3"].total_size_bytes == 32 * 1024**2


def test_os_release_is_parsed_without_shell_evaluation() -> None:
    distro = parse_os_release('PRETTY_NAME="Ubuntu 24.04.3 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n')

    assert distro == DistributionInfo(
        pretty_name="Ubuntu 24.04.3 LTS",
        identifier="ubuntu",
        version_id="24.04",
    )


def test_sysfs_cache_parser_deduplicates_shared_instances(tmp_path: Path) -> None:
    cpu_root = tmp_path / "cpu"
    for cpu in (0, 1):
        cache = cpu_root / f"cpu{cpu}" / "cache"
        for index, (level, kind, size, shared) in enumerate(
            (
                (1, "Data", "64K", str(cpu)),
                (1, "Instruction", "64K", str(cpu)),
                (2, "Unified", "1M", str(cpu)),
                (3, "Unified", "8M", "0-1"),
            )
        ):
            entry = cache / f"index{index}"
            entry.mkdir(parents=True)
            (entry / "level").write_text(str(level))
            (entry / "type").write_text(kind)
            (entry / "size").write_text(size)
            (entry / "shared_cpu_list").write_text(shared)
            (entry / "id").write_text(str(index if level < 3 else 99))

    caches = {
        item.name: item
        for item in parse_linux_cache_sysfs(sysfs_root=cpu_root, allowed_cpus=(0, 1))
    }

    assert caches["l1d"].instances == 2
    assert caches["l1d"].total_size_bytes == 128 * 1024
    assert caches["l2"].instances == 2
    assert caches["l3"].instances == 1
    assert caches["l3"].shared_cpu_lists == ((0, 1),)


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
    assert mac.to_schema().real_benchmark_eligible is False
    assert linux.real_benchmark_eligible is True


def test_linux_doctor_prefers_lscpu_model_and_declares_unexposed_cluster_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor: 0\nCPU implementer: 0x41\nCPU part: 0xd49\nFeatures: asimddp\n")
    os_release = tmp_path / "os-release"
    os_release.write_text('PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n')
    sysfs = tmp_path / "missing-sysfs"

    def command(command: tuple[str, ...]) -> str | None:
        if command and command[0] == "lscpu":
            return LSCPU_JSON
        if command and command[-1] == "--version":
            return "fixture tool 1.0"
        return None

    monkeypatch.setattr(detect_module, "_run_text", command)
    monkeypatch.setattr(detect_module, "detect_cpu_features", lambda **_kwargs: {})
    monkeypatch.setattr(detect_module, "_linux_memory_bytes", lambda: 16 * 1024**3)
    info = collect_system_info(
        filesystem_path=tmp_path,
        system="Linux",
        machine="aarch64",
        cpuinfo_path=cpuinfo,
        os_release_path=os_release,
        sysfs_root=sysfs,
    )
    shared = info.to_schema()

    assert shared.real_benchmark_eligible
    assert shared.cpu_model == "Neoverse-N2"
    assert shared.cpu_identifiers["cpu_implementer"] == "0x41"
    assert shared.distribution is not None
    assert shared.distribution.pretty_name == "Ubuntu 24.04 LTS"
    assert (shared.sockets, shared.numa_nodes) == (1, 1)
    assert shared.physical_cores == 4
    assert shared.memory_bytes == 16 * 1024**3
    assert shared.tool_versions["compiler"] == "fixture tool 1.0"
    assert {cache.name for cache in shared.cache_layout} == {"l1d", "l1i", "l2", "l3"}
    limitation_fields = {item.field for item in shared.provenance_limitations}
    assert {"instruction_features", "heterogeneous_clusters"} <= limitation_fields
    assert shared.target_provenance_status == "limited"


def test_linux_doctor_declares_every_masked_required_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor: 0\nCPU implementer: 0x41\nCPU part: 0xd0c\n")
    monkeypatch.setattr(detect_module, "_run_text", lambda _command: None)
    monkeypatch.setattr(detect_module, "_linux_memory_bytes", lambda: None)
    monkeypatch.setattr(detect_module, "detect_cpu_features", lambda **_kwargs: {})
    monkeypatch.setattr(topology_module, "_command_text", lambda _command: None)

    info = collect_system_info(
        filesystem_path=tmp_path,
        system="Linux",
        machine="aarch64",
        cpuinfo_path=cpuinfo,
        os_release_path=tmp_path / "missing-os-release",
        sysfs_root=tmp_path / "missing-sysfs",
    )
    shared = info.to_schema()
    fields = {item.field for item in shared.provenance_limitations}

    assert shared.cpu_model == "unknown"
    assert shared.physical_cores is None
    assert shared.memory_bytes is None
    assert shared.tool_versions["compiler"] is None
    assert shared.target_provenance_status == "limited"
    assert {
        "cpu_model",
        "distribution",
        "physical_cores",
        "memory_bytes",
        "compiler",
        "sockets",
        "numa_nodes",
        "cache_l1d",
        "cache_l1i",
        "cache_l2",
        "cache_l3",
        "instruction_features",
        "heterogeneous_clusters",
    } <= fields
    assert all(
        any(text.startswith(f"{item.code}:") for text in shared.limitations)
        for item in shared.provenance_limitations
    )


def test_schema_rejects_unknown_cpu_model_with_empty_limitations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detect_module, "_run_text", lambda _command: None)
    monkeypatch.setattr(detect_module, "detect_cpu_features", lambda **_kwargs: {})
    monkeypatch.setattr(topology_module, "_command_text", lambda _command: None)
    info = collect_system_info(
        filesystem_path=tmp_path,
        system="Linux",
        machine="aarch64",
        cpuinfo_path=tmp_path / "missing-cpuinfo",
        os_release_path=tmp_path / "missing-os-release",
        sysfs_root=tmp_path / "missing-sysfs",
    )
    payload = info.to_schema_payload()
    payload["provenance_limitations"] = []
    payload["limitations"] = []

    with pytest.raises(ValidationError, match="absent without a structured limitation"):
        SystemInfoSchema.model_validate(payload)


@pytest.mark.parametrize(
    ("schema_version", "expected_error_type"),
    [
        pytest.param(None, "missing", id="missing"),
        pytest.param("1.0.0", "literal_error", id="v1"),
        pytest.param("3.0.0", "literal_error", id="future"),
    ],
)
def test_system_info_schema_rejects_missing_old_and_future_versions(
    schema_version: str | None, expected_error_type: str
) -> None:
    payload: dict[str, object] = {
        "architecture": "aarch64",
        "operating_system": "Linux",
        "kernel": "fixture",
        "logical_cores": 1,
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version

    with pytest.raises(ValidationError) as error:
        SystemInfoSchema.model_validate(payload)

    version_errors = [row for row in error.value.errors() if row["loc"] == ("schema_version",)]
    assert len(version_errors) == 1
    assert version_errors[0]["type"] == expected_error_type


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
    caches = _complete_cache_layout()
    topology = Topology(
        logical_cpus=2,
        physical_cores=2,
        allowed_cpus=(0, 1),
        cores=(CoreInfo(0, capacity=1024), CoreInfo(1, capacity=1024)),
        affinity_candidates=(AffinityCandidate("all_allowed", (0, 1)),),
        sockets=1,
        numa_nodes=1,
        cache_layout=caches,
    )
    info = SystemInfo(
        schema_version=SYSTEM_INFO_SCHEMA_VERSION,
        captured_at="2026-08-13T00:00:00+00:00",
        architecture="aarch64",
        architecture_raw="arm64",
        operating_system="Darwin",
        kernel="test",
        cpu_model="Fixture Arm CPU",
        python_version="3.12",
        arm64=True,
        real_benchmark_eligible=True,
        cpu_features={"dotprod": FeatureEvidence(True, ("sysctl: explicit",))},
        topology=topology,
        memory_total_bytes=1024,
        filesystem_free_bytes=2048,
        tool_versions={"compiler": "fixture-cc 1.0", "python": "3.12"},
        distribution=DistributionInfo("Fixture Linux", "fixture", "1"),
        sockets=1,
        numa_nodes=1,
        cache_layout=caches,
    )
    payload = info.to_schema_payload()
    assert info.schema_version == SYSTEM_INFO_SCHEMA_VERSION
    assert payload["schema_version"] == SYSTEM_INFO_SCHEMA_VERSION
    assert payload["logical_cores"] == 2
    assert payload["memory_bytes"] == 1024
    assert payload["features"]["dotprod"]["supported"] is True
    assert payload["affinity_candidates"] == {"all_allowed": [0, 1]}
    assert payload["filesystem_free_bytes"] == 2048
    assert payload["real_benchmark_eligible"] is True
    assert payload["sockets"] == 1
    assert {row["name"] for row in payload["cache_layout"]} == {"l1d", "l1i", "l2", "l3"}
    shared = info.to_schema()
    assert shared.schema_version == SYSTEM_INFO_SCHEMA_VERSION
    assert shared.architecture == "aarch64"
    assert shared.logical_cores == 2
    markdown = render_doctor_markdown(info)
    assert "Total memory: 1024 bytes" in markdown
    assert "Compiler: `fixture-cc 1.0`" in markdown
    assert "Heterogeneous core groups: not detected in the exposed" in markdown
    assert "## Tool versions" in markdown
    assert "## Affinity candidates" in markdown
    assert "`all_allowed`: CPUs `0,1`" in markdown
