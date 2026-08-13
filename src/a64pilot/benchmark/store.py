"""Append-only raw evidence store with integrity hashing."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from a64pilot.provenance import integrity_manifest, verify_integrity, write_json
from a64pilot.schemas import BenchmarkRecord


class ArtifactStore:
    def __init__(self, root: Path | str = "artifacts/raw") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def append_record(self, record: BenchmarkRecord) -> Path:
        path = self.run_dir(record.run_id) / "requests.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
        return path

    def write_metadata(self, run_id: str, name: str, value: object) -> Path:
        if "/" in name or "\\" in name or not name.endswith((".json", ".yaml", ".txt")):
            raise ValueError("metadata name must be a simple .json/.yaml/.txt filename")
        path = self.run_dir(run_id) / name
        if name.endswith(".json"):
            write_json(path, value)
        else:
            path.write_text(str(value), encoding="utf-8")
        return path

    def records(self, *, measured_only: bool = False) -> Iterator[BenchmarkRecord]:
        for path in sorted(self.root.glob("*/requests.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = BenchmarkRecord.model_validate_json(line)
                if not measured_only or record.evidence_kind == "measured":
                    yield record

    def finalize(self, run_id: str) -> Path:
        directory = self.run_dir(run_id)
        manifest = integrity_manifest(directory)
        path = directory / "integrity.json"
        write_json(path, {"sha256": manifest})
        return path

    def verify(self, run_id: str) -> list[str]:
        directory = self.run_dir(run_id)
        path = directory / "integrity.json"
        if not path.exists():
            return ["missing: integrity.json"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        return verify_integrity(directory, payload.get("sha256", {}))

    def import_records(self, records: Iterable[BenchmarkRecord]) -> int:
        count = 0
        touched: set[str] = set()
        for record in records:
            self.append_record(record)
            touched.add(record.run_id)
            count += 1
        for run_id in touched:
            self.finalize(run_id)
        return count
