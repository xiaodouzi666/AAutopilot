"""Stable command-line surface for AArch64 Autopilot."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
import yaml

from a64pilot import __version__
from a64pilot.provenance import write_json

app = typer.Typer(
    name="a64pilot",
    help="Quality-gated, evidence-producing AI inference optimization for Arm64 CPUs.",
    no_args_is_help=False,
    invoke_without_command=True,
)
models_app = typer.Typer(help="List, download, and verify official GGUF model artifacts.")
benchmark_app = typer.Typer(help="Validate quality data or run real Arm64 benchmarks.")
app.add_typer(models_app, name="models")
app.add_typer(benchmark_app, name="benchmark")


def _print_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


@app.callback()
def callback(
    context: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Print the package version.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON to stdout.")] = False,
    artifacts_dir: Annotated[Path, typer.Option(help="Public artifact directory.")] = Path(
        "artifacts"
    ),
) -> None:
    """Inspect architecture, Arm features, topology, memory, and toolchain."""

    from a64pilot.hardware.detect import collect_system_info, render_doctor_markdown

    info = collect_system_info()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    payload = info.to_schema().model_dump(mode="json")
    write_json(artifacts_dir / "system-info.json", payload)
    (artifacts_dir / "system-info.md").write_text(render_doctor_markdown(info), encoding="utf-8")
    if json_output:
        _print_json(payload)
    else:
        typer.echo(render_doctor_markdown(info), nl=False)
        typer.echo(f"Saved {artifacts_dir / 'system-info.json'}")


@app.command("build")
def build_command(
    variant: Annotated[str, typer.Argument(help="generic, kleidiai, or all")] = "all",
    dry_run: Annotated[
        bool, typer.Option(help="Print plans without cloning or compiling.")
    ] = False,
    jobs: Annotated[int | None, typer.Option(help="Parallel native build jobs.")] = None,
) -> None:
    """Build fair generic and KleidiAI llama.cpp variants from the source lock."""

    from a64pilot.build.cmake import (
        BuildVariant,
        assert_fair_build_pair,
        collect_build_artifact,
        create_build_plan,
        execute_build,
        write_build_manifest,
    )
    from a64pilot.build.llama_source import ensure_source, read_source_lock

    if variant not in {"generic", "kleidiai", "all"}:
        raise typer.BadParameter("variant must be generic, kleidiai, or all")
    lock = read_source_lock()
    checkout = ensure_source(lock=lock, dry_run=dry_run)
    variants = (
        [BuildVariant.GENERIC, BuildVariant.KLEIDIAI]
        if variant == "all"
        else [BuildVariant(variant)]
    )
    plans = [create_build_plan(item, source_commit=checkout.commit, jobs=jobs) for item in variants]
    if len(plans) == 2:
        assert_fair_build_pair(plans[0], plans[1])
    if dry_run:
        _print_json({"source": checkout.to_dict(), "plans": [plan.to_dict() for plan in plans]})
        return
    artifacts = []
    for plan in plans:
        typer.echo(f"Building {plan.variant.value} from {plan.source_commit[:12]}…")
        execute_build(plan)
        artifacts.append(collect_build_artifact(plan))
    write_build_manifest(artifacts)
    typer.echo("Saved artifacts/build-manifest.json")


@models_app.command("list")
def models_list() -> None:
    """List reviewed official Qwen GGUF candidates."""

    from a64pilot.models.registry import default_registry

    _print_json([spec.to_dict() for spec in default_registry()])


@models_app.command("download")
def models_download(
    minimum: Annotated[
        bool,
        typer.Option(
            "--minimum/--all",
            help="Download KleidiAI-compatible weak/strong Q4_0 plus strong Q8_0 only.",
        ),
    ] = True,
    dry_run: Annotated[bool, typer.Option(help="Plan without network or file writes.")] = False,
    models_dir: Annotated[Path, typer.Option(help="Ignored local model directory.")] = Path(
        "models"
    ),
) -> None:
    """Resolve immutable official model revisions, download, hash, and manifest them."""

    from a64pilot.models.download import download_models, write_manifest
    from a64pilot.models.registry import default_registry

    specs = default_registry()
    if minimum:
        keep = {"weak-q4-0", "strong-q4-0", "strong-q8-0"}
        specs = tuple(spec for spec in specs if spec.model_id in keep)
    manifest = download_models(specs, output_dir=models_dir, dry_run=dry_run)
    if dry_run:
        _print_json(manifest.to_dict())
        return
    write_manifest(manifest)
    _print_json(manifest.to_dict())


@models_app.command("verify")
def models_verify(
    manifest: Annotated[Path, typer.Option(help="Generated model manifest.")] = Path(
        "artifacts/model-manifest.json"
    ),
    models_dir: Annotated[Path, typer.Option(help="Local model directory.")] = Path("models"),
) -> None:
    """Rehash all complete model manifest rows."""

    from a64pilot.models.checksum import verify_manifest

    result = verify_manifest(manifest, models_dir=models_dir)
    _print_json(result.to_dict())
    if not result.valid:
        raise typer.Exit(2)


@benchmark_app.command("quality")
def benchmark_quality(
    validate_only: Annotated[
        bool, typer.Option("--validate-only", help="Validate cases, split, and leakage guards.")
    ] = True,
    calibrate: Annotated[
        bool,
        typer.Option("--calibrate", help="Run all 40 measured weak/strong calibration cases."),
    ] = False,
    held_out: Annotated[
        bool,
        typer.Option("--held-out", help="Evaluate all 20 held-out cases exactly once."),
    ] = False,
    frozen: Annotated[
        bool,
        typer.Option("--frozen", help="Require and replay the immutable calibrated policy."),
    ] = False,
    profile: Annotated[
        Path,
        typer.Option(help="Measured A3 strong-only profile supplying frozen runtime settings."),
    ] = Path("artifacts/optimized-profile.yaml"),
    policy: Annotated[Path, typer.Option(help="Immutable A4 calibration policy artifact.")] = Path(
        "artifacts/a4-frozen-policy.json"
    ),
    results: Annotated[
        Path, typer.Option(help="Combined calibration and held-out quality artifact.")
    ] = Path("artifacts/quality-results.json"),
) -> None:
    """Validate data, calibrate A4 on 40 cases, or replay it on 20 held-out cases."""

    from a64pilot.benchmark.quality import (
        load_cases,
        load_split,
        routing_view,
        stable_file_sha256,
        validate_dataset,
    )

    if calibrate and held_out:
        raise typer.BadParameter("choose --calibrate or --held-out, not both")
    if held_out != frozen:
        raise typer.BadParameter("held-out evaluation requires both --held-out and --frozen")
    if frozen and not held_out:
        raise typer.BadParameter("--frozen is only valid with --held-out")

    cases_path = Path("demo/cases.jsonl")
    split_path = Path("demo/split.json")
    cases = load_cases(cases_path)
    split = load_split(split_path)
    validate_dataset(cases, split)
    # Building every routing view proves labels are stripped at the boundary.
    for case in cases:
        routing_view(case)
    dataset_result = {
        "valid": True,
        "cases": len(cases),
        "calibration": len(split.calibration),
        "held_out": len(split.test),
        "cases_sha256": stable_file_sha256(cases_path),
        "split_sha256": stable_file_sha256(split_path),
        "mode": "validation-only" if validate_only else "validation-only-no-model-output",
    }
    Path("artifacts").mkdir(exist_ok=True)
    write_json("artifacts/quality-dataset.json", dataset_result)
    if not calibrate and not held_out:
        _print_json(dataset_result)
        return

    from a64pilot.benchmark.cascade import (
        CascadeRuntimePlan,
        CascadeWorkflowError,
        collect_real_component_outputs,
        evaluate_held_out,
        freeze_calibration,
        load_frozen_calibration,
        preflight_held_out_evaluation,
    )

    try:
        if calibrate:
            from a64pilot.benchmark.quality import QualityGateConfig as CascadeQualityGateConfig
            from a64pilot.runtime.deployment import load_measured_profile
            from a64pilot.settings import load_settings

            measured = load_measured_profile(profile)
            if measured.backend != "kleidiai" or measured.model_role != "strong":
                raise CascadeWorkflowError(
                    "A4 calibration requires a measured KleidiAI A3 strong-only profile"
                )
            runtime = CascadeRuntimePlan(
                binary=measured.binary,
                cmake_cache=measured.cmake_cache,
                weak_model=_model_path("weak-q4-0"),
                strong_model=measured.model,
                threads=measured.threads,
                batch=measured.batch,
                ubatch=measured.ubatch,
                parallel=measured.parallel,
                context=measured.context,
                affinity=measured.affinity,
                weak_quantization="Q4_0",
                strong_quantization=measured.quantization,
            )
            evidence = collect_real_component_outputs(
                runtime,
                split="calibration",
                phase="calibration",
                include_weak=True,
            )
            configured_gate = load_settings().quality_gate
            payload = freeze_calibration(
                runtime,
                evidence,
                policy_path=policy,
                gate_config=CascadeQualityGateConfig(**configured_gate.model_dump()),
            )
            _print_json(
                {
                    "mode": "calibration",
                    "case_count": 40,
                    "freeze_id": payload["freeze_id"],
                    "policy": payload["policy"],
                    "policy_path": str(policy),
                }
            )
            return

        existing, status_recovered = preflight_held_out_evaluation(
            policy_path=policy,
            cases_path=cases_path,
            split_path=split_path,
            results_path=results,
        )
        if existing is not None:
            _print_json(
                {
                    "mode": "held-out-frozen-already-recorded",
                    "case_count": 20,
                    "freeze_id": existing["freeze_id"],
                    "a4_admitted_by_quality_gate": existing["a4_admitted_by_quality_gate"],
                    "shipping_profile": existing["shipping_profile"],
                    "route_counts": existing["held_out"]["route_counts"],
                    "route_shares": existing["held_out"]["route_shares"],
                    "escalation_rate": existing["held_out"]["escalation_rate"],
                    "results_path": str(results),
                    "status_recovered": status_recovered,
                }
            )
            return
        frozen_payload, routing_policy, runtime, _ = load_frozen_calibration(policy)
        evidence = collect_real_component_outputs(
            runtime,
            split="test",
            phase="held-out",
            include_weak=not routing_policy.fallback_strong_only,
        )
        payload = evaluate_held_out(evidence, policy_path=policy, results_path=results)
        _print_json(
            {
                "mode": "held-out-frozen",
                "case_count": 20,
                "freeze_id": frozen_payload["freeze_id"],
                "a4_admitted_by_quality_gate": payload["a4_admitted_by_quality_gate"],
                "shipping_profile": payload["shipping_profile"],
                "route_counts": payload["held_out"]["route_counts"],
                "route_shares": payload["held_out"]["route_shares"],
                "escalation_rate": payload["held_out"]["escalation_rate"],
                "results_path": str(results),
            }
        )
    except (CascadeWorkflowError, ValueError, OSError) as exc:
        typer.echo(f"A4 quality workflow failed: {exc}", err=True)
        raise typer.Exit(2) from exc


def _default_threads() -> int:
    from a64pilot.hardware.detect import collect_system_info

    info = collect_system_info()
    return max(1, info.topology.physical_cores or info.topology.logical_cpus)


def _model_path(model_id: str, models_dir: Path = Path("models")) -> Path:
    from a64pilot.models.registry import get_model

    return models_dir / get_model(model_id).expected_filename


def _fair_run_split(limit: int | None) -> str:
    """Keep partial smoke rows out of the formal held-out evidence set."""

    return "test" if limit is None else "micro"


def _all_run_limit(stage: str, *, quick: bool) -> int | None:
    """Never truncate the mandatory complete held-out A1/A2 comparison."""

    if stage.lower() in {"a1", "a2"}:
        return None
    return 10 if quick else None


@benchmark_app.command("fair")
def benchmark_fair(
    generic_binary: Annotated[Path, typer.Option()] = Path("build/llama-generic/bin/llama-server"),
    kleidiai_binary: Annotated[Path, typer.Option()] = Path(
        "build/llama-kleidiai/bin/llama-server"
    ),
    model: Annotated[Path | None, typer.Option(help="Strong Q4_0 GGUF.")] = None,
    threads: Annotated[int | None, typer.Option()] = None,
    repetitions: Annotated[int, typer.Option(min=1, max=10)] = 1,
    limit: Annotated[int | None, typer.Option(min=1, max=20)] = None,
) -> None:
    """Run the primary same-machine generic-Q4_0/KleidiAI-Q4_0 service pair."""

    from a64pilot.benchmark.runner import (
        RealServiceBenchmark,
        RuntimeCandidate,
        run_candidate_sync,
    )
    from a64pilot.report.render import render_report

    strong_q4 = model or _model_path("strong-q4-0")
    selected_threads = threads or _default_threads()
    common = dict(
        model=strong_q4,
        model_role="strong",
        quantization="Q4_0",
        threads=selected_threads,
        batch=256,
        ubatch=128,
        parallel=1,
        context=2048,
    )
    benchmark = RealServiceBenchmark()
    run_split = _fair_run_split(limit)
    candidates = [
        RuntimeCandidate(
            candidate_id="a1-generic-q4-0",
            stage="a1",
            backend="generic",
            binary=generic_binary,
            cmake_cache=generic_binary.parent.parent / "CMakeCache.txt",
            **common,
        ),
        RuntimeCandidate(
            candidate_id="a2-kleidiai-q4-0",
            stage="a2",
            backend="kleidiai",
            binary=kleidiai_binary,
            cmake_cache=kleidiai_binary.parent.parent / "CMakeCache.txt",
            **common,
        ),
    ]
    for candidate in candidates:
        typer.echo(f"Running {candidate.candidate_id}…")
        run_candidate_sync(
            benchmark,
            candidate,
            split=run_split,
            repetitions=repetitions,
            limit=limit,
            warmups=1,
        )
    outputs = render_report()
    typer.echo(f"Rendered {outputs['html']}")


@benchmark_app.command("tune")
def benchmark_tune(
    kleidiai_binary: Annotated[Path, typer.Option()] = Path(
        "build/llama-kleidiai/bin/llama-server"
    ),
    model: Annotated[Path | None, typer.Option(help="Strong Q4_0 GGUF.")] = None,
    max_candidates: Annotated[int, typer.Option(min=2, max=32)] = 8,
    calibration_cases: Annotated[int, typer.Option(min=1, max=40)] = 4,
    finalists: Annotated[int, typer.Option(min=1, max=4)] = 2,
    repetitions: Annotated[int, typer.Option(min=1, max=3)] = 1,
    max_minutes: Annotated[float, typer.Option(min=1, max=120)] = 45.0,
    quick: Annotated[bool, typer.Option("--quick/--full")] = True,
) -> None:
    """Calibrate a topology-derived KleidiAI matrix, then validate A3 finalists."""

    from a64pilot.benchmark.runner import RealServiceBenchmark
    from a64pilot.hardware.detect import collect_system_info
    from a64pilot.optimize.tune import TuneSearchError, run_bounded_tune
    from a64pilot.report.render import render_report
    from a64pilot.settings import load_settings

    try:
        plan = run_bounded_tune(
            benchmark=RealServiceBenchmark(),
            system_info=collect_system_info(),
            binary=kleidiai_binary,
            model=model or _model_path("strong-q4-0"),
            gate=load_settings().quality_gate,
            max_candidates=max_candidates,
            calibration_cases=calibration_cases,
            finalists=finalists,
            repetitions=repetitions,
            max_minutes=max_minutes,
            quick=quick,
        )
    except TuneSearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    render_report()
    _print_json(
        {
            "status": plan["status"],
            "search_plan": "artifacts/search-plan.json",
            "selected_a3_candidate_id": plan["selected_a3_candidate_id"],
        }
    )


@benchmark_app.command("all")
def benchmark_all(
    repetitions: Annotated[int, typer.Option(min=1, max=10)] = 1,
    quick: Annotated[
        bool,
        typer.Option(
            help=(
                "Use six topology-derived calibration candidates and two full held-out A3 "
                "finalists; A1/A2 always use all held-out cases."
            )
        ),
    ] = False,
) -> None:
    """Run A0–A3 where inputs exist, prioritizing the mandatory fair A1/A2 pair."""

    from a64pilot.benchmark.runner import RealServiceBenchmark, RuntimeCandidate, run_candidate_sync
    from a64pilot.hardware.detect import collect_system_info
    from a64pilot.optimize.tune import TuneSearchError, run_bounded_tune
    from a64pilot.report.render import render_report
    from a64pilot.settings import load_settings

    generic = Path("build/llama-generic/bin/llama-server")
    kleidiai = Path("build/llama-kleidiai/bin/llama-server")
    strong_q4 = _model_path("strong-q4-0")
    strong_q8 = _model_path("strong-q8-0")
    threads = _default_threads()
    benchmark = RealServiceBenchmark()
    common = dict(
        model_role="strong", threads=threads, batch=256, ubatch=128, parallel=1, context=2048
    )
    candidates = []
    if strong_q8.is_file():
        candidates.append(
            RuntimeCandidate(
                "a0-generic-q8",
                "a0",
                "generic",
                generic,
                generic.parent.parent / "CMakeCache.txt",
                strong_q8,
                quantization="Q8_0",
                **common,
            )
        )
    candidates.extend(
        [
            RuntimeCandidate(
                "a1-generic-q4-0",
                "a1",
                "generic",
                generic,
                generic.parent.parent / "CMakeCache.txt",
                strong_q4,
                quantization="Q4_0",
                **common,
            ),
            RuntimeCandidate(
                "a2-kleidiai-q4-0",
                "a2",
                "kleidiai",
                kleidiai,
                kleidiai.parent.parent / "CMakeCache.txt",
                strong_q4,
                quantization="Q4_0",
                **common,
            ),
        ]
    )
    for candidate in candidates:
        typer.echo(f"Running {candidate.candidate_id}…")
        run_candidate_sync(
            benchmark,
            candidate,
            split="test",
            repetitions=repetitions,
            limit=_all_run_limit(candidate.stage, quick=quick),
            warmups=1,
        )
    try:
        search_plan = run_bounded_tune(
            benchmark=benchmark,
            system_info=collect_system_info(),
            binary=kleidiai,
            model=strong_q4,
            gate=load_settings().quality_gate,
            max_candidates=6 if quick else 12,
            calibration_cases=4 if quick else 8,
            finalists=2 if quick else 3,
            repetitions=repetitions,
            max_minutes=45.0 if quick else 90.0,
            quick=quick,
        )
    except TuneSearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    cascade_status = Path("artifacts/cascade-status.json")
    if not cascade_status.exists():
        write_json(
            cascade_status,
            {
                "status": "not-run",
                "shipping_fallback": "best measured strong-only profile",
                "reason": (
                    "A4 is admitted only after weak/strong calibration and held-out quality pass"
                ),
            },
        )
    render_report()
    typer.echo(
        "A0–A2 and bounded A3 search completed; "
        f"A3={search_plan['selected_a3_candidate_id'] or 'quality-gate fallback'}, "
        "A4 remains fail-closed unless calibrated."
    )


@app.command()
def optimize(
    profile_path: Annotated[Path, typer.Option()] = Path("artifacts/optimized-profile.yaml"),
) -> None:
    """Select a measured feasible Pareto profile from existing raw service rows."""

    from a64pilot.optimize.search import (
        candidate_result_from_records,
        select_frozen_deployment,
    )
    from a64pilot.provenance import sha256_file
    from a64pilot.report.integrity import validate_evidence_bundle
    from a64pilot.settings import load_settings

    records, evidence_errors = validate_evidence_bundle("artifacts", require_records=True)
    if evidence_errors:
        typer.echo("Strict evidence validation failed: " + "; ".join(evidence_errors), err=True)
        raise typer.Exit(2)
    grouped: dict[str, list] = {}
    for record in records:
        if record.split != "test" or record.evidence_kind != "measured":
            continue
        grouped.setdefault(record.candidate_id, []).append(record)
    candidates = {}
    baseline = None
    configs: dict[str, dict[str, object]] = {}
    for candidate_id, rows in grouped.items():
        result = candidate_result_from_records(rows)
        configs[candidate_id] = result.config
        if rows[0].stage == "baseline":
            baseline = result
        else:
            candidates[candidate_id] = result
    if baseline is None:
        typer.echo("Fair A1 baseline is missing.", err=True)
        raise typer.Exit(2)
    search_plan_path = Path("artifacts/search-plan.json")
    search_plan = None
    search_plan_sha256 = None
    if search_plan_path.is_file():
        try:
            loaded_plan = json.loads(search_plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            typer.echo("Search plan is unreadable.", err=True)
            raise typer.Exit(2) from exc
        if not isinstance(loaded_plan, dict):
            typer.echo("Search plan root must be a mapping.", err=True)
            raise typer.Exit(2)
        search_plan = loaded_plan
        search_plan_sha256 = sha256_file(search_plan_path)
    try:
        selection = select_frozen_deployment(
            candidates,
            baseline,
            load_settings().quality_gate,
            search_plan=search_plan,
        )
    except ValueError as exc:
        typer.echo(f"Frozen profile selection failed: {exc}", err=True)
        raise typer.Exit(2) from exc
    selected = selection.selected
    payload = {
        "profile_id": selected.candidate_id,
        "status": "measured",
        "backend": selected.backend,
        "model_role": selected.model,
        "cpu_only": True,
        "config": configs[selected.candidate_id],
        "quality_score": selected.quality_score,
        "safety_score": selected.safety_score,
        "p95_latency_ms": selected.p95_latency_ms,
        "requests_per_second": selected.requests_per_second,
        "peak_rss_mb": selected.peak_rss_mb,
        "source_run_ids": selected.source_run_ids,
        "selection_basis": selection.basis,
        "search_plan_sha256": search_plan_sha256,
        "calibration_plan_sha256": search_plan_sha256,
        "frozen_candidate_receipt": selection.frozen_candidate_receipt,
        "gate": {
            key: {"passed": value.passed, "reasons": list(value.reasons)}
            for key, value in selection.decisions.items()
        },
    }
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    typer.echo(f"Selected {selected.candidate_id}; saved {profile_path}")


@app.command()
def report(
    allow_pending: Annotated[
        bool, typer.Option(help="Render an honest no-claims dashboard before Arm measurements.")
    ] = False,
) -> None:
    """Regenerate the offline evidence report from raw artifacts."""

    from a64pilot.report.claims import has_demonstrated_improvement
    from a64pilot.report.render import render_report
    from a64pilot.schemas import Claim

    outputs = render_report()
    claims = json.loads(Path(outputs["claims"]).read_text(encoding="utf-8"))
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")
    if not allow_pending and not has_demonstrated_improvement(
        [Claim.model_validate(item) for item in claims]
    ):
        typer.echo(
            "The preregistered primary mean-TTFT claim does not have a positive "
            "confidence interval above zero; "
            "the report is honest but not submission-ready.",
            err=True,
        )
        raise typer.Exit(2)


async def _fixture_smoke() -> dict[str, object]:
    from a64pilot.agent.validator import validate_response
    from a64pilot.api.app import FIXTURE_MODEL_ID, create_app

    application = create_app(fixture_mode=True)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://a64pilot.local") as client:
        health = await client.get("/health")
        health.raise_for_status()
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-A64Pilot-Debug": "1"},
            json={
                "model": FIXTURE_MODEL_ID,
                "messages": [{"role": "user", "content": "Synthetic alert: /srv is 99% full."}],
                "temperature": 0,
                "max_tokens": 192,
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        validation = validate_response(content)
        if not validation.valid:
            raise RuntimeError("fixture response failed schema/safety validation")
        return {
            "mode": "fixture_not_benchmark_evidence",
            "health": health.json(),
            "response": payload,
            "debug": {
                key: value
                for key, value in response.headers.items()
                if key.lower().startswith("x-a64pilot-")
            },
            "validation": validation.as_dict(),
        }


@app.command()
def smoke(
    fixture: Annotated[
        bool, typer.Option(help="Use the deterministic non-model responder; never evidence.")
    ] = False,
) -> None:
    """Run the fastest end-to-end sanity check."""

    if fixture:
        result = asyncio.run(_fixture_smoke())
        write_json("artifacts/fixture-smoke.json", result)
        _print_json(result)
        return
    typer.echo(
        "Real smoke requires built binaries and model weights. Run `a64pilot benchmark fair --limit 1` on Arm64 Linux.",
        err=True,
    )
    raise typer.Exit(2)


@app.command()
def serve(
    fixture: Annotated[bool, typer.Option(help="Explicit non-model demo responder.")] = False,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8088,
    upstream: Annotated[
        str | None, typer.Option(help="Existing local llama-server base URL.")
    ] = None,
    profile: Annotated[
        Path, typer.Option(help="Measured profile used when no external upstream is supplied.")
    ] = Path("artifacts/optimized-profile.yaml"),
) -> None:
    """Launch the localhost OpenAI-compatible proxy and report endpoint."""

    from a64pilot.api.app import UpstreamResponder, create_app
    from a64pilot.runtime.openai_client import OpenAIClient

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("remote binding is disabled by the stable CLI")
    if fixture and upstream:
        raise typer.BadParameter("choose fixture mode or an upstream, not both")
    if fixture:
        application = create_app(fixture_mode=True)
    elif upstream:
        responder = UpstreamResponder(
            OpenAIClient(upstream),
            upstream_model="a64pilot-selected",
            backend="selected",
            profile_id="external-selected-profile",
            cpu_only_verified=False,
        )
        application = create_app(responder=responder, model_ids=["a64pilot-selected"])
    else:
        from a64pilot.runtime.deployment import DeploymentProfileError, serve_measured_profile

        try:
            serve_measured_profile(profile, host=host, port=port)
        except DeploymentProfileError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        return
    uvicorn.run(application, host=host, port=port, log_level="info")


@app.command()
def demo(
    fixture: Annotated[bool, typer.Option(help="Use the labelled fixture responder.")] = False,
) -> None:
    """Serve the API and generated report locally."""

    serve(
        fixture=fixture,
        host="127.0.0.1",
        port=8088,
        upstream=None,
        profile=Path("artifacts/optimized-profile.yaml"),
    )


@app.command("verify-backends")
def verify_backends() -> None:
    """Require measured generic and verified KleidiAI CPU-only rows."""

    from a64pilot.report.integrity import validate_evidence_bundle

    records, errors = validate_evidence_bundle("artifacts", require_records=True)
    generic = any(record.backend == "generic" and record.cpu_only_verified for record in records)
    kleidiai = any(
        record.backend == "kleidiai" and record.cpu_only_verified and record.kleidiai_verified
        for record in records
    )
    _print_json(
        {
            "generic_cpu_only": generic,
            "kleidiai_cpu_only_and_verified": kleidiai,
            "evidence_errors": errors,
        }
    )
    if errors or not (generic and kleidiai):
        raise typer.Exit(2)


@app.command("verify-claims")
def verify_claims() -> None:
    """Validate that every public claim resolves only to measured raw rows."""

    from a64pilot.report.claims import generate_claims, has_demonstrated_improvement
    from a64pilot.report.integrity import validate_evidence_bundle, verify_claim_sources
    from a64pilot.schemas import Claim

    path = Path("artifacts/claims.json")
    if not path.is_file():
        typer.echo("Missing artifacts/claims.json", err=True)
        raise typer.Exit(2)
    claims = [Claim.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    records, errors = validate_evidence_bundle("artifacts", require_records=True)
    errors.extend(verify_claim_sources(claims, records))
    expected = generate_claims(records, split_path=Path("demo/split.json"))
    if [claim.model_dump(mode="json") for claim in claims] != [
        claim.model_dump(mode="json") for claim in expected
    ]:
        errors.append("claims.json does not exactly match recomputed claims")
    if not has_demonstrated_improvement(claims):
        errors.append(
            "the preregistered primary mean-TTFT claim does not have a positive "
            "confidence interval above zero"
        )
    _print_json({"claims": len(claims), "errors": errors})
    if not claims or errors:
        raise typer.Exit(2)


@app.command()
def submission(
    allow_pending: Annotated[
        bool, typer.Option(help="Render a local draft before target evidence.")
    ] = False,
    video_url: Annotated[str | None, typer.Option(help="Public YouTube/Vimeo/Youku URL.")] = None,
) -> None:
    """Render English Devpost copy, video script, and compliance checklist."""

    from a64pilot.report.submission import SubmissionNotReady, render_submission

    try:
        outputs = render_submission(allow_pending=allow_pending, video_url=video_url)
    except SubmissionNotReady as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")


@app.command()
def verify(
    source_only: Annotated[
        bool, typer.Option(help="Validate source, data, and fixture flow before target evidence.")
    ] = False,
) -> None:
    """Run dataset, secret, provenance, and generated-artifact gates."""

    from a64pilot.benchmark.quality import load_cases, load_split, validate_dataset
    from a64pilot.report.claims import generate_claims, has_demonstrated_improvement
    from a64pilot.report.integrity import (
        SECRET_PATTERN,
        validate_evidence_bundle,
        verify_claim_sources,
        verify_submission_tree,
    )
    from a64pilot.schemas import Claim

    validate_dataset(load_cases("demo/cases.jsonl"), load_split("demo/split.json"))
    secret_hits: list[str] = []
    ignored_roots = {
        ".a64pilot",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "models",
        "third_party",
    }
    for path in Path(".").rglob("*"):
        if not path.is_file() or any(part in ignored_roots for part in path.parts):
            continue
        if path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if SECRET_PATTERN.search(text):
            secret_hits.append(str(path))
    errors = [f"secret pattern: {path}" for path in secret_hits]
    if not source_only:
        from a64pilot.benchmark.cascade import verify_cascade_evidence

        records, evidence_errors = validate_evidence_bundle("artifacts", require_records=True)
        errors.extend(evidence_errors)
        errors.extend(verify_cascade_evidence("artifacts"))
        claims_path = Path("artifacts/claims.json")
        if claims_path.is_file():
            try:
                claims = [
                    Claim.model_validate(item)
                    for item in json.loads(claims_path.read_text(encoding="utf-8"))
                ]
            except Exception as exc:
                claims = []
                errors.append(f"claims schema: {type(exc).__name__}")
            errors.extend(verify_claim_sources(claims, records))
            expected = generate_claims(records, split_path=Path("demo/split.json"))
            if [claim.model_dump(mode="json") for claim in claims] != [
                claim.model_dump(mode="json") for claim in expected
            ]:
                errors.append("claims.json does not exactly match recomputed claims")
            if not claims:
                errors.append("no strict measured claims")
            elif not has_demonstrated_improvement(claims):
                errors.append(
                    "the preregistered primary mean-TTFT claim does not have a positive "
                    "confidence interval above zero"
                )
        else:
            errors.append("missing artifacts/claims.json")
        errors.extend(verify_submission_tree())
    _print_json({"source_only": source_only, "errors": errors})
    if errors:
        raise typer.Exit(2)


if __name__ == "__main__":  # pragma: no cover
    app()
