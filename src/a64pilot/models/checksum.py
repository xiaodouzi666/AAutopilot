"""Streaming SHA-256 and safe model-manifest verification."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class ChecksumResult:
    path: str
    expected_sha256: str
    actual_sha256: str | None
    bytes: int | None
    valid: bool
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "bytes": self.bytes,
            "valid": self.valid,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ManifestVerification:
    valid: bool
    results: tuple[ChecksumResult, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "results": [result.to_dict() for result in self.results],
            "errors": list(self.errors),
        }


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading a multi-gigabyte GGUF into memory."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(
    path: Path, expected_sha256: str, *, expected_bytes: int | None = None
) -> ChecksumResult:
    expected = expected_sha256.lower()
    if not _SHA256_PATTERN.fullmatch(expected):
        return ChecksumResult(str(path), expected, None, None, False, "invalid expected SHA-256")
    try:
        size = path.stat().st_size
    except OSError:
        return ChecksumResult(str(path), expected, None, None, False, "file is missing")
    if expected_bytes is not None and size != expected_bytes:
        return ChecksumResult(
            str(path),
            expected,
            None,
            size,
            False,
            f"size mismatch: expected {expected_bytes}, got {size}",
        )
    try:
        actual = sha256_file(path)
    except OSError:
        return ChecksumResult(str(path), expected, None, size, False, "file is unreadable")
    return ChecksumResult(
        str(path),
        expected,
        actual,
        size,
        actual == expected,
        None if actual == expected else "SHA-256 mismatch",
    )


def _load_manifest(manifest: Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(manifest, Path):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read model manifest: {manifest}") from exc
        if not isinstance(payload, dict):
            raise ValueError("model manifest root must be an object")
        return payload
    return manifest


def _safe_model_path(base: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    base_resolved = base.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(f"model path escapes configured model directory: {raw_path}")
    return resolved


def verify_manifest(
    manifest: Path | Mapping[str, Any], *, models_dir: Path = Path("models")
) -> ManifestVerification:
    """Verify every complete manifest row, rejecting path traversal."""

    payload = _load_manifest(manifest)
    rows = payload.get("models")
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        return ManifestVerification(False, errors=("manifest models must be a list",))
    results: list[ChecksumResult] = []
    errors: list[str] = []
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, Mapping):
            errors.append(f"model row {index} is not an object")
            continue
        if raw_row.get("status", "complete") not in {"complete", "downloaded", "verified"}:
            errors.append(f"model row {index} is not complete")
            continue
        raw_path = raw_row.get("local_path") or raw_row.get("filename")
        expected_hash = raw_row.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            errors.append(f"model row {index} lacks path or SHA-256")
            continue
        try:
            path = _safe_model_path(models_dir, raw_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        raw_bytes = raw_row.get("bytes")
        expected_bytes = raw_bytes if isinstance(raw_bytes, int) else None
        result = verify_file(path, expected_hash, expected_bytes=expected_bytes)
        results.append(result)
        if not result.valid:
            errors.append(f"{path.name}: {result.error}")
    if not rows:
        errors.append("manifest contains no models")
    return ManifestVerification(not errors, tuple(results), tuple(errors))
