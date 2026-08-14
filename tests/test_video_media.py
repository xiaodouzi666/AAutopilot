from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_media_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "render-demo-video.py"
    spec = importlib.util.spec_from_file_location("a64pilot_render_demo_video", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def media() -> ModuleType:
    return load_media_module()


def write_pending_bundle(root: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    artifacts = root / "artifacts"
    figures = artifacts / "figures"
    figures.mkdir(parents=True)
    claims: list[dict[str, object]] = []
    report: dict[str, object] = {
        "evidence_status": "measurement-pending",
        "claims": claims,
    }
    (artifacts / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    (artifacts / "report-data.json").write_text(json.dumps(report), encoding="utf-8")
    for name in ("ablation.png", "pareto.png"):
        (figures / name).write_bytes(b"png")
    return report, claims


def valid_target_receipt(source_run_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "evidence_status": "measured",
        "workflow": "arm64-evidence",
        "github_run_id": "123456789",
        "github_run_attempt": 1,
        "github_sha": "a" * 40,
        "runner_name": "GitHub Actions 1000000000",
        "repository": "xiaodouzi666/AAutopilot",
        "workflow_ref": (
            "xiaodouzi666/AAutopilot/.github/workflows/arm64-evidence.yml@refs/heads/main"
        ),
        "workflow_sha": "a" * 40,
        "architecture": "aarch64",
        "operating_system": "Linux",
        "kernel": "6.11.0-arm64",
        "case_id": "incident-041",
        "candidate_id": "strong-kleidiai-q4-0",
        "backend": "kleidiai",
        "model": "strong",
        "diagnosis": "disk_pressure",
        "severity": "high",
        "tool_calls": ["check_disk"],
        "output_validation": "schema_safety_consistency_passed",
        "source_run_id": source_run_id,
    }


def write_target_receipt(
    root: Path,
    *,
    source_run_id: str,
    override: dict[str, object] | None = None,
) -> Path:
    receipt = valid_target_receipt(source_run_id)
    receipt.update(override or {})
    path = root / "artifacts" / "submission" / media_name()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def media_name() -> str:
    return "arm-target-demo-receipt.json"


def test_final_media_refuses_pending_evidence(tmp_path: Path, media: ModuleType) -> None:
    report, claims = write_pending_bundle(tmp_path)
    with pytest.raises(media.MediaNotReady, match="final video refused"):
        media.validate_evidence(
            report=report,
            claims=claims,
            artifacts=tmp_path / "artifacts",
            draft=False,
        )


def test_draft_media_allows_pending_only_with_watermark_policy(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    report, claims = write_pending_bundle(tmp_path)
    media.validate_final_evidence_against_raw(
        root=tmp_path,
        report=report,
        claims=claims,
        draft=True,
    )
    (tmp_path / "assets" / "submission").mkdir(parents=True)
    (tmp_path / "assets" / "submission" / media.THUMBNAIL_NAME).write_bytes(b"png")
    slides = media.build_slides(root=tmp_path, report=report, claims=claims, draft=True)
    assert any(slide.title == "Arm measurement pending" for slide in slides)
    assert all("%" not in line for slide in slides for line in slide.lines if line[:1].isdigit())


def test_final_claim_slide_labels_primary_and_paired_row_count(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    source_run_id = "a" * 32
    source_rows = [source_run_id, *(f"source-{index:02d}" for index in range(39))]
    claim = {
        "claim_id": media.PRIMARY_CLAIM_ID,
        "metric": "Q4_0 mean time-to-first-token reduction",
        "value": 3.0,
        "unit": "%",
        "confidence_interval": [1.0, 5.0],
        "demonstrated": True,
        "source_rows": source_rows,
    }
    report = {
        "evidence_status": "measured",
        "claims": [claim],
        "system": {
            "architecture": "aarch64",
            "operating_system": "Linux",
            "kernel": "6.11.0-arm64",
        },
    }
    write_target_receipt(tmp_path, source_run_id=source_run_id)
    assets = tmp_path / "assets" / "submission"
    assets.mkdir(parents=True)
    (assets / media.THUMBNAIL_NAME).write_bytes(b"png")

    slides = media.build_slides(root=tmp_path, report=report, claims=[claim], draft=False)
    claim_slide = next(slide for slide in slides if slide.title == claim["metric"])
    assert "PRIMARY • 20 paired cases • 40 formal source rows" in claim_slide.lines
    assert "only this outcome can unlock final publication" in claim_slide.lines


def test_linux_narration_uses_offline_espeak_ng(
    tmp_path: Path,
    media: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media.shutil,
        "which",
        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None,
    )
    backend = media._select_narration_backend(
        macos_voice="Samantha",
        require_narration=True,
    )
    assert backend.engine == "espeak-ng offline English"
    assert backend.voice == "en-us"
    assert backend.audio_suffix == ".wav"

    commands: list[list[str]] = []

    def fake_run(command: list[str], *, capture: bool = False) -> None:
        assert not capture
        commands.append(command)

    monkeypatch.setattr(media, "_run", fake_run)
    destination = tmp_path / "narration.wav"
    media._narrate(
        "AArch64 Autopilot measured the Arm target.",
        destination,
        backend=backend,
        words_per_minute=185,
    )
    assert commands == [
        [
            "/usr/bin/espeak-ng",
            "-v",
            "en-us",
            "-s",
            "185",
            "-w",
            str(destination),
            "AArch64 Autopilot measured the Arm target.",
        ]
    ]


def test_macos_narration_keeps_builtin_say(
    media: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        media.shutil,
        "which",
        lambda name: "/usr/bin/say" if name == "say" else "/opt/homebrew/bin/espeak-ng",
    )
    backend = media._select_narration_backend(
        macos_voice="Samantha",
        require_narration=True,
    )
    assert backend.engine == "macOS say"
    assert backend.voice == "Samantha"
    assert backend.audio_suffix == ".aiff"


def test_final_media_refuses_silent_narration_backend(
    media: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media.platform, "system", lambda: "Linux")
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)
    with pytest.raises(media.MediaNotReady, match="requires an offline narration engine"):
        media._select_narration_backend(
            macos_voice="Samantha",
            require_narration=True,
        )
    backend = media._select_narration_backend(
        macos_voice="Samantha",
        require_narration=False,
    )
    assert backend.engine == "silent draft fallback"
    assert backend.voice is None


def test_measured_claims_must_match_report_and_have_sources(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    artifacts = tmp_path / "artifacts"
    figures = artifacts / "figures"
    figures.mkdir(parents=True)
    for name in media.REPORT_FIGURES:
        (figures / name).write_bytes(b"png")
    claim = {
        "claim_id": media.PRIMARY_CLAIM_ID,
        "metric": "Q4_0 mean time-to-first-token reduction",
        "value": 12.5,
        "unit": "%",
        "source_rows": ["generic-incident-001", "kleidiai-incident-001"],
        "confidence_interval": [2.0, 20.0],
        "demonstrated": True,
    }
    report = {"evidence_status": "measured", "claims": [claim]}
    media.validate_evidence(
        report=report,
        claims=[claim],
        artifacts=artifacts,
        draft=False,
    )
    without_sources = {**claim, "source_rows": []}
    with pytest.raises(media.MediaNotReady, match="no source rows"):
        media.validate_evidence(
            report={"evidence_status": "measured", "claims": [without_sources]},
            claims=[without_sources],
            artifacts=artifacts,
            draft=False,
        )


def test_claim_file_must_exactly_match_report(tmp_path: Path, media: ModuleType) -> None:
    claims_path = tmp_path / "claims.json"
    claims_path.write_text("[]", encoding="utf-8")
    with pytest.raises(media.MediaNotReady, match="does not exactly match"):
        media._claims({"claims": [{"value": 1}]}, claims_path)


def test_final_media_requires_a_demonstrated_positive_claim(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    artifacts = tmp_path / "artifacts"
    figures = artifacts / "figures"
    figures.mkdir(parents=True)
    for name in media.REPORT_FIGURES:
        (figures / name).write_bytes(b"png")
    claim = {
        "claim_id": media.PRIMARY_CLAIM_ID,
        "metric": "Q4_0 mean time-to-first-token reduction",
        "value": 1.0,
        "unit": "%",
        "source_rows": ["generic", "kleidiai"],
        "confidence_interval": [-1.0, 3.0],
        "demonstrated": False,
    }
    with pytest.raises(media.MediaNotReady, match="primary mean-TTFT claim"):
        media.validate_evidence(
            report={"evidence_status": "measured", "claims": [claim]},
            claims=[claim],
            artifacts=artifacts,
            draft=False,
        )


def test_positive_secondary_cannot_unlock_final_media(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    artifacts = tmp_path / "artifacts"
    figures = artifacts / "figures"
    figures.mkdir(parents=True)
    for name in media.REPORT_FIGURES:
        (figures / name).write_bytes(b"png")
    primary = {
        "claim_id": media.PRIMARY_CLAIM_ID,
        "metric": "Q4_0 mean time-to-first-token reduction",
        "value": 1.0,
        "unit": "%",
        "source_rows": ["generic", "kleidiai"],
        "confidence_interval": [-1.0, 3.0],
        "demonstrated": False,
    }
    secondary = {
        **primary,
        "claim_id": "fair_q4_0_p95_latency_reduction",
        "metric": "Q4_0 p95 end-to-end latency reduction",
        "confidence_interval": [1.0, 3.0],
        "demonstrated": True,
    }
    with pytest.raises(media.MediaNotReady, match="primary mean-TTFT claim"):
        media.validate_evidence(
            report={"evidence_status": "measured", "claims": [primary, secondary]},
            claims=[primary, secondary],
            artifacts=artifacts,
            draft=False,
        )


def test_final_media_recomputes_claims_from_raw_gate(
    tmp_path: Path,
    media: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    figures = artifacts / "figures"
    figures.mkdir(parents=True)
    for name in media.REPORT_FIGURES:
        (figures / name).write_bytes(b"png")
    generic_run_id = "e" * 32
    kleidiai_run_id = "f" * 32
    claim = {
        "claim_id": media.PRIMARY_CLAIM_ID,
        "metric": "Q4_0 mean time-to-first-token reduction",
        "value": 12.5,
        "unit": "%",
        "source_rows": [generic_run_id, kleidiai_run_id],
        "confidence_interval": [2.0, 20.0],
        "demonstrated": True,
    }
    report = {
        "evidence_status": "measured",
        "claims": [claim],
        "system": {
            "architecture": "aarch64",
            "operating_system": "Linux",
            "kernel": "6.11.0-arm64",
        },
    }
    write_target_receipt(tmp_path, source_run_id=generic_run_id)

    class FakeClaim:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        @classmethod
        def model_validate(cls, payload: dict[str, object]) -> FakeClaim:
            return cls(payload)

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return self.payload

    import a64pilot.report.claims as claim_module
    import a64pilot.report.integrity as integrity_module
    import a64pilot.schemas as schema_module

    monkeypatch.setattr(schema_module, "Claim", FakeClaim)
    monkeypatch.setattr(
        integrity_module, "validate_evidence_bundle", lambda *_args, **_kw: ([], [])
    )
    monkeypatch.setattr(integrity_module, "verify_claim_sources", lambda *_args: [])
    monkeypatch.setattr(claim_module, "verify_claim_held_out_coverage", lambda *_args, **_kw: [])
    monkeypatch.setattr(
        claim_module,
        "generate_claims",
        lambda *_args, **_kw: [FakeClaim(claim)],
    )
    monkeypatch.setattr(claim_module, "has_demonstrated_improvement", lambda *_args: True)

    media.validate_final_evidence_against_raw(
        root=tmp_path,
        report=report,
        claims=[claim],
        draft=False,
    )

    monkeypatch.setattr(claim_module, "generate_claims", lambda *_args, **_kw: [])
    with pytest.raises(media.MediaNotReady, match="do not exactly match"):
        media.validate_final_evidence_against_raw(
            root=tmp_path,
            report=report,
            claims=[claim],
            draft=False,
        )


def test_final_media_requires_official_arm_target_functioning_receipt(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    source_run_id = "b" * 32
    report = {
        "evidence_status": "measured",
        "claims": [{"source_rows": [source_run_id]}],
        "system": {
            "architecture": "aarch64",
            "operating_system": "Linux",
            "kernel": "6.11.0-arm64",
        },
    }
    with pytest.raises(media.MediaNotReady, match="cannot read required JSON"):
        media.load_target_receipt(root=tmp_path, report=report)

    path = write_target_receipt(tmp_path, source_run_id=source_run_id)
    receipt = media.load_target_receipt(root=tmp_path, report=report)
    assert receipt.run_id == "123456789"
    assert receipt.diagnosis == "disk_pressure"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operating_system"] = "Darwin"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(media.MediaNotReady, match="not from an Arm64 Linux target"):
        media.load_target_receipt(root=tmp_path, report=report)


def test_target_receipt_must_be_claim_bound_and_validated(
    tmp_path: Path,
    media: ModuleType,
) -> None:
    source_run_id = "c" * 32
    report = {
        "evidence_status": "measured",
        "claims": [{"source_rows": [source_run_id]}],
        "system": {
            "architecture": "aarch64",
            "operating_system": "Linux",
            "kernel": "6.11.0-arm64",
        },
    }
    write_target_receipt(
        tmp_path,
        source_run_id="d" * 32,
        override={"output_validation": "not_validated"},
    )
    with pytest.raises(media.MediaNotReady, match="did not pass final validation"):
        media.load_target_receipt(root=tmp_path, report=report)
    write_target_receipt(tmp_path, source_run_id="d" * 32)
    with pytest.raises(media.MediaNotReady, match="not tied to a headline claim"):
        media.load_target_receipt(root=tmp_path, report=report)
