from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REDACTOR: dict[str, Any] = runpy.run_path(str(ROOT / "scripts/redact-artifacts.py"))


def test_llama_elapsed_prefix_is_not_mistaken_for_an_ip_address() -> None:
    text = "0.10.168.200 D que start_loop: waiting for new tasks\n"
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted == text
    assert categories == set()


def test_real_ip_is_still_redacted_beside_a_llama_elapsed_prefix() -> None:
    text = "0.10.168.200 I srv peer=10.42.0.7 public=203.0.113.8\n"
    redacted, categories = REDACTOR["redact_text"](text, allow_llama_elapsed_prefix=True)
    assert redacted == ("0.10.168.200 I srv peer=<redacted-ip> public=<redacted-ip>\n")
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
    text = "0.10.168.200 D que start_loop: waiting for new tasks\n"
    runtime_log = tmp_path / "runtime" / "server.stderr.log"
    runtime_log.parent.mkdir()
    runtime_log.write_text(text, encoding="utf-8")
    ordinary_report = tmp_path / "report.txt"
    ordinary_report.write_text(text, encoding="utf-8")

    findings, scanned = REDACTOR["process_in_place"]([tmp_path], write=False)

    assert scanned == 2
    assert findings == [{"path": str(ordinary_report), "categories": ["ip_address"]}]
