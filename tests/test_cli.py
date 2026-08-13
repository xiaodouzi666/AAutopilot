from __future__ import annotations

import json
import platform
from pathlib import Path

from typer.testing import CliRunner

from a64pilot.cli import _all_run_limit, _fair_run_split, app
from a64pilot.schemas import SystemInfo

runner = CliRunner()


def test_version_option_exits_successfully() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_no_arguments_prints_help_successfully() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Quality-gated" in result.stdout
    assert "Commands" in result.stdout


def test_partial_fair_run_is_micro_evidence() -> None:
    assert _fair_run_split(None) == "test"
    assert _fair_run_split(1) == "micro"


def test_quick_mode_never_truncates_formal_a1_a2() -> None:
    assert _all_run_limit("a0", quick=True) == 10
    assert _all_run_limit("a1", quick=True) is None
    assert _all_run_limit("a2", quick=True) is None
    assert _all_run_limit("a3", quick=True) == 10


def test_doctor_writes_shared_schema(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--json", "--artifacts-dir", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads((tmp_path / "system-info.json").read_text(encoding="utf-8"))
    shared = SystemInfo.model_validate(payload)
    assert shared.architecture in {"aarch64", "x86_64"}
    assert shared.operating_system == platform.system()
    assert shared.arm64 is (shared.architecture == "aarch64")
    assert shared.real_benchmark_eligible is (
        shared.architecture == "aarch64" and shared.operating_system == "Linux"
    )
