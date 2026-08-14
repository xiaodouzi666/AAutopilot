#!/usr/bin/env python3
"""Find or redact private identity, network, and credential material in artifacts."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import ipaddress
import json
import re
import shutil
import socket
import sys
from collections.abc import Iterable
from pathlib import Path

BINARY_SUFFIXES = {
    ".bin",
    ".gif",
    ".gguf",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".tar",
    ".webm",
    ".zip",
}
TOKEN_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s\"']+"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?-----END \1-----",
        re.DOTALL,
    ),
)
IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
LLAMA_ELAPSED_PREFIX = r"(?:0|[1-9]\d{0,2})\.[0-5]\d\.\d{3}\.\d{3}"
LLAMA_ELAPSED_LINE = re.compile(rf"(?m)^(?P<timestamp>{LLAMA_ELAPSED_PREFIX}) (?P<level>[DIWE]) ")
LLAMA_ELAPSED_MIN_RUN = 3
LLAMA_ELAPSED_MAX_START_US = 5_000_000
LLAMA_ELAPSED_MAX_GAP_US = 60_000_000
SSH_PATTERN = re.compile(r"(?<![\w.-])(?:ssh|scp)\s+(?:-[^\s]+\s+)*[^\s@]+@[^\s]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if redaction would change a file.")
    mode.add_argument("--write", action="store_true", help="Redact text files in place.")
    mode.add_argument(
        "--output",
        type=Path,
        help="Copy one input tree to a new sanitized tree; the destination must not exist.",
    )
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("artifacts")])
    return parser.parse_args()


def sensitive_ipv4_replacement(
    match: re.Match[str],
    *,
    protected_llama_prefixes: frozenset[int] = frozenset(),
    normalize_llama_elapsed_prefixes: bool = False,
) -> str:
    value = match.group(0)
    # llama.cpp verbosity-5 logs prefix each line with an elapsed counter such as
    # ``0.10.168.200 D``.  Some counters are also syntactically valid IPv4 addresses. Internal
    # checks trust only a coherent timestamp sequence from reviewed runtime paths; public copies
    # normalize every ambiguous token. A real address elsewhere on the line is always redacted.
    if match.start() in protected_llama_prefixes:
        return "<llama-elapsed>" if normalize_llama_elapsed_prefixes else value
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.is_loopback or address.is_unspecified:
        return value
    return "<redacted-ip>"


def private_literals() -> tuple[tuple[str, str, str], ...]:
    values: list[tuple[str, str, str]] = []
    home = str(Path.home())
    username = getpass.getuser()
    hostname = socket.gethostname()
    if home and home not in {"/", "."}:
        values.append((home, "<redacted-home>", "home_path"))
    if username and len(username) >= 3 and username.lower() not in {"root", "runner"}:
        values.append((username, "<redacted-user>", "username"))
    if hostname and hostname not in {"localhost"}:
        values.append((hostname, "<redacted-host>", "hostname"))
    return tuple(values)


def is_llama_runtime_evidence(path: Path) -> bool:
    if (path.name.endswith(".stderr.log") and path.parent.name == "runtime") or (
        path.name == "runtime-proof.txt" and path.parent.parent.name == "raw"
    ):
        return True
    parts = path.parts
    try:
        probe_index = parts.index("performance-probes-raw")
    except ValueError:
        return False
    relative = parts[probe_index:]
    if len(relative) < 4 or not re.fullmatch(r"[0-9a-f]{32}", relative[1]):
        return False
    if relative[2] == "micro":
        return bool(
            len(relative) == 4
            and re.fullmatch(
                r"(?:generic|kleidiai)-(?:q8_0|q4_0)-t[1-9]\d*\.stderr\.txt",
                relative[3],
            )
        )
    if relative[2] != "service":
        return False
    if len(relative) == 4:
        return bool(re.fullmatch(r"(?:generic|kleidiai)-p[12]-combined\.log", relative[3]))
    return bool(
        len(relative) == 5
        and re.fullmatch(r"(?:generic|kleidiai)-p[12]", relative[3])
        and relative[4].endswith(".stderr.log")
    )


def llama_elapsed_us(value: str) -> int:
    minutes, seconds, milliseconds, microseconds = (int(part) for part in value.split("."))
    return minutes * 60_000_000 + seconds * 1_000_000 + milliseconds * 1_000 + microseconds


def protected_llama_elapsed_prefixes(text: str) -> frozenset[int]:
    matches = list(LLAMA_ELAPSED_LINE.finditer(text))
    if len(matches) < LLAMA_ELAPSED_MIN_RUN:
        return frozenset()
    timestamps = [llama_elapsed_us(match.group("timestamp")) for match in matches]
    if timestamps[0] > LLAMA_ELAPSED_MAX_START_US:
        return frozenset()
    if any(
        not 0 <= timestamps[index] - timestamps[index - 1] <= LLAMA_ELAPSED_MAX_GAP_US
        for index in range(1, len(timestamps))
    ):
        return frozenset()
    return frozenset(match.start("timestamp") for match in matches)


def redact_text(
    text: str,
    *,
    allow_llama_elapsed_prefix: bool = False,
    normalize_llama_elapsed_prefixes: bool = False,
) -> tuple[str, set[str]]:
    categories: set[str] = set()
    output = text
    for literal, replacement, category in private_literals():
        if literal in output:
            output = output.replace(literal, replacement)
            categories.add(category)
    for pattern in TOKEN_PATTERNS:
        revised, count = pattern.subn("<redacted-credential>", output)
        if count:
            categories.add("credential")
            output = revised
    output, count = SSH_PATTERN.subn("<redacted-ssh-command>", output)
    if count:
        categories.add("ssh_command")
    protected_prefixes = (
        protected_llama_elapsed_prefixes(output) if allow_llama_elapsed_prefix else frozenset()
    )
    revised = IPV4_PATTERN.sub(
        lambda match: sensitive_ipv4_replacement(
            match,
            protected_llama_prefixes=protected_prefixes,
            normalize_llama_elapsed_prefixes=normalize_llama_elapsed_prefixes,
        ),
        output,
    )
    if revised != output:
        categories.add("ip_address")
        output = revised
    return output, categories


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for root in paths:
        if not root.exists():
            continue
        if root.is_symlink():
            raise ValueError(f"refusing symlink input: {root}")
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"refusing symlink under artifact tree: {path}")
            if path.is_file():
                yield path


def is_text(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return False
    return b"\x00" not in sample


def process_in_place(paths: list[Path], *, write: bool) -> tuple[list[dict[str, object]], int]:
    findings: list[dict[str, object]] = []
    scanned = 0
    for path in iter_files(paths):
        if not is_text(path):
            continue
        scanned += 1
        original = path.read_text(encoding="utf-8", errors="replace")
        revised, categories = redact_text(
            original,
            allow_llama_elapsed_prefix=is_llama_runtime_evidence(path),
            normalize_llama_elapsed_prefixes=write,
        )
        if revised == original:
            continue
        findings.append({"path": str(path), "categories": sorted(categories)})
        if write:
            path.write_text(revised, encoding="utf-8")
    return findings, scanned


def sanitized_copy(source: Path, destination: Path) -> tuple[list[dict[str, object]], int]:
    if not source.is_dir():
        raise ValueError("--output requires exactly one directory input")
    if destination.exists():
        raise ValueError(f"sanitized destination already exists: {destination}")
    destination.mkdir(parents=True)
    findings: list[dict[str, object]] = []
    scanned = 0
    for source_path in iter_files([source]):
        relative = source_path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_text(source_path):
            scanned += 1
            original = source_path.read_text(encoding="utf-8", errors="replace")
            revised, categories = redact_text(
                original,
                allow_llama_elapsed_prefix=is_llama_runtime_evidence(source_path),
                normalize_llama_elapsed_prefixes=True,
            )
            target.write_text(revised, encoding="utf-8")
            if revised != original:
                findings.append({"path": str(relative), "categories": sorted(categories)})
        else:
            shutil.copy2(source_path, target)
    _refresh_public_integrity(destination)
    # A complete benchmark bundle is already replayed against the live host, binaries, and
    # models before this copy. Bind the sanitized A0--A4 namespaces to that private antecedent
    # without repeating those expensive checks in the public namespace. Sparse redactor unit
    # fixtures and early-failure trees deliberately have no receipt; the success-gated workflow
    # requires a complete receipt before publication.
    from a64pilot.report.public_derivation import (
        build_public_derivation_receipt,
        has_benchmark_derivation_inputs,
    )

    if has_benchmark_derivation_inputs(source):
        build_public_derivation_receipt(
            source,
            destination,
            redaction_findings=findings,
            require_complete=False,
        )
    return findings, scanned


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refresh_public_integrity(destination: Path) -> None:
    """Rehash sanitized copies so redaction remains transparent and reviewable."""

    # Main A0-A3 rows live at ``raw/``.  A4 quality-calibration component rows
    # deliberately live under ``a4/runs/<session>/raw/`` so they cannot be
    # mistaken for live cascade performance.  Rehash every evidence store after
    # public redaction, regardless of that intentional namespace separation.
    raw_roots = sorted(path for path in destination.rglob("raw") if path.is_dir())
    for raw_root in raw_roots:
        for run_dir in sorted(path for path in raw_root.iterdir() if path.is_dir()):
            hashes = {
                str(path.relative_to(run_dir)): _sha256(path)
                for path in sorted(run_dir.rglob("*"))
                if path.is_file() and path.name != "integrity.json"
            }
            (run_dir / "integrity.json").write_text(
                json.dumps({"sha256": hashes}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    # Supporting protocol probes use one session-level raw manifest rather than
    # per-request evidence-store manifests.  Redaction can change native logs,
    # so bind the public summary to the sanitized bytes before packaging.
    probe_path = destination / "performance-probes.json"
    if probe_path.is_file():
        try:
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            raw_relative = Path(probe["raw_root"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("invalid performance-probe artifact during rehash") from exc
        raw_root = (destination / raw_relative).resolve()
        if (
            raw_relative.is_absolute()
            or ".." in raw_relative.parts
            or not raw_root.is_relative_to(destination.resolve())
            or not raw_root.is_dir()
        ):
            raise ValueError("unsafe or missing performance-probe raw root")
        probe["raw_files"] = {
            str(path.relative_to(destination.resolve())): _sha256(path)
            for path in sorted(raw_root.rglob("*"))
            if path.is_file()
        }
        probe_path.write_text(
            json.dumps(probe, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    report_files = [
        "report.html",
        "report.md",
        "claims.json",
        "benchmark-results.json",
    ]
    report_files.extend(
        name
        for name in ("report-data.json", "performance-probes.json")
        if (destination / name).is_file()
    )
    if all((destination / name).is_file() for name in report_files):
        (destination / "report-integrity.json").write_text(
            json.dumps(
                {name: _sha256(destination / name) for name in report_files},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def main() -> int:
    args = parse_args()
    try:
        if args.output is not None:
            if len(args.paths) != 1:
                raise ValueError("--output accepts exactly one source directory")
            findings, scanned = sanitized_copy(args.paths[0], args.output)
            mode = "sanitized-copy"
        else:
            findings, scanned = process_in_place(args.paths, write=args.write)
            mode = "write" if args.write else "check"
    except (OSError, ValueError) as exc:
        print(f"artifact redaction failed: {exc}", file=sys.stderr)
        return 2

    payload = {
        "mode": mode,
        "scanned_text_files": scanned,
        "changed_or_sensitive_files": len(findings),
        "findings": findings,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.check and findings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
