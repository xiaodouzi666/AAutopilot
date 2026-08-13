"""Hashing, atomic artifact writes, integrity manifests, and public redaction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

PRIVATE_PATTERNS = (
    (re.compile(r"(?i)(authorization:\s*)(?:bearer\s+)?\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token|password|secret|api[_-]?key)(\s*[:=]\s*)\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"/Users/[^/\s]+"), "/Users/[REDACTED]"),
    (re.compile(r"/home/[^/\s]+"), "/home/[REDACTED]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_text(path: Path | str, content: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path | str, value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def redact_text(text: str) -> str:
    result = text
    for pattern, replacement in PRIVATE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def integrity_manifest(root: Path | str, *, exclude: set[str] | None = None) -> dict[str, str]:
    directory = Path(root)
    excluded = exclude or {"integrity.json"}
    return {
        str(path.relative_to(directory)): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name not in excluded
    }


def verify_integrity(root: Path | str, manifest: dict[str, str]) -> list[str]:
    directory = Path(root).resolve()
    errors: list[str] = []
    expected_files = set(manifest)
    actual_files = {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and path.name != "integrity.json"
    }
    for relative in sorted(actual_files - expected_files):
        errors.append(f"unexpected file: {relative}")
    for relative, expected in manifest.items():
        relative_path = Path(relative)
        unresolved = directory / relative_path
        path = unresolved.resolve()
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or not path.is_relative_to(directory)
        ):
            errors.append(f"unsafe integrity path: {relative}")
        elif not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"invalid integrity SHA-256: {relative}")
        elif not path.is_file() or unresolved.is_symlink():
            errors.append(f"missing: {relative}")
        elif sha256_file(path) != expected:
            errors.append(f"hash mismatch: {relative}")
    return errors
