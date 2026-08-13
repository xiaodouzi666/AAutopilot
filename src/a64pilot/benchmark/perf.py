"""Best-effort Linux perf collection that never blocks the core benchmark."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PerfResult:
    available: bool
    returncode: int | None
    command: list[str]
    output_path: str | None
    reason: str | None


def collect(command: Sequence[str], output: Path | str) -> PerfResult:
    perf = shutil.which("perf")
    if not perf:
        return PerfResult(False, None, list(command), None, "perf executable unavailable")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses,task-clock,context-switches"
    wrapped = [perf, "stat", "-j", "-e", events, "--", *command]
    completed = subprocess.run(wrapped, capture_output=True, text=True, check=False)
    destination.write_text(completed.stderr, encoding="utf-8")
    reason = (
        None
        if completed.returncode == 0
        else "perf unavailable or permission denied; core timing continued"
    )
    return PerfResult(True, completed.returncode, wrapped, str(destination), reason)


def parse_json_lines(path: Path | str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
