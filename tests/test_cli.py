from __future__ import annotations

import json
import platform
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.main import get_command
from typer.testing import CliRunner

import a64pilot.benchmark.cascade as cascade_module
from a64pilot.cli import _all_run_limit, _fair_run_split, app
from a64pilot.schemas import SYSTEM_INFO_SCHEMA_VERSION, SystemInfo

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def _registered_option_names(*command_path: str) -> set[str]:
    """Return Click's registered spellings without depending on Rich rendering."""

    command: Any = get_command(app)
    for name in command_path:
        commands = getattr(command, "commands", None)
        if not isinstance(commands, dict):
            raise AssertionError(f"{command_path!r} does not resolve to a command group")
        child = commands.get(name)
        if child is None:
            raise AssertionError(f"command {name!r} is not registered in {command_path!r}")
        command = child
    return {
        spelling
        for parameter in getattr(command, "params", ())
        for spelling in (
            *getattr(parameter, "opts", ()),
            *getattr(parameter, "secondary_opts", ()),
        )
    }


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


def test_quick_mode_never_truncates_formal_a0_a1_a2() -> None:
    assert _all_run_limit("a0", quick=True) is None
    assert _all_run_limit("a1", quick=True) is None
    assert _all_run_limit("a2", quick=True) is None
    assert _all_run_limit("a3", quick=True) == 10


def test_bounded_protocol_probe_command_is_exposed() -> None:
    result = runner.invoke(app, ["benchmark", "probes", "--help"])
    assert result.exit_code == 0
    assert "p1/p2 concurrency" in result.stdout
    assert "--max-minutes" in _registered_option_names("benchmark", "probes")


def test_probe_verifier_exposes_private_and_public_roots() -> None:
    result = runner.invoke(app, ["verify-probes", "--help"])
    assert result.exit_code == 0
    option_names = _registered_option_names("verify-probes")
    assert {"--artifacts-dir", "--manifest-only"} <= option_names


@pytest.mark.parametrize(
    "arguments",
    (
        ["benchmark", "all", "--formal-max-minutes", "nan"],
        ["benchmark", "quality", "--calibrate", "--max-minutes", "0"],
    ),
)
def test_real_benchmark_phase_budgets_must_be_finite_and_positive(
    arguments: list[str],
) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2
    assert "finite and positive" in result.output


def test_held_out_cli_preflights_before_component_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("cases.jsonl", "split.json"):
        (demo / name).write_text(
            (ROOT / "demo" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    existing = {
        "freeze_id": "f" * 64,
        "a4_admitted_by_quality_gate": False,
        "shipping_profile": "a3-strong-only",
        "held_out": {
            "route_counts": {"weak": 0, "strong": 20, "weak_then_strong": 0},
            "route_shares": {"weak": 0.0, "strong": 100.0, "weak_then_strong": 0.0},
            "escalation_rate": 0.0,
        },
    }
    monkeypatch.setattr(
        cascade_module,
        "preflight_held_out_evaluation",
        lambda **_kwargs: (existing, False),
    )

    def fail_if_collected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("held-out component inference ran before preflight")

    monkeypatch.setattr(cascade_module, "collect_real_component_outputs", fail_if_collected)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["benchmark", "quality", "--held-out", "--frozen"])

    assert result.exit_code == 0, result.output
    assert "held-out-frozen-already-recorded" in result.stdout


def test_held_out_cli_persists_reservation_before_component_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("cases.jsonl", "split.json"):
        (demo / name).write_text(
            (ROOT / "demo" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    events: list[str] = []
    monkeypatch.setattr(
        cascade_module,
        "preflight_held_out_evaluation",
        lambda **_kwargs: (None, False),
    )
    monkeypatch.setattr(
        cascade_module,
        "load_frozen_calibration",
        lambda *_args, **_kwargs: (
            {"freeze_id": "f" * 64},
            SimpleNamespace(fallback_strong_only=False),
            object(),
            object(),
        ),
    )

    def reserve(**_kwargs: object) -> dict[str, object]:
        events.append("reserve")
        return {"status": "held-out-in-progress"}

    def collect(*_args: object, **kwargs: object) -> object:
        assert events == ["reserve"]
        assert isinstance(kwargs.get("deadline"), float)
        events.append("collect")
        return object()

    monkeypatch.setattr(cascade_module, "reserve_held_out_evaluation", reserve)
    monkeypatch.setattr(cascade_module, "collect_real_component_outputs", collect)
    monkeypatch.setattr(
        cascade_module,
        "evaluate_held_out",
        lambda *_args, **_kwargs: {
            "a4_admitted_by_quality_gate": False,
            "shipping_profile": "a3-strong-only",
            "held_out": {
                "route_counts": {"weak": 0, "strong": 20, "weak_then_strong": 0},
                "route_shares": {"weak": 0.0, "strong": 100.0, "weak_then_strong": 0.0},
                "escalation_rate": 0.0,
            },
        },
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["benchmark", "quality", "--held-out", "--frozen"])

    assert result.exit_code == 0, result.output
    assert events == ["reserve", "collect"]


def test_held_out_cli_does_not_infer_when_reservation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("cases.jsonl", "split.json"):
        (demo / name).write_text(
            (ROOT / "demo" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(
        cascade_module,
        "preflight_held_out_evaluation",
        lambda **_kwargs: (None, False),
    )
    monkeypatch.setattr(
        cascade_module,
        "load_frozen_calibration",
        lambda *_args, **_kwargs: (
            {"freeze_id": "f" * 64},
            SimpleNamespace(fallback_strong_only=False),
            object(),
            object(),
        ),
    )

    def refuse(**_kwargs: object) -> object:
        raise cascade_module.CascadeWorkflowError("held-out inference is already reserved")

    def fail_if_collected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("held-out inference ran after reservation failure")

    monkeypatch.setattr(cascade_module, "reserve_held_out_evaluation", refuse)
    monkeypatch.setattr(cascade_module, "collect_real_component_outputs", fail_if_collected)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["benchmark", "quality", "--held-out", "--frozen"])

    assert result.exit_code == 2
    assert "already reserved" in result.output


def test_doctor_writes_shared_schema(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--json", "--artifacts-dir", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads((tmp_path / "system-info.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == SYSTEM_INFO_SCHEMA_VERSION
    shared = SystemInfo.model_validate(payload)
    assert shared.architecture in {"aarch64", "x86_64"}
    assert shared.operating_system == platform.system()
    assert shared.arm64 is (shared.architecture == "aarch64")
    assert shared.real_benchmark_eligible is (
        shared.architecture == "aarch64" and shared.operating_system == "Linux"
    )
    if shared.cpu_model == "unknown":
        assert any(item.field == "cpu_model" for item in shared.provenance_limitations)
        assert shared.limitations
