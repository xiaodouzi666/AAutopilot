"""Generate headline claims only from complete fair held-out record pairs."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from a64pilot.benchmark.statistics import (
    latency_reduction_pct,
    paired_bootstrap_interval,
    throughput_increase_pct,
)
from a64pilot.schemas import BenchmarkRecord, Claim

DEFAULT_SPLIT_PATH = Path("demo/split.json")
REQUIRED_HELD_OUT_CASES = 20
FAIR_Q4_QUANTIZATIONS = frozenset({"Q4_0"})
PRIMARY_CLAIM_ID = "fair_q4_0_mean_ttft_reduction"


def load_required_test_case_ids(
    split_path: Path | str = DEFAULT_SPLIT_PATH,
) -> tuple[str, ...]:
    """Load and strictly validate the fixed formal held-out case IDs."""

    path = Path(split_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["test"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot load formal held-out split: {path}") from exc
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError("formal held-out split must be a list of non-empty case IDs")
    case_ids = tuple(values)
    if len(case_ids) != REQUIRED_HELD_OUT_CASES or len(set(case_ids)) != len(case_ids):
        raise ValueError(
            f"formal held-out split must contain exactly {REQUIRED_HELD_OUT_CASES} unique case IDs"
        )
    return case_ids


def _signature(record: BenchmarkRecord) -> tuple[object, ...]:
    """Fields that must be identical for the direct backend ablation."""

    return (
        record.model_file_sha256,
        record.quantization.upper(),
        record.model_role,
        record.threads,
        record.batch,
        record.ubatch,
        record.parallel,
        record.context,
        tuple(record.affinity),
        record.split,
    )


def _pair_keys(records: Sequence[BenchmarkRecord]) -> list[tuple[str, int]]:
    return [(record.case_id, record.repetition) for record in records]


def _uniform_repetitions(
    records: Sequence[BenchmarkRecord], required_case_ids: frozenset[str]
) -> bool:
    repetitions: dict[str, set[int]] = defaultdict(set)
    for record in records:
        repetitions[record.case_id].add(record.repetition)
    if set(repetitions) != set(required_case_ids):
        return False
    expected = next(iter(repetitions.values()), set())
    if not expected or expected != set(range(max(expected) + 1)):
        return False
    return all(values == expected for values in repetitions.values())


def _eligible_pair(
    generic: Sequence[BenchmarkRecord],
    kleidiai: Sequence[BenchmarkRecord],
    required_case_ids: frozenset[str],
) -> bool:
    if not generic or len(generic) != len(kleidiai):
        return False
    generic_keys = _pair_keys(generic)
    kleidiai_keys = _pair_keys(kleidiai)
    if len(generic_keys) != len(set(generic_keys)):
        # Re-running into the same raw store must not silently double-weight
        # whichever cases happen to have duplicate repetition indices.
        return False
    if len(kleidiai_keys) != len(set(kleidiai_keys)):
        return False
    if generic_keys != kleidiai_keys:
        return False
    if {record.case_id for record in generic} != set(required_case_ids):
        return False
    if not _uniform_repetitions(generic, required_case_ids):
        return False
    if any(
        record.evidence_kind != "measured"
        or record.split != "test"
        or record.backend != "generic"
        or record.stage != "baseline"
        or record.model_role != "strong"
        or record.quantization.upper() not in FAIR_Q4_QUANTIZATIONS
        or not record.cpu_only_verified
        for record in generic
    ):
        return False
    if any(
        record.evidence_kind != "measured"
        or record.split != "test"
        or record.backend != "kleidiai"
        or record.stage != "kleidiai"
        or record.model_role != "strong"
        or record.quantization.upper() not in FAIR_Q4_QUANTIZATIONS
        or not record.cpu_only_verified
        or not record.kleidiai_verified
        for record in kleidiai
    ):
        return False
    if len({_signature(record) for record in (*generic, *kleidiai)}) != 1:
        return False
    if any(
        record.e2e_ms <= 0
        or not math.isfinite(record.e2e_ms)
        or record.ttft_ms is None
        or record.ttft_ms <= 0
        or not math.isfinite(record.ttft_ms)
        or not math.isfinite(record.quality_score)
        or not math.isfinite(record.safety_score)
        for record in (*generic, *kleidiai)
    ):
        return False
    if not all(
        record.schema_valid and record.safety_score == 100.0 for record in (*generic, *kleidiai)
    ):
        return False
    generic_quality = sum(record.quality_score for record in generic) / len(generic)
    kleidiai_quality = sum(record.quality_score for record in kleidiai) / len(kleidiai)
    return kleidiai_quality >= generic_quality - 1.0


def _fair_pairs(
    records: Iterable[BenchmarkRecord],
    required_case_ids: frozenset[str],
) -> tuple[list[BenchmarkRecord], list[BenchmarkRecord]] | None:
    groups: dict[
        tuple[object, ...],
        dict[str, dict[str, list[BenchmarkRecord]]],
    ] = defaultdict(lambda: {"generic": defaultdict(list), "kleidiai": defaultdict(list)})
    for record in records:
        if record.split != "test" or record.evidence_kind != "measured":
            continue
        if record.backend == "generic" and record.stage == "baseline" and record.cpu_only_verified:
            groups[_signature(record)]["generic"][record.candidate_id].append(record)
        elif (
            record.backend == "kleidiai"
            and record.stage == "kleidiai"
            and record.cpu_only_verified
            and record.kleidiai_verified
        ):
            groups[_signature(record)]["kleidiai"][record.candidate_id].append(record)

    eligible: list[
        tuple[
            int,
            str,
            str,
            tuple[object, ...],
            list[BenchmarkRecord],
            list[BenchmarkRecord],
        ]
    ] = []
    for signature, backends in groups.items():
        for generic_id, generic_rows in backends["generic"].items():
            generic = sorted(generic_rows, key=lambda item: (item.case_id, item.repetition))
            for kleidiai_id, kleidiai_rows in backends["kleidiai"].items():
                kleidiai = sorted(kleidiai_rows, key=lambda item: (item.case_id, item.repetition))
                if _eligible_pair(generic, kleidiai, required_case_ids):
                    eligible.append(
                        (
                            len(generic),
                            generic_id,
                            kleidiai_id,
                            signature,
                            generic,
                            kleidiai,
                        )
                    )
    if not eligible:
        return None
    *_, generic, kleidiai = max(
        eligible,
        key=lambda item: (item[0], item[1], item[2], str(item[3])),
    )
    return generic, kleidiai


def verify_claim_held_out_coverage(
    claims: Iterable[Claim],
    records: Iterable[BenchmarkRecord],
    *,
    split_path: Path | str = DEFAULT_SPLIT_PATH,
) -> list[str]:
    """Recheck that every formal claim cites one complete A1/A2 pair."""

    required = frozenset(load_required_test_case_ids(split_path))
    record_map = {record.run_id: record for record in records}
    errors: list[str] = []
    for claim in claims:
        if len(claim.source_rows) != len(set(claim.source_rows)):
            errors.append(f"{claim.claim_id}: duplicate source row IDs")
            continue
        source = [record_map[row_id] for row_id in claim.source_rows if row_id in record_map]
        if len(source) != len(claim.source_rows):
            # Missing rows are reported by the general provenance verifier.
            continue
        generic = sorted(
            (record for record in source if record.candidate_id == claim.baseline_candidate),
            key=lambda item: (item.case_id, item.repetition),
        )
        kleidiai = sorted(
            (record for record in source if record.candidate_id == claim.optimized_candidate),
            key=lambda item: (item.case_id, item.repetition),
        )
        if len(generic) + len(kleidiai) != len(source):
            errors.append(f"{claim.claim_id}: source rows include unrelated candidates")
            continue
        if not _eligible_pair(generic, kleidiai, required):
            generic_cases = len({record.case_id for record in generic})
            kleidiai_cases = len({record.case_id for record in kleidiai})
            errors.append(
                f"{claim.claim_id}: formal A1/A2 sources are not a fair complete "
                f"{REQUIRED_HELD_OUT_CASES}-case held-out pair "
                f"(generic={generic_cases}, kleidiai={kleidiai_cases})"
            )
    return errors


def generate_claims(
    records: Iterable[BenchmarkRecord],
    *,
    split_path: Path | str = DEFAULT_SPLIT_PATH,
) -> list[Claim]:
    required_case_ids = frozenset(load_required_test_case_ids(split_path))
    pair = _fair_pairs(records, required_case_ids)
    if pair is None:
        return []
    generic, kleidiai = pair
    baseline_latency = [item.e2e_ms for item in generic]
    optimized_latency = [item.e2e_ms for item in kleidiai]
    baseline_p95 = float(np.percentile(baseline_latency, 95))
    optimized_p95 = float(np.percentile(optimized_latency, 95))
    latency_value = latency_reduction_pct(baseline_p95, optimized_p95)
    latency_ci = paired_bootstrap_interval(
        baseline_latency,
        optimized_latency,
        reducer=lambda values: float(np.percentile(values, 95)),
    )

    baseline_rps = [1000.0 / item.e2e_ms for item in generic if item.e2e_ms > 0]
    optimized_rps = [1000.0 / item.e2e_ms for item in kleidiai if item.e2e_ms > 0]
    base_rps = float(np.median(baseline_rps))
    opt_rps = float(np.median(optimized_rps))
    throughput_value = throughput_increase_pct(base_rps, opt_rps)
    throughput_ci = paired_bootstrap_interval(
        baseline_rps,
        optimized_rps,
        statistic=throughput_increase_pct,
    )

    baseline_ttft = [float(item.ttft_ms) for item in generic if item.ttft_ms is not None]
    optimized_ttft = [float(item.ttft_ms) for item in kleidiai if item.ttft_ms is not None]
    baseline_mean_ttft = float(np.mean(baseline_ttft))
    optimized_mean_ttft = float(np.mean(optimized_ttft))
    ttft_value = latency_reduction_pct(baseline_mean_ttft, optimized_mean_ttft)
    ttft_ci = paired_bootstrap_interval(
        baseline_ttft,
        optimized_ttft,
        reducer=lambda values: float(np.mean(values)),
    )

    rows = [item.run_id for item in (*generic, *kleidiai)]
    baseline_id = generic[0].candidate_id
    optimized_id = kleidiai[0].candidate_id
    return [
        Claim(
            claim_id=PRIMARY_CLAIM_ID,
            metric="Q4_0 mean time-to-first-token reduction",
            value=ttft_value,
            unit="%",
            baseline_candidate=baseline_id,
            optimized_candidate=optimized_id,
            source_rows=rows,
            formula=(
                "(generic_q4_0_mean_ttft_ms - kleidiai_q4_0_mean_ttft_ms) / "
                "generic_q4_0_mean_ttft_ms * 100"
            ),
            confidence_interval=ttft_ci,
            demonstrated=ttft_ci[0] > 0,
        ),
        Claim(
            claim_id="fair_q4_0_p95_latency_reduction",
            metric="Q4_0 p95 end-to-end latency reduction",
            value=latency_value,
            unit="%",
            baseline_candidate=baseline_id,
            optimized_candidate=optimized_id,
            source_rows=rows,
            formula="(generic_q4_0_p95_ms - kleidiai_q4_0_p95_ms) / generic_q4_0_p95_ms * 100",
            confidence_interval=latency_ci,
            demonstrated=latency_ci[0] > 0,
        ),
        Claim(
            claim_id="fair_q4_0_request_throughput_increase",
            metric="Q4_0 median per-request throughput increase",
            value=throughput_value,
            unit="%",
            baseline_candidate=baseline_id,
            optimized_candidate=optimized_id,
            source_rows=rows,
            formula="(kleidiai_q4_0_median_rps - generic_q4_0_median_rps) / generic_q4_0_median_rps * 100",
            confidence_interval=throughput_ci,
            demonstrated=throughput_ci[0] > 0,
        ),
    ]


def has_demonstrated_improvement(claims: Iterable[Claim]) -> bool:
    """Return whether the prospectively registered primary claim survives uncertainty.

    End-to-end p95 latency and request throughput remain transparent secondary outcomes; they
    cannot unlock publication by chance when the primary mean-TTFT interval crosses zero.
    """

    return any(
        claim.claim_id == PRIMARY_CLAIM_ID
        and claim.demonstrated
        and claim.value > 0
        and claim.confidence_interval is not None
        and claim.confidence_interval[0] > 0
        for claim in claims
    )


__all__ = [
    "DEFAULT_SPLIT_PATH",
    "FAIR_Q4_QUANTIZATIONS",
    "PRIMARY_CLAIM_ID",
    "REQUIRED_HELD_OUT_CASES",
    "generate_claims",
    "has_demonstrated_improvement",
    "load_required_test_case_ids",
    "verify_claim_held_out_coverage",
]
