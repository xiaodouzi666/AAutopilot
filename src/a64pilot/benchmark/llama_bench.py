"""Versioned, fail-closed ``llama-bench`` command and text parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LLAMA_BENCH_PARSER_VERSION = "markdown-table-v1"


@dataclass(frozen=True, slots=True)
class LlamaBenchCapabilities:
    """CPU-safety options discovered from the pinned binary's help text."""

    device_none: bool
    gpu_layers_zero: bool
    verbose_runtime_log: bool

    @property
    def cpu_only_complete(self) -> bool:
        return self.device_none and self.gpu_layers_zero and self.verbose_runtime_log


@dataclass(frozen=True)
class LlamaBenchResult:
    test: str
    model: str
    size_mib: float | None
    threads: int | None
    tokens_per_second: float
    tokens_per_second_stddev: float | None


def inspect_help(text: str) -> LlamaBenchCapabilities:
    """Discover the two runtime flags required for explicit CPU-only execution."""

    # The pinned common-params parser exposes ``--device`` and ``-ngl``.  Do not
    # optimistically send either flag to a different binary: a missing safeguard
    # makes the microbenchmark ineligible instead of silently weakening proof.
    device = bool(re.search(r"(?<![\w-])--device(?:[=\s,]|$)", text))
    gpu_layers = bool(re.search(r"(?<![\w-])-ngl(?:[=\s,]|$)", text))
    verbose = bool(re.search(r"(?<![\w-])(?:-v|--verbose)(?:[=\s,]|$)", text))
    return LlamaBenchCapabilities(
        device_none=device,
        gpu_layers_zero=gpu_layers,
        verbose_runtime_log=verbose,
    )


def build_command(
    binary: Path | str,
    model: Path | str,
    *,
    threads: int,
    repetitions: int = 3,
    prompt_tokens: int = 128,
    generation_tokens: int = 64,
    capabilities: LlamaBenchCapabilities | None = None,
) -> list[str]:
    discovered = capabilities or LlamaBenchCapabilities(
        device_none=True,
        gpu_layers_zero=True,
        verbose_runtime_log=True,
    )
    if not discovered.cpu_only_complete:
        raise ValueError("llama-bench cannot express complete CPU-only intent")
    if repetitions < 3:
        raise ValueError("microbenchmark requires at least three measured repetitions")
    if threads < 1 or prompt_tokens < 1 or generation_tokens < 1:
        raise ValueError("threads and token counts must be positive")
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
        "--device",
        "none",
        "-ngl",
        "0",
        "-v",
    ]


def parse_output(text: str) -> list[LlamaBenchResult]:
    """Parse the pinned Markdown-table schema while rejecting ambiguous lines."""

    results: list[LlamaBenchResult] = []
    header: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        normalized = [cell.strip().strip("*").lower() for cell in cells]
        if "test" in normalized and any(value in {"t/s", "tokens/s"} for value in normalized):
            if header is not None and normalized != header:
                raise ValueError("multiple incompatible llama-bench table headers")
            header = normalized
            continue
        if header is None or len(cells) != len(header):
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        values = dict(zip(header, cells, strict=True))
        rate_cell = values.get("t/s", values.get("tokens/s", ""))
        match = re.fullmatch(
            r"(?P<mean>\d+(?:\.\d+)?)\s*±\s*(?P<std>\d+(?:\.\d+)?)",
            rate_cell,
        )
        if not match:
            raise ValueError("llama-bench result row has an unsupported token-rate cell")
        test = values.get("test", "")
        if not re.fullmatch(r"(?:pp|tg)\d+", test):
            raise ValueError(f"unsupported llama-bench test label: {test!r}")
        model = values.get("model", "unknown")
        thread_cell = values.get("threads", "")
        threads = int(thread_cell) if re.fullmatch(r"\d+", thread_cell) else None
        size = None
        size_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*MiB", values.get("size", ""))
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
    keys = [result.test for result in results]
    if len(keys) != len(set(keys)):
        raise ValueError("llama-bench output repeats a test row")
    return results


def require_tests(
    results: list[LlamaBenchResult],
    *,
    prompt_tokens: int,
    generation_tokens: int,
    threads: int,
) -> tuple[LlamaBenchResult, LlamaBenchResult]:
    """Return exactly the requested pp/tg rows or reject the benchmark output."""

    by_test = {result.test: result for result in results}
    expected = (f"pp{prompt_tokens}", f"tg{generation_tokens}")
    if set(by_test) != set(expected):
        raise ValueError("llama-bench output must contain exactly " + " and ".join(expected))
    selected = tuple(by_test[name] for name in expected)
    if any(result.threads != threads for result in selected):
        raise ValueError("llama-bench output does not report the requested thread count")
    if any(
        result.tokens_per_second <= 0
        or result.tokens_per_second_stddev is None
        or result.tokens_per_second_stddev < 0
        for result in selected
    ):
        raise ValueError("llama-bench output has an invalid token-rate summary")
    return selected


__all__ = [
    "LLAMA_BENCH_PARSER_VERSION",
    "LlamaBenchCapabilities",
    "LlamaBenchResult",
    "build_command",
    "inspect_help",
    "parse_output",
    "require_tests",
]
