from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from a64pilot.models.checksum import sha256_file, verify_manifest
from a64pilot.models.download import (
    DownloadError,
    RemoteFile,
    RepositorySnapshot,
    download_models,
    write_manifest,
)
from a64pilot.models.gguf import GgufError, parse_gguf_inventory, verify_model_inventory
from a64pilot.models.registry import (
    OFFICIAL_QWEN_REPOSITORIES,
    RegistryError,
    default_registry,
    get_model,
    required_registry,
    resolve_filename,
)


class FakeHubClient:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.download_calls = 0

    def snapshot(self, repository: str, revision: str) -> RepositorySnapshot:
        filename = get_model("weak-q4-0").expected_filename
        return RepositorySnapshot(
            repository,
            revision,
            "c" * 40,
            (
                RemoteFile(
                    filename,
                    size=len(self.data),
                    sha256=hashlib.sha256(self.data).hexdigest(),
                    etag="lfs-etag",
                ),
            ),
        )

    def download(
        self,
        repository: str,
        filename: str,
        revision: str,
        local_dir: Path,
        *,
        force: bool = False,
    ) -> Path:
        self.download_calls += 1
        path = local_dir / Path(filename).name
        path.write_bytes(self.data)
        return path


def _tiny_gguf(*tensors: tuple[str, tuple[int, ...], int]) -> bytes:
    """Build a data-free GGUF v3 header for inventory parser tests."""

    payload = bytearray(b"GGUF")
    payload.extend(struct.pack("<IQQ", 3, len(tensors), 0))
    for name, dimensions, tensor_type in tensors:
        encoded = name.encode("utf-8")
        payload.extend(struct.pack("<Q", len(encoded)))
        payload.extend(encoded)
        payload.extend(struct.pack("<I", len(dimensions)))
        payload.extend(struct.pack(f"<{len(dimensions)}Q", *dimensions))
        payload.extend(struct.pack("<IQ", tensor_type, 0))
    return bytes(payload)


def test_registry_has_reviewed_official_candidates() -> None:
    registry = default_registry()
    assert len(registry) == 3
    assert {spec.repository for spec in registry} == OFFICIAL_QWEN_REPOSITORIES
    assert all(spec.license_id == "Apache-2.0" for spec in registry)
    assert all(len(spec.revision) == 40 and spec.revision != "main" for spec in registry)
    assert all(len(spec.expected_sha256) == 64 for spec in registry)
    assert all(spec.expected_bytes > 0 for spec in registry)
    assert {spec.model_id for spec in registry}.issuperset(
        {"weak-q4-0", "strong-q4-0", "strong-q8-0"}
    )
    assert {spec.model_id for spec in required_registry()} == {
        "weak-q4-0",
        "strong-q4-0",
        "strong-q8-0",
    }
    assert all(spec.kleidiai_compatible for spec in required_registry())


def test_registry_pins_official_qwen_file_metadata() -> None:
    expected = {
        "weak-q4-0": (
            "9217f5db79a29953eb74d5343926648285ec7e67",
            "qwen2.5-0.5b-instruct-q4_0.gguf",
            428_730_208,
            "7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed",
        ),
        "strong-q4-0": (
            "91cad51170dc346986eccefdc2dd33a9da36ead9",
            "qwen2.5-1.5b-instruct-q4_0.gguf",
            1_066_227_232,
            "dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb",
        ),
        "strong-q8-0": (
            "91cad51170dc346986eccefdc2dd33a9da36ead9",
            "qwen2.5-1.5b-instruct-q8_0.gguf",
            1_894_532_128,
            "d7efb072e7724d25048a4fda0a3e10b04bdef5d06b1403a1c93bd9f1240a63c8",
        ),
    }
    assert {
        spec.model_id: (
            spec.revision,
            spec.expected_filename,
            spec.expected_bytes,
            spec.expected_sha256,
        )
        for spec in required_registry()
    } == expected
    assert get_model("weak-q4-0").expected_tensor_histogram == (
        ("F32", 121),
        ("Q4_0", 169),
        ("Q8_0", 1),
    )
    strong = get_model("strong-q4-0")
    assert strong.expected_tensor_histogram == (("F32", 141), ("Q4_0", 197), ("Q6_K", 1))
    assert [fallback.to_dict() for fallback in strong.reviewed_kleidiai_fallbacks] == [
        {"name": "output.weight", "type": "Q6_K", "dimensions": [1536, 151936]}
    ]


def test_filename_resolution_is_case_insensitive() -> None:
    spec = get_model("strong-q4-0")
    remote = spec.expected_filename.upper()
    assert resolve_filename(spec, ["README.md", remote]) == remote


def test_ambiguous_quantization_fallback_is_rejected() -> None:
    spec = get_model("strong-q4-0")
    with pytest.raises(RegistryError, match="ambiguous"):
        resolve_filename(spec, ["a-q4_0.gguf", "b-q4_0.gguf"])


def test_dry_run_is_offline_and_side_effect_free(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    manifest = download_models(output_dir=model_dir, dry_run=True)
    assert manifest.dry_run
    assert len(manifest.models) == 3
    assert all(row.status == "planned" and row.sha256 is None for row in manifest.models)
    assert not model_dir.exists()
    with pytest.raises(DownloadError, match="at least one"):
        download_models((), output_dir=model_dir, dry_run=True)


def test_download_hashes_manifests_and_resumes(tmp_path: Path) -> None:
    data = _tiny_gguf(("weight", (32, 32), 2))
    client = FakeHubClient(data)
    model_dir = tmp_path / "models"
    source = tmp_path / "source.gguf"
    source.write_bytes(data)
    inventory = parse_gguf_inventory(source)
    spec = replace(
        get_model("weak-q4-0"),
        revision="c" * 40,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_bytes=len(data),
        expected_tensor_histogram=inventory.histogram,
        expected_tensor_inventory_sha256=inventory.sha256,
        reviewed_kleidiai_fallbacks=(),
    )
    manifest = download_models((spec,), output_dir=model_dir, client=client)
    row = manifest.models[0]
    assert row.status == "complete"
    assert row.resolved_revision == "c" * 40
    assert row.sha256 == hashlib.sha256(data).hexdigest()
    assert row.bytes == len(data)
    assert client.download_calls == 1

    second = download_models((spec,), output_dir=model_dir, client=client)
    assert second.models[0].resumed
    assert client.download_calls == 1

    manifest_path = tmp_path / "model-manifest.json"
    write_manifest(second, manifest_path)
    payload = json.loads(manifest_path.read_text())
    assert payload["models"][0]["repository"] == spec.repository
    assert payload["models"][0]["quantization"] == "Q4_0"
    assert payload["models"][0]["kleidiai_compatible"] is True
    assert verify_manifest(payload, models_dir=model_dir).valid
    shared = second.to_schema()
    assert shared.models[0].revision == "c" * 40
    assert shared.models[0].local_path == spec.expected_filename
    assert shared.models[0].tensor_type_histogram == {"Q4_0": 1}


def test_gguf_inventory_rejects_truncation_and_inventory_substitution(tmp_path: Path) -> None:
    path = tmp_path / "tiny.gguf"
    path.write_bytes(_tiny_gguf(("output.weight", (1536, 151936), 14)))
    parsed = parse_gguf_inventory(path)
    assert parsed.histogram == (("Q6_K", 1),)
    assert parsed.tensors[0].name == "output.weight"

    spec = replace(
        get_model("strong-q4-0"),
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_bytes=path.stat().st_size,
    )
    proof = verify_model_inventory(path, spec, actual_sha256=spec.expected_sha256)
    assert not proof.verified
    assert any("histogram" in error for error in proof.errors)

    path.write_bytes(b"GGUF\x03")
    with pytest.raises(GgufError, match="truncated"):
        parse_gguf_inventory(path)


def test_download_rejects_metadata_different_from_pinned_registry(tmp_path: Path) -> None:
    data = b"metadata substitution"
    client = FakeHubClient(data)
    wrong_hash = replace(
        get_model("weak-q4-0"),
        revision="c" * 40,
        expected_sha256="0" * 64,
        expected_bytes=len(data),
    )
    with pytest.raises(DownloadError, match="SHA-256 differs from pinned registry"):
        download_models((wrong_hash,), output_dir=tmp_path / "hash", client=client)

    wrong_size = replace(
        get_model("weak-q4-0"),
        revision="c" * 40,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_bytes=len(data) + 1,
    )
    with pytest.raises(DownloadError, match="size differs from pinned registry"):
        download_models((wrong_size,), output_dir=tmp_path / "size", client=client)


def test_download_rejects_unexpected_resolved_revision(tmp_path: Path) -> None:
    spec = get_model("weak-q4-0")
    with pytest.raises(DownloadError, match="unexpected revision"):
        download_models((spec,), output_dir=tmp_path / "models", client=FakeHubClient(b"bytes"))


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    payload = {
        "models": [
            {
                "filename": "../outside.gguf",
                "sha256": "0" * 64,
                "bytes": 0,
                "status": "complete",
            }
        ]
    }
    result = verify_manifest(payload, models_dir=tmp_path / "models")
    assert not result.valid
    assert any("escapes" in error for error in result.errors)


def test_streaming_sha256(tmp_path: Path) -> None:
    path = tmp_path / "tiny.gguf"
    path.write_bytes(b"abc")
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()
