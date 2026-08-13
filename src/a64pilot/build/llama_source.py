"""Acquisition and verification of a pinned official llama.cpp checkout."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

OFFICIAL_LLAMA_REPOSITORY = "https://github.com/ggml-org/llama.cpp.git"
DEFAULT_SOURCE_DIR = Path("third_party/llama.cpp")
DEFAULT_LOCK_PATH = Path("third_party/llama.cpp.lock")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class SourceError(RuntimeError):
    """Raised when the official source cannot be pinned or verified."""


@dataclass(frozen=True, slots=True)
class SourceLock:
    repository: str
    commit: str

    def __post_init__(self) -> None:
        if self.repository != OFFICIAL_LLAMA_REPOSITORY:
            raise ValueError("llama.cpp source lock must use the official repository")
        if not _COMMIT_PATTERN.fullmatch(self.commit):
            raise ValueError("source lock commit must be a full 40-character Git SHA")

    def to_dict(self) -> dict[str, str]:
        return {"repository": self.repository, "commit": self.commit.lower()}


@dataclass(frozen=True, slots=True)
class SourceCheckout:
    path: Path
    repository: str
    commit: str
    commands: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    dry_run: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "repository": self.repository,
            "commit": self.commit,
            "commands": [list(command) for command in self.commands],
            "dry_run": self.dry_run,
        }


def read_source_lock(path: Path = DEFAULT_LOCK_PATH) -> SourceLock:
    """Read a JSON lock, while accepting a legacy one-line commit lock."""

    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SourceError(f"missing llama.cpp source lock: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    try:
        if isinstance(payload, dict):
            return SourceLock(
                repository=str(payload.get("repository", OFFICIAL_LLAMA_REPOSITORY)),
                commit=str(payload["commit"]),
            )
        return SourceLock(OFFICIAL_LLAMA_REPOSITORY, text)
    except (KeyError, ValueError) as exc:
        raise SourceError(f"invalid llama.cpp source lock: {path}") from exc


def write_source_lock(lock: SourceLock, path: Path = DEFAULT_LOCK_PATH) -> None:
    """Write a deterministic, reviewable source lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(lock.to_dict(), indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")


def _run_git(arguments: Sequence[str], *, cwd: Path | None = None, timeout_s: float = 300.0) -> str:
    command = ["git", *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise SourceError(f"failed to run Git command: {' '.join(command)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        summary = detail[-1][:300] if detail else f"exit code {completed.returncode}"
        raise SourceError(f"Git command failed ({' '.join(command)}): {summary}")
    return completed.stdout.strip()


def resolve_remote_commit(
    revision: str = "HEAD", *, repository: str = OFFICIAL_LLAMA_REPOSITORY
) -> str:
    """Resolve an official remote revision to a full immutable commit SHA."""

    if repository != OFFICIAL_LLAMA_REPOSITORY:
        raise SourceError("refusing to resolve a non-official llama.cpp repository")
    output = _run_git(("ls-remote", repository, revision), timeout_s=30)
    matches = [line.split()[0] for line in output.splitlines() if line.split()]
    if not matches or not _COMMIT_PATTERN.fullmatch(matches[0]):
        raise SourceError(f"could not resolve llama.cpp revision: {revision}")
    return matches[0].lower()


def pin_remote_revision(
    revision: str = "HEAD", *, lock_path: Path = DEFAULT_LOCK_PATH
) -> SourceLock:
    """Resolve and persist a revision after compatibility testing."""

    lock = SourceLock(OFFICIAL_LLAMA_REPOSITORY, resolve_remote_commit(revision))
    write_source_lock(lock, lock_path)
    return lock


def current_commit(source_dir: Path = DEFAULT_SOURCE_DIR) -> str:
    commit = _run_git(("rev-parse", "HEAD"), cwd=source_dir)
    if not _COMMIT_PATTERN.fullmatch(commit):
        raise SourceError(f"checkout returned an invalid commit: {commit!r}")
    return commit.lower()


def verify_official_remote(source_dir: Path = DEFAULT_SOURCE_DIR) -> None:
    remote = _run_git(("remote", "get-url", "origin"), cwd=source_dir)
    accepted = {
        OFFICIAL_LLAMA_REPOSITORY,
        OFFICIAL_LLAMA_REPOSITORY.removesuffix(".git"),
        "git@github.com:ggml-org/llama.cpp.git",
    }
    if remote not in accepted:
        raise SourceError(f"existing llama.cpp checkout has an unexpected origin: {remote!r}")


def ensure_source(
    *,
    lock: SourceLock | None = None,
    lock_path: Path = DEFAULT_LOCK_PATH,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    dry_run: bool = False,
) -> SourceCheckout:
    """Clone/fetch and detach at the exact locked commit.

    The command plan is returned even in real mode so build manifests can show
    precisely how the checkout was produced.
    """

    selected = lock or read_source_lock(lock_path)
    if selected.repository != OFFICIAL_LLAMA_REPOSITORY:
        raise SourceError("source lock does not reference official llama.cpp")

    commands: list[tuple[str, ...]] = []
    if not source_dir.exists():
        commands.append(
            (
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                selected.repository,
                str(source_dir),
            )
        )
        commands.append(
            ("git", "-C", str(source_dir), "fetch", "--depth", "1", "origin", selected.commit)
        )
        commands.append(("git", "-C", str(source_dir), "checkout", "--detach", selected.commit))
        if not dry_run:
            source_dir.parent.mkdir(parents=True, exist_ok=True)
            _run_git(
                (
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    selected.repository,
                    str(source_dir),
                )
            )
            _run_git(("fetch", "--depth", "1", "origin", selected.commit), cwd=source_dir)
            _run_git(("checkout", "--detach", selected.commit), cwd=source_dir)
    else:
        if not (source_dir / ".git").exists():
            raise SourceError(f"source directory is not a Git checkout: {source_dir}")
        verify_official_remote(source_dir)
        if current_commit(source_dir) != selected.commit.lower():
            commands.extend(
                (
                    (
                        "git",
                        "-C",
                        str(source_dir),
                        "fetch",
                        "--depth",
                        "1",
                        "origin",
                        selected.commit,
                    ),
                    ("git", "-C", str(source_dir), "checkout", "--detach", selected.commit),
                )
            )
            if not dry_run:
                _run_git(("fetch", "--depth", "1", "origin", selected.commit), cwd=source_dir)
                _run_git(("checkout", "--detach", selected.commit), cwd=source_dir)

    if not dry_run and current_commit(source_dir) != selected.commit.lower():
        raise SourceError("llama.cpp checkout does not match the pinned commit")
    return SourceCheckout(
        path=source_dir,
        repository=selected.repository,
        commit=selected.commit.lower(),
        commands=tuple(commands),
        dry_run=dry_run,
    )
