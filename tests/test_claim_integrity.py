from __future__ import annotations

import json
from pathlib import Path

import pytest

from a64pilot.report.claims import (
    REQUIRED_HELD_OUT_CASES,
    generate_claims,
    has_demonstrated_improvement,
    load_required_test_case_ids,
    verify_claim_held_out_coverage,
)
from a64pilot.report.integrity import verify_claim_sources
from a64pilot.schemas import BenchmarkRecord

ROOT = Path(__file__).resolve().parents[1]
SPLIT_PATH = ROOT / "demo/split.json"
TEST_CASE_IDS = load_required_test_case_ids(SPLIT_PATH)


def row(
    run: str,
    backend: str,
    stage: str,
    latency: float,
    verified: bool,
    *,
    case_id: str = TEST_CASE_IDS[0],
    repetition: int = 0,
    candidate_id: str | None = None,
    quantization: str = "Q4_0",
    split: str = "test",
) -> BenchmarkRecord:
    return BenchmarkRecord(
        run_id=run,
        candidate_id=candidate_id or f"{backend}-q4",
        stage=stage,
        case_id=case_id,
        repetition=repetition,
        split=split,
        backend=backend,
        model_role="strong",
        model_file_sha256="a" * 64,
        quantization=quantization,
        threads=4,
        batch=128,
        ubatch=64,
        parallel=1,
        cpu_only_verified=True,
        kleidiai_verified=verified,
        start_ns=1,
        first_token_ns=2,
        end_ns=3,
        ttft_ms=1,
        e2e_ms=latency,
        completion_tokens=10,
        generation_tok_s=10,
        peak_rss_mb=100,
        schema_valid=True,
        quality_score=100,
        safety_score=100,
        command=["llama-server", "--n-gpu-layers", "0"],
    )


def complete_pair(*, repetitions: int = 1) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for repetition in range(repetitions):
        for index, case_id in enumerate(TEST_CASE_IDS):
            records.extend(
                [
                    row(
                        f"g-{repetition}-{index}",
                        "generic",
                        "baseline",
                        100 + index,
                        False,
                        case_id=case_id,
                        repetition=repetition,
                    ),
                    row(
                        f"k-{repetition}-{index}",
                        "kleidiai",
                        "kleidiai",
                        (100 + index) * 0.8,
                        True,
                        case_id=case_id,
                        repetition=repetition,
                    ),
                ]
            )
    return records


def test_fixed_formal_split_has_exactly_twenty_unique_test_cases() -> None:
    assert len(TEST_CASE_IDS) == REQUIRED_HELD_OUT_CASES == 20
    assert len(set(TEST_CASE_IDS)) == 20


def test_claims_require_complete_fair_measured_held_out_pair() -> None:
    records = complete_pair()
    claims = generate_claims(records, split_path=SPLIT_PATH)
    assert len(claims) == 2
    assert claims[0].value == pytest.approx(20)
    assert len(claims[0].source_rows) == 40
    assert verify_claim_sources(claims, records) == []
    assert verify_claim_held_out_coverage(claims, records, split_path=SPLIT_PATH) == []


def test_single_case_smoke_never_generates_headline_claim() -> None:
    records = [
        row("g1", "generic", "baseline", 100, False),
        row("k1", "kleidiai", "kleidiai", 80, True),
    ]
    assert generate_claims(records, split_path=SPLIT_PATH) == []


def test_micro_smoke_rows_do_not_contaminate_later_complete_formal_pair() -> None:
    smoke = [
        row("smoke-g", "generic", "baseline", 100, False, split="micro"),
        row("smoke-k", "kleidiai", "kleidiai", 80, True, split="micro"),
    ]
    claims = generate_claims([*smoke, *complete_pair()], split_path=SPLIT_PATH)
    assert len(claims) == 2
    assert all("smoke" not in run_id for claim in claims for run_id in claim.source_rows)


def test_test_labeled_smoke_duplicate_fail_closes_formal_claim() -> None:
    smoke = [
        row("smoke-g", "generic", "baseline", 100, False),
        row("smoke-k", "kleidiai", "kleidiai", 80, True),
    ]
    assert generate_claims([*smoke, *complete_pair()], split_path=SPLIT_PATH) == []


def test_ten_case_quick_run_never_generates_headline_claim() -> None:
    records = complete_pair()[:20]
    assert len({record.case_id for record in records}) == 10
    assert generate_claims(records, split_path=SPLIT_PATH) == []


def test_missing_case_on_either_backend_prevents_claim() -> None:
    records = complete_pair()
    missing_generic = [
        record
        for record in records
        if not (record.backend == "generic" and record.case_id == TEST_CASE_IDS[-1])
    ]
    assert generate_claims(missing_generic, split_path=SPLIT_PATH) == []

    missing_kleidiai = [
        record
        for record in records
        if not (record.backend == "kleidiai" and record.case_id == TEST_CASE_IDS[-1])
    ]
    assert generate_claims(missing_kleidiai, split_path=SPLIT_PATH) == []


def test_pairing_uses_case_and_repetition_not_run_id_order() -> None:
    records = complete_pair(repetitions=2)
    claims = generate_claims(list(reversed(records)), split_path=SPLIT_PATH)
    assert claims[0].value == pytest.approx(20)
    assert len(claims[0].source_rows) == 80


def test_nonuniform_or_duplicate_repetitions_prevent_claim() -> None:
    records = complete_pair()
    duplicate = records + [records[0].model_copy(update={"run_id": "duplicate-row"})]
    assert generate_claims(duplicate, split_path=SPLIT_PATH) == []

    records = complete_pair(repetitions=2)
    nonuniform = [
        record
        for record in records
        if not (
            record.repetition == 1
            and record.case_id == TEST_CASE_IDS[-1]
            and record.backend == "kleidiai"
        )
    ]
    assert generate_claims(nonuniform, split_path=SPLIT_PATH) == []


def test_nonpositive_latency_prevents_claim() -> None:
    records = complete_pair()
    records[0] = records[0].model_copy(update={"e2e_ms": 0.0})
    assert generate_claims(records, split_path=SPLIT_PATH) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantization", "Q4_K_M"),
        ("model_file_sha256", "b" * 64),
        ("threads", 8),
        ("batch", 256),
        ("parallel", 2),
        ("affinity", [0, 1]),
    ],
)
def test_any_backend_ablation_mismatch_prevents_claim(field: str, value: object) -> None:
    records = complete_pair()
    records = [
        record.model_copy(update={field: value}) if record.backend == "kleidiai" else record
        for record in records
    ]
    assert generate_claims(records, split_path=SPLIT_PATH) == []


def test_fixture_micro_or_calibration_rows_never_generate_claims() -> None:
    generic = row("g1", "generic", "baseline", 100, False).model_copy(
        update={
            "evidence_kind": "fixture",
            "split": "fixture",
            "backend": "fixture",
            "model_role": "fixture",
        }
    )
    assert generate_claims([generic], split_path=SPLIT_PATH) == []

    for split in ("micro", "calibration"):
        records = [record.model_copy(update={"split": split}) for record in complete_pair()]
        assert generate_claims(records, split_path=SPLIT_PATH) == []


def test_coverage_verifier_rejects_tampered_partial_claim_sources() -> None:
    records = complete_pair()
    claims = generate_claims(records, split_path=SPLIT_PATH)
    tampered = [
        claim.model_copy(update={"source_rows": claim.source_rows[:-2]}) for claim in claims
    ]
    errors = verify_claim_held_out_coverage(tampered, records, split_path=SPLIT_PATH)
    assert len(errors) == 2
    assert all("20-case held-out pair" in error for error in errors)


def test_split_loader_rejects_non_twenty_or_duplicate_manifest(tmp_path: Path) -> None:
    too_short = tmp_path / "short.json"
    too_short.write_text(json.dumps({"test": list(TEST_CASE_IDS[:-1])}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 20 unique"):
        load_required_test_case_ids(too_short)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps({"test": [*TEST_CASE_IDS[:-1], TEST_CASE_IDS[0]]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="exactly 20 unique"):
        load_required_test_case_ids(duplicate)


def test_submission_winner_requires_positive_interval() -> None:
    winning = generate_claims(complete_pair(), split_path=SPLIT_PATH)
    assert has_demonstrated_improvement(winning)
    inconclusive = [
        claim.model_copy(update={"demonstrated": False, "confidence_interval": (-1.0, 2.0)})
        for claim in winning
    ]
    assert not has_demonstrated_improvement(inconclusive)
