from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REDACTOR: dict[str, Any] = runpy.run_path(str(ROOT / "scripts/redact-artifacts.py"))
LLAMA_LOG = (
    "0.00.002.243 I build: pinned llama.cpp\n"
    "0.10.168.198 D que start_loop: waiting for new tasks\n"
    "0.10.168.199 D que start_loop: processing new tasks\n"
    "0.10.168.200 D que start_loop: update slots\n"
)


def test_llama_elapsed_prefix_is_not_mistaken_for_an_ip_address() -> None:
    redacted, categories = REDACTOR["redact_text"](LLAMA_LOG, allow_llama_elapsed_prefix=True)
    assert redacted == LLAMA_LOG
    assert categories == set()


def test_real_ip_is_still_redacted_beside_a_llama_elapsed_prefix() -> None:
    text = LLAMA_LOG.replace("update slots", "peer=10.42.0.7 public=203.0.113.8")
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted == LLAMA_LOG.replace("update slots", "peer=<redacted-ip> public=<redacted-ip>")
    assert categories == {"ip_address"}


def test_ip_like_value_outside_exact_log_prefix_remains_sensitive() -> None:
    text = "elapsed=0.10.168.200 D but this is not a line prefix\n"
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted == "elapsed=<redacted-ip> D but this is not a line prefix\n"
    assert categories == {"ip_address"}


def test_noncanonical_elapsed_shape_remains_sensitive() -> None:
    for text in (
        "10.60.123.200 D invalid seconds\n",
        "10.20.123.200 T unsupported log level\n",
    ):
        redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
        assert redacted.startswith("<redacted-ip> ")
        assert categories == {"ip_address"}


def test_elapsed_shape_is_redacted_outside_reviewed_runtime_evidence() -> None:
    text = "0.10.168.200 D ordinary report text\n"
    redacted, categories = REDACTOR["redact_text"](text)
    assert redacted == "<redacted-ip> D ordinary report text\n"
    assert categories == {"ip_address"}


def test_only_reviewed_runtime_paths_enable_elapsed_prefix_handling(tmp_path: Path) -> None:
    runtime_log = tmp_path / "runtime" / "server.stderr.log"
    runtime_log.parent.mkdir()
    raw_proof = tmp_path / "raw" / "run-id" / "runtime-proof.txt"
    raw_proof.parent.mkdir(parents=True)
    unrelated = tmp_path / "other" / "server.stderr.log"
    unrelated.parent.mkdir()

    assert REDACTOR["is_llama_runtime_evidence"](runtime_log)
    assert REDACTOR["is_llama_runtime_evidence"](raw_proof)
    assert not REDACTOR["is_llama_runtime_evidence"](unrelated)


def test_artifact_check_uses_runtime_path_scope(tmp_path: Path) -> None:
    runtime_log = tmp_path / "runtime" / "server.stderr.log"
    runtime_log.parent.mkdir()
    runtime_log.write_text(LLAMA_LOG, encoding="utf-8")
    ordinary_report = tmp_path / "report.txt"
    ordinary_report.write_text(LLAMA_LOG, encoding="utf-8")

    findings, scanned = REDACTOR["process_in_place"]([tmp_path], write=False)

    assert scanned == 2
    assert findings == [{"path": str(ordinary_report), "categories": ["ip_address"]}]


def test_isolated_real_ip_in_runtime_evidence_is_still_redacted(tmp_path: Path) -> None:
    for address, level in (("10.42.100.200", "W"), ("8.18.100.200", "E")):
        runtime_log = tmp_path / "runtime" / f"{address}.stderr.log"
        runtime_log.parent.mkdir(exist_ok=True)
        runtime_log.write_text(f"{address} {level} isolated address\n", encoding="utf-8")

        findings, scanned = REDACTOR["process_in_place"]([runtime_log], write=False)

        assert scanned == 1
        assert findings == [{"path": str(runtime_log), "categories": ["ip_address"]}]


def test_address_sequence_cannot_bootstrap_elapsed_prefix_handling() -> None:
    text = (
        "10.42.100.200 W first address\n"
        "10.42.100.201 W second address\n"
        "10.42.100.202 W third address\n"
    )
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted.count("<redacted-ip>") == 3
    assert categories == {"ip_address"}


def test_elapsed_sequence_accepts_minute_rollover() -> None:
    text = (
        "0.59.999.998 D before rollover\n"
        "0.59.999.999 D at rollover\n"
        "1.00.000.001 D after rollover\n"
    )
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted == text
    assert categories == set()


def test_public_copy_normalizes_ambiguous_elapsed_prefix() -> None:
    text = (
        "0.00.001.001 D start\n"
        "0.59.999.999 D before rollover\n"
        "1.00.000.001 D after rollover\n"
        "1.42.100.200 W canonical clock that is also a valid IPv4 address\n"
    )
    redacted, categories = REDACTOR["redact_text"](
        text,
        allow_llama_elapsed_prefix=True,
        normalize_llama_elapsed_prefixes=True,
    )
    assert "1.42.100.200" not in redacted
    assert "<llama-elapsed> W canonical clock" in redacted
    assert categories == {"ip_address"}


def test_sanitized_copy_normalizes_then_passes_strict_recheck(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    runtime_log = source / "runtime" / "server.stderr.log"
    runtime_log.parent.mkdir(parents=True)
    runtime_log.write_text(LLAMA_LOG, encoding="utf-8")
    destination = tmp_path / "artifacts-public"

    findings, scanned = REDACTOR["sanitized_copy"](source, destination)

    public_log = destination / "runtime" / runtime_log.name
    assert scanned == 1
    assert findings == [
        {"path": str(runtime_log.relative_to(source)), "categories": ["ip_address"]}
    ]
    assert public_log.read_text(encoding="utf-8").count("<llama-elapsed>") == 4
    public_findings, public_scanned = REDACTOR["process_in_place"]([destination], write=False)
    assert public_scanned == 1
    assert public_findings == []


def test_sanitized_copy_rehashes_nested_a4_component_store(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    run_dir = source / "a4" / "runs" / "held-out-example" / "raw" / ("a" * 32)
    run_dir.mkdir(parents=True)
    proof = run_dir / "runtime-proof.txt"
    proof.write_text("peer=10.42.0.7\n", encoding="utf-8")
    (run_dir / "integrity.json").write_text(
        json.dumps(
            {"sha256": {"runtime-proof.txt": hashlib.sha256(proof.read_bytes()).hexdigest()}}
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "artifacts-public"

    REDACTOR["sanitized_copy"](source, destination)

    public_run = destination / run_dir.relative_to(source)
    public_proof = public_run / "runtime-proof.txt"
    manifest = json.loads((public_run / "integrity.json").read_text(encoding="utf-8"))
    assert public_proof.read_text(encoding="utf-8") == "peer=<redacted-ip>\n"
    assert (
        manifest["sha256"]["runtime-proof.txt"]
        == hashlib.sha256(public_proof.read_bytes()).hexdigest()
    )
