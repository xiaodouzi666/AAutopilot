"""Verify that a public evidence tree is an exact sanitized derivative.

The private evidence verifier is intentionally host-bound: it replays the current
Arm64 machine, native binaries, and multi-gigabyte model files.  A published copy
cannot repeat those checks.  This module instead binds every public A0--A4 raw row
to the already-verified private row, while replaying the inexpensive schemas and
integrity manifests in the public namespace.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from a64pilot.provenance import verify_integrity, write_json
from a64pilot.schemas import BenchmarkRecord

PUBLIC_DERIVATION_SCHEMA_VERSION: Final[str] = "1.0.0"
PUBLIC_DERIVATION_RECEIPT: Final[str] = "public-derivation.json"

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_SESSION_ID = re.compile(r"^(?:calibration|held-out)-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_FILES = frozenset(
    {
        "integrity.json",
        "request.json",
        "requests.jsonl",
        "response.json",
        "run-config.json",
        "runtime-proof.txt",
    }
)
_TYPED_RUN_FILES = frozenset({"request.json", "requests.jsonl", "response.json", "run-config.json"})
_SEMANTIC_FILES = (
    "a4-frozen-policy.json",
    "cascade-status.json",
    "optimized-profile.yaml",
    "quality-results.json",
    "search-plan.json",
)


class PublicDerivationError(ValueError):
    """Raised when a public tree is not a safe deterministic derivative."""


@dataclass(frozen=True, slots=True)
class _RunSnapshot:
    run_id: str
    semantic_sha256: str
    manifest_sha256: str
    typed_sha256: dict[str, str]
    runtime_proof_sha256: str


@dataclass(frozen=True, slots=True)
class _BundleSnapshot:
    root: Path
    tree: dict[str, str]
    tree_sha256: str
    stores: dict[str, dict[str, _RunSnapshot]]
    semantic_files: dict[str, dict[str, str]]
    semantic_payloads: dict[str, dict[str, Any]]
    semantic_sha256: str
    complete: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PublicDerivationError("evidence contains a non-canonical JSON value") from exc
    return hashlib.sha256(encoded).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicDerivationError(f"invalid JSON evidence {path.name}") from exc


def _load_mapping(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise PublicDerivationError(f"{path.name} must contain a JSON object")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str):
        raise PublicDerivationError("receipt path must be text")
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != value:
        raise PublicDerivationError(f"unsafe receipt path: {value}")
    return value


def _assert_safe_tree(root: Path | str) -> Path:
    path = Path(root)
    if not path.is_dir() or path.is_symlink():
        raise PublicDerivationError(f"missing or unsafe artifact root: {path}")
    resolved = path.resolve()
    for child in sorted(path.rglob("*")):
        if child.is_symlink():
            raise PublicDerivationError(f"artifact tree contains a symlink: {child}")
        if not child.is_dir() and not child.is_file():
            raise PublicDerivationError(f"artifact tree contains a special file: {child}")
        if not child.resolve().is_relative_to(resolved):
            raise PublicDerivationError(f"artifact path escapes its root: {child}")
    return resolved


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / PUBLIC_DERIVATION_RECEIPT
    }


def _store_roots(root: Path) -> dict[str, Path]:
    stores: dict[str, Path] = {}
    main = root / "raw"
    if main.is_dir():
        stores["raw"] = main

    sessions_root = root / "a4" / "runs"
    if sessions_root.exists():
        if not sessions_root.is_dir():
            raise PublicDerivationError("a4/runs must be a directory")
        for session in sorted(sessions_root.iterdir()):
            if not session.is_dir() or not _SESSION_ID.fullmatch(session.name):
                raise PublicDerivationError(f"invalid A4 session entry: {session.name}")
            raw = session / "raw"
            if not raw.is_dir():
                raise PublicDerivationError(f"A4 session has no raw store: {session.name}")
            stores[f"a4/runs/{session.name}/raw"] = raw

    discovered = {path.relative_to(root).as_posix() for path in root.rglob("raw") if path.is_dir()}
    if discovered != set(stores):
        unexpected = sorted(discovered.difference(stores))
        missing = sorted(set(stores).difference(discovered))
        details = unexpected or missing
        raise PublicDerivationError(
            "artifact tree contains an unrecognized raw namespace: " + ", ".join(details)
        )
    return stores


def _same_typed(actual: object, expected: object) -> bool:
    return type(actual) is type(expected) and actual == expected


def _validate_run_fields(
    run_id: str,
    record: BenchmarkRecord,
    config: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any],
) -> None:
    prefix = f"run {run_id}"
    if record.run_id != run_id:
        raise PublicDerivationError(f"{prefix} directory disagrees with its typed row")
    if record.evidence_kind != "measured":
        raise PublicDerivationError(f"{prefix} is not measured evidence")

    candidate = config.get("candidate")
    if not isinstance(candidate, dict):
        raise PublicDerivationError(f"{prefix} candidate config is missing")
    candidate_fields = {
        "candidate_id": record.candidate_id,
        "backend": record.backend,
        "model_role": record.model_role,
        "quantization": record.quantization,
        "threads": record.threads,
        "batch": record.batch,
        "ubatch": record.ubatch,
        "parallel": record.parallel,
        "context": record.context,
        "affinity": record.affinity,
    }
    for name, expected in candidate_fields.items():
        actual = list(candidate.get(name) or []) if name == "affinity" else candidate.get(name)
        if not _same_typed(actual, expected):
            raise PublicDerivationError(f"{prefix} candidate {name} disagrees with its row")
    if not isinstance(config.get("dataset"), dict):
        raise PublicDerivationError(f"{prefix} dataset receipt is missing")
    if not isinstance(config.get("triage_schema"), dict):
        raise PublicDerivationError(f"{prefix} response schema receipt is missing")
    if not isinstance(config.get("backend_proof"), dict) or not isinstance(
        config.get("cpu_only_proof"), dict
    ):
        raise PublicDerivationError(f"{prefix} runtime proof receipts are missing")
    if not isinstance(config.get("prompt_sha256"), str) or not _SHA256.fullmatch(
        config["prompt_sha256"]
    ):
        raise PublicDerivationError(f"{prefix} prompt receipt is invalid")

    request_fields = {
        "case_id": record.case_id,
        "repetition": record.repetition,
        "model": record.candidate_id,
    }
    for name, expected in request_fields.items():
        if not _same_typed(request.get(name), expected):
            raise PublicDerivationError(f"{prefix} request {name} disagrees with its row")
    if not isinstance(request.get("messages"), list) or not isinstance(
        request.get("response_format"), dict
    ):
        raise PublicDerivationError(f"{prefix} request prompt/schema fields are invalid")

    content = response.get("content")
    finish_reason = response.get("finish_reason")
    if not isinstance(content, str) or (
        finish_reason is not None and not isinstance(finish_reason, str)
    ):
        raise PublicDerivationError(f"{prefix} response fields are invalid")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise PublicDerivationError(f"{prefix} response usage is missing")
    for name, expected in {
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
    }.items():
        if not _same_typed(usage.get(name), expected) or expected <= 0:
            raise PublicDerivationError(f"{prefix} response {name} disagrees with its row")

    timing = response.get("timing")
    if not isinstance(timing, dict):
        raise PublicDerivationError(f"{prefix} response timing is missing")
    for name, expected in {
        "start_ns": record.start_ns,
        "first_content_token_ns": record.first_token_ns,
        "end_ns": record.end_ns,
        "ttft_ms": record.ttft_ms,
        "e2e_ms": record.e2e_ms,
    }.items():
        if not _same_typed(timing.get(name), expected):
            raise PublicDerivationError(f"{prefix} response timing {name} disagrees")

    score = response.get("score")
    if not isinstance(score, dict):
        raise PublicDerivationError(f"{prefix} response score is missing")
    for name, expected in {
        "schema_valid": record.schema_valid,
        "quality_score": record.quality_score,
        "safety_score": record.safety_score,
        "issues": record.errors,
    }.items():
        if not _same_typed(score.get(name), expected):
            raise PublicDerivationError(f"{prefix} response score {name} disagrees")


def _snapshot_run(run_dir: Path) -> _RunSnapshot:
    run_id = run_dir.name
    if not _RUN_ID.fullmatch(run_id):
        raise PublicDerivationError(f"invalid evidence run ID: {run_id}")
    actual = {path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file()}
    if actual != _RUN_FILES:
        missing = sorted(_RUN_FILES.difference(actual))
        extra = sorted(actual.difference(_RUN_FILES))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise PublicDerivationError(f"run {run_id} inventory is invalid: {'; '.join(detail)}")

    manifest = _load_mapping(run_dir / "integrity.json")
    if set(manifest) != {"sha256"} or not isinstance(manifest["sha256"], dict):
        raise PublicDerivationError(f"run {run_id} integrity manifest has an invalid schema")
    hashes = manifest["sha256"]
    if set(hashes) != _RUN_FILES.difference({"integrity.json"}) or any(
        not isinstance(name, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
        for name, digest in hashes.items()
    ):
        raise PublicDerivationError(f"run {run_id} integrity inventory is invalid")
    integrity_errors = verify_integrity(run_dir, hashes)
    if integrity_errors:
        raise PublicDerivationError(
            f"run {run_id} integrity failed: " + "; ".join(integrity_errors)
        )

    lines = [
        line
        for line in (run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 1:
        raise PublicDerivationError(f"run {run_id} requests.jsonl must contain exactly one row")
    try:
        record = BenchmarkRecord.model_validate_json(lines[0])
    except Exception as exc:
        raise PublicDerivationError(f"run {run_id} has an invalid BenchmarkRecord") from exc
    config = _load_mapping(run_dir / "run-config.json")
    request = _load_mapping(run_dir / "request.json")
    response = _load_mapping(run_dir / "response.json")
    _validate_run_fields(run_id, record, config, request, response)
    semantic = {
        "record": record.model_dump(mode="json"),
        "request": request,
        "response": response,
        "run_config": config,
    }
    return _RunSnapshot(
        run_id=run_id,
        semantic_sha256=_canonical_sha256(semantic),
        manifest_sha256=_sha256_file(run_dir / "integrity.json"),
        typed_sha256={name: _sha256_file(run_dir / name) for name in sorted(_TYPED_RUN_FILES)},
        runtime_proof_sha256=_sha256_file(run_dir / "runtime-proof.txt"),
    )


def _snapshot_stores(root: Path) -> dict[str, dict[str, _RunSnapshot]]:
    result: dict[str, dict[str, _RunSnapshot]] = {}
    seen_run_ids: set[str] = set()
    for namespace, store in _store_roots(root).items():
        if any(not child.is_dir() for child in store.iterdir()):
            raise PublicDerivationError(f"raw namespace contains a non-run entry: {namespace}")
        runs: dict[str, _RunSnapshot] = {}
        for run_dir in sorted(store.iterdir()):
            snapshot = _snapshot_run(run_dir)
            if snapshot.run_id in seen_run_ids:
                raise PublicDerivationError(
                    f"duplicate run ID across namespaces: {snapshot.run_id}"
                )
            seen_run_ids.add(snapshot.run_id)
            runs[snapshot.run_id] = snapshot
        result[namespace] = runs
    return result


def _semantic_file_payload(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        value = _load_json(path)
    else:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise PublicDerivationError(f"invalid YAML evidence {path.name}") from exc
    if not isinstance(value, dict):
        raise PublicDerivationError(f"{path.name} must contain a mapping")
    return value


def _source_run_ids(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_run_ids":
                if not isinstance(child, list) or not all(
                    isinstance(item, str) and _RUN_ID.fullmatch(item) for item in child
                ):
                    raise PublicDerivationError("source_run_ids contains an invalid run ID")
                found.extend(child)
            else:
                found.extend(_source_run_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_source_run_ids(child))
    return found


def _validate_a4_source(
    source: object,
    *,
    phase: str,
    stores: dict[str, dict[str, _RunSnapshot]],
) -> str:
    if not isinstance(source, dict):
        raise PublicDerivationError(f"A4 {phase} source evidence is missing")
    session_value = source.get("session_dir")
    if not isinstance(session_value, str):
        raise PublicDerivationError(f"A4 {phase} session path is missing")
    session_path = Path(session_value)
    if (
        session_path.is_absolute()
        or ".." in session_path.parts
        or len(session_path.parts) != 4
        or session_path.parts[:3] != ("artifacts", "a4", "runs")
        or not _SESSION_ID.fullmatch(session_path.name)
        or not session_path.name.startswith(f"{phase}-")
    ):
        raise PublicDerivationError(f"A4 {phase} session path is not canonical")
    namespace = f"a4/runs/{session_path.name}/raw"
    runs = stores.get(namespace)
    if runs is None:
        raise PublicDerivationError(f"A4 {phase} session is absent from the public namespace")
    referenced: list[str] = []
    for role in ("weak", "strong"):
        ids = source.get(f"{role}_run_ids")
        if not isinstance(ids, list) or not all(
            isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) for run_id in ids
        ):
            raise PublicDerivationError(f"A4 {phase} {role} run IDs are invalid")
        referenced.extend(ids)
    if len(referenced) != len(set(referenced)) or set(referenced) != set(runs):
        raise PublicDerivationError(f"A4 {phase} run inventory does not match its source receipt")
    return namespace


def _validate_semantic_references(
    payloads: dict[str, dict[str, Any]],
    stores: dict[str, dict[str, _RunSnapshot]],
    *,
    require_complete: bool,
) -> None:
    main_runs = set(stores.get("raw", {}))
    for name in ("search-plan.json", "optimized-profile.yaml"):
        payload = payloads.get(name)
        if payload is None:
            continue
        referenced = _source_run_ids(payload)
        missing = sorted(set(referenced).difference(main_runs))
        if missing:
            raise PublicDerivationError(
                f"{name} cites runs outside the main raw namespace: " + ", ".join(missing)
            )

    referenced_sessions: set[str] = set()
    policy = payloads.get("a4-frozen-policy.json")
    if policy is not None:
        referenced_sessions.add(
            _validate_a4_source(policy.get("source_evidence"), phase="calibration", stores=stores)
        )
    results = payloads.get("quality-results.json")
    if results is not None:
        held_out = results.get("held_out")
        if not isinstance(held_out, dict):
            raise PublicDerivationError("A4 held-out result is missing")
        referenced_sessions.add(
            _validate_a4_source(held_out.get("source_evidence"), phase="held-out", stores=stores)
        )
    actual_sessions = {name for name in stores if name != "raw"}
    if (require_complete or referenced_sessions) and referenced_sessions != actual_sessions:
        raise PublicDerivationError("A4 session inventory does not match the frozen receipts")


def _snapshot_bundle(root: Path | str, *, require_complete: bool) -> _BundleSnapshot:
    resolved = _assert_safe_tree(root)
    tree = _tree_snapshot(resolved)
    stores = _snapshot_stores(resolved)
    semantic_payloads: dict[str, dict[str, Any]] = {}
    semantic_files: dict[str, dict[str, str]] = {}
    for name in _SEMANTIC_FILES:
        path = resolved / name
        if not path.is_file():
            continue
        payload = _semantic_file_payload(path)
        semantic_payloads[name] = payload
        semantic_files[name] = {
            "sha256": _sha256_file(path),
            "semantic_sha256": _canonical_sha256(payload),
        }

    a4_namespaces = [name for name in stores if name != "raw"]
    complete = (
        bool(stores.get("raw"))
        and set(semantic_files) == set(_SEMANTIC_FILES)
        and any(name.startswith("a4/runs/calibration-") for name in a4_namespaces)
        and any(name.startswith("a4/runs/held-out-") for name in a4_namespaces)
    )
    if require_complete and not complete:
        raise PublicDerivationError("public A0-A4 derivation is incomplete")
    _validate_semantic_references(
        semantic_payloads,
        stores,
        require_complete=require_complete,
    )
    semantic_index = {
        "semantic_files": semantic_files,
        "stores": {
            namespace: {run_id: run.semantic_sha256 for run_id, run in sorted(runs.items())}
            for namespace, runs in sorted(stores.items())
        },
    }
    return _BundleSnapshot(
        root=resolved,
        tree=tree,
        tree_sha256=_canonical_sha256(tree),
        stores=stores,
        semantic_files=semantic_files,
        semantic_payloads=semantic_payloads,
        semantic_sha256=_canonical_sha256(semantic_index),
        complete=complete,
    )


def has_benchmark_derivation_inputs(root: Path | str) -> bool:
    """Return whether a tree contains at least one complete benchmark run shape."""

    base = Path(root)
    candidates = list(base.glob("raw/*")) + list(base.glob("a4/runs/*/raw/*"))
    return any(
        run.is_dir() and all((run / name).is_file() for name in _RUN_FILES) for run in candidates
    )


def _normalise_findings(findings: list[dict[str, object]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for finding in findings:
        if set(finding) != {"path", "categories"}:
            raise PublicDerivationError("redaction finding has an invalid schema")
        path = _safe_relative(finding["path"])
        categories = finding["categories"]
        if (
            path in result
            or not isinstance(categories, list)
            or not categories
            or not all(isinstance(item, str) and item for item in categories)
            or categories != sorted(set(categories))
        ):
            raise PublicDerivationError(f"invalid redaction finding: {path}")
        result[path] = categories
    return result


def _run_file(path: str) -> str | None:
    parts = Path(path).parts
    if len(parts) == 3 and parts[0] == "raw" and _RUN_ID.fullmatch(parts[1]):
        return parts[2]
    if (
        len(parts) == 6
        and parts[:2] == ("a4", "runs")
        and _SESSION_ID.fullmatch(parts[2])
        and parts[3] == "raw"
        and _RUN_ID.fullmatch(parts[4])
    ):
        return parts[5]
    return None


def _regenerated_kind(path: str) -> str | None:
    if path == "performance-probes.json":
        return "performance-probe-manifest-v1"
    if path == "report-integrity.json":
        return "report-integrity-manifest-v1"
    if _run_file(path) == "integrity.json":
        return "run-integrity-manifest-v1"
    return None


def _changes(
    private: _BundleSnapshot,
    public: _BundleSnapshot,
    *,
    findings: dict[str, list[str]] | None,
) -> list[dict[str, object]]:
    if set(private.tree) != set(public.tree):
        missing = sorted(set(private.tree).difference(public.tree))
        extra = sorted(set(public.tree).difference(private.tree))
        raise PublicDerivationError(
            "private/public file inventories differ"
            + (f"; missing={missing}" if missing else "")
            + (f"; extra={extra}" if extra else "")
        )
    changed = {path for path in private.tree if private.tree[path] != public.tree[path]}
    if findings is not None:
        unknown = sorted(set(findings).difference(changed))
        if unknown:
            raise PublicDerivationError(
                "redaction findings do not describe changed bytes: " + ", ".join(unknown)
            )

    result: list[dict[str, object]] = []
    for path in sorted(changed):
        run_name = _run_file(path)
        if path in _SEMANTIC_FILES or run_name in _TYPED_RUN_FILES:
            raise PublicDerivationError(f"semantic evidence changed during redaction: {path}")
        kind = _regenerated_kind(path)
        categories: list[str] = []
        if kind is None:
            if findings is None:
                kind = "redacted-text-v1"
            elif path in findings:
                kind = "redacted-text-v1"
                categories = findings[path]
            else:
                raise PublicDerivationError(f"unexplained public evidence change: {path}")
        result.append(
            {
                "categories": categories,
                "kind": kind,
                "path": path,
                "private_sha256": private.tree[path],
                "public_sha256": public.tree[path],
            }
        )
    return result


def _merged_stores(
    private: _BundleSnapshot,
    public: _BundleSnapshot,
) -> list[dict[str, object]]:
    if set(private.stores) != set(public.stores):
        raise PublicDerivationError("private/public raw namespaces differ")
    stores: list[dict[str, object]] = []
    for namespace in sorted(private.stores):
        private_runs = private.stores[namespace]
        public_runs = public.stores[namespace]
        if set(private_runs) != set(public_runs):
            raise PublicDerivationError(f"private/public run inventory differs: {namespace}")
        merged_runs: list[dict[str, object]] = []
        for run_id in sorted(private_runs):
            private_run = private_runs[run_id]
            public_run = public_runs[run_id]
            if (
                private_run.semantic_sha256 != public_run.semantic_sha256
                or private_run.typed_sha256 != public_run.typed_sha256
            ):
                raise PublicDerivationError(
                    f"typed evidence changed during redaction: {namespace}/{run_id}"
                )
            merged_runs.append(
                {
                    "private_manifest_sha256": private_run.manifest_sha256,
                    "private_runtime_proof_sha256": private_run.runtime_proof_sha256,
                    "public_manifest_sha256": public_run.manifest_sha256,
                    "public_runtime_proof_sha256": public_run.runtime_proof_sha256,
                    "run_id": run_id,
                    "semantic_sha256": private_run.semantic_sha256,
                    "typed_sha256": private_run.typed_sha256,
                }
            )
        stores.append({"namespace": namespace, "runs": merged_runs})
    return stores


def build_public_derivation_receipt(
    private_root: Path | str,
    public_root: Path | str,
    *,
    redaction_findings: list[dict[str, object]] | None = None,
    require_complete: bool = False,
) -> dict[str, object]:
    """Build and write a deterministic private-to-public derivation receipt."""

    private = _snapshot_bundle(private_root, require_complete=require_complete)
    public = _snapshot_bundle(public_root, require_complete=require_complete)
    if private.semantic_sha256 != public.semantic_sha256:
        raise PublicDerivationError("private/public A0-A4 semantic snapshots differ")
    if private.semantic_files != public.semantic_files:
        raise PublicDerivationError("top-level A3/A4 semantic files changed during redaction")
    findings = _normalise_findings(redaction_findings) if redaction_findings is not None else None
    receipt: dict[str, object] = {
        "changes": _changes(private, public, findings=findings),
        "complete": private.complete and public.complete,
        "private_file_count": len(private.tree),
        "private_tree_sha256": private.tree_sha256,
        "public_file_count": len(public.tree),
        "public_tree_sha256": public.tree_sha256,
        "schema_version": PUBLIC_DERIVATION_SCHEMA_VERSION,
        "semantic_files": private.semantic_files,
        "semantic_sha256": private.semantic_sha256,
        "stores": _merged_stores(private, public),
    }
    write_json(Path(public_root) / PUBLIC_DERIVATION_RECEIPT, receipt)
    return receipt


def _receipt(path: Path) -> dict[str, Any]:
    receipt = _load_mapping(path)
    required = {
        "changes",
        "complete",
        "private_file_count",
        "private_tree_sha256",
        "public_file_count",
        "public_tree_sha256",
        "schema_version",
        "semantic_files",
        "semantic_sha256",
        "stores",
    }
    if set(receipt) != required:
        raise PublicDerivationError("public derivation receipt has unexpected fields")
    if receipt["schema_version"] != PUBLIC_DERIVATION_SCHEMA_VERSION:
        raise PublicDerivationError("unsupported public derivation receipt schema")
    if type(receipt["complete"]) is not bool:
        raise PublicDerivationError("public derivation completeness flag is invalid")
    for name in ("private_file_count", "public_file_count"):
        if type(receipt[name]) is not int or receipt[name] < 0:
            raise PublicDerivationError(f"public derivation {name} is invalid")
    for name in ("private_tree_sha256", "public_tree_sha256", "semantic_sha256"):
        if not isinstance(receipt[name], str) or not _SHA256.fullmatch(receipt[name]):
            raise PublicDerivationError(f"public derivation {name} is invalid")
    if not isinstance(receipt["changes"], list) or not isinstance(receipt["stores"], list):
        raise PublicDerivationError("public derivation collections are invalid")
    if not isinstance(receipt["semantic_files"], dict):
        raise PublicDerivationError("public derivation semantic files are invalid")
    return receipt


def _expected_stores(receipt: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for store in receipt["stores"]:
        if not isinstance(store, dict) or set(store) != {"namespace", "runs"}:
            raise PublicDerivationError("public derivation store entry is invalid")
        namespace = _safe_relative(store["namespace"])
        if namespace in result or not isinstance(store["runs"], list):
            raise PublicDerivationError(f"duplicate or invalid receipt namespace: {namespace}")
        runs: dict[str, dict[str, Any]] = {}
        for run in store["runs"]:
            required = {
                "private_manifest_sha256",
                "private_runtime_proof_sha256",
                "public_manifest_sha256",
                "public_runtime_proof_sha256",
                "run_id",
                "semantic_sha256",
                "typed_sha256",
            }
            if not isinstance(run, dict) or set(run) != required:
                raise PublicDerivationError("public derivation run entry is invalid")
            run_id = run["run_id"]
            if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or run_id in runs:
                raise PublicDerivationError("public derivation run ID is invalid")
            typed = run["typed_sha256"]
            if not isinstance(typed, dict) or set(typed) != _TYPED_RUN_FILES:
                raise PublicDerivationError(f"public derivation typed hashes are invalid: {run_id}")
            digests = [
                run["private_manifest_sha256"],
                run["private_runtime_proof_sha256"],
                run["public_manifest_sha256"],
                run["public_runtime_proof_sha256"],
                run["semantic_sha256"],
                *typed.values(),
            ]
            if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in digests):
                raise PublicDerivationError(f"public derivation run hashes are invalid: {run_id}")
            runs[run_id] = run
        result[namespace] = runs
    return result


def _expected_changes(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    allowed_kinds = {
        "performance-probe-manifest-v1",
        "redacted-text-v1",
        "report-integrity-manifest-v1",
        "run-integrity-manifest-v1",
    }
    for change in receipt["changes"]:
        required = {"categories", "kind", "path", "private_sha256", "public_sha256"}
        if not isinstance(change, dict) or set(change) != required:
            raise PublicDerivationError("public derivation change entry is invalid")
        path = _safe_relative(change["path"])
        kind = change["kind"]
        categories = change["categories"]
        if (
            path in result
            or kind not in allowed_kinds
            or not isinstance(categories, list)
            or not all(isinstance(item, str) and item for item in categories)
            or categories != sorted(set(categories))
        ):
            raise PublicDerivationError(f"public derivation change is invalid: {path}")
        expected_kind = _regenerated_kind(path)
        if (expected_kind is not None and kind != expected_kind) or (
            expected_kind is None and kind != "redacted-text-v1"
        ):
            raise PublicDerivationError(f"public derivation change kind is invalid: {path}")
        if (kind == "redacted-text-v1") != bool(categories):
            raise PublicDerivationError(f"public derivation change categories are invalid: {path}")
        if path in _SEMANTIC_FILES or _run_file(path) in _TYPED_RUN_FILES:
            raise PublicDerivationError(f"receipt permits a semantic evidence change: {path}")
        for name in ("private_sha256", "public_sha256"):
            if not isinstance(change[name], str) or not _SHA256.fullmatch(change[name]):
                raise PublicDerivationError(f"public derivation change hash is invalid: {path}")
        if change["private_sha256"] == change["public_sha256"]:
            raise PublicDerivationError(f"public derivation change has identical bytes: {path}")
        result[path] = change
    return result


def _verify_against_receipt(
    receipt: dict[str, Any],
    public: _BundleSnapshot,
    private: _BundleSnapshot | None,
    *,
    require_complete: bool,
) -> None:
    if require_complete and receipt["complete"] is not True:
        raise PublicDerivationError("public derivation receipt is not complete")
    if receipt["public_file_count"] != len(public.tree):
        raise PublicDerivationError("public derivation file count changed")
    if receipt["public_tree_sha256"] != public.tree_sha256:
        raise PublicDerivationError("public derivation tree digest changed")
    if receipt["semantic_sha256"] != public.semantic_sha256:
        raise PublicDerivationError("public derivation semantic digest changed")
    if receipt["semantic_files"] != public.semantic_files:
        raise PublicDerivationError("public derivation semantic file receipts changed")

    expected_stores = _expected_stores(receipt)
    if set(expected_stores) != set(public.stores):
        raise PublicDerivationError("public derivation raw namespace inventory changed")
    for namespace, public_runs in public.stores.items():
        expected_runs = expected_stores[namespace]
        if set(expected_runs) != set(public_runs):
            raise PublicDerivationError(f"public derivation run inventory changed: {namespace}")
        for run_id, run in public_runs.items():
            expected = expected_runs[run_id]
            if (
                expected["semantic_sha256"] != run.semantic_sha256
                or expected["typed_sha256"] != run.typed_sha256
                or expected["public_manifest_sha256"] != run.manifest_sha256
                or expected["public_runtime_proof_sha256"] != run.runtime_proof_sha256
            ):
                raise PublicDerivationError(
                    f"public derivation run receipt changed: {namespace}/{run_id}"
                )

    expected_changes = _expected_changes(receipt)
    for path, change in expected_changes.items():
        if path not in public.tree or public.tree[path] != change["public_sha256"]:
            raise PublicDerivationError(f"public derivation changed file is missing: {path}")

    if private is None:
        return
    if receipt["private_file_count"] != len(private.tree):
        raise PublicDerivationError("private derivation file count changed")
    if receipt["private_tree_sha256"] != private.tree_sha256:
        raise PublicDerivationError("private derivation tree digest changed")
    if private.semantic_sha256 != public.semantic_sha256:
        raise PublicDerivationError("private/public derivation semantics differ")
    if private.semantic_files != public.semantic_files:
        raise PublicDerivationError("private/public semantic files differ")
    receipt_findings = {
        path: change["categories"]
        for path, change in expected_changes.items()
        if change["kind"] == "redacted-text-v1"
    }
    actual_changes = _changes(private, public, findings=receipt_findings)
    if {item["path"]: item for item in actual_changes} != expected_changes:
        raise PublicDerivationError("private/public change mapping disagrees with the receipt")
    merged = _merged_stores(private, public)
    if merged != receipt["stores"]:
        raise PublicDerivationError("private/public store mapping disagrees with the receipt")


def verify_public_derivation(
    public_root: Path | str = "artifacts-public",
    *,
    private_root: Path | str | None = None,
    require_complete: bool = True,
) -> list[str]:
    """Replay a public receipt without accessing current host, model, or build files."""

    try:
        public_path = Path(public_root)
        public = _snapshot_bundle(public_path, require_complete=require_complete)
        receipt = _receipt(public.root / PUBLIC_DERIVATION_RECEIPT)
        private = (
            _snapshot_bundle(private_root, require_complete=require_complete)
            if private_root is not None
            else None
        )
        _verify_against_receipt(
            receipt,
            public,
            private,
            require_complete=require_complete,
        )
    except (OSError, PublicDerivationError) as exc:
        return [str(exc)]
    return []
