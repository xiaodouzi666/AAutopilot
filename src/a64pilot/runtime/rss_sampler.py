"""Resident-memory sampling for a process and all of its descendants."""

from __future__ import annotations

import csv
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - dependency is present in normal installs
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class RssSample:
    monotonic_ns: int
    rss_bytes: int
    process_count: int


def process_tree_rss(pid: int) -> tuple[int, int]:
    """Return combined RSS bytes and process count for a live process tree."""

    if psutil is None:
        statm = Path(f"/proc/{pid}/statm")
        if not statm.exists():
            return 0, 0
        pages = int(statm.read_text(encoding="utf-8").split()[1])
        return pages * int(os.sysconf("SC_PAGE_SIZE")), 1

    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0, 0

    total = 0
    count = 0
    seen: set[int] = set()
    for process in processes:
        if process.pid in seen:
            continue
        seen.add(process.pid)
        try:
            total += int(process.memory_info().rss)
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total, count


class RssSampler:
    """Sample a process tree on a small background thread."""

    def __init__(self, pid: int, interval_s: float = 0.075) -> None:
        if pid <= 0:
            raise ValueError("pid must be positive")
        if not 0.05 <= interval_s <= 60:
            raise ValueError("interval_s must be between 0.05 and 60 seconds")
        self.pid = pid
        self.interval_s = interval_s
        self._samples: list[RssSample] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> RssSampler:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._sample_once()
        self._thread = threading.Thread(
            target=self._run,
            name=f"a64pilot-rss-{self.pid}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _sample_once(self) -> None:
        rss_bytes, count = process_tree_rss(self.pid)
        sample = RssSample(time.monotonic_ns(), rss_bytes, count)
        with self._lock:
            self._samples.append(sample)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample_once()

    def stop(self, timeout_s: float = 2.0) -> tuple[RssSample, ...]:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)
        self._sample_once()
        return self.samples

    @property
    def samples(self) -> tuple[RssSample, ...]:
        with self._lock:
            return tuple(self._samples)

    @property
    def peak_rss_bytes(self) -> int:
        return max((sample.rss_bytes for sample in self.samples), default=0)

    def write_csv(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("monotonic_ns", "rss_bytes", "rss_mb", "process_count"),
            )
            writer.writeheader()
            for sample in self.samples:
                writer.writerow(
                    {
                        "monotonic_ns": sample.monotonic_ns,
                        "rss_bytes": sample.rss_bytes,
                        "rss_mb": f"{sample.rss_bytes / (1024 * 1024):.6f}",
                        "process_count": sample.process_count,
                    }
                )
        return path


def peak_rss_bytes(samples: Iterable[RssSample]) -> int:
    return max((sample.rss_bytes for sample in samples), default=0)


# Compatibility aliases use the all-caps initialism expected by the benchmark
# package while keeping the class names readable in this module.
RSSSample = RssSample
RSSSampler = RssSampler

__all__ = [
    "RSSSample",
    "RSSSampler",
    "RssSample",
    "RssSampler",
    "peak_rss_bytes",
    "process_tree_rss",
]
