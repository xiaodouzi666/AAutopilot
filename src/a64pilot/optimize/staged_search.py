"""Resumable, wall-clock-bounded staged candidate evaluation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from a64pilot.benchmark.plan import BenchmarkCandidate, candidate_cache_key
from a64pilot.provenance import write_json
from a64pilot.schemas import CandidateResult

Runner = Callable[[BenchmarkCandidate], CandidateResult]


class StagedSearch:
    def __init__(self, cache_dir: Path | str = "artifacts/search-cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        candidates: Iterable[BenchmarkCandidate],
        runner: Runner,
        *,
        provenance: dict[str, object],
        max_minutes: float,
        force: bool = False,
    ) -> list[CandidateResult]:
        deadline = time.monotonic() + max_minutes * 60.0
        results: list[CandidateResult] = []
        for candidate in candidates:
            if time.monotonic() >= deadline:
                break
            key = candidate_cache_key(candidate, provenance)
            path = self.cache_dir / f"{key}.json"
            if path.exists() and not force:
                results.append(
                    CandidateResult.model_validate_json(path.read_text(encoding="utf-8"))
                )
                continue
            result = runner(candidate)
            if not result.measured:
                raise ValueError("optimizer cache accepts measured results only")
            write_json(path, result)
            results.append(result)
        write_json(
            self.cache_dir / "last-search.json",
            {
                "candidate_ids": [result.candidate_id for result in results],
                "completed": len(results),
                "provenance": provenance,
            },
        )
        return results


def load_candidate_results(path: Path | str) -> list[CandidateResult]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("candidates", [])
    return [CandidateResult.model_validate(item) for item in payload]
