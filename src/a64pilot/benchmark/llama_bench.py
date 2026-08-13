"""Version-tolerant llama-bench command and text parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LlamaBenchResult:
    test: str
    model: str
    size_mib: float | None
    threads: int | None
    tokens_per_second: float
    tokens_per_second_stddev: float | None


def build_command(
    binary: Path | str,
    model: Path | str,
    *,
    threads: int,
    repetitions: int = 3,
    prompt_tokens: int = 128,
    generation_tokens: int = 64,
) -> list[str]:
    return [
        str(binary),
        "-m",
        str(model),
        "-t",
        str(threads),
        "-r",
        str(repetitions),
        "-p",
        str(prompt_tokens),
        "-n",
        str(generation_tokens),
    ]


def parse_output(text: str) -> list[LlamaBenchResult]:
    """Parse current Markdown-table output while rejecting ambiguous lines."""

    results: list[LlamaBenchResult] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        joined = " | ".join(cells)
        match = re.search(r"(?P<mean>\d+(?:\.\d+)?)\s*±\s*(?P<std>\d+(?:\.\d+)?)", joined)
        if not match:
            continue
        test = next((cell for cell in cells if re.fullmatch(r"(?:pp|tg)\d+", cell)), "unknown")
        model = cells[0] if cells else "unknown"
        threads = None
        for cell in cells:
            if re.fullmatch(r"\d+", cell):
                value = int(cell)
                if 0 < value <= 4096:
                    threads = value
        size = None
        for cell in cells:
            size_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*MiB", cell)
            if size_match:
                size = float(size_match.group(1))
        results.append(
            LlamaBenchResult(
                test=test,
                model=model,
                size_mib=size,
                threads=threads,
                tokens_per_second=float(match.group("mean")),
                tokens_per_second_stddev=float(match.group("std")),
            )
        )
    if not results:
        raise ValueError("no supported llama-bench result rows found")
    return results
