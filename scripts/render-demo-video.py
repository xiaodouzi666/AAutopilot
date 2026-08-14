#!/usr/bin/env python3
"""Render an evidence-driven, silent-music demo video for submission.

Final mode fails closed unless the current artifacts contain validated measured
claims. Draft mode is deliberately watermarked on every frame and must not be
published as benchmark evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_TITLE = "AArch64 Autopilot"
REPOSITORY_URL = "github.com/xiaodouzi666/AAutopilot"
DEFAULT_MAX_DURATION = 179.0
THUMBNAIL_NAME = "aarch64-autopilot-thumbnail.png"
REPORT_FIGURES = ("ablation.png", "pareto.png")
TARGET_RECEIPT_NAME = "arm-target-demo-receipt.json"
TARGET_RECEIPT_SCHEMA = "1.0"
ESPEAK_VOICE = "en-us"
PRIMARY_CLAIM_ID = "fair_q4_0_mean_ttft_reduction"


class MediaNotReady(RuntimeError):
    """Raised when final evidence or a required rendering dependency is absent."""


@dataclass(frozen=True, slots=True)
class Slide:
    title: str
    lines: tuple[str, ...]
    narration: str
    image: Path | None = None


@dataclass(frozen=True, slots=True)
class NarrationBackend:
    """One local-only narration implementation selected for the whole render."""

    engine: str
    executable: str | None
    voice: str | None
    audio_suffix: str


@dataclass(frozen=True, slots=True)
class TargetReceipt:
    path: Path
    run_id: str
    run_attempt: int
    commit_sha: str
    runner_name: str
    repository: str
    workflow_ref: str
    workflow_sha: str
    architecture: str
    operating_system: str
    kernel: str
    case_id: str
    candidate_id: str
    backend: str
    model: str
    diagnosis: str
    severity: str
    tool_calls: tuple[str, ...]
    output_validation: str
    source_run_id: str


def load_target_receipt(*, root: Path, report: Mapping[str, Any]) -> TargetReceipt:
    """Load target-functioning proof emitted only by the official Arm CI run."""

    path = root / "artifacts" / "submission" / TARGET_RECEIPT_NAME
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise MediaNotReady("Arm target demo receipt must be a JSON object")
    required = {
        "schema_version",
        "evidence_status",
        "workflow",
        "github_run_id",
        "github_run_attempt",
        "github_sha",
        "runner_name",
        "repository",
        "workflow_ref",
        "workflow_sha",
        "architecture",
        "operating_system",
        "kernel",
        "case_id",
        "candidate_id",
        "backend",
        "model",
        "diagnosis",
        "severity",
        "tool_calls",
        "output_validation",
        "source_run_id",
    }
    missing = required - payload.keys()
    if missing:
        raise MediaNotReady(f"Arm target demo receipt is missing fields: {sorted(missing)}")
    if payload["schema_version"] != TARGET_RECEIPT_SCHEMA:
        raise MediaNotReady("Arm target demo receipt schema version is unsupported")
    if payload["evidence_status"] != "measured" or report.get("evidence_status") != "measured":
        raise MediaNotReady("Arm target demo receipt is not measured evidence")
    if payload["workflow"] != "arm64-evidence":
        raise MediaNotReady("Arm target demo receipt did not come from the official workflow")
    expected_workflow_ref = (
        "xiaodouzi666/AAutopilot/.github/workflows/arm64-evidence.yml@refs/heads/main"
    )
    if (
        payload["repository"] != "xiaodouzi666/AAutopilot"
        or payload["workflow_ref"] != expected_workflow_ref
    ):
        raise MediaNotReady("Arm target demo receipt has the wrong repository or workflow ref")
    if payload["architecture"] != "aarch64" or str(payload["operating_system"]).lower() != "linux":
        raise MediaNotReady("Arm target demo receipt is not from an Arm64 Linux target")
    if payload["output_validation"] != "schema_safety_consistency_passed":
        raise MediaNotReady("Arm target demo receipt response did not pass final validation")
    if payload["backend"] not in {"generic", "kleidiai"}:
        raise MediaNotReady("Arm target demo receipt backend is invalid")
    text_fields = required - {
        "github_run_attempt",
        "tool_calls",
    }
    if any(
        not isinstance(payload[field], str) or not payload[field].strip() for field in text_fields
    ):
        raise MediaNotReady("Arm target demo receipt contains an empty or non-string field")
    if not isinstance(payload["github_run_attempt"], int) or payload["github_run_attempt"] < 1:
        raise MediaNotReady("Arm target demo receipt run attempt is invalid")
    if not re.fullmatch(r"[0-9]+", payload["github_run_id"]):
        raise MediaNotReady("Arm target demo receipt GitHub run ID is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", payload["github_sha"]):
        raise MediaNotReady("Arm target demo receipt commit SHA is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", payload["workflow_sha"]):
        raise MediaNotReady("Arm target demo receipt workflow SHA is invalid")
    if payload["workflow_sha"] != payload["github_sha"]:
        raise MediaNotReady("Arm target demo receipt workflow and checkout SHAs disagree")
    if not re.fullmatch(r"[0-9a-f]{32}", payload["source_run_id"]):
        raise MediaNotReady("Arm target demo receipt source run ID is invalid")
    tools = payload["tool_calls"]
    if (
        not isinstance(tools, list)
        or not tools
        or any(not isinstance(tool, str) or not tool for tool in tools)
    ):
        raise MediaNotReady("Arm target demo receipt tool calls are invalid")
    system = report.get("system")
    if not isinstance(system, Mapping):
        raise MediaNotReady("report has no target system provenance")
    for receipt_key, system_key in (
        ("architecture", "architecture"),
        ("operating_system", "operating_system"),
        ("kernel", "kernel"),
    ):
        if str(payload[receipt_key]).lower() != str(system.get(system_key, "")).lower():
            raise MediaNotReady(
                f"Arm target demo receipt disagrees with report system: {receipt_key}"
            )
    claims = report.get("claims")
    if not isinstance(claims, list) or not any(
        isinstance(claim, Mapping) and payload["source_run_id"] in claim.get("source_rows", [])
        for claim in claims
    ):
        raise MediaNotReady("Arm target demo receipt is not tied to a headline claim source row")
    return TargetReceipt(
        path=path,
        run_id=payload["github_run_id"],
        run_attempt=payload["github_run_attempt"],
        commit_sha=payload["github_sha"],
        runner_name=payload["runner_name"],
        repository=payload["repository"],
        workflow_ref=payload["workflow_ref"],
        workflow_sha=payload["workflow_sha"],
        architecture=payload["architecture"],
        operating_system=payload["operating_system"],
        kernel=payload["kernel"],
        case_id=payload["case_id"],
        candidate_id=payload["candidate_id"],
        backend=payload["backend"],
        model=payload["model"],
        diagnosis=payload["diagnosis"],
        severity=payload["severity"],
        tool_calls=tuple(tools),
        output_validation=payload["output_validation"],
        source_run_id=payload["source_run_id"],
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaNotReady(f"cannot read required JSON artifact: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise MediaNotReady(f"required executable is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-600:]
        raise MediaNotReady(f"media command failed ({command[0]}): {detail}") from exc


def _claims(report: Mapping[str, Any], claims_path: Path) -> list[dict[str, Any]]:
    payload = _read_json(claims_path)
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise MediaNotReady("claims.json must be a JSON list of objects")
    report_claims = report.get("claims")
    if report_claims != payload:
        raise MediaNotReady("claims.json does not exactly match report-data.json")
    return payload


def validate_evidence(
    *,
    report: Mapping[str, Any],
    claims: list[dict[str, Any]],
    artifacts: Path,
    draft: bool,
) -> None:
    pending = report.get("evidence_status") != "measured" or not claims
    if pending and not draft:
        raise MediaNotReady(
            "final video refused: same-machine measured claims are pending; rerun after the "
            "Arm evidence pipeline succeeds, or use --draft for a watermarked preview"
        )
    if not pending:
        required_claim_fields = {
            "claim_id",
            "metric",
            "value",
            "unit",
            "source_rows",
            "confidence_interval",
            "demonstrated",
        }
        for claim in claims:
            missing = required_claim_fields - claim.keys()
            if missing:
                raise MediaNotReady(f"claim is missing required fields: {sorted(missing)}")
            interval = claim["confidence_interval"]
            if not (
                isinstance(interval, list)
                and len(interval) == 2
                and all(isinstance(item, (int, float)) and math.isfinite(item) for item in interval)
            ):
                raise MediaNotReady("claim confidence interval is not finite")
            if not isinstance(claim["value"], (int, float)) or not math.isfinite(claim["value"]):
                raise MediaNotReady("claim value is not finite")
            rows = claim["source_rows"]
            if not isinstance(rows, list) or not rows:
                raise MediaNotReady("measured claim has no source rows")
        primary = next(
            (claim for claim in claims if claim["claim_id"] == PRIMARY_CLAIM_ID),
            None,
        )
        if primary is None or claims[0]["claim_id"] != PRIMARY_CLAIM_ID:
            raise MediaNotReady(
                "final video refused: the preregistered primary mean-TTFT claim is missing "
                "or is not first"
            )
        if not (
            primary["demonstrated"] is True
            and float(primary["value"]) > 0
            and float(primary["confidence_interval"][0]) > 0
        ):
            raise MediaNotReady(
                "final video refused: the preregistered primary mean-TTFT claim does not "
                "have a positive confidence interval above zero"
            )
    for name in REPORT_FIGURES:
        figure = artifacts / "figures" / name
        if not figure.is_file():
            raise MediaNotReady(f"required report figure is missing: {figure}")


def validate_final_evidence_against_raw(
    *,
    root: Path,
    report: Mapping[str, Any],
    claims: list[dict[str, Any]],
    draft: bool,
) -> None:
    """Reuse the submission evidence gate before marking media publishable."""

    validate_evidence(
        report=report,
        claims=claims,
        artifacts=root / "artifacts",
        draft=draft,
    )
    if draft:
        return
    source_root = root / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    try:
        from a64pilot.report.claims import (
            generate_claims,
            has_demonstrated_improvement,
            verify_claim_held_out_coverage,
        )
        from a64pilot.report.integrity import validate_evidence_bundle, verify_claim_sources
        from a64pilot.schemas import Claim

        records, errors = validate_evidence_bundle(root / "artifacts", require_records=True)
        typed_claims = [Claim.model_validate(item) for item in claims]
        errors.extend(verify_claim_sources(typed_claims, records))
        errors.extend(
            verify_claim_held_out_coverage(
                typed_claims,
                records,
                split_path=root / "demo" / "split.json",
            )
        )
        expected = generate_claims(records, split_path=root / "demo" / "split.json")
        if [item.model_dump(mode="json") for item in typed_claims] != [
            item.model_dump(mode="json") for item in expected
        ]:
            errors.append("media claims do not exactly match claims recomputed from raw evidence")
        if not has_demonstrated_improvement(typed_claims):
            errors.append(
                "the preregistered primary mean-TTFT claim does not have a positive "
                "confidence interval above zero"
            )
    except Exception as exc:
        if isinstance(exc, MediaNotReady):
            raise
        raise MediaNotReady(
            f"strict final evidence validation could not run: {type(exc).__name__}: {exc}"
        ) from exc
    if errors:
        raise MediaNotReady("strict final evidence validation failed: " + "; ".join(errors))
    load_target_receipt(root=root, report=report)


def _claim_narration(claim: Mapping[str, Any]) -> str:
    metric = str(claim["metric"])
    value = float(claim["value"])
    unit = str(claim["unit"])
    low, high = (float(item) for item in claim["confidence_interval"])
    if low > 0:
        interval = "The interval excludes zero on the positive side."
    elif high < 0:
        interval = "The interval excludes zero on the negative side."
    else:
        interval = "The interval crosses zero."
    primary = claim["claim_id"] == PRIMARY_CLAIM_ID
    decision = (
        "The preregistered final gate passes."
        if primary and claim["demonstrated"]
        else (
            "The final gate does not pass."
            if primary
            else "This transparent secondary outcome cannot unlock publication."
        )
    )
    role = "primary" if primary else "secondary"
    return (
        f"The {role} {metric} is {value:.2f} {unit}, with a paired 95 percent interval of "
        f"{low:.2f} to {high:.2f} {unit}. {interval} {decision}"
    )


def build_slides(
    *,
    root: Path,
    report: Mapping[str, Any],
    claims: list[dict[str, Any]],
    draft: bool,
) -> list[Slide]:
    assets = root / "assets" / "submission"
    artifacts = root / "artifacts"
    thumbnail = assets / THUMBNAIL_NAME
    if not thumbnail.is_file():
        raise MediaNotReady(f"thumbnail is missing: {thumbnail}")
    slides = [
        Slide(
            "AArch64 Autopilot",
            ("QUALITY-GATED AI ON ARM CPUs", "CPU-only • evidence-first • open source"),
            "Running an AI agent on Arm is easy. Proving that it is optimized without "
            "sacrificing quality is harder. AArch64 Autopilot makes one Arm CPU its own "
            "benchmark lab, optimizer, and validated endpoint.",
            thumbnail,
        ),
        Slide(
            "One fair pipeline",
            (
                "same llama.cpp commit and Qwen2.5 1.5B Q4_0 model",
                "generic CPU ↔ verified KleidiAI Q4 kernel",
                "inventory: 197 Q4_0 tensors + one reviewed Q6_K output.weight",
                "split v2: 20 cases frozen from 36 never-executed candidates",
            ),
            "The fair comparison serves one Qwen model through same-commit generic and KleidiAI "
            "builds, proves CPU-only execution and the KleidiAI Q4 marker, and rejects inventory "
            "drift. After run six exposed and retired v1, split v2 froze twenty cases from "
            "thirty-six never-executed candidates using only category and case ID. All decisions "
            "freeze before final evaluation.",
        ),
    ]
    if draft:
        slides.append(
            Slide(
                "Arm measurement pending",
                (
                    "No performance number is published from fixture or macOS output",
                    "Final render unlocks only after validated same-machine Arm evidence",
                ),
                "This is a draft preview. Same-machine Arm Linux measurement is still pending, "
                "so no benchmark number appears and this video must not be submitted as evidence.",
                artifacts / "figures" / "ablation.png",
            )
        )
    else:
        receipt = load_target_receipt(root=root, report=report)
        slides.append(
            Slide(
                "Functioning on the Arm target",
                (
                    f"official GitHub Arm runner • run {receipt.run_id}",
                    f"{receipt.repository} • verified workflow revision",
                    f"{receipt.operating_system} {receipt.architecture} • kernel {receipt.kernel}",
                    f"real {receipt.backend} response • {receipt.case_id}",
                    f"diagnosis: {receipt.diagnosis} • severity: {receipt.severity}",
                    f"validated tools: {', '.join(receipt.tool_calls)}",
                ),
                "This is the project functioning on the official GitHub hosted Arm64 Linux "
                f"runner, run {receipt.run_id}. A real {receipt.backend} model request for "
                f"{receipt.case_id} returned {receipt.diagnosis}, invoked only validated "
                "read-only tools, and passed the same schema, safety, and consistency gate "
                "used by the deployed endpoint on the split-v2 final holdout.",
            )
        )
        for index, claim in enumerate(claims):
            low, high = claim["confidence_interval"]
            role = "PRIMARY" if claim["claim_id"] == PRIMARY_CLAIM_ID else "SECONDARY"
            slides.append(
                Slide(
                    str(claim["metric"]),
                    (
                        f"{role} • 20 paired cases • {len(claim['source_rows'])} formal source rows",
                        f"{float(claim['value']):.2f}{claim['unit']}",
                        f"paired 95% interval: {float(low):.2f} to {float(high):.2f}{claim['unit']}",
                        (
                            "only this outcome can unlock final publication"
                            if role == "PRIMARY"
                            else "transparent secondary; never unlocks publication"
                        ),
                    ),
                    _claim_narration(claim),
                    artifacts / "figures" / REPORT_FIGURES[index % len(REPORT_FIGURES)],
                )
            )
    slides.extend(
        [
            Slide(
                "Validated strong-only API",
                (
                    "OpenAI-compatible localhost endpoint",
                    "shared strict triage schema",
                    "tool-policy + safety + consistency validation",
                    "invalid upstream output → fail-closed HTTP 502",
                ),
                "The submitted endpoint serves the measured strong-only profile. It always "
                "uses the benchmark triage schema and validates read-only tool arguments, "
                "safety, and consistency. Invalid upstream output is never forwarded.",
                artifacts / "figures" / "pareto.png",
            ),
            Slide(
                "Inspect every claim",
                (
                    "commands • model and binary hashes • run IDs",
                    "sanitized formal rows • paired formulas • confidence intervals",
                    "full redacted raw capture • attested release bundle",
                    REPOSITORY_URL,
                ),
                "The repository carries the sanitized formal rows and claim index. The full "
                "redacted raw capture is in the attested release bundle, while the public GitHub "
                "Actions run provides authoritative execution provenance.",
            ),
            Slide(
                "Measured. Quality-gated. GPU-free.",
                (REPOSITORY_URL,),
                "AArch64 Autopilot makes Arm migration measurable: which configuration wins on "
                "this target, what tradeoff it makes, and whether it remains safe.",
                thumbnail,
            ),
        ]
    )
    return slides


def _escape_markup(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_font() -> Path:
    configured = os.getenv("A64PILOT_VIDEO_FONT")
    candidates = [
        Path(configured) if configured else None,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise MediaNotReady(
        "no reviewed video font is available; install DejaVu Sans or set A64PILOT_VIDEO_FONT"
    )


def _render_slide(
    slide: Slide,
    destination: Path,
    *,
    magick: str,
    font: Path,
    draft: bool,
) -> None:
    command = [magick]
    if slide.image:
        command += [
            str(slide.image),
            "-auto-orient",
            "-resize",
            "1920x1080^",
            "-gravity",
            "center",
            "-extent",
            "1920x1080",
            "-fill",
            "#06101de6",
            "-colorize",
            "32%",
        ]
    else:
        command += ["-size", "1920x1080", "gradient:#050a16-#102544"]
    command += [
        "-font",
        str(font),
        "-gravity",
        "northwest",
        "-fill",
        "#38e6a8",
        "-pointsize",
        "24",
        "-annotate",
        "+110+90",
        "CPU-ONLY ARM AI OPTIMIZATION",
        "-fill",
        "white",
        "-pointsize",
        "64",
        "-annotate",
        "+110+155",
        _escape_markup(slide.title),
        "-fill",
        "#d7e2f4",
        "-pointsize",
        "31",
        "-interline-spacing",
        "16",
        "-annotate",
        "+115+300",
        _escape_markup("\n".join(f"• {line}" for line in slide.lines)),
        "-gravity",
        "southwest",
        "-fill",
        "#93a8c4",
        "-pointsize",
        "24",
        "-annotate",
        "+110+70",
        REPOSITORY_URL,
    ]
    if draft:
        command += [
            "-gravity",
            "center",
            "-fill",
            "#ffcc00cc",
            "-stroke",
            "#190d00",
            "-strokewidth",
            "2",
            "-pointsize",
            "78",
            "-annotate",
            "+0+0",
            "DRAFT — MEASUREMENT PENDING",
        ]
    command += [str(destination)]
    _run(command)


def _write_silence(path: Path, duration: float) -> None:
    frames = max(1, math.ceil(duration * 44_100))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44_100)
        handle.writeframes(b"\0\0" * frames)


def _select_narration_backend(*, macos_voice: str, require_narration: bool) -> NarrationBackend:
    """Choose an offline English narrator without contacting a network service."""

    system = platform.system()
    if system == "Linux" and (executable := shutil.which("espeak-ng")):
        return NarrationBackend(
            engine="espeak-ng offline English",
            executable=executable,
            voice=ESPEAK_VOICE,
            audio_suffix=".wav",
        )
    if system == "Darwin" and (executable := shutil.which("say")):
        return NarrationBackend(
            engine="macOS say",
            executable=executable,
            voice=macos_voice,
            audio_suffix=".aiff",
        )
    if executable := shutil.which("espeak-ng"):
        return NarrationBackend(
            engine="espeak-ng offline English",
            executable=executable,
            voice=ESPEAK_VOICE,
            audio_suffix=".wav",
        )
    if require_narration:
        raise MediaNotReady(
            "final video requires an offline narration engine: install espeak-ng on Linux "
            "or use macOS with the built-in say command"
        )
    return NarrationBackend(
        engine="silent draft fallback",
        executable=None,
        voice=None,
        audio_suffix=".wav",
    )


def _narrate(
    text: str,
    destination: Path,
    *,
    backend: NarrationBackend,
    words_per_minute: int,
) -> None:
    if backend.engine == "macOS say":
        assert backend.executable is not None and backend.voice is not None
        _run(
            [
                backend.executable,
                "-v",
                backend.voice,
                "-r",
                str(words_per_minute),
                "-o",
                str(destination),
                text,
            ]
        )
        return
    if backend.engine == "espeak-ng offline English":
        assert backend.executable is not None and backend.voice is not None
        _run(
            [
                backend.executable,
                "-v",
                backend.voice,
                "-s",
                str(words_per_minute),
                "-w",
                str(destination),
                text,
            ]
        )
        return
    # Draft previews remain portable when neither local narrator is installed.
    word_count = len(re.findall(r"\b[\w'-]+\b", text))
    _write_silence(destination, max(4.0, word_count / words_per_minute * 60.0))


def _duration(path: Path, *, ffprobe: str) -> float:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise MediaNotReady(f"cannot determine duration: {path}") from exc


def render_video(
    *,
    root: Path,
    output: Path,
    draft: bool,
    max_duration: float,
    voice: str,
    words_per_minute: int,
) -> dict[str, Any]:
    artifacts = root / "artifacts"
    report = _read_json(artifacts / "report-data.json")
    if not isinstance(report, dict):
        raise MediaNotReady("report-data.json must be an object")
    claims_path = artifacts / "claims.json"
    claims = _claims(report, claims_path)
    validate_final_evidence_against_raw(
        root=root,
        report=report,
        claims=claims,
        draft=draft,
    )
    slides = build_slides(root=root, report=report, claims=claims, draft=draft)
    target_receipt = None if draft else load_target_receipt(root=root, report=report)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    magick = shutil.which("magick") or shutil.which("convert")
    if not ffmpeg or not ffprobe or not magick:
        raise MediaNotReady("ffmpeg, ffprobe, and ImageMagick are required")
    font = _find_font()
    narration_backend = _select_narration_backend(
        macos_voice=voice,
        require_narration=not draft,
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="a64pilot-video-") as temporary:
        work = Path(temporary)
        clips: list[Path] = []
        for index, slide in enumerate(slides):
            frame = work / f"slide-{index:02d}.png"
            audio = work / f"slide-{index:02d}{narration_backend.audio_suffix}"
            clip = work / f"slide-{index:02d}.mp4"
            _render_slide(slide, frame, magick=magick, font=font, draft=draft)
            _narrate(
                slide.narration,
                audio,
                backend=narration_backend,
                words_per_minute=words_per_minute,
            )
            audio_duration = _duration(audio, ffprobe=ffprobe)
            slide_duration = max(6.0, audio_duration + 0.7)
            _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(frame),
                    "-i",
                    str(audio),
                    "-t",
                    f"{slide_duration:.3f}",
                    "-vf",
                    "format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(clip),
                ]
            )
            clips.append(clip)
        concat = work / "concat.txt"
        concat.write_text("".join(f"file '{path}'\n" for path in clips), encoding="utf-8")
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )

    duration = _duration(output, ffprobe=ffprobe)
    if duration >= max_duration:
        output.unlink(missing_ok=True)
        raise MediaNotReady(
            f"rendered duration {duration:.2f}s exceeds strict limit {max_duration:.2f}s"
        )
    sources = [
        claims_path,
        artifacts / "report-data.json",
        root / "assets" / "submission" / THUMBNAIL_NAME,
        root / "scripts" / "render-demo-video.py",
        *(artifacts / "figures" / name for name in REPORT_FIGURES),
    ]
    if target_receipt is not None:
        sources.append(target_receipt.path)
    manifest = {
        "schema_version": "1.0",
        "mode": "draft_measurement_pending" if draft else "final_measured",
        "publishable": not draft,
        "music": "none",
        "narration": narration_backend.engine,
        "narration_offline": True,
        "voice": narration_backend.voice,
        "words_per_minute": words_per_minute,
        "font": font.name,
        "duration_seconds": round(duration, 3),
        "max_duration_seconds": max_duration,
        "claims": claims,
        "target_device_footage": (
            None
            if target_receipt is None
            else {
                "kind": "official_arm_runner_terminal_receipt",
                "github_run_id": target_receipt.run_id,
                "github_run_attempt": target_receipt.run_attempt,
                "github_sha": target_receipt.commit_sha,
                "github_run_url": (
                    f"https://github.com/{target_receipt.repository}/actions/runs/"
                    f"{target_receipt.run_id}"
                ),
                "source_run_id": target_receipt.source_run_id,
            }
        ),
        "sources": {str(path.relative_to(root)): _sha256(path) for path in sources},
        "output": str(output.relative_to(root)),
        "output_sha256": _sha256(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--draft",
        action="store_true",
        help="allow pending evidence but watermark every frame and mark output non-publishable",
    )
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    parser.add_argument("--voice", default="Samantha")
    parser.add_argument("--words-per-minute", type=int, default=185)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate inputs and final/draft policy without invoking media tools",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if args.max_duration <= 0 or args.max_duration >= 180:
        raise SystemExit("--max-duration must be greater than zero and strictly below 180")
    if args.words_per_minute < 120 or args.words_per_minute > 240:
        raise SystemExit("--words-per-minute must be between 120 and 240")
    try:
        report = _read_json(root / "artifacts" / "report-data.json")
        if not isinstance(report, dict):
            raise MediaNotReady("report-data.json must be an object")
        claims = _claims(report, root / "artifacts" / "claims.json")
        validate_final_evidence_against_raw(
            root=root,
            report=report,
            claims=claims,
            draft=args.draft,
        )
        if args.validate_only:
            print(
                "media inputs valid for "
                + ("watermarked draft" if args.draft else "final measured render")
            )
            return 0
        default_name = "a64pilot-demo-DRAFT.mp4" if args.draft else "a64pilot-demo-final.mp4"
        output = (args.output or root / "artifacts" / "submission" / default_name).resolve()
        if root not in output.parents:
            raise MediaNotReady("output must remain inside the project directory")
        manifest = render_video(
            root=root,
            output=output,
            draft=args.draft,
            max_duration=args.max_duration,
            voice=args.voice,
            words_per_minute=args.words_per_minute,
        )
    except MediaNotReady as exc:
        print(f"video render refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
