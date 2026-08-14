#!/usr/bin/env python3
"""Reject unresolved placeholders on publishable surfaces with an explicit legacy allowlist."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOKEN_PATTERN = re.compile(
    r"\[\[AUTO:|\{\{|\bTBD\b|TODO_METRIC|YOUR_RESULT|(?<![A-Za-z0-9])sk-[A-Za-z0-9]{8,}"
)

# These two documents are preserved only as a record of the superseded v1 plan.  They are not
# renderer inputs or submission surfaces, and both must retain an unmistakable legacy banner.
LEGACY_ALLOWLIST = {
    "docs/05-devpost-submission-draft.md",
    "docs/06-video-script.md",
}
LEGACY_BANNER = "Historical planning"

FINAL_FILES = {
    "FINAL_HANDOFF.md",
    "README.md",
    "devpost-submission.md",
    "artifacts/devpost-writeup-final.md",
    "artifacts/video-script-final.md",
    "artifacts/submission-checklist.md",
    "artifacts/screenshots/captions.md",
    "artifacts/screenshots/manifest.json",
    "artifacts/quality-summary.json",
}


def _matches(path: Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [(0, f"unreadable: {exc}")]
    return [
        (line_number, line.strip())
        for line_number, line in enumerate(lines, start=1)
        if TOKEN_PATTERN.search(line)
    ]


def scan(root: Path) -> list[str]:
    errors: list[str] = []
    targets = set(FINAL_FILES)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        targets.update(
            path.relative_to(root).as_posix()
            for path in docs_dir.rglob("*.md")
            if path.relative_to(root).as_posix() not in LEGACY_ALLOWLIST
        )
    for relative in sorted(targets):
        path = root / relative
        if not path.is_file():
            errors.append(f"missing publishable surface: {relative}")
            continue
        for line_number, line in _matches(path):
            errors.append(f"unresolved token: {relative}:{line_number}: {line}")
    for relative in sorted(LEGACY_ALLOWLIST):
        path = root / relative
        if not path.is_file():
            errors.append(f"missing allowlisted legacy document: {relative}")
            continue
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10])
        if LEGACY_BANNER not in head:
            errors.append(f"legacy document has no required banner: {relative}")
        if not _matches(path):
            errors.append(f"legacy allowlist is stale (no tokens remain): {relative}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    errors = scan(root)
    if errors:
        print("final placeholder scan failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        "final placeholder scan passed: publishable surfaces clean; "
        "2 bannered legacy documents allowlisted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
