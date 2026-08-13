"""Resumable official-model downloads with resolved revision manifests."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from .checksum import sha256_file
from .gguf import verify_model_inventory
from .registry import ModelSpec, default_registry, resolve_filename


class DownloadError(RuntimeError):
    """Raised when official model metadata or bytes cannot be verified."""


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True, slots=True)
class RemoteFile:
    path: str
    size: int | None = None
    sha256: str | None = None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    repository: str
    requested_revision: str
    resolved_revision: str
    files: tuple[RemoteFile, ...]


class HubClient(Protocol):
    def snapshot(self, repository: str, revision: str) -> RepositorySnapshot: ...

    def download(
        self,
        repository: str,
        filename: str,
        revision: str,
        local_dir: Path,
        *,
        force: bool = False,
    ) -> Path: ...


class HuggingFaceHubClient:
    """Small adapter that keeps huggingface_hub an optional bootstrap extra."""

    def __init__(self) -> None:
        try:
            from huggingface_hub import HfApi, hf_hub_download
        except ImportError as exc:
            raise DownloadError(
                "model downloads require huggingface_hub; install project model dependencies"
            ) from exc
        self._api = HfApi()
        self._download = hf_hub_download

    def snapshot(self, repository: str, revision: str) -> RepositorySnapshot:
        try:
            info = self._api.model_info(
                repo_id=repository,
                revision=revision,
                files_metadata=True,
            )
        except Exception as exc:  # library-specific exceptions vary by version
            raise DownloadError(
                f"could not resolve metadata for official repository {repository}"
            ) from exc
        resolved = str(getattr(info, "sha", "") or "")
        if not _REVISION_PATTERN.fullmatch(resolved):
            raise DownloadError(f"repository {repository} did not return an immutable revision")
        files: list[RemoteFile] = []
        for sibling in getattr(info, "siblings", ()) or ():
            path = str(getattr(sibling, "rfilename", "") or "")
            if not path:
                continue
            lfs = getattr(sibling, "lfs", None)
            if isinstance(lfs, dict):
                expected_hash = lfs.get("sha256")
                size = lfs.get("size")
            else:
                expected_hash = getattr(lfs, "sha256", None) if lfs else None
                size = getattr(lfs, "size", None) if lfs else None
            files.append(
                RemoteFile(
                    path=path,
                    size=int(size) if isinstance(size, int) else None,
                    sha256=str(expected_hash).lower() if expected_hash else None,
                    etag=str(getattr(sibling, "blob_id", "") or "") or None,
                )
            )
        return RepositorySnapshot(repository, revision, resolved.lower(), tuple(files))

    def download(
        self,
        repository: str,
        filename: str,
        revision: str,
        local_dir: Path,
        *,
        force: bool = False,
    ) -> Path:
        try:
            result = self._download(
                repo_id=repository,
                filename=filename,
                revision=revision,
                local_dir=str(local_dir),
                force_download=force,
            )
        except Exception as exc:  # avoid leaking signed URLs/tokens from exception text
            raise DownloadError(
                f"download failed for {repository}/{PurePosixPath(filename).name}"
            ) from exc
        return Path(result)


@dataclass(frozen=True, slots=True)
class DownloadPlanItem:
    model_id: str
    role: str
    repository: str
    requested_revision: str
    resolved_revision: str | None
    filename: str
    quantization: str
    target_path: str
    license: str
    license_url: str
    status: str
    sha256: str | None = None
    bytes: int | None = None
    etag: str | None = None
    resumed: bool = False
    kleidiai_compatible: bool = False
    tensor_type_histogram: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    tensor_inventory_sha256: str | None = None
    reviewed_kleidiai_fallbacks: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "role": self.role,
            "repository": self.repository,
            "requested_revision": self.requested_revision,
            "revision": self.resolved_revision,
            "filename": self.filename,
            "quantization": self.quantization,
            "local_path": self.target_path,
            "license": self.license,
            "license_url": self.license_url,
            "status": self.status,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "etag": self.etag,
            "resumed": self.resumed,
            "kleidiai_compatible": self.kleidiai_compatible,
            "tensor_type_histogram": dict(self.tensor_type_histogram),
            "tensor_inventory_sha256": self.tensor_inventory_sha256,
            "reviewed_kleidiai_fallbacks": list(self.reviewed_kleidiai_fallbacks),
        }


@dataclass(frozen=True, slots=True)
class DownloadManifest:
    dry_run: bool
    models: tuple[DownloadPlanItem, ...] = field(default_factory=tuple)
    schema_version: str = "1.0"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "dry_run": self.dry_run,
            "models": [model.to_dict() for model in self.models],
        }

    def to_schema_payload(self) -> dict[str, object]:
        """Map completed rows to the repository's strict shared schema."""

        if self.dry_run:
            raise DownloadError("a dry-run model plan cannot be validated as downloaded models")
        models: list[dict[str, object]] = []
        for row in self.models:
            if (
                row.status != "complete"
                or row.resolved_revision is None
                or row.sha256 is None
                or row.bytes is None
                or row.tensor_inventory_sha256 is None
            ):
                raise DownloadError(f"model row is incomplete: {row.model_id}")
            models.append(
                {
                    "role": row.role,
                    "repository": row.repository,
                    "revision": row.resolved_revision,
                    "filename": row.filename,
                    "quantization": row.quantization,
                    "sha256": row.sha256,
                    "bytes": row.bytes,
                    "license": row.license,
                    "local_path": row.target_path,
                    "kleidiai_compatible": row.kleidiai_compatible,
                    "tensor_type_histogram": dict(row.tensor_type_histogram),
                    "tensor_inventory_sha256": row.tensor_inventory_sha256,
                    "reviewed_kleidiai_fallbacks": list(row.reviewed_kleidiai_fallbacks),
                }
            )
        return {
            "schema_version": "1.0.0",
            "generated_at": self.generated_at,
            "models": models,
        }

    def to_schema(self) -> object:
        from a64pilot.schemas import ModelManifest

        return ModelManifest.model_validate(self.to_schema_payload())


def dry_run_manifest(
    specs: Iterable[ModelSpec] | None = None, *, output_dir: Path = Path("models")
) -> DownloadManifest:
    """Plan reviewed filenames without network access or filesystem mutation."""

    rows = tuple(
        DownloadPlanItem(
            model_id=spec.model_id,
            role=spec.role.value,
            repository=spec.repository,
            requested_revision=spec.revision,
            resolved_revision=None,
            filename=spec.expected_filename,
            quantization=spec.quantization,
            target_path=str(output_dir / spec.expected_filename),
            license=spec.license_id,
            license_url=spec.license_url,
            status="planned",
            kleidiai_compatible=spec.kleidiai_compatible,
            tensor_type_histogram=spec.expected_tensor_histogram,
            tensor_inventory_sha256=spec.expected_tensor_inventory_sha256,
            reviewed_kleidiai_fallbacks=tuple(
                fallback.to_dict() for fallback in spec.reviewed_kleidiai_fallbacks
            ),
        )
        for spec in (tuple(specs) if specs is not None else default_registry())
    )
    return DownloadManifest(dry_run=True, models=rows)


def _remote_file(snapshot: RepositorySnapshot, path: str) -> RemoteFile:
    matches = [file for file in snapshot.files if file.path == path]
    if len(matches) != 1:
        raise DownloadError(f"resolved model file has missing or duplicate metadata: {path}")
    return matches[0]


def _target_for(output_dir: Path, repository_path: str) -> Path:
    # Registry resolution can match nested repository paths, but local models
    # are intentionally flat and cannot escape the ignored models directory.
    name = PurePosixPath(repository_path).name
    if not name or name in {".", ".."}:
        raise DownloadError("resolved repository path has no safe filename")
    return output_dir / name


def download_models(
    specs: Iterable[ModelSpec] | None = None,
    *,
    output_dir: Path = Path("models"),
    dry_run: bool = False,
    client: HubClient | None = None,
) -> DownloadManifest:
    """Download, resume, hash, and manifest reviewed official model files.

    Dry-run is deliberately offline and side-effect-free.  In real mode the
    repository's immutable resolved commit and LFS SHA-256 are required before
    a row is marked complete.
    """

    selected = tuple(specs) if specs is not None else default_registry()
    if not selected:
        raise DownloadError("at least one reviewed model must be selected")
    if dry_run:
        return dry_run_manifest(selected, output_dir=output_dir)
    hub = client or HuggingFaceHubClient()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots: dict[tuple[str, str], RepositorySnapshot] = {}
    rows: list[DownloadPlanItem] = []
    for spec in selected:
        key = (spec.repository, spec.revision)
        if key not in snapshots:
            snapshots[key] = hub.snapshot(spec.repository, spec.revision)
        snapshot = snapshots[key]
        if not _REVISION_PATTERN.fullmatch(snapshot.resolved_revision):
            raise DownloadError(
                f"official repository did not resolve to a full commit: {spec.repository}"
            )
        if snapshot.resolved_revision.lower() != spec.revision.lower():
            raise DownloadError(
                f"official repository resolved to an unexpected revision: {spec.repository}"
            )
        remote_path = resolve_filename(spec, [file.path for file in snapshot.files])
        if remote_path != spec.expected_filename:
            raise DownloadError(
                f"official repository filename differs from pinned registry: {remote_path}"
            )
        metadata = _remote_file(snapshot, remote_path)
        if not metadata.sha256 or not _SHA256_PATTERN.fullmatch(metadata.sha256):
            raise DownloadError(
                f"official repository did not expose an LFS SHA-256 for {remote_path}"
            )
        if metadata.sha256.lower() != spec.expected_sha256:
            raise DownloadError(
                f"official LFS SHA-256 differs from pinned registry for {remote_path}"
            )
        if metadata.size != spec.expected_bytes:
            raise DownloadError(
                f"official file size differs from pinned registry for {remote_path}"
            )
        target = _target_for(output_dir, remote_path)
        resumed = False
        if target.is_file():
            size_matches = metadata.size is None or target.stat().st_size == metadata.size
            resumed = size_matches and sha256_file(target) == metadata.sha256.lower()
        if not resumed:
            downloaded = hub.download(
                spec.repository,
                remote_path,
                snapshot.resolved_revision,
                output_dir,
                force=target.exists(),
            )
            if downloaded.resolve() != target.resolve():
                # Nested HF paths are permitted, but the public model layout is
                # flat.  Refuse to copy implicitly; official defaults are flat.
                raise DownloadError(
                    f"download client returned an unexpected local path for {remote_path}"
                )
        actual_size = target.stat().st_size
        actual_hash = sha256_file(target)
        if metadata.size is not None and actual_size != metadata.size:
            raise DownloadError(f"downloaded size does not match metadata for {target.name}")
        if actual_hash != metadata.sha256.lower():
            raise DownloadError(f"downloaded SHA-256 does not match metadata for {target.name}")
        inventory_proof = verify_model_inventory(target, spec, actual_sha256=actual_hash)
        if not inventory_proof.verified:
            raise DownloadError(
                f"downloaded GGUF inventory differs from the reviewed registry for {target.name}: "
                + "; ".join(inventory_proof.errors)
            )
        rows.append(
            DownloadPlanItem(
                model_id=spec.model_id,
                role=spec.role.value,
                repository=spec.repository,
                requested_revision=spec.revision,
                resolved_revision=snapshot.resolved_revision,
                filename=remote_path,
                quantization=spec.quantization,
                target_path=target.name,
                license=spec.license_id,
                license_url=spec.license_url,
                status="complete",
                sha256=actual_hash,
                bytes=actual_size,
                etag=metadata.etag,
                resumed=resumed,
                kleidiai_compatible=spec.kleidiai_compatible,
                tensor_type_histogram=inventory_proof.tensor_histogram,
                tensor_inventory_sha256=inventory_proof.inventory_sha256,
                reviewed_kleidiai_fallbacks=tuple(
                    tensor.to_dict() for tensor in inventory_proof.reviewed_fallback_tensors
                ),
            )
        )
    return DownloadManifest(dry_run=False, models=tuple(rows))


def write_manifest(
    manifest: DownloadManifest,
    path: Path = Path("artifacts/model-manifest.json"),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.to_schema().model_dump(mode="json")
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
